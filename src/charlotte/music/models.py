"""Music value objects and explicit resource ownership."""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import discord


class TrackState(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    PLAYING = "playing"
    DISPOSED = "disposed"


@dataclass(frozen=True, slots=True)
class RequestContext:
    guild_id: int
    text_channel_id: int
    requester_id: int
    requester_display_name: str


@dataclass(slots=True)
class Track:
    provider: str
    title: str
    requester_id: int
    requester_display_name: str
    request_channel_id: int
    canonical_url: str | None = None
    duration: float | None = None
    provider_data: dict[str, Any] = field(default_factory=dict)
    owned_resource: io.BytesIO | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: TrackState = TrackState.QUEUED

    @property
    def upload_size(self) -> int:
        return int(self.provider_data.get("upload_size", 0))

    def dispose(self) -> None:
        if self.state is TrackState.DISPOSED:
            return
        self.state = TrackState.DISPOSED
        if self.owned_resource is not None and not self.owned_resource.closed:
            self.owned_resource.close()


@dataclass(slots=True)
class PreparedAudio:
    source: discord.AudioSource
    seekable: bool
    owned_resources: tuple[Any, ...] = ()
    _cleaned: bool = False

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        first_error: BaseException | None = None
        try:
            self.source.cleanup()
        except BaseException as exc:
            first_error = exc
        for resource in self.owned_resources:
            if not getattr(resource, "closed", True):
                try:
                    resource.close()
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
        if first_error is not None:
            raise first_error


@dataclass(frozen=True, slots=True)
class QueueItem:
    title: str
    requester: str
    duration: float | None
    paused: bool = False


@dataclass(frozen=True, slots=True)
class QueueView:
    current: QueueItem | None
    upcoming: list[QueueItem]


@dataclass(frozen=True, slots=True)
class AddResult:
    started: bool
    queued_position: int | None


@dataclass(frozen=True, slots=True)
class PlayCommitResult:
    add_result: AddResult
    moved: bool
    remote_move: bool
    removed_count: int


@dataclass(frozen=True, slots=True)
class SkipResult:
    skipped_title: str | None
    next_title: str | None


@dataclass(frozen=True, slots=True)
class StopResult:
    removed_count: int
