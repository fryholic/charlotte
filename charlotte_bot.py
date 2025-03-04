import io
import math
from datetime import datetime
import requests
from discord import File
import matplotlib.pyplot as plt
import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
import os
from dotenv import load_dotenv
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# 각 서버별 큐와 채널 정보 저장
queues = {}
text_channels = {}

# 차단 목록 초기화
raw_ids = os.getenv('BLOCKED_USER_IDS', '').strip()
BLOCKED_USER_IDS = []
if raw_ids:
    try:
        BLOCKED_USER_IDS = [int(x.strip()) for x in raw_ids.split(',') if x.strip()]
    except ValueError as e:
        print(f"⚠️ 초기화 실패 - 잘못된 사용자 ID 형식: {e}")
        BLOCKED_USER_IDS = []

# 감시 핸들러
class EnvFileHandler(FileSystemEventHandler):
    def __init__(self, bot):
        self.bot = bot

    def on_modified(self, event):
        if event.src_path.endswith('.env'):
            print("\n🔔 .env 파일 변경 감지!")
            load_dotenv(override=True)
            
            
            raw_ids = os.getenv('BLOCKED_USER_IDS', '').strip()
            new_ids = []
            if raw_ids: # 검증
                try:
                    new_ids = [int(x.strip()) for x in raw_ids.split(',') if x.strip()]
                except ValueError as e:
                    print(f"⚠️ 잘못된 사용자 ID 형식: {e}")
                    return
            
            # 차단 목록 업데이트
            global BLOCKED_USER_IDS
            BLOCKED_USER_IDS = new_ids
            print(f"🔄 차단 목록 업데이트 완료: {BLOCKED_USER_IDS}")

async def setup_file_watcher(bot):
    observer = Observer()
    event_handler = EnvFileHandler(bot)
    observer.schedule(event_handler, path='/app', recursive=False)
    observer.start()
    print("✅ 파일 감시기 시작됨")
    return observer


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
    'ignoreerrors': False,
    'quiet': False,
    'no_warnings': False,
    'extract_flat': 'in_playlist',
    'http_headers': {  # 헤더 추가로 차단 방지
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    },
    'postprocessors': [{ 
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'opus',  # OPUS 코덱 사용으로 변경
        'preferredquality': '320',
    }],
}

# ffmpeg_options = {
#     'options': '-vn -loglevel quiet -ab 320'  # 오디오 정규화 추가 -af dynaudnorm
# }

# ffmpeg_options = {
#     'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
#     'options': '-vn -b:a 320k -ac 2 -ar 48000 -af dynaudnorm=f=500:g=31:p=0.95:m=10:s=0'  # 네트워크 재연결 옵션 추가
# }

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn -b:a 320k -ac 2 -ar 48000 -af dynaudnorm=f=500:g=31:p=0.95:m=10:s=0'
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
    bot.file_observer = await setup_file_watcher(bot)
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="?help"))

@bot.event
async def on_message(message):
    if message.author.id in BLOCKED_USER_IDS and message.content.startswith(bot.command_prefix):
        print(f"차단된 사용자 : {message.author.id}")
        return
    await bot.process_commands(message)

@bot.event
async def close(self):
    if hasattr(self, 'file_observer'):
        self.file_observer.stop()
        self.file_observer.join()
    await super().close()

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

@bot.event
async def on_voice_state_update(member, before, after):
    if member.guild.id in queues and after.channel is not None:
        if member.voice.self_mute and member.id in BLOCKED_USER_IDS:
            dm = await member.create_dm()
            await dm.send(file=discord.File('./img/charlotte_warn.png'))
            await dm.send("🔇 마이크를 껐습니다. 10초 이내로 다시 켜지 않으면 음성 채널에서 내보냅니다.")
            await asyncio.sleep(10)
            if after.self_mute and member.voice is not None:
                await member.move_to(None)
                await dm.send(file=discord.File('./img/charlotte_kick.gif'))
                await dm.send("🚪 마이크를 켜지 않아 음성 채널에서 내보냈습니다.")

