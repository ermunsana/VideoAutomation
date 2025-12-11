import os
import random
import re
import concurrent.futures
import numpy as np
from pydub import AudioSegment
from moviepy import VideoFileClip, AudioFileClip, CompositeVideoClip, ImageClip
from PIL import Image, ImageDraw
import yt_dlp
from syncedlyrics import search as lrc_search
from font import get_font
import whisperx
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

# ---------------- CONFIG ----------------
LINKS_FILE = "links.txt"
TRASH_FILE = "trash.txt"
BACKGROUND_FOLDER = "backgrounds"
FINAL_FOLDER = "final"
MP3_FOLDER = "mp3"
SEGMENT_DURATION_S = 35
VIDEO_SIZE = 1080
TITLE_FONT_SIZE = 50
LYRIC_FONT_SIZE = 40
MIN_LINE_DURATION_S = 1
CHAR_FACTOR = 0.2
GLOBAL_SYNC_OFFSET_S = 0.0
START_TIME_S = None
SHOW_TITLE = False
MAX_VIDEO_DURATION = 45  # seconds

# Spotify API credentials
SPOTIFY_CLIENT_ID = "9ee4f1bd0800439e888bb839adb47721"
SPOTIFY_CLIENT_SECRET = "07c3077cfc814cf3a5638ab5a711b16e"
sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID,
                                                           client_secret=SPOTIFY_CLIENT_SECRET))

os.makedirs(MP3_FOLDER, exist_ok=True)
os.makedirs(FINAL_FOLDER, exist_ok=True)

# ---------------- LINK HANDLING ----------------
def get_next_link():
    if not os.path.exists(LINKS_FILE):
        raise Exception("links.txt missing.")
    with open(LINKS_FILE, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f.readlines() if l.strip()]
    if not lines:
        raise Exception("No links left in links.txt.")
    link = lines[0]
    with open(LINKS_FILE, "w", encoding="utf-8") as f:
        for l in lines[1:]:
            f.write(l + "\n")
    with open(TRASH_FILE, "a", encoding="utf-8") as f:
        f.write(link + "\n")
    return link

def is_spotify_link(url):
    return "open.spotify.com/track" in url or url.startswith("spotify:track:")

def get_spotify_track_id(url):
    if "open.spotify.com/track/" in url:
        return url.split("track/")[1].split("?")[0]
    if url.startswith("spotify:track:"):
        return url.split(":")[2]
    return None

def get_spotify_metadata(track_id):
    track = sp.track(track_id)
    artist = ", ".join([a['name'] for a in track['artists']])
    song = track['name']
    duration_s = track['duration_ms'] / 1000
    return {"artist": artist, "song": song, "duration": duration_s}

# ---------------- YOUTUBE AUDIO ----------------
def search_youtube(song, artist):
    query = f"{artist} {song}"
    ydl_opts = {"quiet": True, "no_warnings": True, "format": "bestaudio/best"}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.extract_info(f"ytsearch1:{query}", download=False)
        return result['entries'][0]['webpage_url']

def download_audio(url, song_title):
    clean_title_str = re.sub(r'[^\w\d-]', '_', song_title.lower())
    temp_path = os.path.join(MP3_FOLDER, f"{clean_title_str}_temp.mp3")
    if os.path.exists(temp_path):
        return AudioSegment.from_mp3(temp_path)
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}],
        'outtmpl': os.path.join(MP3_FOLDER, "yt_audio"),
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    os.rename(os.path.join(MP3_FOLDER, "yt_audio.mp3"), temp_path)
    return AudioSegment.from_mp3(temp_path)

# ---------------- LRC ----------------
def fetch_lrc_corrected(artist, song, audio_duration_s, timeout=20):
    term = f"{artist} {song}".lower()
    try:
        with concurrent.futures.ThreadPoolExecutor() as ex:
            future = ex.submit(lrc_search, term)
            results = future.result(timeout=timeout)
        if not results:
            return None
        best_lrc, best_diff = None, float("inf")
        for lrc_content in results if isinstance(results, list) else [results]:
            lines = parse_lrc_content(lrc_content)
            if not lines: continue
            lrc_start, lrc_end = lines[0][0][0], lines[-1][0][1]
            diff = abs((lrc_end - lrc_start) - audio_duration_s)
            if diff < best_diff:
                best_diff, best_lrc = diff, lrc_content
        return best_lrc
    except:
        return None

