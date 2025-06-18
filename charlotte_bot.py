import asyncio
import io
import math
from datetime import datetime
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(os.path.dirname(__file__))))
import traceback

import discord
import matplotlib.pyplot as plt
import requests
from discord import File
from discord.ext import commands

from dotenv import load_dotenv

from Modules.KonglishResolver import convert_mixed_string, english_ratio_excluding_code_and_urls
from Modules.LanguageResearcher import detect_text_type
from Modules.ServerClient import ServerClient
from Modules.TrackFactory import TrackFactory

# 차단 목록 초기화
raw_ids = os.getenv('BLOCKED_USER_IDS', '').strip()
BLOCKED_USER_IDS = []
if raw_ids:
    try:
        BLOCKED_USER_IDS = [int(x.strip()) for x in raw_ids.split(',') if x.strip()]
    except ValueError as e:
        print(f"⚠️ 초기화 실패 - 잘못된 사용자 ID 형식: {e}")
        BLOCKED_USER_IDS = []

# -----------------------------------------
# 봇 및 클라이언트 관리
# -----------------------------------------
clients : dict[int, ServerClient] = {}
bot = commands.Bot(command_prefix='!', intents=discord.Intents.all())

@bot.event
async def on_ready():
    print(f'{bot.user.name}이 성공적으로 로그인!')
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
async def on_message(message):
    # # 만약 차단된 사용자가 봇 명령어를 입력하면 무시
    # if message.author.id in BLOCKED_USER_IDS and message.content.startswith(bot.command_prefix):
    #     print(f"차단된 사용자 : {message.author.id}")
    #     return
    await bot.process_commands(message)

    # [일시 중지] Korean Fixer
    # 문자열의 시작이 명령어 접두사가 아닐경우에만, 봇 또는 자기 자신이 입력한 메시지가 아닌 경우에만 실행
    # if not message.content.startswith(bot.command_prefix) and message.author != bot.user and not message.author.bot:
    #     eng_distribution = english_ratio_excluding_code_and_urls(message.content)
    #     if eng_distribution < 0.7:
    #         return

    #     korean_scale = detect_text_type(message.content)["korean_scale"]
    #     if korean_scale > 0.9:
    #         resolved_message = convert_mixed_string(message.content)
    #         await message.channel.send(resolved_message)


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
                    return await ctx.send(f"⚠️ 파일 처리 오류: {str(e)}")
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
                    return await ctx.send(f"⚠️ URL 처리 오류: {str(e)}")

        # 큐에 추가
        client.audio_scheduler.enqueue_list(players)

        # 사용자에게 알림
        added_titles = "\n".join([f"- {p.title}" for p in players])
        await ctx.send(f"**🎶 {len(players)}곡 추가됨:**\n{added_titles}")

        if not client.voice_client.is_playing():
            await play_next(ctx.guild)

    except Exception as e:
        print(f"Unexpected error: {traceback.format_exc()}")
        await ctx.send(f"⚠️ 오류 발생: {str(e)}")

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


# 알 수 없는 명령어 처리

# @bot.event
# async def on_command_error(ctx, error):
#    if isinstance(error, commands.CommandNotFound):
#        await ctx.send("⚠️ 알 수 없는 명령어입니다. ?help 를 참조하세요.")
#    else:
#        print(f'오류 발생: {error}')


# # 차단된 유저가 음성에서 마이크를 끄면 감시 → 강퇴 예시
# @bot.event
# async def on_voice_state_update(member, before, after):
#     if member.id in BLOCKED_USER_IDS:
#         # self_mute가 True라면
#         if after.self_mute:
#             dm = await member.create_dm()
#             await dm.send(file=discord.File('./img/charlotte_warn.png'))
#             await dm.send("🔇 마이크를 껐습니다. 10초 이내로 다시 켜지 않으면 음성 채널에서 내보냅니다.")
#             await asyncio.sleep(10)
#             # 10초 뒤에도 여전히 마이크가 꺼져있다면
#             if member.voice and member.voice.self_mute:
#                 await member.move_to(None)
#                 await dm.send(file=discord.File('./img/charlotte_kick.gif'))
#                 await dm.send("🚪 마이크를 켜지 않아 음성 채널에서 내보냈습니다.")

