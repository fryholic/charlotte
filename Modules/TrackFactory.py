"""TrackFactory orchestrates track source resolution across providers."""

from __future__ import annotations

from typing import Sequence, Type

from Modules.track_sources import TrackQuery, UploadPayload, sort_providers
from Modules.track_sources.base import BaseTrackSource, BaseUploadSource
from Modules.track_sources.providers import (
    SoundCloudSource,
    SpotifySource,
    YouTubeSearchFallback,
    YouTubeUrlSource,
)
from Modules.track_sources.providers.upload import UploadSource


class SourceResolutionError(Exception):
    """Raised when no provider can satisfy the current request."""


class TrackFactory:
    """High level façade that delegates to registered providers only."""

    _URL_PROVIDERS: Sequence[Type[BaseTrackSource]] = sort_providers(
        [SpotifySource, SoundCloudSource, YouTubeUrlSource, YouTubeSearchFallback]
    )
    _UPLOAD_PROVIDER: Type[BaseUploadSource] = UploadSource

    @classmethod
    def register_provider(cls, provider: Type[BaseTrackSource]):
        """Allow runtime extension (e.g. guild specific providers)."""

        cls._URL_PROVIDERS = sort_providers(list(cls._URL_PROVIDERS) + [provider])

    @classmethod
    async def identify_source(cls, query: str):
        track_query = TrackQuery(query)
        for provider in cls._URL_PROVIDERS:
            if not provider.supports(track_query):
                continue
            sources = await provider.create_tracks(track_query)
            if sources:
                return sources
        raise SourceResolutionError("지원되는 오디오 소스를 찾지 못했습니다")

    @classmethod
    async def from_url(cls, url: str, *, loop=None):  # loop 매개변수와의 하위 호환
        return await cls.identify_source(url)

    @classmethod
    async def from_upload(cls, file):
        payload = UploadPayload(file=file)
        return await cls._UPLOAD_PROVIDER.create_tracks(payload)
