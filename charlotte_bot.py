import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
import os
from dotenv import load_dotenv

# 각 서버별 큐와 채널 정보 저장
queues = {}
text_channels = {}

# YouTube 다운로드 설정 (오디오 추출 최적화)
# ytdl_format_options = {
#     'format': 'bestaudio/best',
#     'restrictfilenames': True,
#     'noplaylist': False,  # 플레이리스트 지원 활성화
#     'nocheckcertificate': True,
#     'ignoreerrors': True,  # 오류 무시로 안정성 향상
#     'quiet': True,
#     'no_warnings': True,
#     'postprocessors': [{  # 오디오 추출 포스트프로세서 추가
#         'key': 'FFmpegExtractAudio',
#         'preferredcodec': 'mp3',
#         'preferredquality': '320',
#     }],
# }

ytdl_format_options = {
    'format': 'bestaudio/best',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'quiet': True,
    'no_warnings': True,
    'extract_flat': 'in_playlist',
    'postprocessors': [{ 
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'opus',  # OPUS 코덱 사용으로 변경
        'preferredquality': '320',
    }],
}

# ffmpeg_options = {
#     'options': '-vn -loglevel quiet -ab 320'  # 오디오 정규화 추가 -af dynaudnorm
# }

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn -b:a 320k -ac 2 -ar 48000'  # 네트워크 재연결 옵션 추가
}


ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class MusicPlayer(discord.FFmpegOpusAudio):  # 부모 클래스 변경
    def __init__(self, source, *, data):
        super().__init__(source, **ffmpeg_options)  # FFmpegOpusAudio 초기화
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None):
        loop = loop or asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))
            
            entries = data.get('entries', [])
            if entries:
                return [cls(entry['url'], data=entry) for entry in entries]
                
            return [cls(data['url'], data=data)]
        except Exception as e:
            print(f"Error: {e}")
            return []

bot = commands.Bot(command_prefix='?', intents=discord.Intents.all())

@bot.event
async def on_ready():
    print(f'{bot.user.name}이 성공적으로 로그인!')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="?help"))

@bot.command(name='play')
async def play(ctx, *, url):
    """음악 재생 명령어"""
    if not ctx.author.voice:
        return await ctx.send("먼저 음성 채널에 접속해 주세요!")

    voice_client = ctx.voice_client
    channel = ctx.author.voice.channel
    
    if not voice_client:
        voice_client = await channel.connect()
    elif voice_client.channel != channel:
        await voice_client.move_to(channel)

    text_channels[ctx.guild.id] = ctx.channel

    async with ctx.typing():
        players = await MusicPlayer.from_url(url, loop=bot.loop)
        if not players:
            return await ctx.send("⚠️ 재생할 수 있는 콘텐츠를 찾지 못했습니다!")

        if ctx.guild.id not in queues:
            queues[ctx.guild.id] = []
        queues[ctx.guild.id].extend(players)

        added_titles = "\n".join([p.title for p in players])
        await ctx.send(f"**🎶 {len(players)}곡 추가됨:**\n{added_titles}")

        if not voice_client.is_playing():
            await play_next(ctx.guild)

async def play_next(guild):
    if queues.get(guild.id):
        voice_client = guild.voice_client
        if voice_client and not voice_client.is_playing():
            next_track = queues[guild.id].pop(0)
            
            def after_play(error):
                if error:
                    print(f'재생 오류: {error}')
                    asyncio.run_coroutine_threadsafe(
                        text_channels[guild.id].send(f"⚠️ 재생 오류: {next_track.title}"), 
                        bot.loop
                    )
                asyncio.run_coroutine_threadsafe(play_next(guild), bot.loop)

            voice_client.play(next_track, after=after_play, bitrate=320, signal_type='music')
            await text_channels[guild.id].send(f"**▶️ 재생 중:** {next_track.title}")

@bot.command(name='skip')
async def skip(ctx):
    """현재 곡 건너뛰기"""
    voice_client = ctx.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.stop()
        await ctx.send("⏭️ 건너뛰기 완료!")
    else:
        await ctx.send("재생 중인 곡이 없습니다!")

@bot.command(name='queue')
async def show_queue(ctx):
    """현재 재생 큐 표시"""
    if ctx.guild.id not in queues or len(queues[ctx.guild.id]) == 0:
        return await ctx.send("📭 재생 대기열이 비어 있습니다!")
    
    queue_list = [f"**{i+1}.** {track.title}" for i, track in enumerate(queues[ctx.guild.id])]
    await ctx.send(f"**🎧 재생 대기열 ({len(queue_list)}곡):**\n" + "\n".join(queue_list[:10]))


@bot.command(name='stop')
async def stop(ctx):
    """재생 중지 및 연결 종료"""
    voice_client = ctx.voice_client
    if voice_client:
        queues[ctx.guild.id].clear()
        await voice_client.disconnect()
        await ctx.send("🛑 재생 중지 및 연결 종료")
    else:
        await ctx.send("봇이 음성 채널에 연결되어 있지 않습니다!")

@bot.command(name='pause')
async def pause(ctx):
    """재생 일시정지"""
    voice_client = ctx.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.pause()
        await ctx.send("⏸️ 일시정지")
    else:
        await ctx.send("재생 중인 곡이 없습니다!")

@bot.command(name='resume')
async def resume(ctx):
    """재생 재개"""
    voice_client = ctx.voice_client
    if voice_client and voice_client.is_paused():
        voice_client.resume()
        await ctx.send("▶️ 재생 재개")
    else:
        await ctx.send("일시정지 상태가 아닙니다!")

@play.before_invoke
async def ensure_voice(ctx):
    if ctx.voice_client is None:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()
        else:
            await ctx.send("먼저 음성 채널에 접속해 주세요!")
            raise commands.CommandError("사용자가 음성 채널에 없음")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("⚠️ 알 수 없는 명령어입니다. ?help 를 참조하세요.")
    else:
        print(f'오류 발생: {error}')


load_dotenv

# 봇 토큰 설정
if __name__ == "__main__":
    bot.run(os.getenv('DISCORD_TOKEN'))
