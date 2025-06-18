import asyncio
import io
import aiohttp
import discord
import yt_dlp as youtube_dl
from mutagen import File as MutagenFile
import sys
import os
import tempfile
from pathlib import Path

# deezspot 패키지 경로 추가
sys.path.append(str(Path(__file__).parent.parent.parent / "deezspot"))

try:
    from hashlib import md5 as __md5
    from binascii import a2b_hex as __a2b_hex, b2a_hex as __b2a_hex
    from Crypto.Cipher.Blowfish import new as __newBlowfish, MODE_CBC as __MODE_CBC
    from Crypto.Cipher.AES import new as __newAES, MODE_ECB as __MODE_ECB
except ImportError:
    print("Warning: Crypto library not available, Deezer download will not work")
    __md5 = None
    __a2b_hex = None
    __b2a_hex = None
    __newBlowfish = None
    __MODE_CBC = None
    __newAES = None
    __MODE_ECB = None



#from .SpotifyMetadata import parse_uri, get_filtered_data, SpotifyInvalidUrlException
#from .SpotifyDownloader import get_spotify_download_link, get_data

#from Modules.getMetadata_v2 import get_filtered_data, parse_uri, SpotifyInvalidUrlException
from Modules.getMetadata import get_filtered_data, parse_uri, SpotifyInvalidUrlException
from Modules.getToken_v1 import main as get_token_fast
# from Modules.getToken import main as get_token_slow
from Modules.spotify import Downloader, TokenManager

# deezspot 모듈 import
try:
    import deezspot
    from deezspot.spotloader import SpoLogin
    from deezspot.deezloader.dee_api import API
    from deezspot.deezloader.deegw_api import API_GW
    from deezspot.deezloader.deezer_settings import qualities
    from deezspot.deezloader.__download_utils__ import gen_song_hash
    from deezspot.libutils.utils import set_path
    from deezspot.models import Track as DeezerTrack
    from deezspot.spotloader.__download__ import EASY_DW
    from deezspot.models import Preferences
    DEEZER_AVAILABLE = True
except ImportError:
    print("Warning: deezspot not available")
    DEEZER_AVAILABLE = False

# 환경변수 로딩
from dotenv import load_dotenv
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
    async def from_spotify_url(cls, track, token_manager):
        """Spotify URL에서 deezspot을 통해 버퍼로 오디오 다운로드"""
        if not DEEZER_AVAILABLE:
            raise Exception("deezspot functionality not available")
        
        try:
            # Spotify 트랙 URL 생성 (track 객체에서 URL 추출)
            spotify_url = getattr(track, 'external_urls', {}).get('spotify')
            if not spotify_url:
                # track 객체에서 ID를 사용하여 URL 생성
                track_id = getattr(track, 'id', None)
                if track_id:
                    spotify_url = f"https://open.spotify.com/track/{track_id}"
                else:
                    raise Exception("Cannot extract Spotify URL from track")
            
            # 버퍼로 다운로드
            loop = asyncio.get_event_loop()
            buffer, metadata = await loop.run_in_executor(None, download_spotify_to_buffer, spotify_url)
            
            return cls(buffer, metadata)
            
        except Exception as e:
            print(f"Spotify download error: {str(e)}")
            raise Exception(f"Failed to download from Spotify: {str(e)}")

class TrackFactory:
    # @staticmethod
    # async def initialize():
    #     global token_manager
    #     if token_manager is None:
    #         token_manager = TokenManager()
    #         asyncio.create_task(token_manager.start())
    #         print("✅ TokenManager 초기화 및 시작됨")    @staticmethod
    async def identify_source(query):
        """쿼리 타입을 식별하고 적절한 소스 생성"""
        try:
            # Spotify URL 처리
            url_info = parse_uri(query)
            if url_info['type'] in ['track', 'album', 'playlist']:
                # 토큰매니저 초기화
                token_manager = TokenManager()
                
                # 새로운 Downloader 인스턴스 생성
                downloader = Downloader(
                    token_manager=token_manager,
                    output_path=None,
                    filename_format='title_artist',
                    use_track_numbers=False,
                    use_album_subfolders=False
                )
                
                # 트랙 목록 가져오기
                tracks, _, _ = await downloader.fetch_tracks(query)
                if tracks:
                    first_track = tracks[0]
                    return [await MemoryAudioSource.from_spotify_url(first_track, token_manager)]
                    
        except SpotifyInvalidUrlException:
            # Spotify URL이 아닌 경우, YouTube 처리로 넘어감
            pass
        except Exception as e:
            print(f"Spotify 처리 오류: {str(e)}")
            # 오류 발생 시 YouTube 처리로 넘어감
            pass

        # YouTube URL 또는 검색어 처리
        if 'youtube.com/' in query or 'youtu.be/' in query:
            return await YouTubeSource.from_url(query)
        elif query:  # 검색어로 처리
            return await YouTubeSource.from_url(f"ytsearch:{query}")
        
        return None

    @classmethod
    async def from_url(cls, url, *, loop=None):
        """URL로부터 트랙 생성"""
        return await cls.identify_source(url)

    @classmethod
    async def from_upload(cls, file):
        """업로드된 파일로부터 트랙 생성"""
        return await MemoryAudioSource.from_upload(file)

# -----------------------------------------
# Deezer 복호화 유틸리티 함수들
# -----------------------------------------

