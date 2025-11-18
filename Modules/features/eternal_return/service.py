"""Service layer for the Eternal Return Discord command."""

from __future__ import annotations

import logging
from datetime import datetime
from math import floor
from typing import Any, Dict, List, Optional, Sequence, Tuple

import discord
import requests
from discord import File
from discord.ext import commands

from .constants import (
    MAX_MMR_POINTS,
    PROFILE_ENDPOINT_TEMPLATE,
    REQUEST_TIMEOUT,
    SEASON_ID_MAP,
    TIERS_ENDPOINT,
)
from .plotting import build_mmr_plot

logger = logging.getLogger(__name__)

TierInfo = Dict[str, Optional[str]]
MmrPoint = Tuple[str, int]


class EternalReturnError(Exception):
    """User-facing error for the Eternal Return feature."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def register_er_commands(bot: commands.Bot) -> None:
    """Register the ?er command on the provided bot instance."""

    @bot.command(name="er")
    async def er_stat(ctx: commands.Context, player_id: str):
        try:
            embed, file = build_er_response(player_id)
        except EternalReturnError as exc:
            await ctx.send(exc.message)
            return
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("Unexpected ER command failure")
            await ctx.send(f"❌ ER 처리 중 오류가 발생했습니다: {exc}")
            return

        await ctx.send(file=file, embed=embed)


def build_er_response(player_id: str) -> Tuple[discord.Embed, File]:
    tiers_data = _fetch_tier_info()
    tier_info_map = _build_tier_map(tiers_data)
    profile_data = _fetch_profile(player_id)

    current_season = profile_data.get("meta", {}).get("season")
    season_id = SEASON_ID_MAP.get(current_season or "")
    if not season_id:
        raise EternalReturnError("❌ 현재 시즌 정보를 찾지 못했습니다.")

    season_record = _select_squad_record(
        profile_data.get("playerSeasonOverviews", []), season_id
    )
    if not season_record:
        raise EternalReturnError("❓ 해당 플레이어의 RANK(스쿼드) 데이터가 없습니다.")

    embed = _build_embed(player_id, tier_info_map, season_record)
    mmr_points = _build_mmr_points(season_record.get("mmrStats", []))
    plot_buffer = build_mmr_plot(mmr_points)
    chart_file = File(plot_buffer, filename="mmr_stats.png")
    embed.set_image(url="attachment://mmr_stats.png")
    return embed, chart_file


def _fetch_tier_info() -> Dict[str, Any]:
    try:
        resp = requests.get(TIERS_ENDPOINT, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.warning("Tier API request failed", exc_info=exc)
        raise EternalReturnError(f"❌ 티어 목록 API 요청 실패: {exc}")


def _fetch_profile(player_id: str) -> Dict[str, Any]:
    url = PROFILE_ENDPOINT_TEMPLATE.format(player_id=player_id)
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            raise EternalReturnError(
                f"❌ 프로필 API 오류 (HTTP {resp.status_code}) - 플레이어를 찾을 수 없습니다."
            )
        return resp.json()
    except EternalReturnError:
        raise
    except requests.RequestException as exc:
        logger.warning("Profile API request failed", exc_info=exc)
        raise EternalReturnError(f"❌ 프로필 API 요청 실패: {exc}")


def _build_tier_map(tiers_data: Dict[str, Any]) -> Dict[int, TierInfo]:
    result: Dict[int, TierInfo] = {}
    for tier in tiers_data.get("tiers", []):
        tier_id = tier.get("id")
        if tier_id is None:
            continue
        icon = _sanitize_url(tier.get("iconUrl"))
        image = _sanitize_url(tier.get("imageUrl"))
        result[tier_id] = {
            "name": tier.get("name", "언랭크"),
            "icon": icon,
            "image": image,
        }
    return result


def _sanitize_url(url: Optional[str]) -> Optional[str]:
    if url and url.startswith("//"):
        return "https:" + url
    return url


def _select_squad_record(
    overviews: Sequence[Dict[str, Any]], season_id: int
) -> Optional[Dict[str, Any]]:
    for season in overviews:
        if (
            season.get("seasonId") == season_id
            and season.get("matchingModeId") == 3
            and season.get("teamModeId") == 3
        ):
            return season
    return None


def _build_mmr_points(raw_stats: Sequence[Sequence[Any]]) -> List[MmrPoint]:
    points: List[MmrPoint] = []
    for row in raw_stats:
        if len(row) < 2:
            continue
        date_raw = str(row[0])
        try:
            date_obj = datetime.strptime(date_raw[:8], "%Y%m%d")
        except ValueError:
            continue
        label = date_obj.strftime("%y-%m-%d")
        value = int(row[-1])
        points.append((label, value))
        if len(points) >= MAX_MMR_POINTS:
            break
    return points


def _safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def _fmt(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def _build_embed(
    player_id: str,
    tiers: Dict[int, TierInfo],
    record: Dict[str, Any],
) -> discord.Embed:
    tier_id = record.get("tierId", 0)
    tier_grade_id = record.get("tierGradeId", 0)
    mmr = record.get("mmr", 0)
    tier_mmr = record.get("tierMmr", 0)

    tier_meta = tiers.get(tier_id, {"name": "언랭크", "icon": None, "image": None})
    tier_name = tier_meta.get("name", "언랭크") or "언랭크"
    tier_icon = tier_meta.get("icon")
    detail_tier = (
        f"{tier_name} {tier_grade_id} - {tier_mmr} RP"
        if tier_name != "언랭크"
        else "언랭"
    )

    global_rank_data = record.get("rank", {}).get("global", {})
    local_rank_data = record.get("rank", {}).get("local", {})

    global_rank = global_rank_data.get("rank", 0)
    global_size = global_rank_data.get("rankSize", 1)
    global_percent = _safe_div(global_rank, global_size) * 100

    local_rank = local_rank_data.get("rank", 0)
    local_size = local_rank_data.get("rankSize", 1)
    local_percent = _safe_div(local_rank, local_size) * 100

    play = record.get("play", 0)
    win = record.get("win", 0)
    top2 = record.get("top2", 0)
    top3 = record.get("top3", 0)
    place_sum = record.get("place", 0)
    kills = record.get("playerKill", 0)
    assists = record.get("playerAssistant", 0)
    team_kills = record.get("teamKill", 0)
    damage = record.get("damageToPlayer", 0)

    wr = _safe_div(win, play) * 100
    top2_rate = _safe_div(top2, play) * 100
    top3_rate = _safe_div(top3, play) * 100
    avg_rank = _safe_div(place_sum, play)

    embed = discord.Embed(
        title="이터널리턴 전적",
        description=(
            f"**플레이어:** {player_id}\n"
            f"**티어:** {tier_name}\n"
            f"**MMR(RP):** {mmr} RP\n"
        ),
        color=discord.Color.blue(),
    )
    embed.add_field(name="세부 티어", value=detail_tier, inline=True)

    if tier_icon:
        embed.set_thumbnail(url=tier_icon)

    embed.add_field(
        name="글로벌 랭킹",
        value=f"{global_rank:,}위 (상위 {_fmt(global_percent)}%)",
        inline=False,
    )
    embed.add_field(
        name="지역 랭킹",
        value=f"{local_rank:,}위 (상위 {_fmt(local_percent)}%)",
        inline=False,
    )

    embed.add_field(name="게임 수", value=str(play), inline=True)
    embed.add_field(name="승률", value=f"{_fmt(wr)}%", inline=True)
    embed.add_field(name="평균 TK", value=_fmt(_safe_div(team_kills, play)), inline=True)

    embed.add_field(name="평균 킬", value=_fmt(_safe_div(kills, play)), inline=True)
    embed.add_field(name="평균 어시", value=_fmt(_safe_div(assists, play)), inline=True)
    embed.add_field(name="평균 딜량", value=f"{floor(_safe_div(damage, play)):,}", inline=True)

    embed.add_field(name="TOP 2", value=f"{_fmt(top2_rate)}%", inline=True)
    embed.add_field(name="TOP 3", value=f"{_fmt(top3_rate)}%", inline=True)
    embed.add_field(name="평균 순위", value=_fmt(avg_rank, 1), inline=True)

    return embed
