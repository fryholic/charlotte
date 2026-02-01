import asyncio
import logging
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(os.path.dirname(__file__))))
import traceback

import discord
from discord.ext import commands

from dotenv import load_dotenv
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from Modules.features.emoji_enlarger import build_emoji_embed
from Modules.features.eternal_return import register_er_commands
from Modules.features.konglish.KonglishResolver import (
    convert_mixed_string,
    english_ratio_excluding_code_and_urls,
)
from Modules.features.language_research.LanguageResearcher import detect_text_type
from Modules.ServerClient import ServerClient
from Modules.TrackFactory import TrackFactory
from Modules.ErrorHandler import handle_error

# -----------------------------------------
# 봇 및 클라이언트 관리
# -----------------------------------------
clients : dict[int, ServerClient] = {}
bot = commands.Bot(command_prefix='?', intents=discord.Intents.all())
register_er_commands(bot)


@bot.event
async def on_ready():
    print(f'{bot.user.name}이 성공적으로 로그인!')
    # bot.file_observer = await setup_file_watcher(bot)
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="?help"))

    for guild in bot.guilds:
        if guild.id not in clients:
            clients[guild.id] = ServerClient(guild.id)

    print("🔊 서버 클라이언트 초기화 완료")

@bot.event
async def on_guild_join(guild):
    if guild.id not in clients:
        clients[guild.id] = ServerClient(guild.id)
        print(f"서버 클라이언트 추가: {guild.id}")

@bot.event
async def on_message(message: discord.Message):
    if not message.guild or message.author.bot:
        return

    embed = build_emoji_embed(message)
    if embed:
        try:
            await message.delete()
            await message.channel.send(embed=embed, reference=message.reference, mention_author=False)
        except discord.Forbidden:
            pass
        except Exception as e:
            logging.exception(e)

    await bot.process_commands(message)

@bot.event
async def close(self):
    # 파일 감시 종료
    if hasattr(self, 'file_observer'):
        self.file_observer.stop()
        self.file_observer.join()
    await super().close()


async def play_next(guild: discord.Guild):
    """
    큐에 남은 트랙이 있다면 다음 트랙을 재생.
    """
    client = clients[guild.id]
    voice_client = client.voice_client

    # 예외처리: 봇이 음성채널에 없거나, 이미 무언가 재생중이면 종료
    if not voice_client or not voice_client.is_connected():
        return
    if voice_client.is_playing() or voice_client.is_paused():
        return
    if client.audio_scheduler.is_empty():
        return

    next_track = client.audio_scheduler.dequeue()

    def after_play(error):
        if error:
            print(f'재생 오류: {error}')
            if client.audio_scheduler.text_channel:
                asyncio.run_coroutine_threadsafe(
                    client.audio_scheduler.text_channel.send(f"⚠️ 재생 오류: {next_track.title}"),
                    bot.loop
                )
        # 다음 곡 재생
        fut = asyncio.run_coroutine_threadsafe(play_next(guild), bot.loop)
        try:
            fut.result()
        except:
            pass

    voice_client.play(next_track, after=after_play)
    # 텍스트 채널에 알림
    if client.audio_scheduler.text_channel:
        await client.audio_scheduler.text_channel.send(f"**▶️ 재생 중:** {next_track.title}")


@bot.command(name='play')
async def play(ctx, *, url=None):
    """음악 재생 명령어"""
    if not ctx.author.voice:
        return await ctx.send("먼저 음성 채널에 접속해 주세요!")

    if ctx.guild.id not in clients:
        clients[ctx.guild.id] = ServerClient(ctx.guild.id)


    client = clients[ctx.guild.id]

    try:
        await client.join_voice_channel(ctx.author.voice.channel)
        client.audio_scheduler.text_channel = ctx.channel

        players = []

        if ctx.message.attachments:
            attachment = ctx.message.attachments[0]
            if not attachment.content_type.startswith('audio/'):
                return await ctx.send("⚠️ 오디오 파일만 업로드 가능합니다.")

            async with ctx.typing():
                try:
                    players = await TrackFactory.from_upload(attachment)
                    if not players or not isinstance(players, list):
                        return await ctx.send("⚠️ 파일 처리에 실패했습니다.")
                except Exception as e:
                    return await handle_error(ctx, e, "파일 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")
        else:
            if not url:
                return await ctx.send("URL을 입력하거나 오디오 파일을 업로드해주세요!")


            async with ctx.typing():
                try:
                    players = await TrackFactory.from_url(url)
                    if not players or not isinstance(players, list):
                        return await ctx.send("⚠️ 재생할 수 있는 콘텐츠를 찾지 못했습니다!")
                    
                    # Track 객체 유효성 검사 추가
                    if any(not hasattr(track, 'title') for track in players):
                        return await ctx.send("⚠️ 다시 시도해주세요!")
                        
                except Exception as e:
                    return await handle_error(ctx, e, "음악을 불러오는 중 오류가 발생했습니다. 링크가 올바른지 확인해주세요.")

        # 큐에 추가
        client.audio_scheduler.enqueue_list(players)
        # 사용자에게 알림
        added_titles = "\n".join([f"- {p.title}" for p in players])
        await ctx.send(f"**🎶 {len(players)}곡 추가됨:**\n{added_titles}")

        if not client.voice_client.is_playing():
            await play_next(ctx.guild)

    except Exception as e:
        await handle_error(ctx, e, "알 수 없는 오류가 발생했습니다. 관리자에게 문의해주세요.")

