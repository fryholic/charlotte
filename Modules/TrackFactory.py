import asyncio
import io
import aiohttp
import discord
import yt_dlp as youtube_dl
import sys
import os
import tempfile
import logging
import traceback

from pathlib import Path
from mutagen import File as MutagenFile
from dotenv import load_dotenv
from Modules.getMetadata import get_filtered_data, parse_uri, SpotifyInvalidUrlException

# Spotify/librespot imports
from librespot.core import Session
from librespot.metadata import TrackId
from librespot.audio.decoders import AudioQuality, VorbisOnlyAudioQuality
from deezspot.spotloader.spotify_settings import qualities

# deezspot imports
from deezspot.models.download.preferences import Preferences
from deezspot.spotloader.__download__ import DW_TRACK
from deezspot.spotloader.__init__ import SpoLogin
from deezspot.libutils.utils import link_is_valid, get_ids
from deezspot.spotloader.__spo_api__ import tracking, tracking_album, tracking_episode

# 환경변수 로딩
load_dotenv()

# -----------------------------------------
# 유튜브 다운로드 설정
# -----------------------------------------
ytdl_format_options = {
    'format': 'bestaudio/best',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'quiet': False,
    'no_warnings': False,
    'extract_flat': 'in_playlist',
    'http_headers': {
        'User-Agent': 'Mozilla/5.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    },
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'opus',
        'preferredquality': '320',
    }],
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
     'options': '-vn -b:a 320k -ac 2 -ar 48000'
}

FFMPEG_OPTIONS_MEMORYAUDIOSOURCE = {
    'before_options': (
        '-vn '           # 비디오 스트림 무시
        '-loglevel warning '
    ),
    'options': (
        '-c:a libopus '  # 오디오 코덱 지정
        '-b:a 320k '     # 비트레이트 설정
        '-ar 48000 '     # 샘플 레이트 강제 지정
    )
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

soundcloud_format_options = dict(ytdl_format_options)
soundcloud_format_options.update({
    'noplaylist': False,  # Allow playlist extraction for SoundCloud sets
    'extract_flat': False,  # Need full entries to access the streamable URL
    'default_search': 'scsearch1'
})

soundcloud_ytdl = youtube_dl.YoutubeDL(soundcloud_format_options)

# -----------------------------------------
# Spotify 다운로드 유틸리티
# -----------------------------------------

def _blocking_download_spotify(spotify_url):
    """(Helper) Synchronously downloads a Spotify track and returns a buffer and metadata."""
    try:
        print(f"[DEBUG] Starting blocking download for spotify_url={spotify_url}")

        # Spotify URL 검증 및 메타데이터 추출
        link_is_valid(spotify_url)
        track_id = get_ids(spotify_url)
        print(f"[DEBUG] get_ids returned track_id={track_id}")

        # deezspot SpoLogin 초기화
        client_id = os.getenv('SPOTIFY_CLIENT_ID')
        client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')

        if not client_id or not client_secret:
            raise Exception("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET environment variables must be set")

        # credentials.json 파일 경로 찾기
        credentials_path = None
        possible_paths = [
            os.getenv('SPOTIFY_CREDENTIALS_PATH'),
            '/app/credentials.json',
            '/home/pi/charlotte/credentials.json',
            os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'credentials.json')
        ]
        for path in possible_paths:
            if path and os.path.exists(path):
                credentials_path = path
                break
        
        if not credentials_path:
            raise Exception("Cannot find Spotify credentials.json file")

        print(f"[DEBUG] Initializing SpoLogin with credentials: {credentials_path}")
        spo_login = SpoLogin(
            credentials_path=credentials_path,
            spotify_client_id=client_id,
            spotify_client_secret=client_secret
        )

        song_metadata = tracking(track_id)
        
        if not song_metadata:
            print("[DEBUG] No song_metadata returned from tracking()")
            raise Exception("Failed to get track metadata from Spotify")

        print(f"[DEBUG] Successfully retrieved metadata for: {getattr(song_metadata, 'title', 'Unknown')}")

        # 다운로드 설정
        preferences = Preferences()
        preferences.link = spotify_url
        preferences.ids = track_id
        preferences.song_metadata = song_metadata
        preferences.quality_download = "NORMAL"
        preferences.output_dir = tempfile.mkdtemp()
        preferences.recursive_quality = False
        preferences.recursive_download = False
        preferences.not_interface = True
        preferences.method_save = 0
        preferences.is_episode = False
        preferences.convert_to = None
        preferences.initial_retry_delay = 10
        preferences.retry_delay_increase = 5
        preferences.max_retries = 3

        # 다운로드 실행 (이 부분이 블로킹을 유발합니다)
        print(f"[DEBUG] Calling DW_TRACK with preferences")
        track = DW_TRACK(preferences).dw()

        if not track or not track.success or not track.song_path:
            print(f"[DEBUG] Downloaded track is invalid: {track}")
            raise Exception("Failed to download track")

        # 파일을 메모리 버퍼로 읽기
        print(f"[DEBUG] Reading downloaded file into buffer: {track.song_path}")
        buffer = io.BytesIO()
        with open(track.song_path, 'rb') as f:
            buffer.write(f.read())
        buffer.seek(0)

        # 임시 파일 및 디렉토리 정리
        print(f"[DEBUG] Cleaning up temp file and directory: {track.song_path}")
        try:
            os.remove(track.song_path)
            os.rmdir(os.path.dirname(track.song_path))
            print(f"[DEBUG] Removed temp file and directory")
        except Exception as e:
            print(f"[DEBUG] Failed to remove temp file/directory: {e}")

        # 메타데이터 생성
        metadata = {
            'title': getattr(song_metadata, 'title', 'Unknown'),
            'artist': ' & '.join([getattr(artist, 'name', 'Unknown') for artist in getattr(song_metadata, 'artists', [])]),
            'duration': getattr(song_metadata, 'duration_ms', 0)
        }
        print(f"[DEBUG] Returning buffer and metadata from blocking function: {metadata}")
        return buffer, metadata

    except Exception as e:
        print(f"[DEBUG] Exception in _blocking_download_spotify: {str(e)}")
        traceback.print_exc()
        # 여기서 예외를 다시 발생시켜 run_in_executor가 호출한 곳으로 전달되게 합니다.
        raise Exception(f"Failed to download to memory: {str(e)}")

