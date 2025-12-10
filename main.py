import os
import sys
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
LYRIC_FONT_SIZE = 60
MIN_LINE_DURATION_S = 1.0
CHAR_FACTOR = 0.2
GLOBAL_SYNC_OFFSET_S = 0.0
START_TIME_S = 40.0  # set to None for automatic start
SHOW_TITLE = False
MP3_FOLDER = "mp3"

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

# ---------------- TITLE CLEANING ----------------
def clean_title(raw_title, uploader):
    trash_words = ["official video", "official audio", "music video", "lyrics",
                   "lyric video", "audio", "video", "mv", "hd", "4k"]
    t = raw_title.lower()
    for w in trash_words:
        t = t.replace(w, "")
    t = t.replace(uploader.lower(), "")
    t = t.replace("-", " ")
    t = re.sub(r"\(.*?\)", "", t)
    t = re.sub(r"\[.*?\]", "", t)
    t = " ".join(t.split())
    return t.title()

# ---------------- YOUTUBE METADATA ----------------
def get_youtube_metadata(url):
    opts = {"quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    raw_title = info.get("title", "")
    uploader = info.get("uploader", "")
    cleaned = clean_title(raw_title, uploader)
    return {"song": cleaned, "full_title": raw_title}

# ---------------- AUDIO ----------------
def download_audio(url, song_title):
    clean_title_str = re.sub(r'[^\w\d-]', '_', song_title.lower())
    temp_path = os.path.join(MP3_FOLDER, f"{clean_title_str}_temp.mp3")
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{'key': 'FFmpegExtractAudio',
                            'preferredcodec': 'mp3',
                            'preferredquality': '192'}],
        'outtmpl': os.path.join(MP3_FOLDER, "yt_audio"),
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    os.rename(os.path.join(MP3_FOLDER, "yt_audio.mp3"), temp_path)
    audio = AudioSegment.from_mp3(temp_path)
    print(f"✓ Audio downloaded: {temp_path}")
    print(f"✓ Duration: {len(audio)/1000:.2f}s")
    return audio

def get_segment(audio, duration_s, manual_start):
    if manual_start is not None:
        start_ms = max(0, min(int(manual_start*1000), len(audio)-int(duration_s*1000)))
        return audio[start_ms:start_ms+int(duration_s*1000)], start_ms/1000
    samples = np.array(audio.get_array_of_samples())
    channels = audio.channels
    frame_rate = audio.frame_rate
    num_samples = len(samples)//channels
    window = int(duration_s*frame_rate)
    step = int(frame_rate*0.5)
    max_energy = 0
    best = 0
    for i in range(0, max(0, num_samples-window), step):
        if channels > 1:
            energy = np.sum(samples[i*channels:(i+window)*channels]**2)
        else:
            energy = np.sum(samples[i:i+window]**2)
        if energy > max_energy:
            max_energy = energy
            best = i
    start_ms = int(best/frame_rate*1000)
    trimmed = audio[start_ms:start_ms+int(duration_s*1000)]
    return trimmed, start_ms/1000

# ---------------- LYRICS ----------------
def fetch_lrc(term, timeout=20):
    try:
        with concurrent.futures.ThreadPoolExecutor() as ex:
            future = ex.submit(lrc_search, term)
            result = future.result(timeout=timeout)
        if not result:
            print("WARNING: No synced lyrics found.")
            return None
        return result
    except:
        print("WARNING: Failed fetching synced lyrics.")
        return None

def parse_lrc_content(lrc_content):
    if not lrc_content:
        return []
    lines = []
    for line in lrc_content.splitlines():
        line = line.strip()
        if not line or not line.startswith("["):
            continue
        try:
            ts, txt = line.split("]", 1)
            ts = ts[1:]
            mm, rest = ts.split(":")
            ss, ms = rest.split(".")
            start = int(mm)*60 + int(ss) + int(ms)/100
            start += GLOBAL_SYNC_OFFSET_S
            txt = txt.strip().lower()
            if txt and txt not in ("instrumental", "(instrumental)"):
                lines.append((start, txt))
        except:
            continue
    final = []
    for i in range(len(lines)):
        t0, txt = lines[i]
        # fix: replace MAX_LINE_DURATION_S with dynamic duration
        t_next = lines[i+1][0] if i < len(lines)-1 else t0 + max(MIN_LINE_DURATION_S, len(txt)*CHAR_FACTOR)
        duration = max(MIN_LINE_DURATION_S, min(t_next - t0, len(txt)*CHAR_FACTOR))
        t1 = t0 + duration
        final.append(((t0, t1), txt))
    return final


