import os
import random
import re
import numpy as np
from moviepy import VideoFileClip, AudioFileClip, CompositeVideoClip, ImageClip
from PIL import Image, ImageDraw
import yt_dlp
from syncedlyrics import search as lrc_search
from font import get_font
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import difflib
from pydub import AudioSegment
import tempfile
import json

# Optional: Levenshtein
try:
    import Levenshtein
    USE_LEV = True
except ImportError:
    USE_LEV = False

# ---------------- CONFIG ----------------
LINKS_FILE = "links/links.txt"
TRASH_FILE = "links/trash.txt"
BACKGROUND_FOLDER = "verticalbg"
FINAL_FOLDER = "TiktokAutoUploader/VideosDirPath"
MP3_FOLDER = "mp3"
METADATA_FOLDER = "metadata"

SEGMENT_DURATION_S = 35
VIDEO_SIZE = (1080, 1920)  # Portrait mode (1080x1920)
TITLE_FONT_SIZE = 50
LYRIC_FONT_SIZE = 40
MIN_LINE_DURATION_S = 0.8

CHAR_FACTOR = 0.8
MIN_WORD_DURATION_S = 0.06
MAX_WORD_DURATION_S = 2
FUZZY_MATCH_RATIO = 0.55

GLOBAL_SYNC_OFFSET_S = 0
START_TIME_S = None
SHOW_TITLE = False
MAX_VIDEO_DURATION = 30  # seconds

# Spotify API credentials
SPOTIFY_CLIENT_ID = "9ee4f1bd0800439e888bb839adb47721"
SPOTIFY_CLIENT_SECRET = "b60c490e6437477ba0e276094896a3a8"
sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET
))

os.makedirs(MP3_FOLDER, exist_ok=True)
os.makedirs(FINAL_FOLDER, exist_ok=True)
os.makedirs(METADATA_FOLDER, exist_ok=True)

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

def save_metadata(metadata):
    """Save metadata to a JSON file, overwriting if it already exists."""
    artist = metadata["artist"]
    song_title = metadata["song"]
    metadata_file = os.path.join(METADATA_FOLDER, f"{artist}_{song_title}.json")

    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=4, ensure_ascii=False)
    print(f"[DEBUG] Metadata saved for {artist} - {song_title} at {metadata_file}")

# Fetch metadata from Spotify
def get_spotify_metadata(track_id):
    track = sp.track(track_id)
    artist = ", ".join([a['name'] for a in track['artists']])
    song = track['name']
    duration_s = track['duration_ms'] / 1000
    print(f"[DEBUG] Spotify metadata: {artist} - {song}, duration {duration_s}s")
    
    # Prepare metadata dictionary
    metadata = {"artist": artist, "song": song, "duration": duration_s}
    
    # Save metadata as JSON file (overwrite if exists)
    save_metadata(metadata)
    
    return metadata


# ---------------- YOUTUBE AUDIO ----------------
def search_youtube(song, artist):
    query = f"{artist} {song}"
    print(f"[DEBUG] Searching YouTube: {query}")
    ydl_opts = {"quiet": True, "no_warnings": True, "format": "bestaudio/best"}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.extract_info(f"ytsearch1:{query}", download=False)
        return result['entries'][0]['webpage_url']

