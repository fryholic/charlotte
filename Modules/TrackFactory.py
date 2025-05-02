import asyncio
import io
import aiohttp
import discord
import yt_dlp as youtube_dl
from mutagen import File as MutagenFile



#from .SpotifyMetadata import parse_uri, get_filtered_data, SpotifyInvalidUrlException
#from .SpotifyDownloader import get_spotify_download_link, get_data

#from Modules.getMetadata_v2 import get_filtered_data, parse_uri, SpotifyInvalidUrlException
from Modules.getMetadata import get_filtered_data, parse_uri, SpotifyInvalidUrlException
from Modules.getToken_v1 import main as get_token_fast
# from Modules.getToken import main as get_token_slow
from Modules.spotify import Downloader, TokenManager




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

        try:
            super().__init__(
                buffer,
                pipe=True,
                bitrate=bitrate,
                **FFMPEG_OPTIONS_MEMORYAUDIOSOURCE
            )
        except Exception as e:
            print(f"❗ 부모 클래스 초기화 실패: {str(e)}")
            self.buffer.close()
            raise

    def cleanup(self):
        """리소스 정리"""
        try:
            super().cleanup()
        finally:
            if hasattr(self, 'buffer'):
                try:
                    self.buffer.close()
                    print("[✓] 버퍼 정리 완료")
                except:
                    print("[✗] 버퍼 정리 실패")

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
        # buffer = io.BytesIO()
        # try:
        #     result, msg = await Downloader()._download_track(track, buffer)
        #     if not result:
        #         buffer.close()
        #         raise Exception(msg)
            
        #     # 메타데이터 구성
        #     metadata = {
        #         'title': track.title,
        #         'artist': track.artists,
        #         'duration': track.duration_ms / 1000
        #     }
        
        #     return cls(buffer, metadata)
        # except Exception as e:
        #     buffer.close()
        #     print(f"Spotify Error: {str(e)}")
        #     return
        buffer = io.BytesIO()
        try:
            # 새로운 Downloader 인스턴스 생성
            downloader = Downloader(
                token_manager,
                output_path=None,  # 파일 저장 비활성화
                filename_format='title_artist',
                use_track_numbers=False,
                use_album_subfolders=False
            )
            
            # 트랙 다운로드 및 버퍼에 저장
            success, msg = await downloader._download_track(
                track=track,
                content_type='track',
                content_name='discord_stream'
            )
            
            if not success:
                raise Exception(msg)
            
            # 다운로드된 버퍼에서 메타데이터 추출
            buffer.seek(0)
            metadata = {
                'title': track.title,
                'artist': track.artists,
                'duration': track.duration_ms / 1000
            }
            
            return cls(buffer, metadata)
            
        except Exception as e:
            buffer.close()
            print(f"Spotify Error: {str(e)}")
            return None

class TrackFactory:
    _token_manager = None  # 클래스 변수로 TokenManager 관리

    @classmethod
    async def initialize(cls):
        """초기화 및 토큰 매니저 시작"""
        if not cls._token_manager:
            cls._token_manager = TokenManager()
            cls._token_task = asyncio.create_task(cls._token_manager.start())
            
            # 초기 토큰 획득 대기
            while not cls._token_manager.token:
                print("Waiting for initial token...")
                await asyncio.sleep(1)

    @staticmethod
    async def identify_source(query):
        try:
        #     url_info = parse_uri(query)
        #     if url_info['type'] in ['track', 'album', 'playlist']:
        #         token = await Downloader.get_token()
        #         downloader = Downloader()
        #         tracks, _, _ = await downloader.fetch_tracks(query)
        #         if tracks:
        #             first_track = tracks[0]
        #             return [await MemoryAudioSource.from_spotify_url(first_track)]
        # except SpotifyInvalidUrlException:
        #     pass
        # except Exception as e:
        #     print(f"Spotify 처리 오류: {str(e)}")
        #     return None
            await TrackFactory.initialize()  # 초기화 확인
            
            url_info = parse_uri(query)
            if url_info['type'] in ['track', 'album', 'playlist']:
                # 새로운 Downloader 인스턴스 생성
                downloader = Downloader(
                    token_manager=TrackFactory._token_manager,
                    output_path=None,
                    filename_format='title_artist',
                    use_track_numbers=False,
                    use_album_subfolders=False
                )
                
                # 트랙 목록 가져오기
                tracks, _, _ = await downloader.fetch_tracks(query)
                if tracks:
                    first_track = tracks[0]
                    return [await MemoryAudioSource.from_spotify_url(first_track, TrackFactory._token_manager)]
                    
        except SpotifyInvalidUrlException:
            pass
        except Exception as e:
            print(f"Spotify 처리 오류: {str(e)}")
            return None

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
