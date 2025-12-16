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
BACKGROUND_FOLDER = "backgrounds1920x1080"
FINAL_FOLDER = "TiktokAutoUploader/VideosDirPath"
MP3_FOLDER = "mp3"
METADATA_FOLDER = "metadata"

SEGMENT_DURATION_S = 30
VIDEO_SIZE = (1080, 1920)
LYRIC_FONT_SIZE = 45
MIN_LINE_DURATION_S = 0.8
CHAR_FACTOR = 0.8
GLOBAL_SYNC_OFFSET_S = 0
MAX_VIDEO_DURATION = 30

# Spotify API
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
    print(f"[DEBUG] Retrieved link: {link}")
    return link

def is_spotify_link(url):
    return "open.spotify.com/track" in url or url.startswith("spotify:track:")

def get_spotify_track_id(url):
    if "open.spotify.com/track/" in url:
        return url.split("track/")[1].split("?")[0]
    if url.startswith("spotify:track:"):
        return url.split(":")[2]
    return None

# ---------------- METADATA ----------------
def save_metadata_to_json(metadata):
    artistclean = metadata["artistclean"]
    song_clean = metadata["song"].replace(" ", "_").lower()
    os.makedirs(METADATA_FOLDER, exist_ok=True)
    metadata_file = os.path.join(METADATA_FOLDER, f"{artistclean}_{song_clean}.json")
    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=4, ensure_ascii=False)
    print(f"[DEBUG] Metadata saved to {metadata_file}")

def get_spotify_metadata(track_id):
    track = sp.track(track_id)
    artist = ", ".join([a['name'] for a in track['artists']])
    song = track['name']
    duration_s = track['duration_ms'] / 1000
    print(f"[DEBUG] Spotify metadata: {artist} - {song}, duration {duration_s}s")

    artistclean = artist.replace(" ", "").lower()

    metadata = {
        "artist": artist,           # normal format for YouTube search
        "artistclean": artistclean, # sanitized for TikTok captions / filenames
        "song": song,
        "duration": duration_s
    }

    save_metadata_to_json(metadata)
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
        print(f"[DEBUG] YouTube search result: {result['entries'][0]['title']}")
        return result['entries'][0]['webpage_url']

def search_youtube_scored(song, artist, spotify_duration, max_results=10):
    query = f"{artist} {song}"
    print(f"[DEBUG] YouTube scored search for: {query}")
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        results = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)["entries"]
    scored = []
    for r in results:
        title = r.get("title", "")
        channel = r.get("uploader", "")
        duration = r.get("duration") or 0
        score = similarity(title, song) * 0.55 + similarity(f"{title} {channel}", artist) * 0.35
        if re.search(r"(official audio|audio only)", title, re.I): score += 0.5
        if re.search(r"(official video|music video|\bmv\b)", title, re.I): score -= 0.8
        if re.search(r"(lyrics|lyric video|karaoke|remix|cover|nightcore)", title, re.I): score -= 0.6
        if spotify_duration and duration:
            diff = abs(duration - spotify_duration)
            if diff <= 2: score += 0.4
            elif diff > 15: score -= 0.5
        scored.append({"url": r["webpage_url"], "title": title, "duration": duration, "score": round(score, 3)})
    scored.sort(key=lambda x: x["score"], reverse=True)
    for s in scored:
        print(f"[DEBUG] Candidate: {s['score']} | {s['duration']}s | {s['title']}")
    best = scored[0]
    print(f"[DEBUG] Selected YouTube video: {best['title']}")
    return best["url"]

def download_audio(url, song_title):
    clean_title_str = re.sub(r'[^\w\d-]', '_', song_title.lower())
    temp_path = os.path.join(MP3_FOLDER, f"{clean_title_str}_temp.mp3")
    if os.path.exists(temp_path):
        print(f"[DEBUG] Using cached audio: {temp_path}")
        return AudioSegment.from_mp3(temp_path)
    
    print(f"[DEBUG] Downloading audio: {url}")
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(MP3_FOLDER, clean_title_str + '.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192'
        }],
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
    
    downloaded_path = os.path.join(MP3_FOLDER, clean_title_str + ".mp3")
    if not os.path.exists(downloaded_path):
        raise Exception(f"Download failed, file not found: {downloaded_path}")
    
    os.rename(downloaded_path, temp_path)
    print(f"[DEBUG] Audio downloaded to: {temp_path}")
    return AudioSegment.from_mp3(temp_path)

# ---------------- LRC ----------------
def parse_lrc_content(lrc_content):
    if not lrc_content:
        print("[DEBUG] No LRC content.")
        return []
    raw_lines = []
    for line in lrc_content.splitlines():
        line = line.strip()
        if not line.startswith("["): continue
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
        print("[DEBUG] No valid LRC lines found.")
        return []
    parsed = []
    for i, (t0, txt) in enumerate(raw_lines):
        if i < len(raw_lines)-1:
            est = max(MIN_LINE_DURATION_S, raw_lines[i+1][0] - t0)
        else:
            est = max(MIN_LINE_DURATION_S, len(txt) * CHAR_FACTOR)
        parsed.append(((t0, t0+est), txt))
    print(f"[DEBUG] Parsed {len(parsed)} subtitle lines")
    return parsed

