from time import sleep
from urllib.parse import urlparse, parse_qs
import requests
import json
import dotenv
import os   
from dotenv import load_dotenv
import base64

load_dotenv()

client_id = os.getenv('SPOTIFY_CLIENT_ID')
client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')

auth_url = 'https://accounts.spotify.com/api/token'
playlist_base_url = 'https://api.spotify.com/v1/playlists/{}'
album_base_url = 'https://api.spotify.com/v1/albums/{}'
track_base_url = 'https://api.spotify.com/v1/tracks/{}'

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'application/json',
}

class SpotifyInvalidUrlException(Exception):
    pass

class SpotifyWebsiteParserException(Exception):
    pass

class SpotifyAPIException(Exception):
    pass

def get_access_token():
    """Get access token using client credentials flow"""
    auth_header = base64.b64encode(f'{client_id}:{client_secret}'.encode()).decode()
    auth_headers = {
        'Authorization': f'Basic {auth_header}',
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    
    response = requests.post(
        auth_url,
        headers=auth_headers,
        data={'grant_type': 'client_credentials'}
    )
    
    if response.status_code != 200:
        raise SpotifyAPIException(f"Failed to get access token: {response.text}")
    
    return response.json()['access_token']

def parse_uri(uri):
    u = urlparse(uri)
    if u.netloc == "embed.spotify.com":
        if not u.query:
            raise SpotifyInvalidUrlException("ERROR: url {} is not supported".format(uri))
        qs = parse_qs(u.query)
        return parse_uri(qs['uri'][0])

    if not u.scheme and not u.netloc:
        return {"type": "playlist", "id": u.path}

    if u.scheme == "spotify":
        parts = uri.split(":")
    else:
        if u.netloc != "open.spotify.com" and u.netloc != "play.spotify.com":
            raise SpotifyInvalidUrlException("ERROR: url {} is not supported".format(uri))
        parts = u.path.split("/")

    if parts[1] == "embed":
        parts = parts[1:]

    l = len(parts)
    if l == 3 and parts[1] in ["album", "track", "playlist"]:
        return {"type": parts[1], "id": parts[2]}
    if l == 5 and parts[3] == "playlist":
        return {"type": parts[3], "id": parts[4]}

    raise SpotifyInvalidUrlException("ERROR: unable to determine Spotify URL type or type is unsupported.")

def get_json_from_api(api_url, access_token):
    request_headers = headers.copy()
    request_headers['Authorization'] = f'Bearer {access_token}'
    
    try:
        response = requests.get(api_url, headers=request_headers, timeout=10)
        
        if response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 1)) + 1
            print(f"Rate limited. Retrying after {retry_after} seconds")
            sleep(retry_after)
            return None
            
        response.raise_for_status()
        return response.json()
        
    except requests.exceptions.RequestException as e:
        raise SpotifyAPIException(f"API request failed: {str(e)}")

def get_raw_spotify_data(spotify_url):
    try:
        url_info = parse_uri(spotify_url)
    except SpotifyInvalidUrlException as e:
        return {"error": str(e)}
    
    try:
        access_token = get_access_token()
    except SpotifyAPIException as e:
        return {"error": str(e)}
    
    raw_data = {}
    
    try:
        if url_info['type'] == "playlist":
            # Playlist 데이터 가져오기
            playlist_data = get_json_from_api(
                playlist_base_url.format(url_info["id"]),
                access_token
            )
            
            # 트랙 데이터 가져오기
            tracks = []
            tracks_url = f'{playlist_base_url.format(url_info["id"])}/tracks?market=US&limit=100'
            while tracks_url:
                tracks_data = get_json_from_api(tracks_url, access_token)
                if not tracks_data: break
                tracks.extend(tracks_data['items'])
                tracks_url = tracks_data.get('next')
            
            playlist_data['tracks']['items'] = tracks
            raw_data = playlist_data
            
        elif url_info["type"] == "album":
            # 앨범 데이터 가져오기
            album_data = get_json_from_api(
                album_base_url.format(url_info["id"]),
                access_token
            )
            
            # 트랙 데이터 가져오기
            tracks = []
            tracks_url = f'{album_base_url.format(url_info["id"])}/tracks?limit=50'
            while tracks_url:
                tracks_data = get_json_from_api(tracks_url, access_token)
                if not tracks_data: break
                
                # 각 트랙 상세 정보 가져오기
                for track in tracks_data['items']:
                    track_detail = get_json_from_api(
                        track_base_url.format(track['id']),
                        access_token
                    )
                    if track_detail:
                        tracks.append(track_detail)
                
                tracks_url = tracks_data.get('next')
            
            album_data['tracks']['items'] = tracks
            raw_data = album_data
            
        elif url_info["type"] == "track":
            raw_data = get_json_from_api(
                track_base_url.format(url_info["id"]),
                access_token
            )
            
    except SpotifyAPIException as e:
        return {"error": str(e)}
    
    return raw_data

def format_track_data(track_data):
    artists = []
    artist_ids = []
    for artist in track_data['artists']:
        artists.append(artist['name'])
        artist_ids.append(artist['id'])
    
    image_url = track_data.get('album', {}).get('images', [{}])[0].get('url', '')
    
    return {
        "track": {
            "id": track_data.get('id', ''),
            "uri": track_data.get('uri', ''),
            "artists": ", ".join(artists),
            "artist_ids": artist_ids,
            "name": track_data.get('name', ''),
            "album_id": track_data.get('album', {}).get('id', ''),
            "album_name": track_data.get('album', {}).get('name', ''),
            "duration_ms": track_data.get('duration_ms', 0),
            "images": image_url,
            "release_date": track_data.get('album', {}).get('release_date', ''),
            "track_number": track_data.get('track_number', 0),
            "isrc": track_data.get('external_ids', {}).get('isrc', '')
        }
    }

