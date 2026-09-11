"""Fresh yt-dlp extraction and FFmpeg source construction helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import discord

from charlotte.constants import (
    FFMPEG_DIAGNOSTIC_BYTES,
    FFMPEG_TERMINATE_TIMEOUT,
    YTDLP_SOCKET_TIMEOUT,
)
from charlotte.errors import PlaybackError
from charlotte.music.models import PreparedAudio
from charlotte.music.provider import register_detached_work
from charlotte.observability import log_exception, redact

_DETACHED_CLEANUP_TASKS: set[asyncio.Task[None]] = set()
_LOG = logging.getLogger("charlotte.providers")


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
                try:
                    error = finished_task.exception()
                except asyncio.CancelledError as exc:
                    error = exc
                if error is not None:
                    log_exception(_LOG, error, event="provider.detached_cleanup_failed")
                if completion.done():
                    return
                if error is None:
                    completion.set_result(None)
                else:
                    completion.set_exception(error)

            cleanup_task.add_done_callback(finished)

        task.add_done_callback(cleanup)
        completion.add_done_callback(_consume_completion_exception)
        register_detached_work(completion)
        raise


async def _cleanup_detached_result[T](
    task: asyncio.Task[T], cleanup_cancelled_result: Callable[[T], None] | None
) -> None:
    result = task.result()
    if cleanup_cancelled_result is None:
        return
    await asyncio.to_thread(cleanup_cancelled_result, result)


def _consume_completion_exception(completion: asyncio.Future[None]) -> None:
    if not completion.cancelled():
        completion.exception()


class YtdlpError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StreamDescriptor:
    url: str
    headers: dict[str, str]
    protocol: str | None
    format_id: str | None
    extracted_at: float


class BoundedDiagnosticBuffer:
    """Thread-safe tail buffer compatible with discord.py's stderr drain thread."""

    def __init__(
        self,
        max_bytes: int = FFMPEG_DIAGNOSTIC_BYTES,
        *,
        secrets: tuple[str, ...] = (),
    ) -> None:
        self._max_bytes = max_bytes
        self._secrets = tuple(secret for secret in secrets if secret)
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self.closed = False

    def write(self, data: bytes) -> int:
        if not isinstance(data, bytes):
            raise TypeError("diagnostic data must be bytes")
        with self._lock:
            if self.closed:
                return 0
            self._buffer.extend(data)
            overflow = len(self._buffer) - self._max_bytes
            if overflow > 0:
                del self._buffer[:overflow]
        return len(data)

    def tail(self) -> str:
        with self._lock:
            rendered = bytes(self._buffer).decode("utf-8", errors="replace").strip()
        return str(redact(rendered, secrets=self._secrets))

    def close(self) -> None:
        with self._lock:
            self.closed = True