# ---------------- VIDEO CLIPS ----------------
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

    # lowercase full phrase for matching
    phrase_lower = " ".join([w.lower() for w in song_title_words])

    for line in grid:
        words = line.split()
        total_width = sum(draw.textlength(w, font=font) for w in words) + 10*(len(words)-1)
        x_offset = (VIDEO_SIZE[0] - total_width) // 2

        # check for exact match in line
        line_lower = line.lower()
        idx = line_lower.find(phrase_lower)
        if idx != -1:
            # split line into before, match, after
            before = line[:idx]
            match = line[idx:idx+len(phrase_lower)]
            after = line[idx+len(phrase_lower):]

            for text, color in [(before, "white"), (match, "red"), (after, "white")]:
                for w in text.split():
                    for dx in [-0.5, 0, 0.5]:
                        for dy in [-0.5, 0, 0.5]:
                            if dx or dy:
                                draw.text((x_offset+dx, y_offset+dy), w, font=font, fill=color)
                    draw.text((x_offset, y_offset), w, font=font, fill=color)
                    x_offset += draw.textlength(w, font=font) + 10
        else:
            for w in words:
                for dx in [-0.5, 0, 0.5]:
                    for dy in [-0.5, 0, 0.5]:
                        if dx or dy:
                            draw.text((x_offset+dx, y_offset+dy), w, font=font, fill="white")
                draw.text((x_offset, y_offset), w, font=font, fill="white")
                x_offset += draw.textlength(w, font=font) + 10

        y_offset += LYRIC_FONT_SIZE

    return ImageClip(np.array(img)).with_start(start).with_duration(end-start)


# ---------------- VIDEO CREATION ----------------
def create_video(audio_segment, subtitles, output_path, title):
    print(f"[DEBUG] Creating video for: {title}")
    temp_audio = os.path.join(MP3_FOLDER, "temp_audio.mp3")
    audio_segment.export(temp_audio, format="mp3")
    print(f"[DEBUG] Exported temp audio to {temp_audio}")
    audio_clip = AudioFileClip(temp_audio).with_duration(MAX_VIDEO_DURATION)
    total_duration = audio_clip.duration
    print(f"[DEBUG] Audio duration set to {total_duration}s")
    vids = [os.path.join(BACKGROUND_FOLDER, f) for f in os.listdir(BACKGROUND_FOLDER) if f.lower().endswith((".mp4",".mov",".mkv"))]
    if not vids: raise Exception("No background videos found in " + BACKGROUND_FOLDER)
    bg_file = random.choice(vids)
    print(f"[DEBUG] Using background: {bg_file}")
    bg_clip = VideoFileClip(bg_file).resized(VIDEO_SIZE).subclipped(0, total_duration)
    song_title_phrase = meta["song"].lower()
    text_clips = []
    for (start, end), text in subtitles:
        if start >= total_duration: continue
        end = min(end, total_duration)
        clip = make_text_clip_grid([text], start, end, song_title_phrase)
        text_clips.append(clip)
    final = CompositeVideoClip([bg_clip]+text_clips).with_audio(audio_clip)
    print(f"[DEBUG] Writing video to {output_path}")
    final.write_videofile(output_path, fps=30, codec="libx264", audio_codec="aac", preset="slow", bitrate="15000k")
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
        print("[DEBUG] No results from LRC search")
        return None
    if isinstance(results, str):
        results = [results]
    if not isinstance(results, list):
        results = [results]

    cleaned = []
    for i, r in enumerate(results):
        text = None
        source = None

        if isinstance(r, dict):
            text = r.get("syncedLyrics") or r.get("lyrics")
            source = r.get("source") or r.get("url")
        elif isinstance(r, str):
            text = r

        if isinstance(text, str) and len(text.strip()) >= 4:
            cleaned.append((i, text.strip(), source))
            print(f"[DEBUG] Candidate {i} lyrics length {len(text.strip())}")
            if source:
                print(f"[DEBUG] Candidate {i} source: {source}")
            else:
                print(f"[DEBUG] Candidate {i} source: unknown")

    if not cleaned:
        print("[DEBUG] No cleaned lyrics found")
        return None

    chosen_index, chosen_text, chosen_source = min(cleaned, key=lambda x: len(x[1]))
    print(f"[DEBUG] Selected lyrics from candidate {chosen_index} (source: {chosen_source})")
    return chosen_text

# ---------------- MAIN ----------------
if __name__ == "__main__":
    LINK = get_next_link()
    if not is_spotify_link(LINK): raise Exception("Expected a Spotify track link.")
    track_id = get_spotify_track_id(LINK)
    meta = get_spotify_metadata(track_id)
    print(f"[DEBUG] Spotify track: {meta['song']} by {meta['artist']}")
    youtube_url = search_youtube_scored(meta["song"], meta["artist"], meta["duration"])
    print(f"[DEBUG] Selected YouTube video URL: {youtube_url}")
    audio = download_audio(youtube_url, meta["song"])
    lrc = fetch_lrc_corrected(meta["artist"], meta["song"], SEGMENT_DURATION_S)
    if not lrc: raise Exception("No lyrics found.")
    subs_full = parse_lrc_content(lrc)
    START_TIME = subs_full[0][0][0]
    END_TIME = min(START_TIME + MAX_VIDEO_DURATION, subs_full[-1][0][1])
    trimmed_audio = audio[int(START_TIME*1000):int(END_TIME*1000)]
    subs_adj = []
    for (t0, t1), text in subs_full:
        if t1 <= START_TIME or t0 >= END_TIME: continue
        subs_adj.append(((t0 - START_TIME, t1 - START_TIME), text))
    print(f"[DEBUG] Final subtitle lines: {len(subs_adj)}")
    title = meta["song"]
    video_file = f"{title.replace(' ', '_')}_lyric_video.mp4"
    output_path = os.path.join(FINAL_FOLDER, video_file)
    create_video(trimmed_audio, subs_adj, output_path, title)
    print(f"[DEBUG] Video saved: {output_path}")
    print("[DEBUG] Done")