def parse_lrc_content(lrc_content):
    if not lrc_content: return []
    lines=[]
    for line in lrc_content.splitlines():
        line=line.strip()
        if not line.startswith("["): continue
        try:
            ts,txt=line.split("]",1)
            ts=ts[1:]
            mm,rest=ts.split(":")
            ss,ms=rest.split(".")
            start=int(mm)*60+int(ss)+int(ms)/100
            start+=GLOBAL_SYNC_OFFSET_S
            txt=txt.strip()
            if txt.lower() not in ("instrumental","(instrumental)"):
                lines.append((start,txt))
        except: continue
    final=[]
    for i,(t0,txt) in enumerate(lines):
        t_next = lines[i+1][0] if i<len(lines)-1 else t0+max(MIN_LINE_DURATION_S,len(txt)*CHAR_FACTOR)
        duration=max(MIN_LINE_DURATION_S,min(t_next-t0,len(txt)*CHAR_FACTOR))
        final.append(((t0,t0+duration),txt))
    return final

# ---------------- WHISPERX ----------------
def run_whisperx(audio_path):
    model = whisperx.load_model("base", device="cpu")
    result = model.transcribe(audio_path)
    model_a, metadata = whisperx.load_align_model(language_code=result["language"], device="cpu")
    result_aligned = whisperx.align(result["segments"], model_a, metadata, audio_path, device="cpu")
    word_timings = [(w["start"], w["end"], w["word"]) for w in result_aligned["word_segments"]]
    return word_timings

# ---------------- MERGE LRC + WHISPERX ----------------
def merge_lrc_whisperx(lrc_subs, whisper_words):
    merged = []
    w_idx = 0
    for (line_start, line_end), line_text in lrc_subs:
        words = line_text.split()
        line_clips = []
        for word in words:
            while w_idx < len(whisper_words) and whisper_words[w_idx][0] < line_start:
                w_idx += 1
            if w_idx < len(whisper_words) and whisper_words[w_idx][2].lower() == word.lower():
                start, end, _ = whisper_words[w_idx]
                w_idx += 1
            else:
                start = line_start + (line_end - line_start) * len(line_clips)/len(words)
                end = line_start + (line_end - line_start) * (len(line_clips)+1)/len(words)
            line_clips.append(((start, end), word))
        merged.append(((line_clips[0][0][0], line_clips[-1][0][1]), " ".join([w[1] for w in line_clips])))
    return merged

# ---------------- VIDEO ----------------
def sanitize_filename(name):
    return re.sub(r'[<>:"/\\|?*]', '_', name)

