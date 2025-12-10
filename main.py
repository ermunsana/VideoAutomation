import os
import sys
import numpy as np
import random
from pydub import AudioSegment
from moviepy import VideoFileClip, AudioFileClip, CompositeVideoClip, ImageClip
from PIL import Image, ImageDraw
import yt_dlp
from syncedlyrics import search as lrc_search
import concurrent.futures

from font import get_font  # dynamic font

# ---------------- CONFIG ----------------

YOUTUBE_URL = "https://www.youtube.com/watch?v=AeO81mfRook"
BACKGROUND_VIDEOS = ["gameplay.mp4"]
OUTPUT_VIDEO = "final_video.mp4"
SEARCH_TERM = "pixelated kisses - Joji"
SEGMENT_DURATION_S = 30
VIDEO_SIZE = 1080  # square video
TITLE_FONT_SIZE = 70
LYRIC_FONT_SIZE = 55
MAX_LINE_DURATION_S = 3.0
GLOBAL_SYNC_OFFSET_S = 0.0
START_TIME_S = 35  # set seconds manually or None for auto

# Extract only the song title (remove artist)
SONG_TITLE = SEARCH_TERM.split(" - ")[-2].strip()

# ---------------- AUDIO ----------------

def download_audio(url):
    temp_path = "downloaded_audio.mp3"
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': temp_path[:-4],
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        raise RuntimeError(f"Audio download failed: {e}")

    audio = AudioSegment.from_mp3(temp_path)
    print(f"✓ Audio downloaded: {temp_path}")
    print(f"✓ Duration: {len(audio)/1000:.2f}s")
    return audio

def get_segment(audio, duration_s=SEGMENT_DURATION_S, manual_start=None):
    if manual_start is not None:
        start_ms = int(manual_start * 1000)
        start_ms = max(0, min(start_ms, len(audio) - int(duration_s*1000)))
        trimmed_audio = audio[start_ms:start_ms + int(duration_s*1000)]
        return trimmed_audio, start_ms/1000

    samples = np.array(audio.get_array_of_samples())
    channels = audio.channels
    num_samples = len(samples) // channels
    frame_rate = audio.frame_rate
    window_size = int(duration_s * frame_rate)
    step = int(frame_rate * 0.5)

    max_energy = 0
    start_idx = 0
    max_start = max(0, num_samples - window_size)

    for i in range(0, max_start, step):
        if channels > 1:
            energy = np.sum(samples[i*channels:(i+window_size)*channels]**2)
        else:
            energy = np.sum(samples[i:i+window_size]**2)
        if energy > max_energy:
            max_energy = energy
            start_idx = i

    start_ms = int(start_idx / frame_rate * 1000)
    if start_ms + duration_s*1000 > len(audio):
        start_ms = max(0, len(audio) - int(duration_s*1000))

    trimmed_audio = audio[start_ms:start_ms + int(duration_s*1000)]
    return trimmed_audio, start_ms / 1000

# ---------------- LYRICS ----------------

def fetch_lrc(search_term, timeout=20):
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(lrc_search, search_term)
            lrc_content = future.result(timeout=timeout)
    except (concurrent.futures.TimeoutError, RuntimeError):
        print(f"WARNING: Could not fetch synced lyrics for '{search_term}'.")
        return None

    if not lrc_content:
        print(f"WARNING: No synced lyrics found for '{search_term}'.")
        return None

    return lrc_content

def parse_lrc_content(lrc_content):
    raw_subs = []
    for line in lrc_content.splitlines():
        line = line.strip()
        if not line or not line.startswith("["):
            continue
        try:
            ts_str, text_str = line.split("]", 1)
            ts_str = ts_str[1:]
            mm, rest = ts_str.split(":")
            ss, ms = rest.split(".")
            start_time = int(mm)*60 + int(ss) + int(ms)/100
            start_time += GLOBAL_SYNC_OFFSET_S
            text_str = text_str.strip().lower()
            if text_str and text_str.lower() not in ('(instrumental)','instrumental'):
                raw_subs.append((start_time, text_str))
        except ValueError:
            continue

    final_subs = []
    for i in range(len(raw_subs)):
        start_time, text = raw_subs[i]
        if i < len(raw_subs)-1:
            next_start = raw_subs[i+1][0]
            duration = min(next_start-start_time, MAX_LINE_DURATION_S)
        else:
            duration = MAX_LINE_DURATION_S
        final_subs.append(((start_time, start_time+duration), text))
    return final_subs

# ---------------- WORD-INCREMENTAL ----------------

def make_incremental_word_clips(line_start, line_end, line_text):
    words = line_text.split()
    duration = line_end - line_start
    num_words = len(words)
    if num_words == 0:
        return []

    word_duration = duration / num_words
    clips = []
    for i in range(1, num_words + 1):
        text = " ".join(words[:i])
        start = line_start + (i-1)*word_duration
        end = line_start + i*word_duration
        clip = make_text_clip_grid([text], start, end)
        clips.append(clip)
    return clips