async def download_spotify_to_buffer(spotify_url):
    """Spotify URL을 메모리 버퍼로 직접 다운로드 (비동기 래퍼)"""
    print(f"[DEBUG] Called async download_spotify_to_buffer with spotify_url={spotify_url}")
    loop = asyncio.get_event_loop()
    
    buffer, metadata = await loop.run_in_executor(
        None,
        _blocking_download_spotify,
        spotify_url
    )
    
    return buffer, metadata

class YouTubeSource(discord.FFmpegOpusAudio):
    def __init__(self, source, *, data):
        super().__init__(source, **FFMPEG_OPTIONS)
        self.data = data
        self.title = data.get('title', 'Unknown Title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None):
        """YouTube URL로부터 소스 생성"""
        loop = loop or asyncio.get_event_loop()
        
        try:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))
            
            if 'entries' in data:
                # 플레이리스트
                ret = []
                for entry in data['entries']:
                    if 'url' not in entry:
                        continue
                    ret.append(cls(entry['url'], data=entry))
                return ret
            elif data.get('url'):
                # 단일 영상
                return [cls(data['url'], data=data)]
            return []
        except Exception as e:
            print(f"YouTube Error: {e}")
            return []

class SoundCloudSource(discord.FFmpegOpusAudio):
    def __init__(self, source, *, data):
        super().__init__(source, **FFMPEG_OPTIONS)
        self.data = data
        self.title = data.get('title', 'Unknown Title')
        self.url = data.get('url')
        self.artist = data.get('uploader') or data.get('creator')

    @staticmethod
    def _hydrate_entries(data):
        entries = []
        if not data:
            return entries

        raw_entries = data['entries'] if 'entries' in data else [data]
        for entry in raw_entries:
            if not entry:
                continue
            if 'url' not in entry and entry.get('webpage_url'):
                try:
                    entry = soundcloud_ytdl.extract_info(entry['webpage_url'], download=False)
                except Exception as nested_err:
                    logging.error(f"SoundCloud entry hydration failed: {nested_err}")
                    continue
            if entry.get('url'):
                entries.append(entry)
        return entries

    @classmethod
    async def from_url(cls, query, *, loop=None):
        """SoundCloud URL 또는 검색어로부터 소스 생성"""
        loop = loop or asyncio.get_event_loop()

        def extract():
            info_query = query.strip()
            lower_query = info_query.lower()
            if not info_query.startswith('http'):
                if lower_query.startswith('scsearch:'):
                    info_query = info_query
                else:
                    info_query = f"scsearch1:{info_query}"
            data = soundcloud_ytdl.extract_info(info_query, download=False)
            return cls._hydrate_entries(data)

        try:
            entries = await loop.run_in_executor(None, extract)
            return [cls(entry['url'], data=entry) for entry in entries]
        except Exception as e:
            logging.error(f"SoundCloud Error: {e}")
            return []

