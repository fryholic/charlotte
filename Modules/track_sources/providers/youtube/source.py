"""YouTube source providers."""

from __future__ import annotations

import asyncio
from typing import List

import discord

from Modules.track_sources.base import BaseTrackSource, TrackQuery
from Modules.track_sources.config import FFMPEG_STREAM_OPTIONS
from Modules.track_sources.providers.youtube.ytdl_client import youtube_client


class _YouTubeAudioSource(discord.FFmpegOpusAudio):
    def __init__(self, source: str, *, data: dict):
        super().__init__(source, **FFMPEG_STREAM_OPTIONS)
        self.data = data
        self.title = data.get("title", "Unknown Title")
        self.url = data.get("url")


class YouTubeUrlSource(BaseTrackSource):
    name = "youtube:url"
    priority = 40

    @classmethod
    def supports(cls, query: TrackQuery) -> bool:
        normalized = query.normalized
        return "youtube.com/" in normalized or "youtu.be/" in normalized or normalized.startswith("ytsearch:")

    @classmethod
    async def create_tracks(cls, query: TrackQuery) -> List[discord.FFmpegOpusAudio]:
        ytdl = youtube_client()
        loop = asyncio.get_event_loop()

        def _extract():
            raw_query = query.raw if query.normalized.startswith("ytsearch:") else query.raw
            return ytdl.extract_info(raw_query, download=False)

        data = await loop.run_in_executor(None, _extract)
        if not data:
            return []

        entries = data.get("entries") if "entries" in data else [data]
        sources: list[_YouTubeAudioSource] = []
        for entry in entries:
            if not entry or "url" not in entry:
                continue
            sources.append(_YouTubeAudioSource(entry["url"], data=entry))
        return sources


class YouTubeSearchFallback(BaseTrackSource):
    name = "youtube:search"
    priority = 90

    @classmethod
    def supports(cls, query: TrackQuery) -> bool:
        return True

    @classmethod
    async def create_tracks(cls, query: TrackQuery):
        fallback_query = f"ytsearch:{query.raw}"
        return await YouTubeUrlSource.create_tracks(TrackQuery(fallback_query))
