import asyncio
import io
import aiohttp
import discord
import yt_dlp as youtube_dl
from mutagen import File as MutagenFile

from .SpotifyMetadata import parse_uri, get_filtered_data, SpotifyInvalidUrlException
from .SpotifyDownloader import get_spotify_download_link, get_data

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
        print(f"[1] 버퍼 타입: {type(buffer)}")
        self.buffer = buffer
        print(f"[2] buffer 할당 완료: {hasattr(self, 'buffer')}")
        self.metadata = metadata
        self.title = metadata.get('title', 'Unknown')

        try:
            super().__init__(
                buffer,
                pipe=True,
                bitrate=bitrate,
                **FFMPEG_OPTIONS
            )
            print("[3] 부모 클래스 초기화 성공")
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
    async def from_spotify_url(cls, url):
        buffer = io.BytesIO()
        try:
            # get_spotify_download_link 함수 호출
            final_link = await asyncio.to_thread(get_spotify_download_link, url)
            
            # 오디오 데이터 다운로드
            async with aiohttp.ClientSession() as session:
                async with session.get(final_link) as response:
                    response.raise_for_status()
                    async for chunk in response.content.iter_chunked(8192):
                        buffer.write(chunk)
                    buffer.seek(0)

            # 트랙 메타데이터 가져오기
            track_data = await asyncio.to_thread(get_data, url)
            metadata = {
                'title': track_data.get('song_name', 'Spotify Track'),
                'artist': track_data.get('artist', 'Unknown Artist'),
                'duration': track_data.get('duration', 0)
            }
            
            return [cls(buffer, metadata)]
        except Exception as e:
            buffer.close()
            print(f"Spotify Error: {str(e)}")
            return []

class TrackFactory:
    @staticmethod
    async def identify_source(query):
        try:
            # Spotify URL 체크
            url_info = parse_uri(query)
            if url_info['type'] == 'track':
                spotify_track_url = f"https://open.spotify.com/track/{url_info['id']}"
                return await MemoryAudioSource.from_spotify_url(spotify_track_url)
            else:
                print("앨범/플레이리스트는 지원하지 않습니다.")
                return None
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
