import subprocess
import sys
import os
import json

# Ensure you are in the correct virtual environment
venv_python = "D:/Github/Tiktoker/venv/Scripts/python.exe"  # Path to your virtual environment's python executable

# Function to load metadata
def load_metadata():
    metadata_path = "metadata"  # Adjust to your metadata folder path
    # Ensure the metadata folder exists
    if not os.path.exists(metadata_path):
        print(f"[ERROR] Metadata folder '{metadata_path}' does not exist.")
        sys.exit(1)

    # Find the latest metadata JSON file (artist_song_metadata.json)
    metadata_files = [f for f in os.listdir(metadata_path) if f.endswith('.json')]
    if not metadata_files:
        print(f"[ERROR] No metadata file found in '{metadata_path}'.")
        sys.exit(1)

    metadata_file = max(metadata_files, key=lambda f: os.path.getmtime(os.path.join(metadata_path, f)))  # Get the latest file
    metadata_file_path = os.path.join(metadata_path, metadata_file)

    try:
        with open(metadata_file_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
        return metadata
    except Exception as e:
        print(f"[ERROR] Failed to load metadata from '{metadata_file_path}': {e}")
        sys.exit(1)


metadata = load_metadata()
# Change directory to TiktokAutoUploader if needed
os.chdir("TiktokAutoUploader")

# Load metadata

title = metadata['song']
artist = metadata['artist']

# Get the output video file path (ensure the correct final file)
video_file = f"{title.replace(' ', '_')}_lyric_video.mp4"

# Run the TikTok upload command within the correct environment
try:
    subprocess.run([venv_python, "cli.py", "upload", "-u", "cloud0ra", "-v", video_file, "-t", f"{title} #{artist}", "-vi", "1"], check=True)
    print("TikTok upload successful!")
except subprocess.CalledProcessError as e:
    print(f"[ERROR] Error uploading to TikTok: {e}")
    sys.exit(1)
