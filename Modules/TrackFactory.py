import asyncio
import io
import aiohttp
import discord
import yt_dlp as youtube_dl
import sys
import os
import tempfile
import logging
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
from deezspot.easy_spoty import Spo
from deezspot.models import Preferences
from deezspot.spotloader.__download__ import DW_TRACK, Download_JOB
from deezspot.libutils.utils import link_is_valid, get_ids
from deezspot.spotloader.__spo_api__ import tracking, convert_to_date

# deezspot 패키지 경로 추가
if os.path.exists('/home/pi/deezspot'):
    deezspot_path = Path('/home/pi/deezspot')
elif os.path.exists('/deezspot'):
    deezspot_path = Path('/deezspot')
else:
    deezspot_path = Path(__file__).parent.parent.parent / "deezspot"

sys.path.append(str(deezspot_path))
print(f"Added deezspot path: {deezspot_path}")

# deezspot 모듈 import
SPOTIFY_AVAILABLE = False
try:
    # spotipy 패키지가 있는지 확인
    try:
        import spotipy
    except ImportError:
        print("Warning: spotipy module not installed")
        raise ImportError("spotipy module not installed")
        
    if os.path.exists(deezspot_path):
        # 기본 deezspot 모듈 로딩
        import deezspot
        print("✅ Successfully imported deezspot base module")
        
        # 핵심 모듈 로딩
        from deezspot.spotloader import SpoLogin
        from deezspot.libutils.utils import link_is_valid, get_ids
        from deezspot.spotloader.__spo_api__ import tracking
        from deezspot.spotloader.__download__ import EASY_DW
        from deezspot.models import Preferences
            
        print("✅ Successfully imported all deezspot modules")
        SPOTIFY_AVAILABLE = True
    else:
        print(f"Warning: deezspot directory not found at {deezspot_path}")
except ImportError as e:
    print(f"Warning: deezspot import failed: {e}")
    SPOTIFY_AVAILABLE = False
    
print(f"SPOTIFY_AVAILABLE status: {SPOTIFY_AVAILABLE}")

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
     'options': '-vn -b:a 320k -ac 2 -ar 48000 -af dynaudnorm=f=500:g=31:p=0.95:m=10:s=0'
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
        '-af dynaudnorm=f=500:g=31:p=0.95:m=10:s=0'
    )
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

# -----------------------------------------
# Spotify 다운로드 유틸리티
# -----------------------------------------

async def download_spotify_to_buffer(spotify_url):
    """Spotify URL을 메모리 버퍼로 직접 다운로드"""
    if not SPOTIFY_AVAILABLE:
        raise Exception("Spotify functionality not available")
    
    try:
        # Spotify 인증 초기화
        client_id = os.getenv('SPOTIFY_CLIENT_ID')
        client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')
        
        if not client_id or not client_secret:
            raise Exception("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET environment variables must be set")
        
        print(f"Initializing Spotify API with client_id={client_id[:5]}...")
        
        # Spo 클래스 생성 및 초기화
        spo = Spo(client_id=client_id, client_secret=client_secret)
        
        # librespot 세션 초기화
        credentials_path = None
        possible_paths = [
            os.getenv('SPOTIFY_CREDENTIALS_PATH'),
            '/app/credentials.json',  # Docker path
            '/home/pi/charlotte/credentials.json',  # Local path
            os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'credentials.json')  # Relative path
        ]
        
        for path in possible_paths:
            if path and os.path.exists(path):
                credentials_path = path
                break
                
        if not credentials_path:
            raise Exception("Cannot find Spotify credentials.json file")
            
        print(f"Using Spotify credentials from: {credentials_path}")
        
        # librespot 세션 생성 및 초기화
        session_builder = Session.Builder()
        session_builder.conf.stored_credentials_file = credentials_path
        session = session_builder.stored_file().create()
        Download_JOB(session)  # 여기서 세션을 Download_JOB에 설정
        
        # Spotify URL 검증 및 메타데이터 추출
        link_is_valid(spotify_url)
        track_id = get_ids(spotify_url)
        
        print(f"Fetching metadata for track ID: {track_id}")
        song_metadata = tracking(track_id)
        
        if not song_metadata:
            raise Exception("Failed to get track metadata from Spotify")
            
        print(f"Successfully retrieved metadata for: {song_metadata.get('music', 'Unknown')}")
        
        # 필수 메타데이터 필드 확인
        if not song_metadata or 'music' not in song_metadata or 'artist' not in song_metadata:
            json_track = Spo.get_track(track_id)
            # 메타데이터가 누락된 경우 수동으로 구성
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
        
        # 설정 초기화
        preferences = Preferences()
        preferences.link = spotify_url
        preferences.ids = track_id
        preferences.song_metadata = song_metadata
        preferences.quality_download = "HIGH"
        preferences.output_dir = "./temp"  # 임시 디렉토리
        preferences.recursive_quality = False
        preferences.recursive_download = False
        preferences.not_interface = True  # 진행률 출력 비활성화
        preferences.method_save = 0  # 간단한 경로 형식
        preferences.is_episode = False

        # 트랙 다운로드
        track = DW_TRACK(preferences).dw()
        if not track or not track.success or not track.song_path:
            raise Exception("Failed to download track")

        # 파일을 메모리 버퍼로 읽기
        buffer = io.BytesIO()
        with open(track.song_path, 'rb') as f:
            buffer.write(f.read())
        buffer.seek(0)  # 버퍼 위치를 시작으로 되돌림

        # 임시 파일 정리
        if os.path.exists(track.song_path):
            os.remove(track.song_path)
            try:
                os.rmdir(os.path.dirname(track.song_path))
            except:
                pass  # 디렉토리가 비어있지 않거나 존재하지 않을 수 있음

        # 메타데이터 생성
        metadata = {
            'title': song_metadata.get('music', 'Unknown'),
            'artist': song_metadata.get('artist', 'Unknown'),
            'duration': song_metadata.get('duration', 0)
        }
        
        return buffer, metadata
        
    except Exception as e:
        print(f"Memory download error: {str(e)}")
        raise Exception(f"Failed to download to memory: {str(e)}")

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
        if not SPOTIFY_AVAILABLE:
            raise Exception("Spotify functionality not available")
        
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
            # YouTube URL 확인
            if 'youtube.com/' in query or 'youtu.be/' in query:
                print(f"YouTube URL detected: {query}")
                return await YouTubeSource.from_url(query)
                
            # Spotify URL 확인
            if 'spotify.com/' in query or 'open.spotify.com/' in query:
                print(f"Spotify URL detected: {query}")
                try:
                    url_info = parse_uri(query)
                    if url_info['type'] in ['track', 'album', 'playlist']:
                        if SPOTIFY_AVAILABLE:
                            try:
                                print(f"Attempting Spotify download for: {query}")
                                return await MemoryAudioSource.from_spotify_url(query)
                            except Exception as direct_error:
                                print(f"Spotify download failed: {direct_error}")
                                raise Exception(f"Spotify 트랙을 다운로드할 수 없습니다: {direct_error}")
                        else:
                            raise Exception("Spotify 다운로드 기능을 사용할 수 없습니다")
                except SpotifyInvalidUrlException:
                    raise Exception("잘못된 Spotify URL입니다")
                except Exception as e:
                    print(f"Spotify 처리 오류: {str(e)}")
                    raise Exception(f"Spotify 트랙 처리 중 오류 발생: {str(e)}")
            
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
