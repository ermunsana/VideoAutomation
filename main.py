import os
import sys
import numpy as np
from pydub import AudioSegment
from moviepy import VideoFileClip, AudioFileClip, CompositeVideoClip, ImageClip
from PIL import Image, ImageDraw, ImageFont
import yt_dlp
from syncedlyrics import search as lrc_search
import concurrent.futures
import textwrap

# ---------------- CONFIG ----------------
YOUTUBE_URL = "https://www.youtube.com/watch?v=H2vTrHc-OGk&list=RDtEXYfT_G0W0"
BACKGROUND_VIDEO = "gameplay.mp4"
OUTPUT_VIDEO = "final_video.mp4"
SEGMENT_DURATION = 30
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FONT_PATH = r"C:\Windows\Fonts\Arial.ttf"
FONT_SIZE = 55
SEARCH_TERM = "poster boy - 2hollis"
MAX_LINE_DURATION = 3.0
GLOBAL_SYNC_OFFSET = 0.0

# ------------ GLOBAL SINGLETONS ------------
# Load font once
try:
    FONT = ImageFont.truetype(FONT_PATH, FONT_SIZE)
except:
    FONT = ImageFont.load_default()

# Precompute wrap width
AVG_CHAR_WIDTH = FONT_SIZE * 0.55
WRAP_CHARS = int((VIDEO_WIDTH * 0.9) / AVG_CHAR_WIDTH)


# ---------------- 1. DOWNLOAD AUDIO ----------------
def download_audio(url, output_path="audio"):
    mp3_path = output_path + ".mp3"
    if os.path.exists(mp3_path):
        os.remove(mp3_path)

    ydl_opts = {
        'format': 'bestaudio',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192'
        }],
        'outtmpl': output_path,
        'noplaylist': True,
        'quiet': True
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    print("✓ Audio downloaded:", mp3_path)
    return mp3_path


# ---------------- 2. FIND LOUDEST SEGMENT ----------------
def get_loudest_segment(audio_path, duration_s=SEGMENT_DURATION):
    audio = AudioSegment.from_mp3(audio_path)
    samples = np.array(audio.get_array_of_samples(), dtype=np.float32)

    if audio.channels == 2:
        samples = samples.reshape((-1, 2)).mean(axis=1)

    frame_rate = audio.frame_rate
    window = int(duration_s * frame_rate)
    hop = int(frame_rate * 0.5)

    sq = samples * samples
    kernel = np.ones(window, dtype=np.float32)
    energy = np.convolve(sq, kernel, mode="valid")

    indices = np.arange(0, len(energy), hop)
    best_idx = indices[np.argmax(energy[indices])]

    start_ms = int(best_idx / frame_rate * 1000)
    end_ms = start_ms + duration_s * 1000

    audio[start_ms:end_ms].export("trimmed_audio.mp3", format="mp3")

    return "trimmed_audio.mp3", start_ms / 1000


# ---------------- 3. FETCH + PARSE LYRICS ----------------
def fetch_lrc(search_term, save_path="lyrics.lrc", timeout=10):
    with concurrent.futures.ThreadPoolExecutor() as ex:
        future = ex.submit(lrc_search, search_term)
        try:
            lrc = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise RuntimeError(f"Lyrics fetch timed out for '{search_term}'")

    if not lrc:
        raise RuntimeError(f"No lyrics found for '{search_term}'")

    with open(save_path, "w", encoding="utf-8") as f:
        f.write(lrc)

    return save_path


def parse_lrc_file(path):
    raw = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.startswith("["):
                continue
            try:
                ts, text = line.split("]", 1)
                mm, rest = ts[1:].split(":")
                ss, ms = rest.split(".")
                t = int(mm) * 60 + int(ss) + int(ms) / 100
                t += GLOBAL_SYNC_OFFSET

                text = text.strip()
                if text and text not in ("Instrumental", "(Instrumental)"):
                    raw.append((t, text))
            except:
                continue

    subs = []
    n = len(raw)
    for i in range(n):
        t0, txt = raw[i]
        if i < n - 1:
            next_t = raw[i + 1][0]
            dur = min(next_t - t0, MAX_LINE_DURATION)
        else:
            dur = MAX_LINE_DURATION
        subs.append(((t0, t0 + dur), txt))

    return subs


# ---------------- 4. TEXT CLIP MAKER ----------------
def make_text_clip(text, start, end):
    wrapped = textwrap.fill(text, width=WRAP_CHARS)
    img = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bbox = draw.multiline_textbbox((0, 0), wrapped, font=FONT, align="center")
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    pos = ((VIDEO_WIDTH - w) // 2, (VIDEO_HEIGHT - h) // 2)

    draw.multiline_text(
        pos,
        wrapped,
        font=FONT,
        fill="white",
        align="center",
        stroke_width=4,
        stroke_fill="black"
    )

    arr = np.array(img)
    return ImageClip(arr).with_start(start).with_duration(end - start)


# ---------------- 5. VIDEO BUILDER ----------------
def create_video(audio_file, subtitles, background_video, output_file):
    audio_clip = AudioFileClip(audio_file)
    total = audio_clip.duration

    bg = VideoFileClip(background_video).resized(height=VIDEO_HEIGHT).with_position("center")
    bg = bg.loop(duration=total) if bg.duration < total else bg.subclipped(0, total)

    clips = [bg] + [make_text_clip(txt, t0, t1) for (t0, t1), txt in subtitles]

    final = CompositeVideoClip(clips, size=(VIDEO_WIDTH, VIDEO_HEIGHT)).with_audio(audio_clip)

    final.write_videofile(
        output_file,
        fps=30,
        codec="libx264",
        audio_codec="aac",
        preset="medium",
        ffmpeg_params=["-pix_fmt", "yuv420p"]
    )

    print("✓ Video created:", output_file)


# ---------------- MAIN ----------------
if __name__ == "__main__":
    if not os.path.exists(BACKGROUND_VIDEO):
        print("ERROR: Missing background video.")
        sys.exit(1)

    print("--- Starting Pipeline ---")
    audio_file = download_audio(YOUTUBE_URL)
    trimmed_audio, start_time = get_loudest_segment(audio_file)
    print("✓ Loudest segment starts at:", round(start_time, 2))

    try:
        lrc_path = fetch_lrc(SEARCH_TERM)
        subs_full = parse_lrc_file(lrc_path)

        subs_trimmed = []
        for (t0, t1), txt in subs_full:
            t0 -= start_time
            t1 -= start_time
            if t0 < SEGMENT_DURATION and t1 > -1:
                t0 = max(0, t0)
                t1 = min(SEGMENT_DURATION, max(t0 + 0.1, t1))
                subs_trimmed.append(((t0, t1), txt))

        create_video(trimmed_audio, subs_trimmed, BACKGROUND_VIDEO, OUTPUT_VIDEO)

        print("--- Done ---")

    except RuntimeError as e:
        print("ERROR:", e)
        sys.exit(1)
