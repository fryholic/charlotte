import sys
import os
import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
import requests
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, TDRC, TRCK, TSRC, COMM

import aiohttp

from Modules.getMetadata_v2 import get_filtered_data, parse_uri, SpotifyInvalidUrlException
from Modules.getToken_v1 import main as get_token_fast
from Modules.getToken_v2 import main as get_token_slow

@dataclass
class Track:
    id: str
    title: str
    artists: str
    album: str
    track_number: int
    duration_ms: int
    isrc: str = ""
    image_url: str = ""
    release_date: str = ""

class Downloader:
    _token = None
    _token_expiry = None
    _lock = asyncio.Lock()

    def __init__(self, token=None, output_path=None, filename_format='title_artist', 
                 use_track_numbers=True, use_album_subfolders=False):
        self.token = None
        self.output_path = output_path
        self.filename_format = filename_format
        self.use_track_numbers = use_track_numbers
        self.use_album_subfolders = use_album_subfolders

    @classmethod
    async def get_token(cls):
        async with cls._lock:
            if cls._token is None or datetime.now() > cls._token_expiry:
                cls._token = await get_token_fast()
                cls._token_expiry = datetime.now() + timedelta(minutes=3)
            return cls._token

    

    async def fetch_tracks(self, url):
        try:
            metadata = get_filtered_data(url)
            if "error" in metadata:
                raise Exception(metadata["error"])
            
            url_info = parse_uri(url)
            tracks = []
            
            if url_info["type"] == "track":
                track_data = metadata["track"]
                tracks.append(self._create_track(track_data, 1))
            elif url_info["type"] == "album":
                tracks = self._process_album(metadata)
            elif url_info["type"] == "playlist":
                tracks = self._process_playlist(metadata)
            
            return tracks, url_info["type"], metadata.get("name", "")
            
        except SpotifyInvalidUrlException as e:
            raise Exception(str(e))
        except Exception as e:
            raise Exception(f'Failed to fetch metadata: {str(e)}')

    def _create_track(self, track_data, track_number):
        return Track(
            id=track_data["id"],
            title=track_data["name"],
            artists=track_data["artists"],
            album=track_data.get("album_name", ""),
            track_number=track_number,
            duration_ms=track_data.get("duration_ms", 0),
            isrc=track_data.get("isrc", ""),
            image_url=track_data.get("images", ""),
            release_date=track_data.get("release_date", "")
        )

    def _process_album(self, metadata):
        tracks = []
        album_name = metadata["album_info"]["name"]
        for track in metadata["track_list"]:
            track_data = self._create_track(track, track["track_number"])
            track_data.album = album_name
            tracks.append(track_data)
        return tracks

    def _process_playlist(self, metadata):
        tracks = []
        for track in metadata["track_list"]:
            track_data = self._create_track(track, len(tracks) + 1)
            track_data.album = track.get("album_name", "")
            tracks.append(track_data)
        return tracks

    def download(self, tracks, content_type, content_name):
        total = len(tracks)
        success_count = 0
        failed = []

        for i, track in enumerate(tracks, 1):
            try:
                print(f"Downloading ({i}/{total}): {track.title} - {track.artists}")
                result, msg = self._download_track(track, content_type, content_name)
                if result:
                    success_count += 1
                else:
                    failed.append((track.title, track.artists, msg))
            except Exception as e:
                failed.append((track.title, track.artists, str(e)))

        print(f"\nDownload complete! Success: {success_count}/{total}")
        if failed:
            print("\nFailed downloads:")
            for title, artist, error in failed:
                print(f"- {title} - {artist}: {error}")

    async def _download_track(self, track, buffer):
        try:
            async with aiohttp.ClientSession() as session:
                max_retries = 2
                retry_count = 0
                success = False
                error_message = ""

                while retry_count <= max_retries and not success:
                    token = await self.get_token()
                    api_url = f"https://api.spotidownloader.com/download/{track.id}?token={token}"
                    async with session.get(api_url, headers={
                        'Host': 'api.spotidownloader.com',
                        'Referer': 'https://spotidownloader.com/',
                        'Origin': 'https://spotidownloader.com',
                    }, timeout=aiohttp.ClientTimeout(total=30)) as response:
                        if response.status != 200:
                            error_message = await response.text()
                            if retry_count < max_retries:
                                # 토큰 무효화 및 갱신 유도
                                self.__class__._token = None
                                self.__class__._token_expiry = datetime.now() - timedelta(minutes=1)
                                retry_count += 1
                                continue
                            else:
                                return False, f"API 요청 실패: {error_message}"
                        
                        data = await response.json()
                        if not data.get('success'):
                            error_message = data.get('error', 'Unknown error')
                            if retry_count < max_retries:
                                # 토큰 무효화 및 갱신 유도
                                self.__class__._token = None
                                self.__class__._token_expiry = datetime.now() - timedelta(minutes=1)
                                retry_count += 1
                                continue
                            else:
                                return False, error_message
                        
                        # API 요청 성공 시 루프 탈출
                        success = True

                if not success:
                    return False, error_message

                # 오디오 다운로드 로직
                async with session.get(data['link'], timeout=aiohttp.ClientTimeout(total=300)) as audio_response:
                    if audio_response.status != 200:
                        return False, f"오디오 다운로드 실패: {audio_response.status}"
                    buffer.write(await audio_response.read())
                    buffer.seek(0)
                    await self._embed_metadata(buffer, track)  # 메타데이터 추가
                    buffer.seek(0)
                    return True, "성공"
        except Exception as e:
            return False, str(e)

    def _format_filename(self, track, content_type):
        base = f"{track.artists} - {track.title}" if self.filename_format == "artist_title" \
              else f"{track.title} - {track.artists}"
        base = re.sub(r'[<>:"/\\|?*]', '_', base)
        
        if content_type in ["album", "playlist"] and self.use_track_numbers:
            return f"{track.track_number:02d} - {base}.mp3"
        return f"{base}.mp3"

    def _get_output_path(self, track, content_type, content_name, filename):
        base_path = self.output_path
        
        if content_type == "playlist" and self.use_album_subfolders:
            album_folder = re.sub(r'[<>:"/\\|?*]', '_', track.album)
            base_path = os.path.join(base_path, album_folder)
        elif content_type in ["album", "playlist"]:
            folder_name = re.sub(r'[<>:"/\\|?*]', '_', content_name)
            base_path = os.path.join(base_path, folder_name)
            
        os.makedirs(base_path, exist_ok=True)
        return os.path.join(base_path, filename)

    async def _embed_metadata(self, buffer, track):
        buffer.seek(0)
        if buffer.getvalue() == b"":
            buffer.close()
            raise Exception("버퍼가 비어 있음: 오디오 데이터 없음")

        audio = MP3(buffer, ID3=ID3)
        try:
            audio.add_tags()
        except:
            pass

        audio.tags.add(TIT2(encoding=3, text=track.title))
        audio.tags.add(TPE1(encoding=3, text=track.artists.split(", ")))
        audio.tags.add(TALB(encoding=3, text=track.album))
        audio.tags.add(COMM(encoding=3, lang='eng', desc='Source', text='charlotte'))

        if track.release_date:
            audio.tags.add(TDRC(encoding=3, text=track.release_date))

        audio.tags.add(TRCK(encoding=3, text=str(track.track_number)))
        audio.tags.add(TSRC(encoding=3, text=track.isrc))

        if track.image_url:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(track.image_url) as resp:
                        image_data = await resp.read()
                audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='', data=image_data))
            except Exception as e:
                print(f"커버 아트 추가 오류: {e}")

        buffer.seek(0)
        audio.save(buffer, v2_version=3)
        buffer.seek(0)

