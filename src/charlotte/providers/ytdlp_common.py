"""Fresh yt-dlp extraction and FFmpeg source construction helpers."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any

import discord
import yt_dlp

from charlotte.constants import YTDLP_SOCKET_TIMEOUT
from charlotte.music.models import PreparedAudio


class QuietYtdlpLogger:
    def debug(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass


async def run_blocking[T](
    operation: Callable[[], T], *, cleanup_cancelled_result: Callable[[T], None] | None = None
) -> T:
    """Keep registry inflight accounting true until a worker thread really stops."""

    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancellation:
        try:
            result = await task
        except Exception:
            raise cancellation from None
        try:
            if cleanup_cancelled_result is not None:
                cleanup_cancelled_result(result)
        finally:
            raise cancellation


def extract(url: str, *, playlist: bool) -> dict[str, Any]:
    options: dict[str, Any] = {
        "cachedir": False,
        "extract_flat": False,
        "format": "bestaudio/best",
        "logger": QuietYtdlpLogger(),
        "js_runtimes": {"node": {}},
        "noplaylist": not playlist,
        "playlistend": 1,
        "playlist_items": "1",
        "quiet": True,
        "skip_download": True,
        "socket_timeout": YTDLP_SOCKET_TIMEOUT,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(options) as client:
        result = client.extract_info(url, download=False)
    if not isinstance(result, dict):
        raise yt_dlp.utils.DownloadError("Extractor returned no metadata")
    return result


def first_entry(data: dict[str, Any]) -> dict[str, Any] | None:
    entries = data.get("entries")
    if entries is None:
        return data
    if not isinstance(entries, list):
        entries = list(entries)
    if not entries or not isinstance(entries[0], dict):
        return None
    return entries[0]


async def stream_audio(url: str, *, start_at: float = 0) -> PreparedAudio:
    before = "-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    if start_at > 0:
        before = f"-ss {start_at:.3f} {before}"

    def create() -> tuple[discord.FFmpegOpusAudio, Any]:
        stderr_sink = open(os.devnull, "wb")
        try:
            source = discord.FFmpegOpusAudio(
                url,
                before_options=before,
                options="-vn -c:a libopus -b:a 320k -ar 48000 -ac 2",
                stderr=stderr_sink,
            )
        except Exception:
            stderr_sink.close()
            raise
        return source, stderr_sink

    def cleanup_cancelled(result: tuple[discord.FFmpegOpusAudio, Any]) -> None:
        source, stderr_sink = result
        try:
            source.cleanup()
        finally:
            stderr_sink.close()

    source, stderr_sink = await run_blocking(create, cleanup_cancelled_result=cleanup_cancelled)
    return PreparedAudio(source=source, seekable=True, owned_resources=(stderr_sink,))