class MemoryAudioSource(discord.FFmpegOpusAudio):
    def __init__(self, buffer, metadata, *, bitrate=320):
        self.buffer = buffer
        self.metadata = metadata
        self.title = metadata.get('title', 'Unknown')
        self._is_closed = False

        try:
            super().__init__(
                self.buffer,
                pipe=True,
                bitrate=bitrate,
                **FFMPEG_OPTIONS_MEMORYAUDIOSOURCE
            )
        except Exception as e:
            print(f"❗ 부모 클래스 초기화 실패: {str(e)}")
            self.buffer.close()
            raise

    def _close_buffer(self):
        if not self._is_closed and hasattr(self, 'buffer'):
            try:
                self.buffer.close()
                self._is_closed = True
                print("[✓] 버퍼 정리 완료")
            except Exception as e:
                print(f"[✗] 버퍼 정리 실패: {e}")

    def cleanup(self):
        try:
            super().cleanup()
        finally:
            self._close_buffer()

    @classmethod
    async def from_upload(cls, file):
        buffer = io.BytesIO(await file.read())
        buffer.seek(0)
        
        try:
            loop = asyncio.get_event_loop()
            audio = await loop.run_in_executor(None, MutagenFile, buffer)
            buffer.seek(0)
            
            metadata = {
                'title': audio.tags.get('title', [file.filename])[0] if hasattr(audio, 'tags') and audio.tags else file.filename,
                'artist': audio.tags.get('artist', ['Unknown'])[0] if hasattr(audio, 'tags') and audio.tags else 'Unknown',
                'duration': audio.info.length if hasattr(audio, 'info') else 0
            }
        except Exception as e:
            print(f"메타데이터 추출 실패: {e}")
            buffer.seek(0)
            metadata = {
                'title': file.filename,
                'artist': 'Unknown',
                'duration': 0
            }
        
        return [cls(buffer, metadata)]
        
    @classmethod
    async def from_spotify_url(cls, track):
        """Spotify URL에서 deezspot을 통해 버퍼로 오디오 다운로드"""
        try:
            # Spotify 트랙 정보에서 URL 또는 ID 추출
            spotify_url = None
            track_id = None
            
            # track이 문자열인 경우 (URL인 경우)
            if isinstance(track, str):
                if 'spotify.com' in track:
                    spotify_url = track
                else:
                    track_id = track
            else:
                # track이 객체인 경우
                spotify_url = getattr(track, 'external_urls', {}).get('spotify')
                if not spotify_url:
                    track_id = getattr(track, 'id', None)
            
            # URL이 없고 ID가 있으면 URL 생성
            if not spotify_url and track_id:
                spotify_url = f"https://open.spotify.com/track/{track_id}"
                
            if not spotify_url:
                raise Exception("Cannot extract Spotify URL or ID from track")
                
            print(f"Processing Spotify URL: {spotify_url}")
            
            # 데이터 다운로드
            buffer, metadata = await download_spotify_to_buffer(spotify_url)
            return [cls(buffer, metadata)]
            
        except Exception as e:
            print(f"Spotify download error: {str(e)}")
            raise Exception(f"Failed to download from Spotify: {str(e)}")

