import asyncio

import discord

from charlotte_bot import ffmpeg_options, ytdl


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
