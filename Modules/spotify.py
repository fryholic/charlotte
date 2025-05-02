import argparse
import os
import re
import sys
import asyncio
import aiohttp
from datetime import datetime
from dataclasses import dataclass
import io
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, TDRC, TRCK, TSRC, COMM

from Modules.getMetadata import get_filtered_data, parse_uri, SpotifyInvalidUrlException
from Modules.getToken import get_session_token

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

class TokenManager:
    def __init__(self):
        self.token = None
        self.refresh_interval = 45 * 60  # 45분마다 갱신
        self.running = False
        self._refresh_event = asyncio.Event()

    async def start(self):
        self.running = True
        while self.running:
            try:
                self.token = await get_session_token()
                if not self.token:
                    raise Exception("Failed to fetch token")
                print("Successfully refreshed download token")
                await asyncio.wait_for(self._refresh_event.wait(), self.refresh_interval)
                self._refresh_event.clear()
            except asyncio.TimeoutError:
                pass  # 정기 갱신 시간 도달
            except Exception as e:
                print(f"Token refresh error: {e}, retrying in 30 seconds...")
                await asyncio.sleep(30)

    def trigger_refresh(self):
        self._refresh_event.set()

    def stop(self):
        self.running = False
        self._refresh_event.set()

class Downloader:
    def __init__(self, token_manager, output_path=None, filename_format='title_artist', 
                 use_track_numbers=True, use_album_subfolders=False):
        self.token_manager = token_manager
        self.output_path = output_path
        self.filename_format = filename_format
        self.use_track_numbers = use_track_numbers
        self.use_album_subfolders = use_album_subfolders
        self.failed_tracks = []

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

    async def download_all(self, tracks, content_type, content_name):
        total = len(tracks)
        for idx, track in enumerate(tracks, 1):
            print(f"Downloading {idx}/{total}: {track.title} - {track.artists}")
            success, message = await self._download_track(track, content_type, content_name)
            if not success:
                self.failed_tracks.append((track.title, track.artists, message))
                print(f"Failed: {message}")
            else:
                print("Success")

        if self.failed_tracks:
            print(f"\nCompleted with {len(self.failed_tracks)} errors:")
            for title, artist, error in self.failed_tracks:
                print(f"- {title} by {artist}: {error}")
        else:
            print("\nAll tracks downloaded successfully!")

    async def _download_track(self, track):
        buffer = io.BytesIO()
        max_retries = 2
        retry_count = 0
        error_message = ""

        try:
                async with aiohttp.ClientSession() as session:
                    # API 요청
                    api_url = "https://api.spotidownloader.com/download/"
                    headers = {
                        'Authorization': f'Bearer {self.token_manager.token}',
                        'Host': 'api.spotidownloader.com',
                        'Referer': 'https://spotidownloader.com/',
                        'Origin': 'https://spotidownloader.com',
                        'Content-Type': 'application/json'
                    }
                    payload = {"id": track.id}

                    async with session.post(api_url, 
                                            headers=headers, 
                                            json=payload, 
                                            timeout=30
                    ) as response:
                        if response.status != 200:
                            error_message = await response.text()
                            self.token_manager.trigger_refresh()
                            raise Exception(f"API request failed: {error_message}")

                        data = await response.json()
                        print(data)
                        if not data.get('success'):
                            error_message = data.get('error', 'Unknown error')
                            raise Exception(f"API error: {error_message}")

                    host = data['link'].split('//', 1)[1].split('/', 1)[0]
                    download_headers = {
                        'Host': host,
                        'Referer': 'https://spotidownloader.com/',
                        'Origin': 'https://spotidownloader.com'
                    }

                    audio_url = data['link']
                    print(f"[DEBUG] 오디오 다운로드 URL: {audio_url}")

                    # 오디오 다운로드
                    async with session.get(data['link'], headers=download_headers, timeout=300) as audio_response:
                        if audio_response.status != 200:
                            raise Exception(f"Audio download failed: {audio_response.status}")
                        
                        total_size = 0

                        while True:
                            chunk = await audio_response.content.read(8192)
                            if not chunk:
                                break
                            buffer.write(chunk)
                            total_size += len(chunk)
                        
                        print(f"[DEBUG] 총 다운로드 크기: {total_size}바이트")
                        buffer.seek(0)
                                                
                        return True, "다운로드 성공", buffer

        except Exception as e:
                return False, str(e), buffer
        finally:
                pass

        return False, error_message, None

    def _get_output_path(self, track, content_type, content_name):
        filename = self._format_filename(track, content_type)
        
        if content_type == "playlist" and self.use_album_subfolders:
            album_folder = re.sub(r'[<>:"/\\|?*]', '_', track.album)
            base_path = os.path.join(self.output_path, album_folder)
        elif content_type in ["album", "playlist"]:
            folder_name = re.sub(r'[<>:"/\\|?*]', '_', content_name)
            base_path = os.path.join(self.output_path, folder_name)
        else:
            base_path = self.output_path

        return os.path.join(base_path, filename)

    def _format_filename(self, track, content_type):
        base = f"{track.artists} - {track.title}" if self.filename_format == "artist_title" \
              else f"{track.title} - {track.artists}"
        base = re.sub(r'[<>:"/\\|?*]', '_', base)
        
        if content_type in ["album", "playlist"] and self.use_track_numbers:
            return f"{track.track_number:02d} - {base}.mp3"
        return f"{base}.mp3"

    async def _embed_metadata(self, buffer, track):
        buffer.seek(0)
        audio = MP3(buffer, ID3=ID3)
        try:
            audio.add_tags()
        except:
            pass

        audio.tags.add(TIT2(encoding=3, text=track.title))
        audio.tags.add(TPE1(encoding=3, text=track.artists.split(", ")))
        audio.tags.add(TALB(encoding=3, text=track.album))
        audio.tags.add(COMM(encoding=3, lang='eng', desc='Source', text='github.com/afkarxyz/SpotiDownloader'))

        if track.release_date:
            audio.tags.add(TDRC(encoding=3, text=track.release_date))

        audio.tags.add(TRCK(encoding=3, text=str(track.track_number)))
        if track.isrc:
            audio.tags.add(TSRC(encoding=3, text=track.isrc))

        if track.image_url:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(track.image_url) as resp:
                        image_data = await resp.read()
                audio.tags.add(APIC(
                    encoding=3,
                    mime='image/jpeg',
                    type=3,
                    desc='',
                    data=image_data
                ))
            except Exception as e:
                print(f"Cover art error: {e}")

        buffer.seek(0)
        audio.save(buffer, v2_version=3)
        buffer.seek(0)