def __calcbfkey(songid):
    """Deezer 블로우피시 키 계산"""
    if not __md5:
        raise Exception("Crypto library not available")
    
    __secret_key = "g4el58wc0zvf9na1"
    h = __md5(songid.encode()).hexdigest()
    
    bfkey = "".join(
        chr(
            ord(h[i]) ^ ord(h[i + 16]) ^ ord(__secret_key[i])
        )
        for i in range(16)
    )
    return bfkey

def __blowfishDecrypt(data, key):
    """블로우피시 복호화"""
    if not __newBlowfish:
        raise Exception("Crypto library not available")
    
    __idk = __a2b_hex("0001020304050607")
    c = __newBlowfish(key.encode(), __MODE_CBC, __idk)
    return c.decrypt(data)

def decrypt_to_buffer(content, key):
    """
    Deezer 암호화된 콘텐츠를 버퍼로 복호화
    파일에 저장하지 않고 메모리 버퍼로 반환
    """
    if not DEEZER_AVAILABLE or not __md5:
        raise Exception("Deezer decryption not available")
    
    key = __calcbfkey(key)
    buffer = io.BytesIO()
    seg = 0
    
    for data in content:
        if ((seg % 3) == 0) and (len(data) == 2048):
            data = __blowfishDecrypt(data, key)
        
        buffer.write(data)
        seg += 1
    
    buffer.seek(0)
    return buffer

# -----------------------------------------
# Spotify 클라이언트 설정
# -----------------------------------------

def get_spotify_client():
    """환경변수에서 Spotify 클라이언트 설정을 가져와서 SpoLogin 인스턴스 생성"""
    if not DEEZER_AVAILABLE:
        raise Exception("deezspot not available")
    
    credentials_path = os.getenv('SPOTIFY_CREDENTIALS_PATH', './credentials.json')
    client_id = os.getenv('SPOTIFY_CLIENT_ID')
    client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')
    
    if not client_id or not client_secret:
        raise Exception("Spotify credentials not found in environment variables")
    
    if not os.path.exists(credentials_path):
        raise Exception(f"Credentials file not found: {credentials_path}")
    
    return SpoLogin(
        credentials_path=credentials_path,
        client_id=client_id,
        client_secret=client_secret
    )

def download_spotify_to_buffer(spotify_url):
    """Spotify URL을 메모리 버퍼로 직접 다운로드 (임시파일 없이)"""
    if not DEEZER_AVAILABLE:
        raise Exception("deezspot not available")
    
    try:
        import requests
        
        # deezspot 모듈들 import (try-except로 처리)
        try:
            from deezspot.spotloader.__spo_api__ import link_is_valid, get_ids, tracking
            from deezspot.deezloader.__download__ import Download_JOB
            from deezspot.deezloader.__utils__ import check_track_ids
        except ImportError as ie:
            print(f"Warning: Could not import deezspot modules: {ie}")
            raise Exception("deezspot modules not available")
        
        # Spotify URL 검증 및 메타데이터 추출
        link_is_valid(spotify_url)
        ids = get_ids(spotify_url)
        song_metadata = tracking(ids)
        
        # 커스텀 다운로드: 메모리에서 처리
        buffer = download_track_to_memory(song_metadata)
        
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

def download_track_to_memory(song_metadata):
    """트랙을 메모리 버퍼로 직접 다운로드 (파일 저장 없이)"""
    try:
        import requests
        from deezspot.deezloader.__download__ import Download_JOB
        from deezspot.deezloader.__utils__ import check_track_ids
        
        # Download_JOB의 내부 로직을 직접 사용
        download_job = Download_JOB()
        
        # 다운로드 URL 정보 가져오기
        url_info = download_job._Download_JOB__get_url(song_metadata, "NORMAL")
        
        if not url_info or 'media' not in url_info:
            raise Exception("No download URL available")
        
        # 실제 다운로드 URL 추출
        media_info = url_info['media'][0] if url_info['media'] else None
        if not media_info or 'sources' not in media_info:
            raise Exception("No media sources available")
        
        download_url = media_info['sources'][0]['url']
        
        # 스트리밍으로 암호화된 오디오 다운로드
        response = requests.get(download_url, stream=True, timeout=30)
        response.raise_for_status()
        
        # 암호화된 청크들을 메모리에 수집
        encrypted_chunks = []
        for chunk in response.iter_content(chunk_size=2048):
            if chunk:
                encrypted_chunks.append(chunk)
        
        print(f"Downloaded {len(encrypted_chunks)} encrypted chunks")
        
        # 복호화 키 생성
        fallback_ids = check_track_ids(song_metadata)
        
        # 메모리에서 복호화하여 버퍼로 반환
        return decrypt_audio_to_buffer(encrypted_chunks, fallback_ids)
        
    except Exception as e:
        print(f"Track memory download error: {str(e)}")
        raise

def decrypt_audio_to_buffer(encrypted_content, fallback_ids):
    """암호화된 오디오 콘텐츠를 메모리에서 직접 복호화하여 버퍼로 반환"""
    if not DEEZER_AVAILABLE or not __md5:
        raise Exception("Decryption not available")
    
    key = __calcbfkey(fallback_ids)
    buffer = io.BytesIO()
    seg = 0
    
    for data in encrypted_content:
        if ((seg % 3) == 0) and (len(data) == 2048):
            data = __blowfishDecrypt(data, key)
        
        buffer.write(data)
        seg += 1
    
    buffer.seek(0)
    return buffer

# -----------------------------------------
# 토큰 관리
# -----------------------------------------
