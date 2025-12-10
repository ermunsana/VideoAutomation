import os
import random
import numpy as np
from pydub import AudioSegment
from moviepy import VideoFileClip, AudioFileClip, CompositeVideoClip, ImageClip
from PIL import Image, ImageDraw
import yt_dlp
from syncedlyrics import search as lrc_search
import concurrent.futures
from font import get_font
import re

# ---------------- CONFIG ----------------
LINKS_FILE = "links.txt"
TRASH_FILE = "trash.txt"
BACKGROUND_FOLDER = "backgrounds"
FINAL_FOLDER = "final"
SEGMENT_DURATION_S = 30 
VIDEO_SIZE = 1080
TITLE_FONT_SIZE = 50
LYRIC_FONT_SIZE = 40
MIN_LINE_DURATION_S = 1.0
CHAR_FACTOR = 0.2
GLOBAL_SYNC_OFFSET_S = 0.0
START_TIME_S = None
SHOW_TITLE = False
MP3_FOLDER = "mp3"
MAX_VIDEO_DURATION = 45  # seconds

os.makedirs(MP3_FOLDER, exist_ok=True)
os.makedirs(FINAL_FOLDER, exist_ok=True)

# ---------------- LINK HANDLING ----------------
def get_next_link():
    if not os.path.exists(LINKS_FILE):
        raise Exception("links.txt missing.")
    with open(LINKS_FILE, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f.readlines() if l.strip()]
    if not lines:
        raise Exception("No YouTube links left in links.txt.")
    link = lines[0]
    with open(LINKS_FILE, "w", encoding="utf-8") as f:
        for l in lines[1:]:
            f.write(l + "\n")
    with open(TRASH_FILE, "a", encoding="utf-8") as f:
        f.write(link + "\n")
    return link

# ---------------- YOUTUBE METADATA ----------------
def get_youtube_metadata(url):
    opts = {"quiet": True, "no_warnings": True}
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

    trash_words = ["official video","official audio","music video","lyrics","lyric video","audio","video","mv","hd","4k"]
    t = song.lower()
    for w in trash_words:
        t = t.replace(w, "")
    t = t.replace("-", " ")
    t = re.sub(r"\(.*?\)", "", t)
    t = re.sub(r"\[.*?\]", "", t)
    t = " ".join(t.split())
    song = t.title()

    return {"artist": artist, "song": song, "full_title": raw_title}

# ---------------- AUDIO ----------------
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

def get_segment(audio, duration_s, manual_start, lyrics=None):
    if manual_start is not None:
        start_ms = max(0, min(int(manual_start*1000), len(audio)-int(duration_s*1000)))
        return audio[start_ms:start_ms+int(duration_s*1000)], start_ms/1000

    # Default: start at first lyric or beginning
    first_lyric_time = lyrics[0][0][0] if lyrics else 0.0
    start_ms = int(first_lyric_time*1000)
    trimmed = audio[start_ms:start_ms+int(duration_s*1000)]
    return trimmed, start_ms/1000

# ---------------- LYRICS ----------------
def fetch_lrc_corrected(artist, song, audio_duration_s, timeout=20):
    term = f"{artist} {song}".lower()
    try:
        with concurrent.futures.ThreadPoolExecutor() as ex:
            future = ex.submit(lrc_search, term)
            results = future.result(timeout=timeout)
        if not results:
            return None

        best_lrc, best_diff = None, float("inf")
        for lrc_content in results if isinstance(results,list) else [results]:
            lines = parse_lrc_content(lrc_content)
            if not lines: continue
            lrc_start, lrc_end = lines[0][0][0], lines[-1][0][1]
            diff = abs((lrc_end-lrc_start)-audio_duration_s)
            if diff<best_diff:
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

# ---------------- WORD-INCREMENTAL ----------------
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

# ---------------- VIDEO TEXT ----------------
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
            fill_color = "red" if w.lower() in song_title_words else "white"
            draw.text((x_offset, y_offset), w, font=font, fill=fill_color)
            x_offset += draw.textlength(w, font=font) + 10
        y_offset += LYRIC_FONT_SIZE
    return ImageClip(np.array(img)).with_start(start).with_duration(end-start)