class TrackFactory:
    @staticmethod
    async def identify_source(query):
        """쿼리 타입을 식별하고 적절한 소스 생성"""
        try:
            lower_query = query.lower()
            # YouTube URL 확인
            if 'youtube.com/' in lower_query or 'youtu.be/' in lower_query:
                print(f"YouTube URL detected: {query}")
                return await YouTubeSource.from_url(query)

            # SoundCloud URL 확인
            if (
                'soundcloud.com/' in lower_query
                or 'snd.sc/' in lower_query
                or lower_query.startswith('soundcloud:')
                or lower_query.startswith('soundcloud ')
                or lower_query.startswith('scsearch:')
            ):
                print(f"SoundCloud query detected: {query}")
                normalized_query = query
                if lower_query.startswith('soundcloud:'):
                    normalized_query = query.split(':', 1)[1].strip() or query
                elif lower_query.startswith('soundcloud '):
                    normalized_query = query.split(' ', 1)[1].strip() or query
                sources = await SoundCloudSource.from_url(normalized_query)
                if sources:
                    return sources
                raise Exception("SoundCloud 트랙을 찾을 수 없습니다")
                
            # Spotify URL 확인
            if 'spotify.com/' in lower_query or 'open.spotify.com/' in lower_query:
                print(f"Spotify URL detected: {query}")
                try:
                    url_info = parse_uri(query)
                    if url_info['type'] in ['track', 'album', 'playlist']:
                        try:
                            print(f"Attempting Spotify download for: {query}")
                            return await MemoryAudioSource.from_spotify_url(query)
                        except Exception as direct_error:
                            print(f"Spotify download failed: {direct_error}")
                            raise Exception(f"Spotify 트랙을 다운로드할 수 없습니다: {direct_error}")
                except SpotifyInvalidUrlException:
                    raise Exception("잘못된 Spotify URL입니다")
                except Exception as e:
                    print(f"Spotify 처리 오류: {str(e)}")
                    raise Exception(f"Spotify 트랙 처리 중 오류 발생")
            
            # 일반 검색어인 경우 YouTube 검색
            print(f"Searching YouTube for query: {query}")
            return await YouTubeSource.from_url(f"ytsearch:{query}")
        
        except Exception as e:
            print(f"Source identification error: {str(e)}")
            raise e

    @classmethod
    async def from_url(cls, url, *, loop=None):
        """URL로부터 트랙 생성"""
        return await cls.identify_source(url)

    @classmethod
    async def from_upload(cls, file):
        """업로드된 파일로부터 트랙 생성"""
        return await MemoryAudioSource.from_upload(file)


    @staticmethod
    async def download_track_to_memory(source, url_or_id: str, quality="NORMAL"):
        try:
            if source == "spotify":
                # Get the Spotify track ID if a full URL was provided
                if "spotify.com" in url_or_id:
                    track_id = url_or_id.split("/")[-1].split("?")[0]
                else:
                    track_id = url_or_id

                # Initialize Spotify API with client credentials
                Spo(client_id=os.getenv('SPOTIFY_CLIENT_ID'), client_secret=os.getenv('SPOTIFY_CLIENT_SECRET'))

                # Get track metadata
                song_metadata = tracking(track_id)
                
                # Ensure required metadata fields are present
                if not song_metadata or 'music' not in song_metadata or 'artist' not in song_metadata:
                    json_track = Spo.get_track(track_id)
                    # Manually construct metadata if original tracking fails
                    song_metadata = {
                        'music': json_track.get('name', 'Unknown Title'),
                        'artist': ' & '.join(artist['name'] for artist in json_track.get('artists', [])) or 'Unknown Artist',
                        'album': json_track.get('album', {}).get('name', 'Unknown Album'),
                        'tracknum': json_track.get('track_number', 1),
                        'discnum': json_track.get('disc_number', 1),
                        'year': convert_to_date(json_track.get('album', {}).get('release_date', '')),
                        'duration': json_track.get('duration_ms', 0) // 1000,
                        'ids': track_id
                    }

                    if 'images' in json_track.get('album', {}):
                        song_metadata['image'] = json_track['album']['images'][0]['url']

                # Set up preferences for download
                preferences = Preferences()
                preferences.link = f"https://open.spotify.com/track/{track_id}"
                preferences.ids = track_id 
                preferences.song_metadata = song_metadata
                preferences.quality_download = quality
                preferences.output_dir = "./temp"  # Temporary directory
                preferences.recursive_quality = False
                preferences.recursive_download = False
                preferences.not_interface = True  # Disable progress output
                preferences.method_save = 0  # Simple path format
                preferences.is_episode = False

                # Download track
                track = DW_TRACK(preferences).dw()
                if not track or not track.success or not track.song_path:
                    raise Exception("Failed to download track")

                # Read the file into memory
                with open(track.song_path, 'rb') as f:
                    audio_data = f.read()

                # Clean up the temp file
                if os.path.exists(track.song_path):
                    os.remove(track.song_path)
                    try:
                        os.rmdir(os.path.dirname(track.song_path))
                    except:
                        pass  # Directory might not be empty or might not exist

                return audio_data
            
            elif source == "youtube":
                if "youtu" not in url_or_id:  # If we got video ID instead of URL
                    url = f"https://youtube.com/watch?v={url_or_id}"
                else:
                    url = url_or_id

                # Extract audio to memory using yt-dlp
                ydl_opts = {
                    'format': 'bestaudio/best',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'opus',
                    }],
                }
                
                with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                    try:
                        # Download to memory buffer instead of file
                        buffer = io.BytesIO()
                        
                        # Get video info first
                        info = ydl.extract_info(url, download=False)
                        
                        # Create custom progress hook to write to memory
                        def progress_hook(d):
                            if d['status'] == 'finished':
                                # Get the converted file path
                                file_path = d['filename']
                                # Read the file into our buffer
                                with open(file_path, 'rb') as f:
                                    buffer.write(f.read())
                                # Delete the temporary file
                                os.remove(file_path)
                                try:
                                    # Try to remove the directory if empty
                                    os.rmdir(os.path.dirname(file_path))
                                except:
                                    pass

                        # Add our custom progress hook
                        ydl_opts['progress_hooks'] = [progress_hook]
                        
                        # Perform the download
                        ydl.download([url])
                        
                        # Return the buffer contents
                        return buffer.getvalue()
                        
                    except Exception as e:
                        logging.error(f"YouTube download failed: {str(e)}")
                        raise e

        except Exception as e:
            logging.error(f"Download error: {str(e)}")
            raise Exception(f"Failed to download track: {str(e)}")