def format_album_data(album_data):
    artists = []
    artist_ids = []
    for artist in album_data['artists']:
        artists.append(artist['name'])
        artist_ids.append(artist['id'])
    
    image_url = album_data.get('images', [{}])[0].get('url', '')
    
    track_list = []
    for track in album_data.get('tracks', {}).get('items', []):
        track_id = track['id']
        try:
            track_data = get_json_from_api(
                track_base_url.format(track_id),
                album_data['_token']
            )
            if track_data:
                formatted_track = format_track_data(track_data)
                track_list.append(formatted_track['track'])
            else:
                track_artists = []
                track_artist_ids = []
                for artist in track.get('artists', []):
                    track_artists.append(artist['name'])
                    track_artist_ids.append(artist['id'])
                    
                track_list.append({
                    "id": track.get('id', ''),
                    "uri": track.get('uri', ''),
                    "artists": ", ".join(track_artists),
                    "artist_ids": track_artist_ids,
                    "name": track.get('name', ''),
                    "album_id": album_data.get('id', ''),
                    "album_name": album_data.get('name', ''),
                    "duration_ms": track.get('duration_ms', 0),
                    "images": image_url,
                    "release_date": album_data.get('release_date', ''),
                    "track_number": track.get('track_number', 0),
                    "isrc": track.get('external_ids', {}).get('isrc', '')
                })
        except:
            continue
    
    return {
        "album_info": {
            "id": album_data.get('id', ''),
            "uri": album_data.get('uri', ''),
            "total_tracks": album_data.get('total_tracks', 0),
            "name": album_data.get('name', ''),
            "release_date": album_data.get('release_date', ''),
            "artists": ", ".join(artists),
            "artist_ids": artist_ids,
            "images": image_url
        },
        "track_list": track_list
    }

def format_playlist_data(playlist_data):
    image_url = playlist_data.get('images', [{}])[0].get('url', '')
    
    track_list = []
    for item in playlist_data.get('tracks', {}).get('items', []):
        track = item.get('track', {})
        artists = []
        artist_ids = []
        for artist in track.get('artists', []):
            artists.append(artist['name'])
            artist_ids.append(artist['id'])
            
        track_image = track.get('album', {}).get('images', [{}])[0].get('url', '')
        
        track_list.append({
            "id": track.get('id', ''),
            "uri": track.get('uri', ''),
            "artists": ", ".join(artists),
            "artist_ids": artist_ids,
            "name": track.get('name', ''),
            "album_id": track.get('album', {}).get('id', ''),
            "album_name": track.get('album', {}).get('name', ''),
            "duration_ms": track.get('duration_ms', 0),
            "images": track_image,
            "release_date": track.get('album', {}).get('release_date', ''),
            "track_number": track.get('track_number', 0),
            "isrc": track.get('external_ids', {}).get('isrc', '')
        })
    
    return {
        "playlist_info": {
            "id": playlist_data.get('id', ''),
            "uri": playlist_data.get('uri', ''),
            "tracks": {"total": playlist_data.get('tracks', {}).get('total', 0)},
            "followers": {"total": playlist_data.get('followers', {}).get('total', 0)},
            "owner": {
                "id": playlist_data.get('owner', {}).get('id', ''),
                "uri": playlist_data.get('owner', {}).get('uri', ''),
                "display_name": playlist_data.get('owner', {}).get('display_name', ''),
                "name": playlist_data.get('name', ''),
                "images": image_url
            }
        },
        "track_list": track_list
    }

def process_spotify_data(raw_data, data_type):
    if not raw_data or "error" in raw_data:
        return {"error": "Invalid data provided"}
        
    try:
        if data_type == "track":
            return format_track_data(raw_data)
        elif data_type == "album":
            return format_album_data(raw_data)
        elif data_type == "playlist":
            return format_playlist_data(raw_data)
        else:
            return {"error": "Invalid data type"}
    except Exception as e:
        return {"error": f"Error processing data: {str(e)}"}

def get_filtered_data(spotify_url):
    raw_data = get_raw_spotify_data(spotify_url)
    if raw_data and "error" not in raw_data:
        url_info = parse_uri(spotify_url)
        filtered_data = process_spotify_data(raw_data, url_info['type'])
        return filtered_data
    return {"error": "Failed to get raw data"}

if __name__ == '__main__':
    playlist = "https://open.spotify.com/playlist/37i9dQZEVXbNG2KDcFcKOF"
    album = "https://open.spotify.com/album/7kFyd5oyJdVX2pIi6P4iHE"
    song = "https://open.spotify.com/track/4wJ5Qq0jBN4ajy7ouZIV1c"
    
    filtered_playlist = get_filtered_data(playlist)
    print(json.dumps(filtered_playlist, indent=2))
    
    filtered_album = get_filtered_data(album)
    print(json.dumps(filtered_album, indent=2))
    
    filtered_track = get_filtered_data(song)
    print(json.dumps(filtered_track, indent=2))