def make_text_clip_grid(lines, start, end, song_title_words):
    img = Image.new("RGBA", (VIDEO_SIZE, VIDEO_SIZE), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    font = get_font(LYRIC_FONT_SIZE)
    max_words_per_line = 3
    grid = []

    for line in lines:
        words = line.split()
        temp = []
        for w in words:
            temp.append(w)
            if len(temp) >= max_words_per_line:
                grid.append(" ".join(temp))
                temp = []
        if temp:
            grid.append(" ".join(temp))

    y_offset = (VIDEO_SIZE - LYRIC_FONT_SIZE * len(grid)) // 2

    for line in grid:
        words_in_line = line.split()
        total_width = sum(draw.textlength(w, font=font) for w in words_in_line) + 10*(len(words_in_line)-1)
        x_offset = (VIDEO_SIZE - total_width)//2

        for w in words_in_line:
            fill_color = "white"

            # subtle white glow
            for dx in [-1,0,1]:
                for dy in [-1,0,1]:
                    if dx != 0 or dy != 0:
                        draw.text((x_offset+dx, y_offset+dy), w, font=font, fill="white")

            draw.text((x_offset, y_offset), w, font=font, fill=fill_color)
            x_offset += draw.textlength(w, font=font) + 10

        y_offset += LYRIC_FONT_SIZE

    return ImageClip(np.array(img)).with_start(start).with_duration(end-start)

def make_incremental_word_clips(t0, t1, text, song_title_words):
    words = text.split()
    if not words:
        return []
    duration = t1 - t0
    total_chars = sum(len(w) for w in words)
    clips = []
    current_start = t0
    for i, w in enumerate(words):
        word_duration = duration * len(w)/total_chars
        current_end = current_start + word_duration
        partial = " ".join(words[:i+1])
        clips.append(make_text_clip_grid([partial], current_start, current_end, song_title_words))
        current_start = current_end
    return clips

def create_video(audio_segment, subtitles, bg_folder, output_file, song_title, start_time, incremental=True):
    clean_title_str = sanitize_filename(song_title)
    temp_audio = os.path.join(MP3_FOLDER,f"{clean_title_str}_temp.mp3")
    audio_segment.export(temp_audio, format="mp3")
    audio_clip = AudioFileClip(temp_audio)
    total = audio_clip.duration

    vids = [os.path.join(bg_folder,f) for f in os.listdir(bg_folder) if f.lower().endswith((".mp4",".mov",".mkv"))]
    if not vids: raise Exception("No background videos in folder.")
    bg = random.choice(vids)
    bg_clip = VideoFileClip(bg)
    if bg_clip.duration > total:
        bg_clip = bg_clip.subclipped(0, total)

    song_title_words = set(song_title.lower().split())
    word_clips=[]
    if incremental:
        for (t0,t1),text in subtitles:
            word_clips.extend(make_incremental_word_clips(t0, t1, text, song_title_words))
    else:
        for (t0,t1),text in subtitles:
            word_clips.append(make_text_clip_grid([text], t0, t1, song_title_words))

    clips = [bg_clip] + word_clips

    if SHOW_TITLE:
        img = Image.new("RGBA", (VIDEO_SIZE, VIDEO_SIZE), (0,0,0,0))
        draw = ImageDraw.Draw(img)
        font = get_font(TITLE_FONT_SIZE)
        bbox = draw.textbbox((0,0), song_title, font=font)
        w,h = bbox[2]-bbox[0], bbox[3]-bbox[1]
        pos = ((VIDEO_SIZE-w)//2, 50)
        draw.text(pos, song_title, font=font, fill="white", stroke_width=5, stroke_fill="black")
        clips.append(ImageClip(np.array(img)).with_start(0).with_duration(total))

    final = CompositeVideoClip(clips, size=(VIDEO_SIZE, VIDEO_SIZE)).with_audio(audio_clip)
    final_path = os.path.join(FINAL_FOLDER, sanitize_filename(os.path.basename(output_file)))
    if os.path.exists(final_path):
        base,ext = os.path.splitext(final_path)
        final_path = f"{base}_{int(start_time)}{ext}"

    final.write_videofile(final_path, fps=30, codec="libx264", audio_codec="aac",
                          preset="medium", ffmpeg_params=["-pix_fmt","yuv420p"])
    os.remove(temp_audio)
    print(f"✓ Video ready: {final_path} (duration: {total:.2f}s)")
    return final_path, total

def determine_start_time(song_title):
    base_name = sanitize_filename(song_title.replace(" ", "_"))
    existing = [f for f in os.listdir(FINAL_FOLDER) if f.startswith(base_name)]
    max_start = 0
    for f in existing:
        match = re.search(r'_(\d+)\.mp4$', f)
        if match:
            val = int(match.group(1))
            if val > max_start:
                max_start = val
    return max_start

def get_segment(audio, duration_s, manual_start, lyrics=None):
    if manual_start is not None:
        start_ms = max(0, min(int(manual_start*1000), len(audio)-int(duration_s*1000)))
        return audio[start_ms:start_ms+int(duration_s*1000)], start_ms/1000
    first_lyric_time = lyrics[0][0][0] if lyrics else 0.0
    start_ms = int(first_lyric_time*1000)
    trimmed = audio[start_ms:start_ms+int(duration_s*1000)]
    return trimmed, start_ms/1000

# ---------------- MAIN ----------------
if __name__ == "__main__":
    LINK = get_next_link()

    if is_spotify_link(LINK):
        track_id = get_spotify_track_id(LINK)
        meta = get_spotify_metadata(track_id)
        print(f"✓ Spotify track: {meta['song']} by {meta['artist']}")
        youtube_url = search_youtube(meta['song'], meta['artist'])
        audio = download_audio(youtube_url, meta['song'])
    else:
        youtube_url = LINK
        def get_youtube_metadata(url):
            opts = {"quiet": True, "no_warnings": True, "format": "bestaudio/best"}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            raw_title = info.get("title", "").strip()
            uploader = info.get("uploader", "").strip()
            artist, song = uploader, raw_title
            quoted = re.findall(r'"([^"]+)"', raw_title)
            if quoted:
                song = quoted[0].strip()
                rest = raw_title.replace(f'"{song}"', '').strip()
                artist = rest if rest else uploader
            elif ' - ' in raw_title:
                parts = raw_title.split(' - ', 1)
                artist, song = parts[0].strip(), parts[1].strip()
            song = re.sub(r"(official video|official audio|lyrics|mv|hd|4k|audio|video)", "", song, flags=re.I)
            song = re.sub(r"\(.*?\)|\[.*?\]", "", song)
            song = " ".join(song.split()).title()
            return {"artist": artist, "song": song, "full_title": raw_title}
        meta = get_youtube_metadata(LINK)
        audio = download_audio(LINK, meta['song'])

    OUTPUT_VIDEO_INC = meta['song'].replace(" ","_")+"_lyric_video_incremental.mp4"
    OUTPUT_VIDEO_STD = meta['song'].replace(" ","_")+"_lyric_video_standard.mp4"

    lrc = fetch_lrc_corrected(meta['artist'], meta['song'], SEGMENT_DURATION_S)
    subs_full = parse_lrc_content(lrc) if lrc else []

    clean_title_str = re.sub(r'[^\w\d-]', '_', meta['song'].lower())
    temp_audio_path = os.path.join(MP3_FOLDER, f"{clean_title_str}_temp.mp3")
    audio.export(temp_audio_path, format="mp3")
    try:
        whisper_words = run_whisperx(temp_audio_path)
        if whisper_words:
            subs_full = merge_lrc_whisperx(subs_full, whisper_words)
    except Exception as e:
        print("⚠ WhisperX failed, falling back to LRC only:", e)
        whisper_words = []

    # set START_TIME_S to first lyric
    if subs_full:
        START_TIME_S = subs_full[0][0][0]
    else:
        START_TIME_S = 0

    if START_TIME_S is None:
        segment_duration = SEGMENT_DURATION_S
    else:
        last_lyric_time = subs_full[-1][0][1] if subs_full else 0
        segment_duration = min(MAX_VIDEO_DURATION, last_lyric_time - START_TIME_S)

    trimmed, start = get_segment(audio, segment_duration, START_TIME_S, lyrics=subs_full)

    # adjust subtitle timings to start from 0
    subs_adj=[]
    for (t0,t1),text in subs_full:
        t0 -= START_TIME_S
        t1 -= START_TIME_S
        if t1>0 and t0<segment_duration:
            t0n=max(0,t0)
            t1n=min(segment_duration,max(t0n+0.1,t1))
            subs_adj.append(((t0n,t1n),text))

    print(f"✓ Segment actual start: {START_TIME_S:.2f}s, {len(subs_adj)} lyric lines, segment duration: {segment_duration:.2f}s")

    create_video(trimmed, subs_adj, BACKGROUND_FOLDER, OUTPUT_VIDEO_INC, meta['song'], START_TIME_S, incremental=True)
    create_video(trimmed, subs_adj, BACKGROUND_FOLDER, OUTPUT_VIDEO_STD, meta['song'], START_TIME_S, incremental=False)

    print("--- Done ---")
    