def download_audio(url, song_title):
    print(f"[DEBUG] Downloading audio: {url}")
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
def parse_lrc_content(lrc_content):
    if not lrc_content:
        print("[DEBUG] No LRC content.")
        return []
    raw_lines = []
    for line in lrc_content.splitlines():
        line = line.strip()
        if not line.startswith("["):
            continue
        try:
            ts, txt = line.split("]", 1)
            ts = ts[1:]
            if ":" in ts:
                mm, rest = ts.split(":", 1)
                if "." in rest:
                    ss, ms = rest.split(".", 1)
                    start = int(mm)*60 + int(ss) + float("0."+ms)
                else:
                    start = int(mm)*60 + int(rest)
            else:
                start = float(ts)
            start += GLOBAL_SYNC_OFFSET_S
            txt = txt.strip().lower()
            if txt.lower() not in ("instrumental", "(instrumental)") and txt:
                raw_lines.append((start, txt))
        except Exception as e:
            print(f"[DEBUG] Failed LRC parse: {line}  Error: {e}")

    if not raw_lines:
        return []

    parsed = []
    for i, (t0, txt) in enumerate(raw_lines):
        if i < len(raw_lines)-1:
            est = max(MIN_LINE_DURATION_S, raw_lines[i+1][0] - t0)
        else:
            est = max(MIN_LINE_DURATION_S, len(txt) * CHAR_FACTOR)
        parsed.append(((t0, t0+est), txt))
    return parsed

# ---------------- VIDEO CLIPS ----------------
def sanitize_filename(name):
    return re.sub(r'[<>:"/\\|?*]', '_', name)

