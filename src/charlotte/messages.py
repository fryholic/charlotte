"""Approved user-facing message catalog and render helpers."""

from __future__ import annotations

import math
from collections.abc import Mapping

import discord

MESSAGES: Mapping[str, str] = {
    "access.user_not_in_voice": "먼저 음성 채널에 접속해 주세요!",
    "access.different_voice_channel": "봇과 같은 음성 채널에서 사용해 주세요!",
    "access.admin_play_requires_voice": "재생할 음성 채널에 먼저 접속해 주세요!",
    "access.bot_cannot_connect": "⚠️ 이 음성 채널에 연결할 수 없습니다. 봇의 연결 권한을 확인해 주세요!",
    "access.bot_cannot_speak": "⚠️ 이 음성 채널에서 소리를 재생할 수 없습니다. 봇의 말하기 권한을 확인해 주세요!",
    "access.operator_only": "이 명령은 설정된 bot operator만 사용할 수 있습니다.",
    "common.command_failed": "⚠️ 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    "common.operation_cancelled": "요청이 취소되었습니다. 다시 시도해 주세요!",
    "common.bot_not_connected": "봇이 음성 채널에 연결되어 있지 않습니다!",
    "common.nothing_playing": "재생 중인 곡이 없습니다!",
    "music.play.missing_input": "YouTube/SoundCloud URL 하나 또는 오디오 파일 하나를 보내 주세요!",
    "music.play.ambiguous_input": "URL과 파일을 함께 사용할 수 없습니다. 둘 중 하나만 보내 주세요!",
    "music.play.multiple_attachments": "오디오 파일은 하나만 보내 주세요!",
    "music.play.invalid_attachment": "⚠️ 재생 가능한 오디오 파일이 아닙니다.",
    "music.play.attachment_read_failed": "⚠️ 파일을 읽지 못했습니다. 다시 업로드해 주세요!",
    "music.play.invalid_url": "⚠️ 올바른 YouTube 또는 SoundCloud URL을 입력해 주세요!",
    "music.play.unsupported_host": "⚠️ YouTube와 SoundCloud URL만 지원합니다.",
    "music.youtube.unavailable": "⚠️ 이 영상을 재생할 수 없습니다. 인증 없이 접근 가능한 다른 영상 URL을 보내 주세요!",
    "music.youtube.live_not_supported": "⚠️ 실시간 방송과 예정된 방송은 지원하지 않습니다. 일반 영상이나 Shorts URL을 보내 주세요!",
    "music.youtube.empty_playlist": "⚠️ 재생목록의 첫 영상을 재생할 수 없습니다.",
    "music.soundcloud.unavailable": "⚠️ 이 트랙을 재생할 수 없습니다. 인증 없이 접근 가능한 다른 트랙 URL을 보내 주세요!",
    "music.soundcloud.collection_not_supported": "⚠️ SoundCloud 단일 트랙 URL만 지원합니다.",
    "music.play.retry_exhausted": "⚠️ {title} 재생에 실패해 다음 곡으로 넘어갑니다.",
    "music.play.started": "▶️ 재생 시작: {title}\n요청: {requester}",
    "music.play.queued": "🎶 대기열에 추가: {title}\n요청: {requester}\n순번: {position}",
    "music.play.moved_and_started": "🔀 채널 이동 후 재생: {title}\n채널: {channel}\n요청: {requester}\n정리한 곡: {removed_count}곡",
    "music.skip.success": "⏭️ 건너뜀: {skipped_title}\n다음 곡: {next_title_or_none}",
    "music.skip.empty": "재생 중인 곡이 없습니다!",
    "music.pause.already_paused": "이미 일시정지되어 있습니다!",
    "music.pause.empty": "재생 중인 곡이 없습니다!",
    "music.pause.success": "⏸️ 일시정지: {title}",
    "music.resume.not_paused": "일시정지 상태가 아닙니다!",
    "music.resume.empty": "재생 중인 곡이 없습니다!",
    "music.resume.success": "▶️ 재생 재개: {title}",
    "music.stop.empty": "재생 중이거나 대기 중인 곡이 없습니다!",
    "music.stop.success": "🛑 재생을 정지하고 {removed_count}곡을 정리했습니다.",
    "music.leave.not_connected": "봇이 음성 채널에 연결되어 있지 않습니다!",
    "music.leave.success": "👋 {channel}에서 나가고 {removed_count}곡을 정리했습니다.",
    "music.queue.empty": "📭 재생 대기열이 비어 있습니다!",
    "music.voice.reconnected": "🔄 음성 연결을 복구해 재생을 이어갑니다.",
    "music.voice.reconnect_unseekable": "⚠️ 연결은 복구했지만 {title}을 이어 재생할 수 없어 다음 곡으로 넘어갑니다.",
    "music.voice.reconnect_failed": "⚠️ 음성 연결을 복구하지 못했습니다. 현재 곡은 건너뛰고 대기열은 유지합니다.",
    "emoji.original_lost": "⚠️ 이모지 확대 중 원본 메시지가 삭제되었지만 확대본을 보내지 못했습니다.",
    "emoji.replace_failed": "⚠️ 이모지를 확대하지 못했습니다.",
    "emoji.duplicate_result": "⚠️ 이모지 확대 결과가 중복되었습니다. 관리자에게 오류를 전달했습니다.",
    "extension.active_music": "현재 재생 중이거나 대기 중인 곡이 있어 source Extension을 내리거나 다시 불러올 수 없습니다.",
    "extension.protected": "이 Extension은 실행 중에 내릴 수 없습니다: {name}",
    "extension.not_found": "Extension을 찾을 수 없습니다: {name}",
    "extension.failed": "Extension 작업에 실패했습니다. Docker 로그와 application owner DM을 확인해 주세요.",
    "extension.loaded": "Extension을 불러왔습니다: {name}",
    "extension.unloaded": "Extension을 내렸습니다: {name}",
    "extension.reloaded": "Extension을 다시 불러왔습니다: {name}",
}


def render(message_id: str, **values: object) -> str:
    return MESSAGES[message_id].format(**values)


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def duration_text(seconds: float | None) -> str | None:
    if seconds is None or seconds < 0 or not math.isfinite(seconds):
        return None
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def queue_embed(current: object | None, upcoming: list[object]) -> discord.Embed:
    embed = discord.Embed(title="🎧 재생 대기열", colour=discord.Colour.blurple())
    if current is not None:
        duration = duration_text(getattr(current, "duration", None))
        suffix = f" · {duration}" if duration else ""
        marker = "⏸️" if getattr(current, "paused", False) else "▶️"
        embed.add_field(
            name="현재 재생 중",
            value=(
                f"{marker} {truncate(discord.utils.escape_markdown(current.title), 180)}{suffix}\n"
                f"요청: {truncate(discord.utils.escape_markdown(current.requester), 80)}"
            ),
            inline=False,
        )
    if upcoming:
        lines: list[str] = []
        for index, item in enumerate(upcoming, 1):
            duration = duration_text(getattr(item, "duration", None))
            suffix = f" · {duration}" if duration else ""
            lines.append(
                f"{index}. {truncate(discord.utils.escape_markdown(item.title), 120)}{suffix}\n"
                f"   요청: {truncate(discord.utils.escape_markdown(item.requester), 50)}"
            )
        embed.add_field(name="다음 곡", value="\n".join(lines), inline=False)
    return embed