@bot.command(name='skip')
async def skip(ctx):
    """현재 곡 건너뛰기"""
    client = clients[ctx.guild.id]
    voice_client = client.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.stop()
        await ctx.send("⏭️ 건너뛰기 완료!")
    else:
        await ctx.send("재생 중인 곡이 없습니다!")

@bot.command(name='queue')
async def show_queue(ctx):
    """현재 재생 큐 표시"""
    client = clients[ctx.guild.id]
    if client.audio_scheduler.is_empty():
        return await ctx.send("📭 재생 대기열이 비어 있습니다!")

    queue_list = [
        f"**{i + 1}.** {track.title}"
        for i, track in enumerate(client.audio_scheduler.clone())
    ]
    # 너무 길면 상위 10곡만 보여주기
    display_text = "\n".join(queue_list[:10])
    if len(queue_list) > 10:
        display_text += f"\n... (총 {len(queue_list)}곡)"

    await ctx.send(f"**🎧 재생 대기열:**\n{display_text}")

@bot.command(name='stop')
async def stop(ctx):
    """모든 재생 정지 및 대기열 비우기"""
    client = clients[ctx.guild.id]
    voice_client = client.voice_client
    if voice_client and voice_client.is_connected():
        client.audio_scheduler.clear()  # 대기열 비우기
        if voice_client.is_playing():
            voice_client.stop()  # 재생 중지
        await ctx.send("🛑 모든 재생이 정지되고 대기열이 비워졌습니다.")
    else:
        await ctx.send("봇이 음성 채널에 연결되어 있지 않습니다!")

@bot.command(name='leave')
async def leave(ctx):
    """음성 채널 떠나기"""
    client = clients[ctx.guild.id]
    voice_client = client.voice_client
    if voice_client and voice_client.is_connected():
        await client.leave_voice_channel()
        await ctx.send("👋 음성 채널을 떠났습니다.")
    else:
        await ctx.send("봇이 음성 채널에 연결되어 있지 않습니다!")


@bot.command(name='pause')
async def pause(ctx):
    """재생 일시정지"""
    client = clients[ctx.guild.id]
    voice_client = client.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.pause()
        await ctx.send("⏸️ 일시정지")
    else:
        await ctx.send("재생 중인 곡이 없습니다!")


@bot.command(name='resume')
async def resume(ctx):
    """재생 재개"""
    client = clients[ctx.guild.id]
    voice_client = client.voice_client
    if voice_client and voice_client.is_paused():
        voice_client.resume()
        await ctx.send("▶️ 재생 재개")
    else:
        await ctx.send("일시정지 상태가 아닙니다!")

@bot.event
async def on_voice_state_update(member, before, after):
    """
    음성 채널 상태 업데이트 이벤트 핸들러
    """
    # 봇이 음성 채널에 접속해 있는지 확인
    if member.guild.id in clients:
        client = clients[member.guild.id]
        voice_client = client.voice_client

        if voice_client and voice_client.is_connected():
            # 현재 봇이 있는 음성 채널
            bot_channel = voice_client.channel

            # 음성 채널에 남아 있는 멤버 확인
            remaining_members = [m for m in bot_channel.members if not m.bot]

            # 봇만 남아 있다면 음성 채널 떠나기
            if len(remaining_members) == 0:
                await client.leave_voice_channel()
                print(f"👋 음성 채널을 떠났습니다: {bot_channel}")

load_dotenv()


def _get_discord_token() -> str:
    """Select the proper Discord token based on DEV flag."""
    dev_raw = os.getenv("DEV", "false").strip().lower()
    is_dev = dev_raw in {"1", "true", "yes", "on"}
    token_key = "DISCORD_TOKEN_DEV" if is_dev else "DISCORD_TOKEN"
    token = os.getenv(token_key)
    if not token:
        raise RuntimeError(f"Missing required environment variable: {token_key}")
    return token


if __name__ == "__main__":
    bot.run(_get_discord_token())