# ---------------- VIDEO CLIPS ----------------

def make_text_clip_grid(lines, start, end):
    img = Image.new("RGBA", (VIDEO_SIZE, VIDEO_SIZE), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    font = get_font(LYRIC_FONT_SIZE)

    max_words_per_line = 3
    grid_lines = []
    for line in lines:
        words = line.split()
        line_parts = []
        temp = []
        for w in words:
            temp.append(w)
            if len(temp) >= max_words_per_line:
                line_parts.append(" ".join(temp))
                temp = []
        if temp:
            line_parts.append(" ".join(temp))
        grid_lines.extend(line_parts)

    grid_text = "\n".join(grid_lines)
    bbox = draw.multiline_textbbox((0,0), grid_text, font=font, align="center")
    w,h = bbox[2]-bbox[0], bbox[3]-bbox[1]
    pos = ((VIDEO_SIZE - w)//2, (VIDEO_SIZE - h)//2)  # lyrics stay centered
    draw.multiline_text(pos, grid_text, font=font, fill="white", align="center", stroke_width=3, stroke_fill="black")

    return ImageClip(np.array(img)).with_start(start).with_duration(end-start)

# ---------------- CREATE VIDEO ----------------

def create_video(audio_segment, subtitles, background_videos, output_file, song_title):
    temp_audio_path = "temp_audio.mp3"
    audio_segment.export(temp_audio_path, format="mp3")
    audio_clip = AudioFileClip(temp_audio_path)
    total_duration = audio_clip.duration

    # Random background
    bg_file = random.choice(background_videos)
    bg_clip = VideoFileClip(bg_file)
    bg_clip = bg_clip.resized(height=VIDEO_SIZE, width=VIDEO_SIZE)
    bg_clip = bg_clip.loop(duration=total_duration) if bg_clip.duration < total_duration else bg_clip.subclipped(0,total_duration)

    # Build incremental word clips
    word_clips = []
    for (t0, t1), line in subtitles:
        word_clips.extend(make_incremental_word_clips(t0, t1, line))

    # Title overlay (song title only)
    img = Image.new("RGBA", (VIDEO_SIZE, VIDEO_SIZE), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    font = get_font(TITLE_FONT_SIZE)

    # Use SONG_TITLE only, never SEARCH_TERM
    bbox = draw.textbbox((0,0), SONG_TITLE, font=font, align="center")
    w,h = bbox[2]-bbox[0], bbox[3]-bbox[1]

    margin_top = 50
    pos = ((VIDEO_SIZE - w)//2, margin_top)

    draw.text(pos, SONG_TITLE, font=font, fill="white", align="center", stroke_width=5, stroke_fill="black")
    title_clip = ImageClip(np.array(img)).with_start(0).with_duration(total_duration)

    final_clip = CompositeVideoClip([bg_clip,title_clip]+word_clips, size=(VIDEO_SIZE,VIDEO_SIZE)).with_audio(audio_clip)
    final_clip.write_videofile(output_file, fps=30, codec="libx264", audio_codec="aac", preset="medium", ffmpeg_params=["-pix_fmt","yuv420p"])
    os.remove(temp_audio_path)
    print("✓ Video creation complete:", output_file)

# ---------------- MAIN PIPELINE ----------------

if __name__ == "__main__":
    for bg in BACKGROUND_VIDEOS:
        if not os.path.exists(bg):
            print(f"ERROR: Background video '{bg}' not found.")
            sys.exit(1)

    print("--- Starting Video Generation Pipeline ---")
    try:
        audio = download_audio(YOUTUBE_URL)
        trimmed_audio, start_time = get_segment(audio, SEGMENT_DURATION_S, START_TIME_S)
        print(f"✓ Segment start at {start_time:.2f}s")
        print(f"✓ Final video will be {SEGMENT_DURATION_S}s long")

        lrc_content = fetch_lrc(SEARCH_TERM)
        subtitles_full = parse_lrc_content(lrc_content) if lrc_content else []

        # adjust to segment
        subtitles_adjusted = [((t0-start_time,t1-start_time),text) for (t0,t1),text in subtitles_full]
        subtitles = []
        for (t0,t1),text in subtitles_adjusted:
            if t0 < SEGMENT_DURATION_S and t1 > 0:
                t0_new = max(0.0,t0)
                t1_new = min(SEGMENT_DURATION_S,max(t0_new+0.1,t1))
                subtitles.append(((t0_new,t1_new),text))

        if not subtitles:
            print(f"✓ No synchronized lyrics available, video will have only title overlay.")
        else:
            print(f"✓ Found {len(subtitles)} lines. Incremental word timing applied.")

        create_video(trimmed_audio, subtitles, BACKGROUND_VIDEOS, OUTPUT_VIDEO, SONG_TITLE)
        print("--- Pipeline Complete ---")
    except RuntimeError as e:
        print(f"FATAL ERROR: {e}")
        sys.exit(1)
