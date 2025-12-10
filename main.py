import os
import sys
import numpy as np
from pydub import AudioSegment
# Import necessary components from moviepy
from moviepy import VideoFileClip, AudioFileClip, CompositeVideoClip, ImageClip
# Revert to the lowercase function import
from moviepy.video.fx.Crop import Crop
from PIL import Image, ImageDraw, ImageFont
import yt_dlp
from syncedlyrics import search as lrc_search
import concurrent.futures

# ---------------- CONFIG ----------------
YOUTUBE_URL = "https://www.youtube.com/watch?v=AeO81mfRook&list=RDAeO81mfRook"
BACKGROUND_VIDEO = "gameplay2.mp4"
OUTPUT_VIDEO = "final_video.mp4"
SEGMENT_DURATION = 30  # seconds
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
# NOTE: Ensure this font path is correct on your system.
FONT_PATH = r"C:\Windows\Fonts\Arial.ttf"
FONT_SIZE = 55
SEARCH_TERM = "Pixelated kisses - Joji"

# ---------------- 1. DOWNLOAD AUDIO ----------------
def download_audio(url, output_path="audio"):
    if os.path.exists(output_path + ".mp3"):
        os.remove(output_path + ".mp3")

    ydl_opts = {
        'format': 'bestaudio',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192'
        }],
        'outtmpl': output_path,  # no extension here
        'noplaylist': True,
        'quiet': True
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    final_path = output_path + ".mp3"
    print(f"✓ Audio downloaded: {final_path}")
    return final_path

# ---------------- 2. FIND LOUDEST SEGMENT ----------------
def get_loudest_segment(audio_path, duration_s=SEGMENT_DURATION):
    audio = AudioSegment.from_mp3(audio_path)
    samples = np.array(audio.get_array_of_samples())
    window_size = int(duration_s * audio.frame_rate)
    step = int(audio.frame_rate * 0.5)

    max_energy = 0
    start_idx = 0
    for i in range(0, len(samples) - window_size, step):
        energy = np.sum(samples[i:i+window_size]**2)
        if energy > max_energy:
            max_energy = energy
            start_idx = i

    start_ms = int(start_idx / audio.frame_rate * 1000)
    trimmed = audio[start_ms:start_ms + duration_s*1000]
    trimmed.export("trimmed_audio.mp3", format="mp3")
    return "trimmed_audio.mp3", start_ms/1000

# ---------------- 3. FETCH SYNCHRONIZED LYRICS ----------------
def fetch_lrc(search_term, save_path="lyrics.lrc", timeout=10):
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(lrc_search, search_term)
            lrc = future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        raise RuntimeError(f"Lyrics fetch timed out for '{search_term}'")

    if not lrc:
        raise RuntimeError(f"No lyrics found for '{search_term}'")

    with open(save_path, "w", encoding="utf-8") as f:
        f.write(lrc)
    return save_path

def parse_lrc_file(path):
    subs = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or not line.startswith("["):
            continue
        ts, text = line.split("]", 1)
        ts = ts[1:]
        mm, rest = ts.split(":")
        ss, ms = rest.split(".")
        start = int(mm)*60 + int(ss) + int(ms)/100
        end = start + 2.0 # Default end time
        subs.append(((start, end), text.strip()))

    # Calculate end time based on the next subtitle's start time
    for i in range(len(subs) - 1):
        # Update the current line's end time to be the next line's start time
        subs[i] = ((subs[i][0][0], subs[i+1][0][0]), subs[i][1])

    return subs

# ---------------- 4. CREATE TEXT CLIP ----------------
def make_text_clip(text, start, end):
    img = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    except:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0,0), text, font=font)
    w, h = bbox[2]-bbox[0], bbox[3]-bbox[1]

    # center text
    pos = ((VIDEO_WIDTH-w)//2, (VIDEO_HEIGHT-h)//2)
    draw.text(pos, text, font=font, fill="white", stroke_width=3, stroke_fill="black")

    clip = ImageClip(np.array(img))
    # Set the clip's start and duration to match the subtitle timing
    clip = clip.with_start(start).with_duration(end - start)
    return clip


# ---------------- 5. CREATE VIDEO ----------------
def create_video(audio_file, subtitles, background_video, output_file):
    audio_clip = AudioFileClip(audio_file)
    total_duration = audio_clip.duration

    # Load background
    bg_clip = VideoFileClip(background_video)

    # 1. Resize to fill the height (1920)
    # This maintains aspect ratio, so a landscape video will become very wide (e.g., ~3413px)
    bg_clip = bg_clip.resized(height=VIDEO_HEIGHT)

    # 2. Center the background clip
    # This prepares it to be centered in our vertical frame
    bg_clip = bg_clip.with_position("center")

    # Loop or trim to match audio duration
    if bg_clip.duration < total_duration:
        bg_clip = bg_clip.loop(duration=total_duration)
    else:
        bg_clip = bg_clip.subclipped(0, total_duration)

    # Overlay lyrics
    overlay_clips = [make_text_clip(text, start, end) for (start, end), text in subtitles]

    # 3. Create CompositeVideoClip with a FIXED size (1080x1920)
    # This automatically crops the excess width of the background and solves the "odd number" width error
    final_clip = CompositeVideoClip(
        [bg_clip] + overlay_clips, 
        size=(VIDEO_WIDTH, VIDEO_HEIGHT)
    )
    
    final_clip = final_clip.with_audio(audio_clip)

    final_clip.write_videofile(
        output_file,
        fps=30,
        codec="libx264",
        audio_codec="aac",
        preset="medium",
        ffmpeg_params=["-pix_fmt", "yuv420p"]
    )
    print("✓ Video creation complete:", output_file)

# ---------------- MAIN PIPELINE ----------------
if __name__ == "__main__":
    # Check for required files
    if not os.path.exists(BACKGROUND_VIDEO):
        print(f"ERROR: Background video file '{BACKGROUND_VIDEO}' not found. Please create it or change the variable.")
        sys.exit(1)

    print("--- Starting Video Generation Pipeline ---")
    audio_file = download_audio(YOUTUBE_URL)
    trimmed_audio, start_time = get_loudest_segment(audio_file)
    print(f"✓ Loudest segment starts at: {start_time:.2f} seconds")

    try:
        lrc_path = fetch_lrc(SEARCH_TERM)
        print(f"✓ Lyrics fetched: {lrc_path}")
        subtitles = parse_lrc_file(lrc_path)

        # adjust subtitle start/end times to match trimmed segment
        subtitles = [
            ((max(0, t0 - start_time), max(0, t1 - start_time)), text)
            for (t0, t1), text in subtitles if t1 >= start_time
        ]

        create_video(trimmed_audio, subtitles, BACKGROUND_VIDEO, OUTPUT_VIDEO)
        print("--- Pipeline Complete ---")

    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)