async def get_spotify_token():
        """기존 이벤트 루프 활용 방식으로 변경"""
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, get_token_fast)  # 동기 함수를 비동기로 실행
        except RuntimeError:
            return await get_token_fast()


async def main():
    if len(sys.argv) < 3:
        print("Usage: python downloader.py <spotify_url> <output_dir> [--token <token>] [--mode <fast|slow>]")
        return

    url = sys.argv[1]
    output_dir = sys.argv[2]
    token = None
    mode = 'fast'

    # Parse arguments
    for i in range(3, len(sys.argv)):
        if sys.argv[i] == "--token" and i+1 < len(sys.argv):
            token = sys.argv[i+1]
        elif sys.argv[i] == "--mode" and i+1 < len(sys.argv):
            mode = sys.argv[i+1].lower()

    # Get token if not provided
    if not token:
        print("Fetching token...")
        get_token_func = get_token_slow if mode == "slow" else get_token_fast
        token = await get_token_func()

    # Initialize downloader
    downloader = Downloader(
        token=token,
        output_path=output_dir,
        filename_format='title_artist',
        use_track_numbers=True,
        use_album_subfolders=False
    )

    # Start download process
    try:
        print("Fetching track info...")
        tracks, content_type, content_name = await downloader.fetch_tracks(url)
        print(f"Found {len(tracks)} tracks. Starting download...\n")
        downloader.download(tracks, content_type, content_name)
    except Exception as e:
        print(f"Error: {str(e)}")

if __name__ == "__main__":
    asyncio.run(main())