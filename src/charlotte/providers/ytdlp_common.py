"""Fresh yt-dlp extraction and FFmpeg source construction helpers."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from collections.abc import Callable
from typing import Any

import discord

from charlotte.constants import YTDLP_SOCKET_TIMEOUT
from charlotte.music.models import PreparedAudio
from charlotte.music.provider import register_detached_work

_DETACHED_CLEANUP_TASKS: set[asyncio.Task[None]] = set()


async def run_blocking[T](
    operation: Callable[[], T],
    *,
    cleanup_cancelled_result: Callable[[T], None] | None = None,
) -> T:
    """Run short blocking work without making cancellation wait for the worker."""

    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        completion = asyncio.get_running_loop().create_future()

        def cleanup(completed: asyncio.Task[T]) -> None:
            cleanup_task = asyncio.create_task(
                _cleanup_detached_result(completed, cleanup_cancelled_result)
            )
            _DETACHED_CLEANUP_TASKS.add(cleanup_task)

            def finished(finished_task: asyncio.Task[None]) -> None:
                _DETACHED_CLEANUP_TASKS.discard(finished_task)
                if not completion.done():
                    completion.set_result(None)

            cleanup_task.add_done_callback(finished)

        task.add_done_callback(cleanup)
        register_detached_work(completion)
        raise


async def _cleanup_detached_result[T](
    task: asyncio.Task[T], cleanup_cancelled_result: Callable[[T], None] | None
) -> None:
    try:
        result = task.result()
    except BaseException:
        return
    if cleanup_cancelled_result is None:
        return
    try:
        await asyncio.to_thread(cleanup_cancelled_result, result)
    except BaseException:
        return


class YtdlpError(RuntimeError):
    pass


async def extract(url: str, *, playlist: bool) -> dict[str, Any]:
    """Run yt-dlp behind a subprocess boundary that cancellation can terminate."""

    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--dump-single-json",
        "--no-warnings",
        "--no-cache-dir",
        "--skip-download",
        "--format",
        "bestaudio/best",
        "--socket-timeout",
        str(YTDLP_SOCKET_TIMEOUT),
        "--js-runtimes",
        "node",
    ]
    if playlist:
        command.extend(("--playlist-items", "1"))
    else:
        command.append("--no-playlist")
    command.append(url)

    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await process.communicate()
    except asyncio.CancelledError:
        await asyncio.shield(_kill_process(process))
        raise
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip().rsplit("\n", 1)[-1]
        raise YtdlpError(detail or f"yt-dlp exited with status {process.returncode}")
    try:
        result = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise YtdlpError("yt-dlp returned invalid metadata") from exc
    if not isinstance(result, dict):
        raise YtdlpError("yt-dlp returned no metadata")
    return result


async def _kill_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    await process.wait()


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

    source, stderr_sink = await run_blocking(
        create,
        cleanup_cancelled_result=cleanup_cancelled,
    )
    return PreparedAudio(source=source, seekable=True, owned_resources=(stderr_sink,))