# @bot.command(name='kick')
# async def voice_kick(ctx):
#     for member in ctx.guild.members:
#         if member.id in BLOCKED_USER_IDS:
#             try:
#                 if member.voice and member.voice.channel:
#                     dm = await member.create_dm()
#                     await member.move_to(None)
#                     await dm.send(file=discord.File('./img/charlotte_kick.gif'))
#                     await dm.send("🚪 마이크를 켜지 않아 음성 채널에서 내보냈습니다.")
#             except discord.Forbidden:
#                 pass

@bot.command(name='er')
async def er_stat(ctx, player_id: str):
    """
    이터널리턴 전적 조회 (?er [플레이어 아이디])
    + 한글 폰트 / RP 그래프 데이터 없는 날짜 생략 개선
    """
    # 이하 동일
    tiers_url = "https://er.dakgg.io/api/v1/data/tiers?hl=ko"
    try:
        tiers_resp = requests.get(tiers_url, timeout=10)
        tiers_data = tiers_resp.json()
    except Exception as e:
        await ctx.send(f"❌ 티어 목록 API 요청 실패: {e}")
        return

    tier_info_map = {}
    for t in tiers_data.get("tiers", []):
        t_id = t.get("id")
        t_name = t.get("name")
        t_icon = t.get("iconUrl")
        t_image = t.get("imageUrl")
        if t_icon and t_icon.startswith("//"):
            t_icon = "https:" + t_icon
        if t_image and t_image.startswith("//"):
            t_image = "https:" + t_image
        tier_info_map[t_id] = {
            "name": t_name,
            "icon": t_icon,
            "image": t_image
        }

    profile_url = f"https://er.dakgg.io/api/v1/players/{player_id}/profile"
    try:
        resp = requests.get(profile_url, timeout=10)
        if resp.status_code != 200:
            await ctx.send(f"❌ 프로필 API 오류 (HTTP {resp.status_code}) - 플레이어를 찾을 수 없습니다.")
            return
    except Exception as e:
        await ctx.send(f"❌ 프로필 API 요청 실패: {e}")
        return

    data = resp.json()
    meta_season_str = data.get("meta", {}).get("season", "")
    season_id_map = {
        "SEASON_15": 29,
    }
    current_season_id = season_id_map.get(meta_season_str, None)
    if not current_season_id:
        await ctx.send("❌ 현재 시즌 정보를 찾지 못했습니다.")
        return

    target_record = None
    for season_obj in data.get("playerSeasonOverviews", []):
        if (season_obj.get("seasonId") == current_season_id
                and season_obj.get("matchingModeId") == 3
                and season_obj.get("teamModeId") == 3):
            target_record = season_obj
            break

    if not target_record:
        await ctx.send("❓ 해당 플레이어의 RANK(스쿼드) 데이터가 없습니다.")
        return

    tier_id = target_record.get("tierId", 0)
    tier_grade_id = target_record.get("tierGradeId", 0)
    mmr = target_record.get("mmr", 0)
    tier_mmr = target_record.get("tierMmr", 0)

    tier_name = tier_info_map.get(tier_id, {}).get("name", "언랭크")
    tier_icon = tier_info_map.get(tier_id, {}).get("icon")
    detail_tier = f"{tier_name} {tier_grade_id} - {tier_mmr} RP" if tier_name != "언랭크" else "언랭"

    global_rank_data = target_record.get("rank", {}).get("global", {})
    local_rank_data = target_record.get("rank", {}).get("local", {})

    global_rank = global_rank_data.get("rank", 0)
    global_size = global_rank_data.get("rankSize", 1)
    global_percent = (global_rank / global_size * 100) if global_size else 0

    local_rank_val = local_rank_data.get("rank", 0)
    local_size = local_rank_data.get("rankSize", 1)
    local_percent = (local_rank_val / local_size * 100) if local_size else 0

    def safe_div(a, b):
        return a / b if b else 0

    play = target_record.get("play", 0)
    win = target_record.get("win", 0)
    top2 = target_record.get("top2", 0)
    top3 = target_record.get("top3", 0)
    place_sum = target_record.get("place", 0)
    kills = target_record.get("playerKill", 0)
    assists = target_record.get("playerAssistant", 0)
    team_kills = target_record.get("teamKill", 0)
    damage = target_record.get("damageToPlayer", 0)

    wr = safe_div(win, play) * 100
    avg_kill = safe_div(kills, play)
    avg_assist = safe_div(assists, play)
    avg_damage = safe_div(damage, play)
    avg_team_kill = safe_div(team_kills, play)
    top2_rate = safe_div(top2, play) * 100
    top3_rate = safe_div(top3, play) * 100
    avg_rank = safe_div(place_sum, play)

    def fmt(v, digit=2):
        return f"{v:.{digit}f}"

    mmr_stats = target_record.get("mmrStats", [])
    x_values = []
    x_labels = []
    y_values = []

    for row in mmr_stats:
        if len(row) < 2:
            continue

        if len(x_values) >= 15:
            break
        date_yyyymmdd = str(row[0])

        mmr_val = row[-1]

        try:
            y = int(date_yyyymmdd[:4])
            m = int(date_yyyymmdd[4:6])
            d = int(date_yyyymmdd[6:8])
            date_obj = datetime(y, m, d)
            idx = len(x_values)
            x_values.append(idx)
            x_labels.append(date_obj.strftime("%y-%m-%d"))
            y_values.append(mmr_val)
        except:
            pass

    COLOR = 'white'
    plt.rcParams['text.color'] = COLOR
    plt.rcParams['axes.labelcolor'] = COLOR
    plt.rcParams['xtick.color'] = COLOR
    plt.rcParams['ytick.color'] = COLOR
    plt.rcParams['axes.edgecolor'] = 'none'

    fig, ax = plt.subplots(figsize=(6, 4))
    fig.patch.set_facecolor('none')
    ax.set_facecolor('none')

    ax.invert_xaxis()
    if x_values and y_values:
        ax.plot(x_values, y_values, color=COLOR, marker='o')
        ax.set_xticks(x_values)
        ax.set_xticklabels(x_labels, rotation=45)
    else:
        ax.text(0.5, 0.5, "RP 데이터 없음", ha='center', va='center', transform=ax.transAxes)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100)
    buf.seek(0)
    plt.close(fig)
    file = File(buf, filename="mmr_stats.png")

    embed = discord.Embed(
        title="이터널리턴 전적",
        description=(
            f"**플레이어:** {player_id}\n"
            f"**티어:** {tier_name}\n"
            f"**MMR(RP):** {mmr} RP\n"
        ),
        color=discord.Color.blue()
    )
    embed.add_field(name="세부 티어", value=detail_tier, inline=True)

    if tier_icon:
        embed.set_thumbnail(url=tier_icon)

    embed.add_field(
        name="글로벌 랭킹",
        value=f"{global_rank:,}위 (상위 {fmt(global_percent)}%)",
        inline=False
    )
    embed.add_field(
        name="지역 랭킹",
        value=f"{local_rank_val:,}위 (상위 {fmt(local_percent)}%)",
        inline=False
    )

    embed.add_field(name="게임 수", value=str(play), inline=True)
    embed.add_field(name="승률", value=f"{fmt(wr)}%", inline=True)
    embed.add_field(name="평균 TK", value=fmt(avg_team_kill), inline=True)

    embed.add_field(name="평균 킬", value=fmt(avg_kill), inline=True)
    embed.add_field(name="평균 어시", value=fmt(avg_assist), inline=True)
    embed.add_field(name="평균 딜량", value=f"{math.floor(avg_damage):,}", inline=True)

    embed.add_field(name="TOP 2", value=f"{fmt(top2_rate)}%", inline=True)
    embed.add_field(name="TOP 3", value=f"{fmt(top3_rate)}%", inline=True)
    embed.add_field(name="평균 순위", value=fmt(avg_rank, 1), inline=True)

    embed.set_image(url="attachment://mmr_stats.png")
    await ctx.send(file=file, embed=embed)

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
if __name__ == "__main__":
    bot.run(os.getenv('DISCORD_TOKEN'))
