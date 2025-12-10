import os
import sys
import numpy as np
from pydub import AudioSegment
from moviepy import VideoFileClip, AudioFileClip, CompositeVideoClip, ImageClip
from moviepy.video.fx.Crop import Crop as Crop # Not used, but included for completeness
from PIL import Image, ImageDraw, ImageFont
import yt_dlp
from syncedlyrics import search as lrc_search
import concurrent.futures
import textwrap # <-- NEW: For wrapping long lines

# ---------------- CONFIG ----------------
YOUTUBE_URL = "https://www.youtube.com/watch?v=AeO81mfRook&list=RDAeO81mfRook"
BACKGROUND_VIDEO = "gameplay2.mp4"
OUTPUT_VIDEO = "final_video.mp4"
SEGMENT_DURATION = 30
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
# NOTE: Ensure this font path is correct on your system.
# Replace with a font path that exists on your machine if needed.
FONT_PATH = r"C:\Windows\Fonts\Arial.ttf" 
FONT_SIZE = 55
SEARCH_TERM = "Pixelated kisses - Joji"

# --- SYNCHRONIZATION AND LAYOUT CONFIG ---
MAX_LINE_DURATION = 3.0  # Max seconds a line stays on screen (prevents hanging during instrumental parts)
GLOBAL_SYNC_OFFSET = 0.0 # Adjust this (+/- seconds) if lyrics are consistently early/late
# ----------------------------------------

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
        'outtmpl': output_path,
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

# ---------------- 3. FETCH AND PARSE SYNCHRONIZED LYRICS ----------------
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
    raw_subs = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or not line.startswith("["):
            continue
        # Basic LRC parsing
        try:
            ts_str, text_str = line.split("]", 1)
            ts_str = ts_str[1:]
            mm, rest = ts_str.split(":")
            ss, ms = rest.split(".")
            start = int(mm)*60 + int(ss) + int(ms)/100
            
            # Apply global offset correction
            start += GLOBAL_SYNC_OFFSET
            
            text_str = text_str.strip()
            # Only keep non-empty and non-instructional lines
            if text_str and text_str not in ('(Instrumental)', 'Instrumental'): 
                raw_subs.append((start, text_str))
        except ValueError:
            continue # Skip malformed lines

    final_subs = []
    for i in range(len(raw_subs)):
        start_time, text = raw_subs[i]
        
        # Determine end time
        if i < len(raw_subs) - 1:
            next_start = raw_subs[i+1][0]
            # Duration is gap to next line OR max limit (to prevent hanging)
            duration = min(next_start - start_time, MAX_LINE_DURATION)
        else:
            duration = MAX_LINE_DURATION # Last line duration
            
        end_time = start_time + duration
        final_subs.append(((start_time, end_time), text))

    return final_subs

# ---------------- 4. CREATE TEXT CLIP (With Wrapping) ----------------
def make_text_clip(text, start, end):
    # Create a transparent image matching the video size
    img = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    
    try:
        font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    except:
        font = ImageFont.load_default()

    # --- WRAPPING LOGIC ---
    # Estimate characters per line for 90% of the video width
    avg_char_width = FONT_SIZE * 0.55 # Empirically derived multiplier
    max_chars = int((VIDEO_WIDTH * 0.9) / avg_char_width)
    
    # Wrap text
    wrapped_lines = textwrap.fill(text, width=max_chars, subsequent_indent='   ')
    
    # Calculate size of the wrapped block
    bbox = draw.multiline_textbbox((0,0), wrapped_lines, font=font, align="center")
    w, h = bbox[2]-bbox[0], bbox[3]-bbox[1]

    # Center position
    # The text is centered vertically in the frame
    pos = ((VIDEO_WIDTH - w) // 2, (VIDEO_HEIGHT - h) // 2)

    # Draw text with outline for readability
    draw.multiline_text(
        pos, 
        wrapped_lines, 
        font=font, 
        fill="white", 
        align="center",
        stroke_width=4, 
        stroke_fill="black"
    )

    clip = ImageClip(np.array(img))
    clip = clip.with_start(start).with_duration(end - start)
    return clip

# ---------------- 5. CREATE VIDEO (With Fixed Size) ----------------
def create_video(audio_file, subtitles, background_video, output_file):
    audio_clip = AudioFileClip(audio_file)
    total_duration = audio_clip.duration

    # Load background
    bg_clip = VideoFileClip(background_video)

    # 1. Resize to fill the height (1920) while maintaining aspect ratio
    bg_clip = bg_clip.resized(height=VIDEO_HEIGHT)

    # 2. Center the background clip (this prepares it to be cropped when composited)
    bg_clip = bg_clip.with_position("center")

    # Loop or trim to match audio duration
    if bg_clip.duration < total_duration:
        bg_clip = bg_clip.loop(duration=total_duration)
    else:
        bg_clip = bg_clip.subclipped(0, total_duration)

    # Overlay lyrics
    overlay_clips = [make_text_clip(text, start, end) for (start, end), text in subtitles]

    # 3. Create CompositeVideoClip with a FIXED size (1080x1920)
    # This automatically crops the background to the final vertical dimensions (1080x1920)
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
    # Get the loudest segment and its original start time
    trimmed_audio, start_time = get_loudest_segment(audio_file)
    print(f"✓ Loudest segment starts at: {start_time:.2f} seconds")

    try:
        lrc_path = fetch_lrc(SEARCH_TERM)
        print(f"✓ Lyrics fetched: {lrc_path}")
        subtitles_full = parse_lrc_file(lrc_path)

        # 1. Adjust subtitle start/end times to match the trimmed segment
        subtitles_adjusted = [
            ((t0 - start_time, t1 - start_time), text)
            for (t0, t1), text in subtitles_full
        ]

        # 2. Filter the subtitles to only include those within the SEGMENT_DURATION
        subtitles = []
        for (t0, t1), text in subtitles_adjusted:
            
            # A. Check if the line is within the 30-second clip (with a small buffer)
            if t0 < SEGMENT_DURATION and t1 > -1.0:
                
                # B. Further adjust any negative timestamps to 0.0
                t0_new = max(0.0, t0)
                # Ensure the end time doesn't exceed the segment duration
                t1_new = min(SEGMENT_DURATION, max(t0_new + 0.1, t1)) 

                subtitles.append(((t0_new, t1_new), text))

        # Check if any lyrics remain
        if not subtitles:
            print("WARNING: No synchronized lyrics were found for the selected 30-second segment.")
            # Optionally, you could exit or continue without lyrics here.
            # For robustness, we will create the video anyway, just without lyrics.
            subtitles = [] 
        else:
            print(f"✓ Found {len(subtitles)} synchronized lines for the {SEGMENT_DURATION}-second segment.")


        create_video(trimmed_audio, subtitles, BACKGROUND_VIDEO, OUTPUT_VIDEO)
        print("--- Pipeline Complete ---")

    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)