class _YtdlpPipeOwner:
    """Own yt-dlp and the threads feeding and diagnosing an FFmpeg pipe."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        diagnostics_thread: threading.Thread,
    ) -> None:
        self._process = process
        self._diagnostics_thread = diagnostics_thread
        self._source: BoundedFFmpegOpusAudio | None = None
        self.closed = False

    def bind_source(self, source: BoundedFFmpegOpusAudio) -> None:
        self._source = source

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        process = self._process
        process_error: BaseException | None = None
        try:
            if process.poll() is None:
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
            process.wait(timeout=FFMPEG_TERMINATE_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            process_error = RuntimeError("yt-dlp did not terminate within the cleanup deadline")
            process_error.__cause__ = exc
        finally:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        source = self._source
        writer = getattr(source, "_pipe_writer_thread", None) if source is not None else None
        for thread, label in (
            (writer, "FFmpeg pipe writer"),
            (self._diagnostics_thread, "yt-dlp diagnostic reader"),
        ):
            if thread is not None and thread.is_alive():
                thread.join(timeout=FFMPEG_TERMINATE_TIMEOUT)
            if thread is not None and thread.is_alive():
                raise RuntimeError(f"{label} did not stop within the cleanup deadline")
        if process_error is not None:
            raise process_error


class BoundedFFmpegOpusAudio(discord.FFmpegOpusAudio):
    """Bound cleanup and turn failed or premature FFmpeg EOF into playback errors."""

    def configure_monitoring(
        self,
        diagnostics: BoundedDiagnosticBuffer,
        *,
        expected_duration: float | None,
        start_at: float,
    ) -> None:
        self._charlotte_diagnostics = diagnostics
        self._charlotte_expected_duration = expected_duration
        self._charlotte_start_at = max(0.0, start_at)
        self._charlotte_packets_read = 0

    def read(self) -> bytes:
        packet = next(self._packet_iter, b"")
        if packet:
            self._charlotte_packets_read = getattr(self, "_charlotte_packets_read", 0) + 1
            return packet
        self._raise_for_failed_eof()
        return b""

    def _raise_for_failed_eof(self) -> None:
        process = getattr(self, "_process", None)
        returncode = None
        if process is not None and hasattr(process, "poll"):
            returncode = process.poll()
            if returncode is None:
                try:
                    returncode = process.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    returncode = process.poll()
        packets = getattr(self, "_charlotte_packets_read", 0)
        expected = getattr(self, "_charlotte_expected_duration", None)
        start_at = getattr(self, "_charlotte_start_at", 0.0)
        played = packets * 0.02
        premature = packets == 0
        if isinstance(expected, (int, float)) and expected > 0:
            tolerance = max(10.0, min(30.0, expected * 0.01))
            premature = premature or start_at + played < expected - tolerance
        if returncode not in (None, 0) or premature:
            diagnostics = getattr(self, "_charlotte_diagnostics", None)
            detail = diagnostics.tail() if diagnostics is not None else ""
            summary = f"FFmpeg exited with status {returncode}"
            if premature:
                summary += f" after {played:.2f}s of audio"
            if detail:
                summary += f": {detail[-4096:]}"
            raise PlaybackError(summary)

    def _kill_process(self) -> None:
        process = getattr(self, "_process", None)
        if process is None or not hasattr(process, "poll"):
            return
        try:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=FFMPEG_TERMINATE_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("FFmpeg did not terminate within the cleanup deadline") from exc
        except ProcessLookupError:
            return


async def extract(url: str, *, playlist: bool) -> dict[str, Any]:
    """Run yt-dlp behind a subprocess boundary that cancellation can terminate."""

    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--ignore-config",
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


def non_retryable_ytdlp_error(error: BaseException) -> bool:
    detail = str(error).lower().replace("\N{RIGHT SINGLE QUOTATION MARK}", "'")
    return any(
        marker in detail
        for marker in (
            "sign in to confirm you're not a bot",
            "private video",
            "members-only",
            "not available in your country",
            "not available in your region",
        )
    )


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


def stream_descriptor(info: Mapping[str, Any]) -> StreamDescriptor | None:
    url = info.get("url")
    if not isinstance(url, str) or not url:
        return None
    raw_headers = info.get("http_headers")
    headers = _sanitize_headers(raw_headers if isinstance(raw_headers, Mapping) else None)
    protocol = info.get("protocol")
    format_id = info.get("format_id")
    return StreamDescriptor(
        url=url,
        headers=headers,
        protocol=protocol if isinstance(protocol, str) else None,
        format_id=format_id if isinstance(format_id, str) else None,
        extracted_at=time.monotonic(),
    )


async def stream_audio(
    url: str,
    *,
    start_at: float = 0,
    headers: Mapping[str, str] | None = None,
    expected_duration: float | None = None,
) -> PreparedAudio:
    before = "-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    if start_at > 0:
        before = f"-ss {start_at:.3f} {before}"
    safe_headers = _sanitize_headers(headers)
    if safe_headers:
        header_block = "".join(f"{key}: {value}\r\n" for key, value in safe_headers.items())
        before = f"{before} -headers {shlex.quote(header_block)}"

    def create() -> tuple[BoundedFFmpegOpusAudio, Any]:
        stderr_sink = BoundedDiagnosticBuffer(
            secrets=(url, *safe_headers.values()),
        )
        try:
            source = BoundedFFmpegOpusAudio(
                url,
                before_options=before,
                options="-vn -c:a libopus -b:a 320k -ar 48000 -ac 2",
                stderr=stderr_sink,
            )
            source.configure_monitoring(
                stderr_sink,
                expected_duration=expected_duration,
                start_at=start_at,
            )
        except Exception:
            stderr_sink.close()
            raise
        return source, stderr_sink

    def cleanup_cancelled(result: tuple[BoundedFFmpegOpusAudio, Any]) -> None:
        source, stderr_sink = result
        try:
            source.cleanup()
        finally:
            stderr_sink.close()

    source, stderr_sink = await run_blocking(
        create,
        cleanup_cancelled_result=cleanup_cancelled,
    )
    return PreparedAudio(
        source=source,
        seekable=True,
        owned_resources=(stderr_sink,),
        confirm_first_packet=True,
    )


async def stream_ytdlp_audio(
    page_url: str,
    *,
    start_at: float = 0,
    expected_duration: float | None = None,
) -> PreparedAudio:
    """Stream yt-dlp stdout into FFmpeg without buffering the complete media."""

    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--ignore-config",
        "--quiet",
        "--no-warnings",
        "--no-cache-dir",
        "--no-playlist",
        "--format",
        "bestaudio/best",
        "--socket-timeout",
        str(YTDLP_SOCKET_TIMEOUT),
        "--js-runtimes",
        "node",
        "--output",
        "-",
        page_url,
    ]

    def create() -> tuple[BoundedFFmpegOpusAudio, _YtdlpPipeOwner, BoundedDiagnosticBuffer]:
        diagnostics = BoundedDiagnosticBuffer(secrets=(page_url,))
        process_kwargs: dict[str, Any] = {}
        if os.name == "posix":
            process_kwargs["start_new_session"] = True
        elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            process_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **process_kwargs,
        )
        if process.stdout is None or process.stderr is None:
            process.kill()
            process.wait(timeout=FFMPEG_TERMINATE_TIMEOUT)
            diagnostics.close()
            raise RuntimeError("yt-dlp did not expose streaming pipes")
        diagnostics_thread = threading.Thread(
            target=_drain_diagnostics,
            args=(process.stderr, diagnostics),
            daemon=True,
            name=f"yt-dlp-stderr-reader:pid-{process.pid}",
        )
        diagnostics_thread.start()
        owner = _YtdlpPipeOwner(process, diagnostics_thread)
        options = "-vn -c:a libopus -b:a 320k -ar 48000 -ac 2"
        if start_at > 0:
            options = f"-ss {start_at:.3f} {options}"
        try:
            source = BoundedFFmpegOpusAudio(
                process.stdout,
                pipe=True,
                before_options="-nostdin",
                options=options,
                stderr=diagnostics,
            )
            source.configure_monitoring(
                diagnostics,
                expected_duration=expected_duration,
                start_at=start_at,
            )
            owner.bind_source(source)
        except BaseException:
            owner.close()
            diagnostics.close()
            raise
        return source, owner, diagnostics

    def cleanup_cancelled(
        result: tuple[BoundedFFmpegOpusAudio, _YtdlpPipeOwner, BoundedDiagnosticBuffer],
    ) -> None:
        source, owner, diagnostics = result
        PreparedAudio(
            source=source,
            seekable=True,
            owned_resources=(owner, diagnostics),
        ).cleanup()

    source, owner, diagnostics = await run_blocking(
        create,
        cleanup_cancelled_result=cleanup_cancelled,
    )
    return PreparedAudio(
        source=source,
        seekable=True,
        owned_resources=(owner, diagnostics),
        confirm_first_packet=True,
    )


def _drain_diagnostics(stream: Any, destination: BoundedDiagnosticBuffer) -> None:
    try:
        while chunk := stream.read(4096):
            destination.write(chunk)
    except OSError, ValueError:
        return


def _sanitize_headers(headers: Mapping[Any, Any] | None) -> dict[str, str]:
    safe: dict[str, str] = {}
    for key, value in (headers or {}).items():
        if (
            isinstance(key, str)
            and isinstance(value, str)
            and re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key)
            and "\r" not in value
            and "\n" not in value
        ):
            safe[key] = value
    return safe
