"""Spotify source provider implementation."""

from __future__ import annotations

import discord

from Modules.track_sources.base import BaseTrackSource, TrackQuery
from Modules.track_sources.providers.memory import MemoryAudioSource
from Modules.track_sources.providers.spotify.utils import (
    SpotifyDownloadError,
    download_spotify_to_buffer,
)
from Modules.track_sources.providers.spotify.getMetadata import (
    SpotifyInvalidUrlException,
    parse_uri,
)


class SpotifySource(BaseTrackSource):
    name = "spotify"
    priority = 10

    @classmethod
    def supports(cls, query: TrackQuery) -> bool:
        normalized = query.normalized
        return "spotify.com/" in normalized or normalized.startswith("spotify:")

    @classmethod
    async def create_tracks(cls, query: TrackQuery) -> list[discord.AudioSource]:
        try:
            url_info = parse_uri(query.raw)
        except SpotifyInvalidUrlException as exc:
            raise ValueError(str(exc)) from exc

        if url_info["type"] not in {"track", "album", "playlist"}:
            raise ValueError("지원되지 않는 Spotify URL 입니다")

        try:
            buffer, metadata = await download_spotify_to_buffer(query.raw)
        except SpotifyDownloadError as exc:
            raise ValueError(f"Spotify 트랙 다운로드 실패: {exc}") from exc

        return [MemoryAudioSource(buffer, metadata)]
