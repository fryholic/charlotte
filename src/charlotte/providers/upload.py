"""Discord attachment provider with memory-only probing and playback."""

from __future__ import annotations

import asyncio
import io
import json
import os
from typing import Any
from urllib.parse import ParseResult

import discord
from mutagen import File as MutagenFile

from charlotte.constants import ATTACHMENT_READ_TIMEOUT
from charlotte.errors import PlaybackError, UserInputError
from charlotte.music.models import PreparedAudio, RequestContext, Track
from charlotte.providers.ytdlp_common import run_blocking


class UploadProvider:
    name = "upload"
    supports_upload = True

    def supports_url(self, parsed_url: ParseResult) -> bool:
        return False

    async def inspect_url(
        self, request: RequestContext, parsed_url: ParseResult, raw_url: str
    ) -> Track:
        raise UserInputError("music.play.invalid_url")

    async def inspect_upload(self, request: RequestContext, attachment: Any) -> Track:
        try:
            async with asyncio.timeout(ATTACHMENT_READ_TIMEOUT):
                raw = await attachment.read()
        except TimeoutError as exc:
            raise UserInputError("music.play.attachment_read_failed") from exc
        except Exception as exc:
            raise UserInputError("music.play.attachment_read_failed") from exc
        if not isinstance(raw, bytes) or not raw:
            raise UserInputError("music.play.invalid_attachment")

        try:
            probe = await _probe(raw)
        except Exception as exc:
            raise UserInputError("music.play.invalid_attachment") from exc
        title = await run_blocking(lambda: _metadata_title(raw))
        if not title:
            tags = probe.get("format", {}).get("tags", {})
            if isinstance(tags, dict) and isinstance(tags.get("title"), str):
                title = tags["title"].strip()
        filename = str(getattr(attachment, "filename", "Unknown audio"))
        title = title or filename
        duration = _duration(probe.get("format", {}).get("duration"))
        return Track(
            provider=self.name,
            title=title,
            requester_id=request.requester_id,
            requester_display_name=request.requester_display_name,
            request_channel_id=request.text_channel_id,
            duration=duration,
            owned_resource=io.BytesIO(raw),
            provider_data={
                "filename": filename,
                "content_type_hint": getattr(attachment, "content_type", None),
                "upload_size": len(raw),
            },
        )

    async def prepare(self, track: Track, *, start_at: float = 0) -> PreparedAudio:
        original = track.owned_resource
        if original is None or original.closed:
            raise PlaybackError("Upload buffer is unavailable")
        playback_buffer = io.BytesIO(original.getvalue())

        def create() -> tuple[discord.FFmpegOpusAudio, Any]:
            options = "-vn -c:a libopus -b:a 320k -ar 48000 -ac 2"
            if start_at > 0:
                options = f"-ss {start_at:.3f} {options}"
            stderr_sink = open(os.devnull, "wb")
            try:
                source = discord.FFmpegOpusAudio(
                    playback_buffer,
                    pipe=True,
                    before_options="-nostdin",
                    options=options,
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
                playback_buffer.close()

        try:
            source, stderr_sink = await run_blocking(
                create, cleanup_cancelled_result=cleanup_cancelled
            )
        except BaseException:
            playback_buffer.close()
            raise
        return PreparedAudio(
            source=source,
            seekable=True,
            owned_resources=(playback_buffer, stderr_sink),
        )


async def _probe(raw: bytes) -> dict[str, Any]:
    process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-i",
        "pipe:0",
        "-show_entries",
        "stream=codec_type:format=duration:format_tags=title",
        "-of",
        "json",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await process.communicate(raw)
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise
    if process.returncode != 0:
        raise ValueError("ffprobe rejected attachment bytes")
    parsed = json.loads(stdout.decode("utf-8", errors="replace"))
    if not isinstance(parsed, dict) or not isinstance(parsed.get("format"), dict):
        raise ValueError("ffprobe returned no audio format")
    streams = parsed.get("streams")
    if not isinstance(streams, list) or not any(
        isinstance(stream, dict) and stream.get("codec_type") == "audio" for stream in streams
    ):
        raise ValueError("ffprobe found no audio stream")
    return parsed


def _metadata_title(raw: bytes) -> str | None:
    try:
        media = MutagenFile(io.BytesIO(raw), easy=True)
        if media is None or not media.tags:
            return None
        values = media.tags.get("title")
        if values and isinstance(values[0], str) and values[0].strip():
            return values[0].strip()
    except Exception:
        return None
    return None


def _duration(value: object) -> float | None:
    try:
        duration = float(value)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return None
    return duration if duration >= 0 else None
