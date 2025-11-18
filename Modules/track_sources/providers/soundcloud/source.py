"""SoundCloud source provider implementation."""

from __future__ import annotations

import asyncio
from typing import List

import discord

from Modules.track_sources.base import BaseTrackSource, TrackQuery
from Modules.track_sources.config import FFMPEG_STREAM_OPTIONS
from Modules.track_sources.providers.soundcloud.ytdl_client import soundcloud_client


class _SoundCloudAudioSource(discord.FFmpegOpusAudio):
    def __init__(self, source: str, *, data: dict):
        super().__init__(source, **FFMPEG_STREAM_OPTIONS)
        self.data = data
        self.title = data.get("title", "Unknown Title")
        self.url = data.get("url")
        self.artist = data.get("uploader") or data.get("creator")


class SoundCloudSource(BaseTrackSource):
    name = "soundcloud"
    priority = 30

    @classmethod
    def supports(cls, query: TrackQuery) -> bool:
        normalized = query.normalized
        return (
            "soundcloud.com/" in normalized
            or "snd.sc/" in normalized
            or normalized.startswith("soundcloud:")
            or normalized.startswith("soundcloud ")
            or normalized.startswith("scsearch:")
        )

    @classmethod
    async def create_tracks(cls, query: TrackQuery) -> List[discord.FFmpegOpusAudio]:
        ytdl = soundcloud_client()
        loop = asyncio.get_event_loop()

        def _extract():
            info_query = query.raw.strip()
            lower_query = info_query.lower()
            if not info_query.startswith("http"):
                if lower_query.startswith("scsearch:"):
                    info_query_to_use = info_query
                elif lower_query.startswith("soundcloud:"):
                    info_query_to_use = info_query.split(":", 1)[1].strip() or info_query
                elif lower_query.startswith("soundcloud "):
                    info_query_to_use = info_query.split(" ", 1)[1].strip() or info_query
                else:
                    info_query_to_use = f"scsearch1:{info_query}"
            else:
                info_query_to_use = info_query
            return ytdl.extract_info(info_query_to_use, download=False)

        data = await loop.run_in_executor(None, _extract)
        entries = _hydrate_entries(data)
        return [_SoundCloudAudioSource(entry["url"], data=entry) for entry in entries]


def _hydrate_entries(data):
    entries = []
    if not data:
        return entries

    raw_entries = data["entries"] if "entries" in data else [data]
    client = soundcloud_client()
    for entry in raw_entries:
        if not entry:
            continue
        if "url" not in entry and entry.get("webpage_url"):
            try:
                entry = client.extract_info(entry["webpage_url"], download=False)
            except Exception:
                continue
        if entry.get("url"):
            entries.append(entry)
    return entries
