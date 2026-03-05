import asyncio

import discord

from AudioScheduler import AudioScheduler


class ServerClient:
    def __init__(self, server_id):
        self.server_id = server_id
        self.voice_client: discord.VoiceClient = None
        self.audio_scheduler = AudioScheduler()
        self._connection_lock = asyncio.Lock()
        return

    async def join_voice_channel(self, channel: discord.VoiceChannel):
        """
        해당 음성 채널에 접속하거나, 이미 연결되어 있다면 이동.
        """
        async with self._connection_lock:
            # 길드에 이미 연결된 voice_client가 있는지 최신 상태 확인
            guild_voice_client = channel.guild.voice_client
            if guild_voice_client and guild_voice_client.is_connected():
                self.voice_client = guild_voice_client

            if not self.voice_client or not self.voice_client.is_connected():
                self.voice_client = await channel.connect(timeout=20.0, reconnect=True)
            elif self.voice_client.channel != channel:
                await self.voice_client.move_to(channel)
            print(f"🔊 음성 채널 연결: {channel.name}")
            return self.voice_client

    async def leave_voice_channel(self):
        async with self._connection_lock:
            if self.voice_client and self.voice_client.is_connected():
                await self.voice_client.disconnect(force=True)
                self.voice_client = None
                print("🔇 음성 채널 연결 해제")
        return

    def __del__(self):
        if self.voice_client:
            try:
                loop = asyncio.get_running_loop()
                asyncio.run_coroutine_threadsafe(self.voice_client.disconnect(force=True), loop)
            except RuntimeError:
                pass # Event loop is closed
        print("🔚 서버 클라이언트 삭제")
        return