def make_text_clip_grid(lines, start, end, song_title_words):
    img = Image.new("RGBA", (VIDEO_SIZE[0], VIDEO_SIZE[1]), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    font = get_font(LYRIC_FONT_SIZE)

    max_words_per_line = 3
    grid = []
    for line in lines:
        ws = [w.lower() for w in line.split()]  # convert all words to lowercase
        buff = []
        for w in ws:
            buff.append(w)
            if len(buff) >= max_words_per_line:
                grid.append(" ".join(buff))
                buff = []
        if buff:
            grid.append(" ".join(buff))

    y_offset = (VIDEO_SIZE[1] - LYRIC_FONT_SIZE * len(grid)) // 2
    for line in grid:
        words = line.split()
        total_width = sum(draw.textlength(w, font=font) for w in words) + 10*(len(words)-1)
        x_offset = (VIDEO_SIZE[0] - total_width) // 2

        for w in words:
            # thinner inline glow: smaller offsets
            for dx in [-0.5, 0, 0.5]:
                for dy in [-0.5, 0, 0.5]:
                    if dx or dy:
                        draw.text((x_offset+dx, y_offset+dy), w, font=font, fill="white")
            # main text
            draw.text((x_offset, y_offset), w, font=font, fill="white")
            x_offset += draw.textlength(w, font=font) + 10

        y_offset += LYRIC_FONT_SIZE

    return ImageClip(np.array(img)).with_start(start).with_duration(end-start)

def create_video(audio_segment, subtitles, bg_folder, output_file, song_title, start_time):
    # Continue with your existing code to process the video and audio
    clean = sanitize_filename(song_title)
    temp_audio = os.path.join(MP3_FOLDER, f"{clean}_temp.mp3")
    audio_segment.export(temp_audio, format="mp3")
    audio_clip = AudioFileClip(temp_audio)
    total = audio_clip.duration

    # Fetch random background video
    vids = [os.path.join(bg_folder,f) for f in os.listdir(bg_folder) if f.lower().endswith((".mp4",".mov",".mkv"))]
    if not vids:
        raise Exception("No background videos found.")
    bg = random.choice(vids)
    
    # Resize background video to 1080x1920 (portrait mode)
    bg_clip = VideoFileClip(bg)
    bg_clip_resized = bg_clip.resized((1080, 1920))  # Resize to 1080x1920 for portrait mode
    
    # Adjust the background clip duration to match the audio
    if bg_clip_resized.duration > total:
        bg_clip_resized = bg_clip_resized.subclipped(0, total)

    song_title_words = set(song_title.lower().split())

    word_clips = [make_text_clip_grid([text], t0, t1, song_title_words) for (t0, t1), text in subtitles]

    clips = [bg_clip_resized] + word_clips

    if SHOW_TITLE:
        img = Image.new("RGBA", (VIDEO_SIZE[0], VIDEO_SIZE[1]), (0,0,0,0))
        draw = ImageDraw.Draw(img)
        font = get_font(TITLE_FONT_SIZE)
        bbox = draw.textbbox((0,0), song_title, font=font)
        w,h = bbox[2]-bbox[0], bbox[3]-bbox[1]
        pos = ((VIDEO_SIZE[0]-w)//2, 50)
        draw.text(pos, song_title, font=font, fill="white", stroke_width=5, stroke_fill="black")
        clips.append(ImageClip(np.array(img)).with_start(0).with_duration(total))

    # Create the composite video
    final = CompositeVideoClip(clips)

    # Set audio to the video
    final = final.with_audio(audio_clip)

    # Set the output file path
    outpath = os.path.join(FINAL_FOLDER, sanitize_filename(os.path.basename(output_file)))

    # Write the video file with the correct settings
    final.write_videofile(outpath, fps=30, codec="libx264", audio_codec="aac", preset="veryslow", bitrate="15000k")

    os.remove(temp_audio)
    print(f"[DEBUG] Final video created: {outpath}")
    return outpath, total

# ---------------- FETCH LRC ----------------
def fetch_lrc_corrected(artist, song, max_duration_s):
    print(f"[DEBUG] Searching for LRC: {artist} - {song}")
    try:
        results = lrc_search(f"{artist} {song}")
    except Exception as e:
        print(f"[DEBUG] LRC search failed: {e}")
        return None
    if not results:
        return None
    if isinstance(results, str):
        results = [results]
    if not isinstance(results, list):
        results = [results]
    cleaned = []
    for r in results:
        if isinstance(r, dict):
            text = r.get("syncedLyrics") or r.get("lyrics")
        else:
            text = r
        if not isinstance(text, str) or len(text.strip()) < 4:
            continue
        cleaned.append(text.strip())
    if not cleaned:
        return None
    return min(cleaned, key=len)


def save_metadata_to_json(metadata):
    metadata_file = f"metadata/{metadata['artist']}_{metadata['song']}.json"
    os.makedirs("metadata", exist_ok=True)  # Ensure the metadata folder exists
    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=4)
    print(f"Metadata saved to {metadata_file}")

# ---------------- MAIN ----------------
if __name__ == "__main__":
    LINK = get_next_link()

    if is_spotify_link(LINK):
        track_id = get_spotify_track_id(LINK)
        meta = get_spotify_metadata(track_id)
        print(f"✓ Spotify track: {meta['song']} by {meta['artist']}")

        # Save the metadata to the metadata folder as a JSON file
        save_metadata_to_json(meta)

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
            print(f"[DEBUG] YT metadata -> Artist: {artist}, Song: {song}")
            return {"artist": artist, "song": song, "full_title": raw_title}

        meta = get_youtube_metadata(LINK)
        audio = download_audio(LINK, meta['song'])

    OUTPUT_VIDEO = meta['song'].replace(" ","_")+"_lyric_video.mp4"

    lrc = fetch_lrc_corrected(meta['artist'], meta['song'], SEGMENT_DURATION_S)
    subs_full = parse_lrc_content(lrc) if lrc else []

    START_TIME_S = subs_full[0][0][0] if subs_full else 0
    segment_duration = min(MAX_VIDEO_DURATION, subs_full[-1][0][1] - START_TIME_S) if subs_full else SEGMENT_DURATION_S
    trimmed = audio[int(START_TIME_S*1000):int((START_TIME_S+segment_duration)*1000)]

    subs_adj = []
    for (t0, t1), text in subs_full:
        t0 -= START_TIME_S
        t1 -= START_TIME_S
        if t1 > 0 and t0 < segment_duration:
            t0n = max(0, t0)
            t1n = min(segment_duration, max(t0n+0.1, t1))
            subs_adj.append(((t0n, t1n), text))

    create_video(trimmed, subs_adj, BACKGROUND_FOLDER, OUTPUT_VIDEO, meta['song'], START_TIME_S)

    print("--- Done ---")