# ---------------- WORD-INCREMENTAL ----------------
def make_incremental_word_clips(t0, t1, text, song_title_words):
    words = text.split()
    if not words:
        return []
    duration = t1 - t0
    total_chars = sum(len(w) for w in words)
    word_durations = [duration * len(w)/total_chars for w in words]
    clips = []
    current_start = t0
    for i, w in enumerate(words):
        partial = " ".join(words[:i+1])
        current_end = current_start + word_durations[i]
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
        fill_color = "red" if any(word.lower() in song_title_words for word in line.split()) else "white"
        bbox = draw.textbbox((0,0), line, font=font)
        w,h = bbox[2]-bbox[0], bbox[3]-bbox[1]
        x = (VIDEO_SIZE - w)//2
        draw.text((x,y_offset), line, font=font, fill=fill_color, stroke_width=3, stroke_fill="black")
        y_offset += LYRIC_FONT_SIZE
    return ImageClip(np.array(img)).with_start(start).with_duration(end-start)

# ---------------- CREATE VIDEO ----------------
def create_video(audio_segment, subtitles, bg_folder, output_file, song_title):
    clean_title_str = re.sub(r'[^\w\d-]', '_', song_title.lower())
    temp_audio = os.path.join(MP3_FOLDER, f"{clean_title_str}_temp.mp3")
    audio_segment.export(temp_audio, format="mp3")
    audio_clip = AudioFileClip(temp_audio)
    total = audio_clip.duration

    vids = [os.path.join(bg_folder, f) for f in os.listdir(bg_folder)
            if f.lower().endswith((".mp4",".mov",".mkv"))]
    if not vids:
        raise Exception("No background videos in folder.")
    bg = random.choice(vids)
    bg_clip = VideoFileClip(bg)
    bg_clip = bg_clip.resized(height=VIDEO_SIZE, width=VIDEO_SIZE)
    if bg_clip.duration < total:
        bg_clip = bg_clip.loop(duration=total)
    else:
        bg_clip = bg_clip.subclipped(0, total)

    song_title_words = set(song_title.lower().split())
    word_clips = []
    for (t0,t1), text in subtitles:
        word_clips.extend(make_incremental_word_clips(t0, t1, text, song_title_words))

    clips = [bg_clip] + word_clips

    if SHOW_TITLE:
        img = Image.new("RGBA", (VIDEO_SIZE, VIDEO_SIZE), (0,0,0,0))
        draw = ImageDraw.Draw(img)
        font = get_font(TITLE_FONT_SIZE)
        bbox = draw.textbbox((0,0), song_title, font=font)
        w,h = bbox[2]-bbox[0], bbox[3]-bbox[1]
        pos = ((VIDEO_SIZE - w)//2, 50)
        draw.text(pos, song_title, font=font, fill="white", stroke_width=5, stroke_fill="black")
        title_clip = ImageClip(np.array(img)).with_start(0).with_duration(total)
        clips.append(title_clip)

    final = CompositeVideoClip(clips, size=(VIDEO_SIZE, VIDEO_SIZE)).with_audio(audio_clip)
    final.write_videofile(output_file, fps=30, codec="libx264",
                          audio_codec="aac", preset="medium",
                          ffmpeg_params=["-pix_fmt","yuv420p"])
    os.remove(temp_audio)
    final_path = os.path.join(FINAL_FOLDER, os.path.basename(output_file))
    os.rename(output_file, final_path)
    print("✓ Video ready:", final_path)

# ---------------- MAIN ----------------
if __name__ == "__main__":
    YOUTUBE_URL = get_next_link()
    print("Using link:", YOUTUBE_URL)
    meta = get_youtube_metadata(YOUTUBE_URL)
    SONG_TITLE = meta["song"]
    SEARCH_TERM = SONG_TITLE.lower()
    OUTPUT_VIDEO = SONG_TITLE.replace(" ", "_") + "_lyric_video.mp4"

    print("--- Pipeline Start ---")
    print("Song:", SONG_TITLE)

    audio = download_audio(YOUTUBE_URL, SONG_TITLE)
    trimmed, start = get_segment(audio, SEGMENT_DURATION_S, START_TIME_S)
    print(f"✓ Segment starts at {start:.2f}s")

    lrc = fetch_lrc(SEARCH_TERM)
    subs_full = parse_lrc_content(lrc) if lrc else []

    subs_adj = []
    for (t0,t1), text in subs_full:
        t0 -= start
        t1 -= start
        if t1 > 0 and t0 < SEGMENT_DURATION_S:
            t0n = max(0, t0)
            t1n = min(SEGMENT_DURATION_S, max(t0n+0.1, t1))
            subs_adj.append(((t0n, t1n), text))

    print("✓ Lyrics lines:", len(subs_adj))
    create_video(trimmed, subs_adj, BACKGROUND_FOLDER, OUTPUT_VIDEO, SONG_TITLE)
    print("--- Done ---")
