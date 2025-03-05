import asyncio

import discord
import yt_dlp as youtube_dl

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
ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn -b:a 320k -ac 2 -ar 48000 -af dynaudnorm=f=500:g=31:p=0.95:m=10:s=0'
}
ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class TrackFactory(discord.FFmpegOpusAudio):
    def __init__(self, source, *, data):
        super().__init__(
            source,
            **ffmpeg_options
        )
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None):
        loop = loop or asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))

            # 플레이리스트 형태일 경우 entries 여러 개
            entries = data.get('entries', [])
            if entries:
                # 여러 곡이 들어있다면, 각각 MusicPlayer 인스턴스 만들기
                ret = []
                for entry in entries:
                    if 'url' not in entry:
                        continue
                    ret.append(cls(entry['url'], data=entry))
                return ret
            else:
                # 단일 곡
                return [cls(data['url'], data=data)]
        except Exception as e:
            print(f"Error: {e}")
            return []