async def main():
    parser = argparse.ArgumentParser(description="Download Spotify tracks/albums/playlists")
    parser.add_argument("url", help="Spotify URL")
    parser.add_argument("-o", "--output", required=True, help="Output directory")
    parser.add_argument("--format", choices=["title_artist", "artist_title"], default="title_artist",
                        help="Filename format (default: title_artist)")
    parser.add_argument("--track-numbers", action="store_true", 
                       help="Add track numbers for albums/playlists")
    parser.add_argument("--album-folders", action="store_true", 
                       help="Create album subfolders for playlists")
    
    args = parser.parse_args()

    try:
        # Initialize token manager
        token_manager = TokenManager()
        token_task = asyncio.create_task(token_manager.start())

        # Wait for initial token
        while not token_manager.token:
            print("Waiting for initial token...")
            await asyncio.sleep(1)

        # Initialize downloader
        downloader = Downloader(
            token_manager=token_manager,
            output_path=args.output,
            filename_format=args.format,
            use_track_numbers=args.track_numbers,
            use_album_subfolders=args.album_folders
        )

        # Fetch tracks
        tracks, content_type, content_name = await downloader.fetch_tracks(args.url)
        print(f"Found {len(tracks)} tracks. Starting download...\n")

        # Start download
        await downloader.download_all(tracks, content_type, content_name)

    except SpotifyInvalidUrlException as e:
        print(f"Invalid URL: {e}")
    except Exception as e:
        print(f"Fatal error: {e}")
    finally:
        token_manager.stop()
        await token_task

if __name__ == "__main__":
    asyncio.run(main())