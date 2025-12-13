import subprocess
import sys
import os
import json

# Define the run_upload function to upload the video
def run_upload():
    print("Running TikTok upload...")

    # Read metadata to get the song title and artist
    metadata_file = "metadata/Joji_PIXELATED KISSES.json"
    
    if not os.path.exists(metadata_file):
        print(f"[ERROR] Metadata file {metadata_file} not found.")
        sys.exit(1)
    
    # Load the metadata (song title and artist)
    with open(metadata_file, "r") as f:
        metadata = json.load(f)
    
    song_title = metadata.get('song')
    artist = metadata.get('artist')

    if not song_title or not artist:
        print("[ERROR] Metadata missing song title or artist.")
        sys.exit(1)

    # Set the path to your final video file
    final_video_path = f"TiktokAutoUploader/VideosDirPath/{song_title.replace(' ', '_')}_lyric_video.mp4"

    # Ensure the video file exists
    if not os.path.exists(final_video_path):
        print(f"[ERROR] Video file {final_video_path} not found.")
        sys.exit(1)

    try:
        # Change directory to the folder where the TikTok Auto Uploader resides
        os.chdir("TiktokAutoUploader")

        # Run the TikTok upload command
        subprocess.run(['python', 'cli.py', 'upload', '-u', 'cloud0ra', '-v', final_video_path, '-t', f'{song_title} #{artist}'], check=True)
        
        print("TikTok upload successful!")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Error uploading to TikTok: {e}")
        sys.exit(1)

# Main test function to directly upload the video
def test_upload():
    # Step 1: Run the TikTok upload function
    run_upload()

# Run the test
if __name__ == "__main__":
    test_upload()
