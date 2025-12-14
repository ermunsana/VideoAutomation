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

SEGMENT_DURATION_S = 30
VIDEO_SIZE = (1080, 1920)  # Portrait mode (1080x1920)
LYRIC_FONT_SIZE = 40
MIN_LINE_DURATION_S = 0.8

CHAR_FACTOR = 0.8
MIN_WORD_DURATION_S = 0.06
MAX_WORD_DURATION_S = 2
FUZZY_MATCH_RATIO = 0.55

GLOBAL_SYNC_OFFSET_S = 0
START_TIME_S = None
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


def normalize(text):
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def similarity(a, b):
    if USE_LEV:
        return Levenshtein.ratio(normalize(a), normalize(b))
    return difflib.SequenceMatcher(None, normalize(a), normalize(b)).ratio()



# ---------------- YOUTUBE AUDIO ----------------
def search_youtube(song, artist):
    query = f"{artist} {song}"
    print(f"[DEBUG] Searching YouTube: {query}")
    ydl_opts = {"quiet": True, "no_warnings": True, "format": "bestaudio/best"}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.extract_info(f"ytsearch1:{query}", download=False)
        return result['entries'][0]['webpage_url']
    


def search_youtube_scored(song, artist, spotify_duration, max_results=10):
    query = f"{artist} {song}"
    print(f"\n[YOUTUBE] Query: {query}")

    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        results = ydl.extract_info(
            f"ytsearch{max_results}:{query}",
            download=False
        )["entries"]

    scored = []

    for r in results:
        title = r.get("title", "")
        channel = r.get("uploader", "")
        duration = r.get("duration") or 0

        score = (
            similarity(title, song) * 0.55 +
            similarity(f"{title} {channel}", artist) * 0.35
        )

        # Prefer official audio
        if re.search(r"(official audio|audio only)", title, re.I):
            score += 0.5

        # Nuke music videos
        if re.search(r"(official video|music video|\bmv\b)", title, re.I):
            score -= 0.8

        # Trash filters
        if re.search(r"(lyrics|lyric video|karaoke|remix|cover|nightcore)", title, re.I):
            score -= 0.6

        # Duration match with Spotify
        if spotify_duration and duration:
            diff = abs(duration - spotify_duration)
            if diff <= 2:
                score += 0.4
            elif diff > 15:
                score -= 0.5

        scored.append({
            "url": r["webpage_url"],
            "title": title,
            "duration": duration,
            "score": round(score, 3)
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    print("[YOUTUBE] Candidates:")
    for s in scored:
        print(f"  {s['score']:>5} | {s['duration']:>4}s | {s['title']}")

    best = scored[0]
    print(f"[YOUTUBE] Selected: {best['title']}\n")

    return best["url"]


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

#def build_progressive_line_subs(subtitles):
    """
    Returns a list of ((start, end), full_text_so_far)
    where words accumulate progressively within each lyric line.
    """
    progressive = []

    for (t0, t1), text in subtitles:
        words = text.split()
        if not words:
            continue

        line_duration = t1 - t0
        bias = 0.65  # lower = earlier words appear faster
        step = line_duration * bias / len(words) #


        for i in range(len(words)):
            start = t0 + i * step
            end = t0 + (i + 1) * step if i < len(words) - 1 else t1

            progressive_text = " ".join(words[:i+1])
            progressive.append(((start, end), progressive_text))

    return progressive


def create_video(audio_segment, subtitles, output_path, title):
    
    print(f"[DEBUG] Creating video for: {title}")
    temp_audio = os.path.join(MP3_FOLDER, "temp_audio.mp3")
    audio_segment.export(temp_audio, format="mp3")
    print(f"[DEBUG] Exported temp audio to {temp_audio}")

    audio_clip = AudioFileClip(temp_audio)
    total_duration = audio_clip.duration
    print(f"[DEBUG] Audio duration: {total_duration}s")

    # Select a random background video
    vids = [
        os.path.join(BACKGROUND_FOLDER, f)
        for f in os.listdir(BACKGROUND_FOLDER)
        if f.lower().endswith((".mp4", ".mov", ".mkv"))
    ]
    if not vids:
        raise Exception("No background videos found in " + BACKGROUND_FOLDER)

    bg_file = random.choice(vids)
    print(f"[DEBUG] Using background: {bg_file}")
    bg_clip = VideoFileClip(bg_file).resized(VIDEO_SIZE)

    # Trim background to match audio duration
    if bg_clip.duration > total_duration:
        bg_clip = bg_clip.subclipped(0, total_duration)
        print(f"[DEBUG] Trimmed background to {total_duration}s")

    # Create text clips for each subtitle line using grid layout
    text_clips = []
    song_title_words = set(title.lower().split())
    for (start, end), text in subtitles:
        print(f"[DEBUG] Subtitle: '{text}' ({start:.2f}-{end:.2f}s)")
        clip = make_text_clip_grid([text], start, end, song_title_words)
        text_clips.append(clip)

    # Combine background + text + audio
    final = CompositeVideoClip([bg_clip] + text_clips).with_audio(audio_clip)

    # Write final video
    print(f"[DEBUG] Writing video to {output_path}")
    final.write_videofile(
        output_path,
        fps=30,
        codec="libx264",
        audio_codec="aac",
        preset="slow",
        bitrate="15000k"
    )

    os.remove(temp_audio)
    print(f"[DEBUG] Video creation complete: {output_path}")


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
    # Remove spaces from artist name inside metadata
    metadata['artist'] = metadata['artist'].replace(" ", "")

    # Build metadata file path
    artist_clean = metadata['artist']
    metadata_file = f"metadata/{artist_clean}.json"

    # Ensure metadata folder exists
    os.makedirs("metadata", exist_ok=True)

    # Save metadata
    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=4)

    print(f"[DEBUG] Metadata saved to {metadata_file}")



# ---------------- MAIN ----------------
if __name__ == "__main__":
    # Get next Spotify link
    LINK = get_next_link()

    if not is_spotify_link(LINK):
        raise Exception("Expected a Spotify track link.")

    # Get Spotify metadata
    track_id = get_spotify_track_id(LINK)
    meta = get_spotify_metadata(track_id)
    print(f"[DEBUG] Spotify track: {meta['song']} by {meta['artist']}")

    # Search for YouTube audio
    youtube_url = search_youtube_scored(
        meta["song"],
        meta["artist"],
        meta["duration"]
    )

    # Download audio
    audio = download_audio(youtube_url, meta["song"])

    # Fetch LRC lyrics
    lrc = lrc = fetch_lrc_corrected(meta["artist"], meta["song"], SEGMENT_DURATION_S)
    if not lrc:
        raise Exception("No lyrics found.")
    subs_full = parse_lrc_content(lrc)

    # Determine trim times
    START_TIME = subs_full[0][0][0]
    END_TIME = min(
        START_TIME + MAX_VIDEO_DURATION,
        subs_full[-1][0][1]
    )

    # Trim audio segment
    trimmed_audio = audio[int(START_TIME * 1000):int(END_TIME * 1000)]

    # Adjust subtitle timings relative to trimmed audio
    subs_adj = []
    for (t0, t1), text in subs_full:
        if t1 <= START_TIME or t0 >= END_TIME:
            continue
        subs_adj.append(((t0 - START_TIME, t1 - START_TIME), text))

    print(f"[DEBUG] Final subtitle lines: {len(subs_adj)}")

    # Create video filename (single line-by-line lyric video)
    title = meta["song"]
    video_file = f"{title.replace(' ', '_')}_lyric_video.mp4"
    output_path = os.path.join(FINAL_FOLDER, video_file)

    # Generate the video
    create_video(
        trimmed_audio,
        subs_adj,
        output_path,
        title
    )

    print(f"[DEBUG] Video saved: {output_path}")
    print("[DEBUG] Done")