# ---------------- CREATE VIDEO ----------------
def sanitize_filename(name):
    return re.sub(r'[<>:"/\\|?*]', '_', name)

def create_video(audio_segment, subtitles, bg_folder, output_file, song_title, start_time):
    clean_title_str=sanitize_filename(song_title)
    temp_audio=os.path.join(MP3_FOLDER,f"{clean_title_str}_temp.mp3")
    audio_segment.export(temp_audio,format="mp3")
    audio_clip=AudioFileClip(temp_audio)
    total=audio_clip.duration

    vids=[os.path.join(bg_folder,f) for f in os.listdir(bg_folder) if f.lower().endswith((".mp4",".mov",".mkv"))]
    if not vids: raise Exception("No background videos in folder.")
    bg=random.choice(vids)
    bg_clip=VideoFileClip(bg)
    if bg_clip.duration>total:
        bg_clip=bg_clip.subclipped(0,total)

    song_title_words=set(song_title.lower().split())
    word_clips=[]
    for (t0,t1),text in subtitles:
        word_clips.extend(make_incremental_word_clips(t0,t1,text,song_title_words))

    clips=[bg_clip]+word_clips

    if SHOW_TITLE:
        img=Image.new("RGBA",(VIDEO_SIZE,VIDEO_SIZE),(0,0,0,0))
        draw=ImageDraw.Draw(img)
        font=get_font(TITLE_FONT_SIZE)
        bbox=draw.textbbox((0,0),song_title,font=font)
        w,h=bbox[2]-bbox[0],bbox[3]-bbox[1]
        pos=((VIDEO_SIZE-w)//2,50)
        draw.text(pos,song_title,font=font,fill="white",stroke_width=5,stroke_fill="black")
        clips.append(ImageClip(np.array(img)).with_start(0).with_duration(total))

    final=CompositeVideoClip(clips,size=(VIDEO_SIZE,VIDEO_SIZE)).with_audio(audio_clip)
    final_path=os.path.join(FINAL_FOLDER,sanitize_filename(os.path.basename(output_file)))
    if os.path.exists(final_path):
        base,ext=os.path.splitext(final_path)
        final_path=f"{base}_{int(start_time)}{ext}"

    final.write_videofile(final_path,fps=30,codec="libx264",audio_codec="aac",preset="medium",ffmpeg_params=["-pix_fmt","yuv420p"])
    os.remove(temp_audio)
    print(f"✓ Video ready: {final_path} (duration: {total:.2f}s)")

# ---------------- MAIN ----------------
if __name__=="__main__":
    YOUTUBE_URL=get_next_link()
    meta=get_youtube_metadata(YOUTUBE_URL)
    SONG_TITLE=meta["song"]
    OUTPUT_VIDEO=SONG_TITLE.replace(" ","_")+"_lyric_video.mp4"

    audio=download_audio(YOUTUBE_URL, SONG_TITLE)

    # Fetch full lyrics first
    lrc = fetch_lrc_corrected(meta['artist'], meta['song'], SEGMENT_DURATION_S)
    subs_full = parse_lrc_content(lrc) if lrc else []

    # Determine dynamic segment duration based on lyrics
    if START_TIME_S is None:
        if subs_full:
            last_lyric_time = subs_full[-1][0][1]
            segment_duration = min(MAX_VIDEO_DURATION, last_lyric_time - subs_full[0][0][0])
        else:
            segment_duration = SEGMENT_DURATION_S
    else:
        segment_duration = SEGMENT_DURATION_S

    trimmed, start = get_segment(audio, segment_duration, START_TIME_S, lyrics=subs_full)

    # Adjust subtitles relative to trimmed segment
    subs_adj=[]
    for (t0,t1),text in subs_full:
        t0 -= start
        t1 -= start
        if t1>0 and t0<segment_duration:
            t0n=max(0,t0)
            t1n=min(segment_duration,max(t0n+0.1,t1))
            subs_adj.append(((t0n,t1n),text))

    print(f"✓ Segment starts at {start:.2f}s, {len(subs_adj)} lyric lines, segment duration: {segment_duration:.2f}s")

    create_video(trimmed, subs_adj, BACKGROUND_FOLDER, OUTPUT_VIDEO, SONG_TITLE, start)
    print("--- Done ---")
