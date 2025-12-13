import spotipy
from spotipy.oauth2 import SpotifyOAuth



# Authentication with your Spotify app credentials
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id='9ee4f1bd0800439e888bb839adb47721',          # Replace with your Client ID
    client_secret='b60c490e6437477ba0e276094896a3a8',  # Replace with your Client Secret
    redirect_uri='http://127.0.0.1:5555/callback',    # Replace with your Redirect URI
    scope='playlist-read-private'        # Scope to access private playlists
))

# Playlist ID for the specific playlist
playlist_id = '0kBmAPjjVpzoPfMAvw5gJp'  # Replace with your playlist ID

def get_playlist_tracks(playlist_id):
    # Get the playlist tracks
    results = sp.playlist_tracks(playlist_id)
    track_links = []

    # Extract track URLs
    for item in results['items']:
        track_links.append(item['track']['external_urls']['spotify'])

    return track_links

# Fetch and print track links from the playlist
track_links = get_playlist_tracks(playlist_id)
for link in track_links:
    print(link)
