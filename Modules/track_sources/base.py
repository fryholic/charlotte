"""Core abstractions shared by TrackFactory source providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import discord


@dataclass(frozen=True)
class TrackQuery:
    """Simple value object that normalises the user supplied query string."""

    raw: str

    @property
    def normalized(self) -> str:
        return self.raw.strip().lower()


@dataclass(frozen=True)
class UploadPayload:
    """Container for upload based sources (e.g. Discord attachments)."""

    file: Any

    @property
    def filename(self) -> str:
        return getattr(self.file, "filename", "Unknown")


class BaseTrackSource(ABC):
    """Base class for URL/query driven providers."""

    name: str = "base"
    priority: int = 100

    @classmethod
    @abstractmethod
    def supports(cls, query: TrackQuery) -> bool:
        """Return True when this provider can handle the current query."""

    @classmethod
    @abstractmethod
    async def create_tracks(cls, query: TrackQuery) -> list[discord.AudioSource]:
        """Resolve the query into playable Discord audio sources."""


class BaseUploadSource(ABC):
    """Base class for upload/file driven providers."""

    name: str = "upload"

    @classmethod
    @abstractmethod
    async def create_tracks(cls, payload: UploadPayload) -> list[discord.AudioSource]:
        """Convert the uploaded payload into Discord audio sources."""


def sort_providers(providers: Iterable[type[BaseTrackSource]]) -> list[type[BaseTrackSource]]:
    """Utility helper to sort providers by priority (lower wins)."""

    return sorted(providers, key=lambda provider: getattr(provider, "priority", 100))
