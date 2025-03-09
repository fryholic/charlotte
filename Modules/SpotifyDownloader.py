import requests
import urllib.parse
import re

_SESSION = None

def debug_response(response, max_length=1000):
    content = response.text[:max_length] + '...' if len(response.text) > max_length else response.text
    print(f"[Debug] Response Snippet:\n{content}\n")

def get_cookie(quality='320'):
    global _SESSION
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
        'Referer': 'https://spotisongdownloader.to/'
    }
    try:
        if _SESSION is None:
            _SESSION = requests.Session()

        response = _SESSION.get('https://spotisongdownloader.to/', headers=headers)
        print(f"Cookie Status: {response.status_code}")
        
        cookies = _SESSION.cookies.get_dict()
        return f"PHPSESSID={cookies.get('PHPSESSID', '')}; quality={quality}"
    except Exception as e:
        print(f"Cookie Error: {str(e)}")
        return None

def get_api():
    global _SESSION
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
        'Referer': 'https://spotisongdownloader.to/'
    }
    try:
        response = _SESSION.get('https://spotisongdownloader.to/track.php', headers=headers)
        print(f"API Status: {response.status_code}")
        debug_response(response)

        patterns = [
            r'url:\s*["\'](/api/composer/spotify/[^"\']+)["\']',
            r'url\s*:\s*["\']([^"\']+/api/[^"\']+)["\']',
            r'const apiUrl\s*=\s*["\']([^"\']+)["\']'
        ]

        for pattern in patterns:
            match = re.search(pattern, response.text)
            if match:
                api_endpoint = urllib.parse.urljoin('https://spotisongdownloader.to', match.group(1))
                print(f"API Endpoint Found: {api_endpoint}")
                return api_endpoint

        print("❗ No API Pattern Matched")
        return None
            
    except Exception as e:
        print(f"API Error: {str(e)}")
        return None

def get_data(link):
    try:
        response = requests.get(
            'https://spotisongdownloader.to/api/composer/spotify/xsingle_track.php', 
            params={'url': link},
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
                'Referer': 'https://spotisongdownloader.to/'
            },
            timeout=15
        )
        
        print(f"[DEBUG] GET Data Status: {response.status_code}")

        response.raise_for_status()
        return response.json()
        
    except requests.exceptions.JSONDecodeError:
        print(f"[ERROR] Invalid JSON Response: {response.text[:200]}...")
        return None
        
    except Exception as e:
        print(f"[ERROR] get_data error: {str(e)}")
        return None

def get_url(track_data, cookie):
    api_url = get_api()
    if not api_url:
        print("API URL을 찾을 수 없음")
        return None

    headers = {
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Cookie': cookie,
        'Origin': 'https://spotisongdownloader.to',
        'Referer': 'https://spotisongdownloader.to/track.php',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36'
    }

    payload = {
        'song_name': track_data['song_name'],
        'artist_name': track_data['artist'],
        'url': track_data['url']
    }

    try:
        response = requests.post(api_url, data=payload, headers=headers)
        response.raise_for_status()
        download_data = response.json()
        
        track_data['dlink'] = download_data.get('dlink')
        if not track_data['dlink']:
            print("dlink 필드 누락")
            return None

        return get_id3_url(track_data, cookie)

    except Exception as e:
        print(f"Download Error: {str(e)}")
        return None

def get_id3_url(track_data, cookie):
    headers = {
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Cookie': cookie,
        'Origin': 'https://spotisongdownloader.to',
        'Referer': 'https://spotisongdownloader.to/track.php',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36'
    }

    payload = {
        'url': track_data['dlink'],
        'name': track_data['song_name'],
        'artist': track_data['artist'],
        'album': track_data.get('album', 'Unknown Album'),
        'thumb': track_data.get('thumb', ''),
        'released': track_data.get('released', '')
    }

    try:
        response = requests.post(
            'https://spotisongdownloader.to/api/composer/ffmpeg/saveid3.php',
            data=payload,
            headers=headers
        )
        response.raise_for_status()
        
        filename = response.text.strip()
        return f"https://spotisongdownloader.to/api/composer/ffmpeg/saved/{filename}"
    
    except Exception as e:
        print(f"ID3 Error: {str(e)}")
        return None

def get_spotify_download_link(spotify_url):
    """Spotify URL을 입력받아 다운로드 링크 반환"""
    cookie = get_cookie()
    if not cookie:
        raise ValueError("쿠키 획득 실패")
    
    track_data = get_data(spotify_url)
    if not track_data:
        raise ValueError("트랙 데이터 추출 실패")
    
    final_link = get_url(track_data, cookie)
    if not final_link:
        raise ValueError("다운로드 링크 생성 실패")
    
    return final_link 