import asyncio

import discord

from AudioScheduler import AudioScheduler


class ServerClient:
    def __init__(self, server_id):
        self.server_id = server_id
        self.voice_client: discord.VoiceClient = None
        self.audio_scheduler = AudioScheduler()
        return

    async def join_voice_channel(self, channel: discord.VoiceChannel):
        """
        해당 음성 채널에 접속하거나, 이미 연결되어 있다면 이동.
        """
        if not self.voice_client or not self.voice_client.is_connected():
            self.voice_client = await channel.connect()
        elif self.voice_client.channel != channel:
            await self.voice_client.move_to(channel)
        print(f"🔊 음성 채널 연결: {channel.name}")
        return self.voice_client

    async def leave_voice_channel(self):
        if self.voice_client and self.voice_client.is_connected():
            await self.voice_client.disconnect()
            self.voice_client = None
            print("🔇 음성 채널 연결 해제")
        return

    def __del__(self):
        if self.voice_client:
            asyncio.run_coroutine_threadsafe(self.voice_client.disconnect(), bot.loop)
        print("🔚 서버 클라이언트 삭제")
        return
