from urllib.parse import urlparse, parse_qs
import requests
import json
import os
import base64
from dotenv import load_dotenv
import aiohttp

load_dotenv()

client_id = os.getenv('SPOTIFY_CLIENT_ID')
client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')

auth_url = 'https://accounts.spotify.com/api/token'
playlist_base_url = 'https://api.spotify.com/v1/playlists/{}'
album_base_url = 'https://api.spotify.com/v1/albums/{}'
track_base_url = 'https://api.spotify.com/v1/tracks/{}'

class SpotifyInvalidUrlException(Exception):
    pass

class SpotifyAPIException(Exception):
    pass

def get_access_token():
    auth_header = base64.b64encode(f'{client_id}:{client_secret}'.encode()).decode()
    headers = {
        'Authorization': f'Basic {auth_header}',
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    response = requests.post(auth_url, headers=headers, data={'grant_type': 'client_credentials'})
    if response.status_code != 200:
        raise SpotifyAPIException(f"Failed to get token: {response.text}")
    return response.json()['access_token']

def parse_uri(uri):
    u = urlparse(uri)
    if u.netloc == "embed.spotify.com":
        qs = parse_qs(u.query)
        return parse_uri(qs['uri'][0]) if qs.get('uri') else None

    if u.scheme == "spotify":
        parts = uri.split(":")
    else:
        if u.netloc not in ["open.spotify.com", "play.spotify.com"]:
            raise SpotifyInvalidUrlException(f"Unsupported URL: {uri}")
        parts = u.path.split("/")

    parts = [p for p in parts if p]
    if len(parts) >= 2 and parts[-2] in ["track", "album", "playlist"]:
        return {"type": parts[-2], "id": parts[-1]}
    raise SpotifyInvalidUrlException(f"Unsupported URL structure: {uri}")

def fetch_all_items(url, access_token):
    items = []
    while url:
        response = requests.get(url, headers={'Authorization': f'Bearer {access_token}'})
        if response.status_code != 200:
            raise SpotifyAPIException(f"API error: {response.text}")
        data = response.json()
        items.extend(data['items'])
        url = data.get('next')
    return items

def get_raw_spotify_data(spotify_url):
    try:
        url_info = parse_uri(spotify_url)
        access_token = get_access_token()
    except Exception as e:
        return {"error": str(e)}

    try:
        if url_info['type'] == "playlist":
            playlist = requests.get(
                f'{playlist_base_url.format(url_info["id"])}',
                headers={'Authorization': f'Bearer {access_token}'}
            ).json()
            playlist['tracks']['items'] = fetch_all_items(
                f'{playlist_base_url.format(url_info["id"])}/tracks?limit=100',
                access_token
            )
            return playlist
        elif url_info['type'] == "album":
            album = requests.get(
                f'{album_base_url.format(url_info["id"])}',
                headers={'Authorization': f'Bearer {access_token}'}
            ).json()
            album['tracks']['items'] = fetch_all_items(
                f'{album_base_url.format(url_info["id"])}/tracks?limit=50',
                access_token
            )
            return album
        elif url_info['type'] == "track":
            return requests.get(
                f'{track_base_url.format(url_info["id"])}',
                headers={'Authorization': f'Bearer {access_token}'}
            ).json()
    except Exception as e:
        return {"error": str(e)}

def format_data(raw_data, data_type):
    if data_type == "track":
        return format_track_data(raw_data)
    elif data_type == "album":
        return format_album_data(raw_data)
    elif data_type == "playlist":
        return format_playlist_data(raw_data)
    return {"error": "Invalid data type"}

def format_track_data(data):
    return {
        "track": {
            "id": data['id'],
            "name": data['name'],
            "artists": ", ".join([a['name'] for a in data['artists']]),
            "album_name": data['album']['name'],
            "duration_ms": data['duration_ms'],
            "images": data['album']['images'][0]['url'] if data['album']['images'] else "",
            "release_date": data['album']['release_date'],
            "isrc": data['external_ids'].get('isrc', '')
        }
    }

def format_album_data(data):
    return {
        "album_info": {
            "name": data['name'],
            "release_date": data['release_date'],
            "images": data['images'][0]['url'] if data['images'] else ""
        },
        "track_list": [
            {
                "id": t['id'],
                "name": t['name'],
                "artists": ", ".join([a['name'] for a in t['artists']]),
                "track_number": t['track_number'],
                "duration_ms": t['duration_ms'],
                "isrc": t.get('external_ids', {}).get('isrc', '')
            } for t in data['tracks']['items']
        ]
    }

def format_playlist_data(data):
    return {
        "playlist_info": {
            "name": data['name'],
            "owner": data['owner']['display_name']
        },
        "track_list": [
            {
                "id": item['track']['id'],
                "name": item['track']['name'],
                "artists": ", ".join([a['name'] for a in item['track']['artists']]),
                "album_name": item['track']['album']['name'],
                "duration_ms": item['track']['duration_ms'],
                "images": item['track']['album']['images'][0]['url'] if item['track']['album']['images'] else "",
                "release_date": item['track']['album']['release_date'],
                "isrc": item['track']['external_ids'].get('isrc', '')
            } for item in data['tracks']['items'] if item['track']
        ]
    }

def get_filtered_data(spotify_url):
    raw_data = get_raw_spotify_data(spotify_url)
    if 'error' in raw_data:
        return raw_data
    url_info = parse_uri(spotify_url)
    return format_data(raw_data, url_info['type'])