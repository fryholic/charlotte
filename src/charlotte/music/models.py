"""Music value objects and explicit resource ownership."""

from __future__ import annotations

import io
import threading
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
    max_upload_bytes: int = 0


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
    failure_retries: int = 0

    @property
    def upload_size(self) -> int:
        return int(self.provider_data.get("upload_size", 0))

    def dispose(self) -> None:
        if self.state is TrackState.DISPOSED:
            return
        self.state = TrackState.DISPOSED
        if self.owned_resource is not None and not self.owned_resource.closed:
            self.owned_resource.close()


class _CleanupController:
    """Run source and auxiliary cleanup exactly once across Discord and Charlotte."""

    def __init__(self, source: discord.AudioSource, owned_resources: tuple[Any, ...]) -> None:
        self.source = source
        self.owned_resources = owned_resources
        self._condition = threading.Condition()
        self._cleaning = False
        self._cleaned = False
        self._error: BaseException | None = None
        self._error_reported = False

    def cleanup(self, *, report_error: bool) -> None:
        owns_cleanup = False
        with self._condition:
            if not self._cleaning and not self._cleaned:
                self._cleaning = True
                owns_cleanup = True
            else:
                while not self._cleaned:
                    self._condition.wait()

        if owns_cleanup:
            first_error: BaseException | None = None
            try:
                self.source.cleanup()
            except BaseException as exc:
                first_error = exc
            for resource in self.owned_resources:
                try:
                    if not getattr(resource, "closed", True):
                        resource.close()
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
            with self._condition:
                self._error = first_error
                self._cleaning = False
                self._cleaned = True
                self._condition.notify_all()

        error: BaseException | None = None
        with self._condition:
            if report_error and self._error is not None and not self._error_reported:
                self._error_reported = True
                error = self._error
        if error is not None:
            raise error


class _ManagedAudioSource(discord.AudioSource):
    """AudioSource view whose Discord-owned cleanup is harmless and observable."""

    def __init__(self, controller: _CleanupController) -> None:
        self._controller = controller

    def read(self) -> bytes:
        return self._controller.source.read()

    def is_opus(self) -> bool:
        return self._controller.source.is_opus()

    def cleanup(self) -> None:
        # discord.py also calls AudioSource.cleanup() in its audio thread. The
        # player performs the reporting call via PreparedAudio.cleanup().
        self._controller.cleanup(report_error=False)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._controller.source, name)


@dataclass(slots=True)
class PreparedAudio:
    source: discord.AudioSource
    seekable: bool
    owned_resources: tuple[Any, ...] = ()
    memory_bytes: int = 0
    memory_reservation_id: str | None = None
    _cleanup_controller: _CleanupController = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._cleanup_controller = _CleanupController(self.source, self.owned_resources)
        self.source = _ManagedAudioSource(self._cleanup_controller)

    def cleanup(self) -> None:
        self._cleanup_controller.cleanup(report_error=True)


@dataclass(frozen=True, slots=True)
class UploadReservation:
    id: str
    size: int


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
