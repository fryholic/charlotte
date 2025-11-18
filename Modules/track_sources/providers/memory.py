"""Audio source backed by an in-memory buffer."""

from __future__ import annotations

import io
from typing import Dict

import discord
from mutagen import File as MutagenFile

from Modules.track_sources.config import FFMPEG_MEMORY_OPTIONS


class MemoryAudioSource(discord.FFmpegOpusAudio):
    def __init__(self, buffer: io.BytesIO, metadata: Dict, *, bitrate: int = 320):
        self.buffer = buffer
        self.metadata = metadata
        self.title = metadata.get("title", "Unknown")
        self._is_closed = False

        super().__init__(
            self.buffer,
            pipe=True,
            bitrate=bitrate,
            **FFMPEG_MEMORY_OPTIONS,
        )

    def _close_buffer(self):
        if not self._is_closed and hasattr(self, "buffer"):
            try:
                self.buffer.close()
                self._is_closed = True
            except Exception:
                pass

    def cleanup(self):
        try:
            super().cleanup()
        finally:
            self._close_buffer()

    @classmethod
    async def from_upload(cls, file):
        buffer = io.BytesIO(await file.read())
        buffer.seek(0)

        try:
            audio = await cls._extract_metadata(buffer)
            metadata = {
                "title": audio["title"],
                "artist": audio["artist"],
                "duration": audio["duration"],
            }
        except Exception:
            buffer.seek(0)
            metadata = {"title": getattr(file, "filename", "Unknown"), "artist": "Unknown", "duration": 0}

        return cls(buffer, metadata)

    @staticmethod
    async def _extract_metadata(buffer: io.BytesIO):
        loop = None
        try:
            import asyncio

            loop = asyncio.get_event_loop()
        except RuntimeError:
            pass

        def _load():
            snapshot = buffer.tell()
            buffer.seek(0)
            try:
                audio = MutagenFile(buffer)
            finally:
                buffer.seek(snapshot)
            if not audio:
                raise ValueError("Mutagen metadata unavailable")
            tags = getattr(audio, "tags", {}) or {}
            info = getattr(audio, "info", None)
            return {
                "title": tags.get("title", ["Unknown"])[0] if hasattr(tags, "get") else tags.get("TIT2", "Unknown"),
                "artist": tags.get("artist", ["Unknown"])[0] if hasattr(tags, "get") else tags.get("TPE1", "Unknown"),
                "duration": getattr(info, "length", 0),
            }

        if loop:
            return await loop.run_in_executor(None, _load)
        return _load()