@bot.command(name='er')
async def er_stat(ctx, player_id: str):
    """
    에터널 리턴 전적 조회 (?er [플레이어 아이디])
    + 한글 폰트 / RP 그래프 데이터 없는 날짜 생략 개선
    """
    # ---------------------------------------
    # 1) 티어 목록 가져오기
    # ---------------------------------------
    tiers_url = "https://er.dakgg.io/api/v1/data/tiers?hl=ko"
    try:
        tiers_resp = requests.get(tiers_url, timeout=10)
        tiers_data = tiers_resp.json()
    except Exception as e:
        await ctx.send(f"❌ 티어 목록 API 요청 실패: {e}")
        return

    # tier_id -> dict("name", "icon", "image")
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

    # ---------------------------------------
    # 2) 플레이어 프로필 가져오기
    # ---------------------------------------
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

    # 현재 시즌 판단
    meta_season_str = data.get("meta", {}).get("season", "")  # e.g. "SEASON_15"
    season_id_map = {
        "SEASON_15": 29,
        # 필요 시 확장...
    }
    current_season_id = season_id_map.get(meta_season_str, None)
    if not current_season_id:
        await ctx.send("❌ 현재 시즌 정보를 찾지 못했습니다.")
        return

    # playerSeasonOverviews에서 RANK 스쿼드(matchingModeId=3, teamModeId=3) 데이터 찾기
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

    # ---------------------------------------
    # 3) 전적 파싱
    # ---------------------------------------
    tier_id = target_record.get("tierId", 0)
    tier_grade_id = target_record.get("tierGradeId", 0)
    mmr = target_record.get("mmr", 0)
    tier_mmr = target_record.get("tierMmr", 0)

    # 티어명, 아이콘
    tier_name = tier_info_map.get(tier_id, {}).get("name", "언랭크")
    tier_icon = tier_info_map.get(tier_id, {}).get("icon")  # round 아이콘
    detail_tier = f"{tier_name} {tier_grade_id} - {tier_mmr} RP" if tier_name != "언랭크" else "언랭"

    # 글로벌/지역 랭킹
    global_rank_data = target_record.get("rank", {}).get("global", {})
    local_rank_data = target_record.get("rank", {}).get("local", {})

    global_rank = global_rank_data.get("rank", 0)
    global_size = global_rank_data.get("rankSize", 1)
    global_percent = (global_rank / global_size * 100) if global_size else 0

    local_rank_val = local_rank_data.get("rank", 0)
    local_size = local_rank_data.get("rankSize", 1)
    local_percent = (local_rank_val / local_size * 100) if local_size else 0

    # 전적 계산
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

    # ---------------------------------------
    # 4) RP 그래프(데이터 없는 날짜 생략)
    # ---------------------------------------
    mmr_stats = target_record.get("mmrStats", [])
    # 예: [[20250203,1892,1787,1892],[20250202,1807,1598,1807], ...]

    # X축: 0, 1, 2, ... (데이터 길이만큼 등간격)
    # Ticks는 strftime("%y-%m-%d") 등으로 표현
    x_values = []
    x_labels = []
    y_values = []

    for row in mmr_stats:
        if len(row) < 2:
            continue
        date_yyyymmdd = str(row[0])  # e.g. "20250203"
        mmr_val = row[-1]

        try:
            y = int(date_yyyymmdd[:4])
            m = int(date_yyyymmdd[4:6])
            d = int(date_yyyymmdd[6:8])
            date_obj = datetime(y, m, d)
            # x_values 배열 길이
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

    # 그래프 그리기 (등간격)
    fig, ax = plt.subplots(figsize=(6, 4))  # 그림 크기는 상황에
    fig.patch.set_facecolor('none')
    ax.set_facecolor('none')

    ax.invert_xaxis()
    if x_values and y_values:
        ax.plot(x_values, y_values, color=COLOR, marker='o')

        # X축 눈금 = x_values, 라벨 = x_labels
        ax.set_xticks(x_values)
        ax.set_xticklabels(x_labels, rotation=45)
    else:
        ax.text(0.5, 0.5, "RP 데이터 없음", ha='center', va='center', transform=ax.transAxes)

    plt.tight_layout()

    # PNG로 버퍼 저장
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100)
    buf.seek(0)
    plt.close(fig)

    file = File(buf, filename="mmr_stats.png")

    # ---------------------------------------
    # 5) 임베드 전송
    # ---------------------------------------
    embed = discord.Embed(
        title="이터널 리턴 전적",
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

load_dotenv()

# 봇 토큰 설정
if __name__ == "__main__":
    bot.run(os.getenv('DISCORD_TOKEN'))
