"""One independently locked voice player per Discord guild."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

import discord

from charlotte.config import AppConfig
from charlotte.constants import (
    PROVIDER_OPERATION_TIMEOUT,
    SHUTDOWN_DETACHED_CLEANUP_TIMEOUT,
    SHUTDOWN_VOICE_TIMEOUT,
    VOICE_OPERATION_TIMEOUT,
    VOICE_RECONNECT_ATTEMPTS,
    VOICE_RECONNECT_WINDOW,
)
from charlotte.errors import (
    AccessDeniedError,
    PlaybackError,
    QueueLimitError,
    ResourceCleanupError,
)
from charlotte.messages import render, truncate
from charlotte.music.models import (
    AddResult,
    PlayCommitResult,
    PreparedAudio,
    QueueItem,
    QueueView,
    SkipResult,
    StopResult,
    Track,
    TrackState,
    UploadReservation,
)
from charlotte.music.provider import ProviderRegistry, observe_detached_work
from charlotte.observability import ErrorContext, ErrorReporter, log_exception

type _CleanupBundle = tuple[
    Track | None,
    PreparedAudio | None,
    list[Track],
    PreparedAudio | None,
]


class GuildPlayer:
    def __init__(
        self,
        guild_id: int,
        *,
        bot: discord.Client,
        config: AppConfig,
        providers: ProviderRegistry,
        reporter: ErrorReporter,
    ) -> None:
        self.guild_id = guild_id
        self.bot = bot
        self.config = config
        self.providers = providers
        self.reporter = reporter
        self.voice_client: discord.VoiceClient | None = None
        self.current: Track | None = None
        self.current_prepared: PreparedAudio | None = None
        self.queue: deque[Track] = deque()
        self.prepared_next_track_id: str | None = None
        self.prepared_next: PreparedAudio | None = None
        self.lock = asyncio.Lock()
        self._connection_lock = asyncio.Lock()
        self._advance_lock = asyncio.Lock()
        self._receipt_condition = asyncio.Condition()
        self._issued_receipt = 0
        self._next_receipt = 0
        self._cancelled_receipts: set[int] = set()
        self._generation = 0
        self._closed = False
        self._paused = False
        self._started_at = 0.0
        self._pause_started_at: float | None = None
        self._paused_total = 0.0
        self._prefetch_task: asyncio.Task[None] | None = None
        self._current_prepare_task: asyncio.Task[bool] | None = None
        self._detached_source_cleanup: dict[asyncio.Future[None], str | None] = {}
        self._detached_upload_work: dict[asyncio.Future[None], str | None] = {}
        self._prepared_cleanup_tasks: dict[asyncio.Task[None], tuple[str, str | None]] = {}
        self._source_cleanup_failure: ResourceCleanupError | None = None
        self._upload_reservations: dict[str, int] = {}
        self._deferred_upload_reservations: set[str] = set()
        self._prepared_memory_reservations: dict[str, int] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._report_tasks: set[asyncio.Task[Any]] = set()
        self.log = logging.getLogger(f"charlotte.player.{guild_id}")

    @property
    def bot_channel(self) -> discord.abc.Connectable | None:
        voice = self._active_voice_client()
        return getattr(voice, "channel", None)

    @property
    def has_activity(self) -> bool:
        return self.current is not None or bool(self.queue)

    @property
    def is_paused(self) -> bool:
        return self._paused

    def issue_receipt(self) -> int:
        receipt = self._issued_receipt
        self._issued_receipt += 1
        return receipt

    async def reserve_upload(self, declared_size: object) -> UploadReservation | None:
        """Atomically reserve configured per-guild upload memory before reading bytes."""

        limit = self.config.max_queued_upload_bytes
        if not limit:
            return None
        if not isinstance(declared_size, int) or declared_size < 0:
            raise QueueLimitError()
        async with self.lock:
            if self._closed:
                raise PlaybackError("Player is closed")
            if self._source_cleanup_failure is not None:
                raise self._source_cleanup_failure
            used = self._total_upload_bytes_locked()
            if used + declared_size > limit:
                raise QueueLimitError()
            reservation = UploadReservation(uuid.uuid4().hex, declared_size)
            self._upload_reservations[reservation.id] = reservation.size
            return reservation

    async def adjust_upload_reservation(
        self, reservation: UploadReservation | None, actual_size: int
    ) -> None:
        if reservation is None:
            return
        if actual_size < 0:
            raise QueueLimitError()
        async with self.lock:
            if reservation.id not in self._upload_reservations:
                raise PlaybackError("Upload reservation is no longer active")
            reserved_elsewhere = sum(
                size
                for reservation_id, size in self._upload_reservations.items()
                if reservation_id != reservation.id
            )
            if (
                self._owned_upload_bytes_locked()
                + sum(self._prepared_memory_reservations.values())
                + reserved_elsewhere
                + actual_size
                > self.config.max_queued_upload_bytes
            ):
                raise QueueLimitError()
            self._upload_reservations[reservation.id] = actual_size

    async def release_upload_reservation(self, reservation: UploadReservation | None) -> None:
        if reservation is None:
            return
        async with self.lock:
            if reservation.id in self._deferred_upload_reservations:
                return
            self._upload_reservations.pop(reservation.id, None)

    @contextmanager
    def observe_upload_work(self, reservation: UploadReservation | None):
        """Keep cancelled upload inspection memory reserved until its worker exits."""

        def observe(completion: asyncio.Future[None]) -> None:
            reservation_id = reservation.id if reservation is not None else None
            self._detached_upload_work[completion] = reservation_id
            if reservation_id is not None:
                self._deferred_upload_reservations.add(reservation_id)
            completion.add_done_callback(self._detached_upload_work_done)

        with observe_detached_work(observe):
            yield

    async def wait_for_receipt(self, receipt: int) -> None:
        async with self._receipt_condition:
            await self._receipt_condition.wait_for(lambda: receipt == self._next_receipt)

    async def finish_receipt(self, receipt: int) -> None:
        async with self._receipt_condition:
            if receipt != self._next_receipt:
                raise RuntimeError("Music receipt finished out of order")
            self._next_receipt += 1
            self._advance_cancelled_receipts()
            self._receipt_condition.notify_all()

    async def cancel_receipt(self, receipt: int) -> None:
        async with self._receipt_condition:
            if receipt < self._next_receipt:
                return
            self._cancelled_receipts.add(receipt)
            self._advance_cancelled_receipts()
            self._receipt_condition.notify_all()

    def _advance_cancelled_receipts(self) -> None:
        while self._next_receipt in self._cancelled_receipts:
            self._cancelled_receipts.remove(self._next_receipt)
            self._next_receipt += 1

    async def connect(self, channel: discord.abc.Connectable) -> bool:
        """Connect or move to channel. Return True only when a move occurred."""

        async with self._connection_lock:
            return await self._connect_locked(channel)

    async def _connect_locked(self, channel: discord.abc.Connectable) -> bool:
        guild_voice = getattr(getattr(channel, "guild", None), "voice_client", None)
        if guild_voice is not None and guild_voice.is_connected():
            self.voice_client = guild_voice
        voice = self._active_voice_client()
        if voice is None:
            self.log.info(
                "Voice connecting",
                extra={"event": "voice.connecting", "channel_id": channel.id},
            )
            async with asyncio.timeout(VOICE_OPERATION_TIMEOUT):
                self.voice_client = await channel.connect(
                    timeout=VOICE_OPERATION_TIMEOUT, reconnect=True
                )
            self.log.info(
                "Voice connected",
                extra={"event": "voice.connected", "channel_id": channel.id},
            )
            return False
        if voice.channel != channel:
            self.log.info(
                "Voice moving",
                extra={"event": "voice.moving", "channel_id": channel.id},
            )
            async with asyncio.timeout(VOICE_OPERATION_TIMEOUT):
                await voice.move_to(channel)
            self.log.info(
                "Voice moved",
                extra={"event": "voice.connected", "channel_id": channel.id},
            )
            return True
        return False

    async def add(self, track: Track) -> AddResult:
        accepted = await self._accept_track(track)
        return await self._finish_add(track, *accepted)

    async def commit_play(
        self,
        track: Track,
        channel: discord.abc.Connectable,
        *,
        access_check: Callable[[discord.abc.Connectable | None], bool],
        upload_reservation: UploadReservation | None = None,
    ) -> PlayCommitResult:
        async with self._connection_lock:
            self._assert_access_locked(access_check)
            previous_channel = self.bot_channel
            had_voice = previous_channel is not None
            remote_move = previous_channel is not None and previous_channel != channel
            removed_count = 0
            if remote_move:
                async with self.lock:
                    self._assert_access_locked(access_check)
                    stopped = await self._stop_locked()
                    assert stopped is not None
                    removed_count = stopped.removed_count
            moved = await self._connect_locked(channel)
            try:
                accepted = await self._accept_track(
                    track,
                    access_check=access_check,
                    upload_reservation=upload_reservation,
                )
            except BaseException:
                if not had_voice or remote_move:
                    voice = self._active_voice_client()
                    try:
                        if voice is not None:
                            async with asyncio.timeout(VOICE_OPERATION_TIMEOUT):
                                await voice.disconnect(force=True)
                    except Exception as exc:
                        log_exception(
                            self.log,
                            exc,
                            event="music.play.rollback_disconnect_failed",
                            context={"guild_id": self.guild_id},
                        )
                    finally:
                        self.voice_client = None
                raise
        add_result = await self._finish_add(track, *accepted)
        return PlayCommitResult(
            add_result=add_result,
            moved=moved,
            remote_move=remote_move,
            removed_count=removed_count,
        )

    async def _accept_track(
        self,
        track: Track,
        *,
        access_check: Callable[[discord.abc.Connectable | None], bool] | None = None,
        upload_reservation: UploadReservation | None = None,
    ) -> tuple[bool, bool, int, int | None]:
        start_track = False
        resume_preserved_queue = False
        generation = 0
        async with self.lock:
            self._assert_access_locked(access_check)
            if self._closed:
                raise PlaybackError("Player is closed")
            if self._source_cleanup_failure is not None:
                raise self._source_cleanup_failure
            self.providers.ensure_available(track.provider)
            self._enforce_limits(track, upload_reservation=upload_reservation)
            if self.current is None and not self.queue:
                self.current = track
                track.state = TrackState.PREPARING
                self._generation += 1
                generation = self._generation
                start_track = True
                position = None
            else:
                track.state = TrackState.QUEUED
                self.queue.append(track)
                position = len(self.queue)
                resume_preserved_queue = self.current is None
                self._ensure_prefetch_locked()
            if upload_reservation is not None:
                self._upload_reservations.pop(upload_reservation.id)
                self._deferred_upload_reservations.discard(upload_reservation.id)
        return start_track, resume_preserved_queue, generation, position

    async def _finish_add(
        self,
        track: Track,
        start_track: bool,
        resume_preserved_queue: bool,
        generation: int,
        position: int | None,
    ) -> AddResult:
        self.log.info(
            "Track accepted",
            extra={
                "event": "track.preparing" if start_track else "track.enqueued",
                "track_id": track.id,
                "provider": track.provider,
                "queue_position": position,
            },
        )
        if start_track:
            try:
                started = await self._prepare_current(track, generation, attempts=2)
            except asyncio.CancelledError:
                await self._discard_current_preserve_queue(track, generation)
                raise
            return AddResult(started=started, queued_position=None)
        if resume_preserved_queue and self._active_voice_client() is not None:
            self._spawn(self._advance())
        return AddResult(started=False, queued_position=position)

    async def skip(
        self, *, access_check: Callable[[discord.abc.Connectable | None], bool] | None = None
    ) -> SkipResult:
        async with self._connection_lock:
            async with self.lock:
                self._assert_access_locked(access_check)
                track = self.current
                if track is None:
                    return SkipResult(None, None)
                await self._cancel_current_prepare_locked()
                await self._cancel_prefetch_locked(track_id=track.id)
                prepared = self.current_prepared
                self.current = None
                self.current_prepared = None
                self._paused = False
                self._generation += 1
                voice = self._active_voice_client()
                next_title = self.queue[0].title if self.queue else None
                if voice is not None and (voice.is_playing() or voice.is_paused()):
                    voice.stop()
        cleanup_error: ResourceCleanupError | None = None
        try:
            if prepared is not None:
                await self._safe_cleanup_prepared(prepared, "music.skip.cleanup_failed")
        except ResourceCleanupError as exc:
            cleanup_error = exc
        finally:
            self._safe_dispose_track(track, "music.skip.dispose_failed")
        if cleanup_error is not None:
            raise cleanup_error
        self.log.info(
            "Track skipped",
            extra={"event": "track.skipped", "track_id": track.id},
        )
        await self._advance()
        return SkipResult(track.title, next_title)

    async def pause(
        self, *, access_check: Callable[[discord.abc.Connectable | None], bool] | None = None
    ) -> Track | None:
        async with self._connection_lock:
            async with self.lock:
                self._assert_access_locked(access_check)
                if self.current is None:
                    return None
                if self._paused:
                    return self.current
                voice = self._active_voice_client()
                if voice is None or not voice.is_playing():
                    return None
                voice.pause()
                self._paused = True
                self._pause_started_at = asyncio.get_running_loop().time()
                self.log.info(
                    "Track paused",
                    extra={"event": "track.paused", "track_id": self.current.id},
                )
                return self.current

    async def resume(
        self, *, access_check: Callable[[discord.abc.Connectable | None], bool] | None = None
    ) -> Track | None:
        async with self._connection_lock:
            async with self.lock:
                self._assert_access_locked(access_check)
                if self.current is None or not self._paused:
                    return None
                voice = self._active_voice_client()
                if voice is None or not voice.is_paused():
                    self._paused = False
                    self._pause_started_at = None
                    return None
                voice.resume()
                now = asyncio.get_running_loop().time()
                if self._pause_started_at is not None:
                    self._paused_total += now - self._pause_started_at
                self._pause_started_at = None
                self._paused = False
                self.log.info(
                    "Track resumed",
                    extra={"event": "track.resumed", "track_id": self.current.id},
                )
                return self.current

    async def stop(
        self, *, access_check: Callable[[discord.abc.Connectable | None], bool] | None = None
    ) -> StopResult:
        async with self._connection_lock:
            async with self.lock:
                self._assert_access_locked(access_check)
                result = await self._stop_locked()
                assert result is not None
        self.log.info(
            "Queue cleared",
            extra={
                "event": "queue.cleared",
                "removed_count": result.removed_count,
            },
        )
        return result

    async def leave(
        self,
        *,
        access_check: Callable[[discord.abc.Connectable | None], bool] | None = None,
        _voice_timeout: float = VOICE_OPERATION_TIMEOUT,
    ) -> tuple[str | None, StopResult]:
        async with self._connection_lock:
            channel = self.bot_channel
            async with self.lock:
                self._assert_access_locked(access_check)
                result = await self._stop_locked()
                assert result is not None
            voice = self._active_voice_client()
            if voice is not None:
                async with asyncio.timeout(_voice_timeout):
                    await voice.disconnect(force=True)
            self.voice_client = None
        self.log.info(
            "Voice disconnected",
            extra={"event": "voice.disconnected", "channel_id": getattr(channel, "id", None)},
        )
        return getattr(channel, "name", None), result

    async def leave_if_empty(self, expected_channel: discord.abc.Connectable) -> bool:
        """Disconnect only if the same connected channel is still empty of humans."""

        deferred_cleanup: list[_CleanupBundle] = []
        async with self._connection_lock:
            voice = self._active_voice_client()
            channel = getattr(voice, "channel", None)
            if channel is not expected_channel:
                return False
            async with self.lock:
                voice = self._active_voice_client()
                channel = getattr(voice, "channel", None)
                members = getattr(channel, "members", ())
                if channel is not expected_channel or any(
                    not getattr(member, "bot", False) for member in members
                ):
                    return False
                result = await self._stop_locked(
                    still_valid=lambda: (
                        getattr(self._active_voice_client(), "channel", None) is expected_channel
                        and not any(
                            not getattr(member, "bot", False)
                            for member in getattr(expected_channel, "members", ())
                        )
                    ),
                    deferred_cleanup=deferred_cleanup,
                )
                if result is None:
                    return False
            voice = self._active_voice_client()
            if voice is None or getattr(voice, "channel", None) is not expected_channel:
                return False
            try:
                async with asyncio.timeout(VOICE_OPERATION_TIMEOUT):
                    await voice.disconnect(force=True)
                self.voice_client = None
            finally:
                for cleanup_bundle in deferred_cleanup:
                    await self._cleanup_detached(*cleanup_bundle)
        self.log.info(
            "Voice disconnected from empty channel",
            extra={
                "event": "voice.empty_disconnected",
                "channel_id": getattr(expected_channel, "id", None),
                "removed_count": result.removed_count,
            },
        )
        return True

    async def queue_view(self) -> QueueView:
        async with self.lock:
            current = (
                QueueItem(
                    title=self.current.title,
                    requester=self.current.requester_display_name,
                    duration=self.current.duration,
                    paused=self._paused,
                )
                if self.current is not None
                else None
            )
            limit = 4 if current is not None else 5
            upcoming = [
                QueueItem(
                    title=track.title,
                    requester=track.requester_display_name,
                    duration=track.duration,
                )
                for track in list(self.queue)[:limit]
            ]
        return QueueView(current=current, upcoming=upcoming)

    async def close(self) -> None:
        async with self.lock:
            if self._closed:
                return
            self._closed = True
        try:
            await self.leave(_voice_timeout=SHUTDOWN_VOICE_TIMEOUT)
        except Exception as exc:
            log_exception(
                self.log,
                exc,
                event="music.shutdown.leave_failed",
                context={"guild_id": self.guild_id},
            )
        finally:
            tasks = [task for task in self._tasks if task is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException) and not isinstance(
                        result, asyncio.CancelledError
                    ):
                        log_exception(
                            self.log,
                            result,
                            event="music.shutdown.task_failed",
                            context={"guild_id": self.guild_id},
                        )
            report_tasks = list(self._report_tasks)
            for task in report_tasks:
                task.cancel()
            if report_tasks:
                await asyncio.gather(*report_tasks, return_exceptions=True)
            await self._drain_source_cleanup()

    async def recover_voice(
        self, channel: discord.abc.Connectable, *, expected_track_id: str | None
    ) -> None:
        last_error: BaseException | None = None
        notification: str | None = None
        report_error = False
        advance_queue = False
        async with self._connection_lock:
            if self._active_voice_client() is not None:
                return
            async with self.lock:
                track = self.current
                queue_only = track is None
                if self._closed:
                    return
                if queue_only:
                    if expected_track_id is not None or not self.queue:
                        return
                    track = self.queue[0]
                    preparing = False
                    previous = None
                    seekable = False
                    offset = 0.0
                    was_paused = False
                else:
                    if track.id != expected_track_id:
                        return
                    preparing = track.state is TrackState.PREPARING
                    await self._cancel_current_prepare_locked()
                    await self._cancel_prefetch_locked(track_id=track.id)
                    previous = self.current_prepared
                    seekable = previous.seekable if previous is not None else False
                    offset = self._playback_offset_locked() if previous is not None else 0.0
                    was_paused = self._paused
                    self.current_prepared = None
                    track.state = TrackState.PREPARING
                self.voice_client = None
                self._generation += 1
                generation = self._generation
            if previous is not None:
                await self._safe_cleanup_prepared(previous, "music.recovery.cleanup_failed")

            loop = asyncio.get_running_loop()
            deadline = loop.time() + VOICE_RECONNECT_WINDOW
            candidate: discord.VoiceClient | None = None
            for _ in range(VOICE_RECONNECT_ATTEMPTS):
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    async with asyncio.timeout(remaining):
                        candidate = await channel.connect(
                            timeout=min(5.0, remaining), reconnect=False
                        )
                    break
                except Exception as exc:
                    last_error = exc

            async with self.lock:
                if queue_only:
                    valid = (
                        self.current is None
                        and bool(self.queue)
                        and self.queue[0] is track
                        and self._generation == generation
                        and not self._closed
                    )
                else:
                    valid = (
                        self.current is track
                        and self._generation == generation
                        and not self._closed
                    )
            if not valid:
                if candidate is not None:
                    try:
                        async with asyncio.timeout(VOICE_OPERATION_TIMEOUT):
                            await candidate.disconnect(force=True)
                    except Exception as exc:
                        log_exception(self.log, exc, event="music.recovery.stale_disconnect_failed")
                return
            self.voice_client = candidate

            if self._active_voice_client() is None:
                if not queue_only:
                    await self._discard_current_preserve_queue(track, generation)
                notification = render("music.voice.reconnect_failed")
                report_error = last_error is not None
            elif queue_only:
                notification = render("music.voice.reconnected")
                advance_queue = True
            elif preparing and previous is None:
                started = await self._prepare_current(track, generation, attempts=1)
                if started:
                    notification = render("music.voice.reconnected")
            elif not seekable:
                await self._discard_current_preserve_queue(track, generation)
                notification = render(
                    "music.voice.reconnect_unseekable", title=self._safe_title(track.title)
                )
                await self._advance()
            else:
                started = await self._prepare_current(
                    track, generation, attempts=1, start_at=offset
                )
                if started:
                    if was_paused:
                        await self._restore_pause(track, generation)
                    notification = render("music.voice.reconnected")

            if advance_queue:
                await self._advance()

        if notification is not None:
            await self._notify(track.request_channel_id, notification)
        if report_error and last_error is not None:
            await self._report(last_error, "music.voice.reconnect_failed", track)

    async def _prepare_current(
        self,
        track: Track,
        generation: int,
        *,
        attempts: int,
        start_at: float = 0,
    ) -> bool:
        async with self.lock:
            if self.current is not track or self._generation != generation or self._closed:
                return False
            task = self._spawn(
                self._prepare_and_play(
                    track,
                    generation,
                    attempts=attempts,
                    start_at=start_at,
                )
            )
            self._current_prepare_task = task
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            if task.cancelled() and (caller is None or caller.cancelling() == 0):
                return False
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        finally:
            if self._current_prepare_task is task:
                self._current_prepare_task = None

    async def _prepare_and_play(
        self,
        track: Track,
        generation: int,
        *,
        attempts: int,
        start_at: float = 0,
    ) -> bool:
        last_error: BaseException | None = None
        for attempt in range(attempts):
            prepared: PreparedAudio | None = None
            try:
                prepared = await self._prepare_audio(track, start_at=start_at)
                if await self._play_prepared(track, generation, prepared, start_at=start_at):
                    return True
                await self._safe_cleanup_prepared(prepared, "music.prepare.stale_cleanup_failed")
                return False
            except asyncio.CancelledError:
                if prepared is not None:
                    await self._safe_cleanup_prepared(
                        prepared, "music.prepare.cancel_cleanup_failed"
                    )
                raise
            except ResourceCleanupError as exc:
                last_error = exc
                break
            except Exception as exc:
                last_error = exc
            if prepared is not None:
                try:
                    await self._safe_cleanup_prepared(prepared, "music.prepare.cleanup_failed")
                except ResourceCleanupError as exc:
                    last_error = exc
                    break
            if attempt + 1 >= attempts or track.failure_retries >= 1:
                break
            track.failure_retries += 1
        if last_error is None:
            return False
        await self._fail_current(track, generation, last_error)
        return False

    async def _play_prepared(
        self,
        track: Track,
        generation: int,
        prepared: PreparedAudio,
        *,
        start_at: float = 0,
    ) -> bool:
        loop = asyncio.get_running_loop()
        async with self.lock:
            if self.current is not track or self._generation != generation or self._closed:
                return False
            if self._source_cleanup_failure is not None:
                raise self._source_cleanup_failure
            voice = self._active_voice_client()
            if voice is None:
                raise PlaybackError("Voice client is not connected")

            def after(error: Exception | None) -> None:
                try:
                    loop.call_soon_threadsafe(self._schedule_finished, track.id, generation, error)
                except RuntimeError as schedule_error:
                    log_exception(
                        self.log,
                        schedule_error,
                        event="music.callback.schedule_failed",
                    )

            voice.play(prepared.source, after=after)
            self.current_prepared = prepared
            track.state = TrackState.PLAYING
            self._paused = False
            self._started_at = loop.time() - start_at
            self._paused_total = 0.0
            self._pause_started_at = None
            self._ensure_prefetch_locked()
            self.log.info(
                "Track started",
                extra={
                    "event": "track.started",
                    "track_id": track.id,
                    "provider": track.provider,
                },
            )
            return True

    async def _restore_pause(self, track: Track, generation: int) -> None:
        async with self.lock:
            if self.current is not track or self._generation != generation or self._closed:
                return
            voice = self._active_voice_client()
            if voice is None or not voice.is_playing():
                return
            voice.pause()
            self._paused = True
            self._pause_started_at = asyncio.get_running_loop().time()

    def _schedule_finished(self, track_id: str, generation: int, error: Exception | None) -> None:
        self._spawn(self._on_finished(track_id, generation, error))

    async def _on_finished(self, track_id: str, generation: int, error: Exception | None) -> None:
        retry_generation: int | None = None
        async with self.lock:
            if (
                self.current is None
                or self.current.id != track_id
                or self._generation != generation
            ):
                return
            track = self.current
            prepared = self.current_prepared
            self.current_prepared = None
            self._paused = False
            self._generation += 1
            if error is not None and track.failure_retries < 1 and not self._closed:
                track.failure_retries += 1
                track.state = TrackState.PREPARING
                retry_generation = self._generation
            else:
                self.current = None
        cleanup_error: ResourceCleanupError | None = None
        try:
            if prepared is not None:
                await self._safe_cleanup_prepared(prepared, "music.finished.cleanup_failed")
        except ResourceCleanupError as exc:
            cleanup_error = exc
        if retry_generation is not None:
            if cleanup_error is not None:
                raise cleanup_error
            self.log.warning(
                "Playback failed; retrying the same track once",
                extra={
                    "event": "music.playback.retrying",
                    "track_id": track.id,
                    "exception_type": type(error).__name__,
                },
            )
            await self._prepare_current(track, retry_generation, attempts=1)
            return
        self._safe_dispose_track(track, "music.finished.dispose_failed")
        if cleanup_error is not None:
            raise cleanup_error
        if error is not None:
            await self._notify(
                track.request_channel_id,
                render("music.play.retry_exhausted", title=self._safe_title(track.title)),
            )
            await self._report(error, "music.playback.failed", track)
        else:
            self.log.info(
                "Track finished",
                extra={"event": "track.finished", "track_id": track.id},
            )
        await self._advance()

    async def _advance(self) -> None:
        async with self._advance_lock:
            await self._advance_serialized()

    async def _advance_serialized(self) -> None:
        stale_prepared: PreparedAudio | None = None
        prefetch_task: asyncio.Task[Any] | None = None
        async with self.lock:
            if (
                self._closed
                or self.current is not None
                or not self.queue
                or self._active_voice_client() is None
                or self._source_cleanup_failure is not None
            ):
                return
            track = self.queue.popleft()
            track.state = TrackState.PREPARING
            self.current = track
            self._generation += 1
            generation = self._generation
            if self.prepared_next_track_id == track.id:
                prepared = self.prepared_next
                if prepared is None:
                    prefetch_task = self._prefetch_task
            else:
                prepared = None
                stale_prepared = self.prepared_next
            if prepared is not None or prefetch_task is None:
                self.prepared_next = None
                self.prepared_next_track_id = None
                self._prefetch_task = None
        if stale_prepared is not None:
            await self._safe_cleanup_prepared(stale_prepared, "music.prefetch.stale_cleanup_failed")
        if prefetch_task is not None:
            await asyncio.gather(prefetch_task, return_exceptions=True)
            async with self.lock:
                if self.current is not track or self._generation != generation:
                    return
                if self.prepared_next_track_id == track.id:
                    prepared = self.prepared_next
                    self.prepared_next = None
                    self.prepared_next_track_id = None
                self._prefetch_task = None
        if prepared is not None:
            try:
                if await self._play_prepared(track, generation, prepared):
                    return
            except Exception as exc:
                try:
                    await self._safe_cleanup_prepared(
                        prepared, "music.prefetch.play_cleanup_failed"
                    )
                except ResourceCleanupError as cleanup_error:
                    await self._fail_current(track, generation, cleanup_error)
                    return
                self.log.warning(
                    "prefetched source play failed",
                    extra={
                        "event": "music.prefetch.play_failed",
                        "exception_type": type(exc).__name__,
                    },
                )
                if track.failure_retries < 1:
                    track.failure_retries += 1
                    await self._prepare_current(track, generation, attempts=1)
                else:
                    await self._fail_current(track, generation, exc)
                return
            await self._safe_cleanup_prepared(prepared, "music.prefetch.rejected_cleanup_failed")
        await self._prepare_current(track, generation, attempts=2)

    def _ensure_prefetch_locked(self) -> None:
        if (
            self.current is None
            or not self.queue
            or self.prepared_next is not None
            or self.prepared_next_track_id is not None
            or self._source_cleanup_failure is not None
        ):
            return
        track = self.queue[0]
        self.prepared_next_track_id = track.id
        generation = self._generation
        self._prefetch_task = self._spawn(self._prefetch(track, generation))

    async def _prefetch(self, track: Track, generation: int) -> None:
        prepared: PreparedAudio | None = None
        try:
            prepared = await self._prepare_audio(track)
        except asyncio.CancelledError:
            if prepared is not None:
                await self._safe_cleanup_prepared(prepared, "music.prefetch.cancel_cleanup_failed")
            raise
        except Exception as exc:
            async with self.lock:
                if self.prepared_next_track_id == track.id:
                    self.prepared_next_track_id = None
                    if not isinstance(exc, ResourceCleanupError) and track.failure_retries < 1:
                        track.failure_retries += 1
            await self._report(exc, "music.prefetch.failed", track)
            return
        try:
            async with self.lock:
                valid = (
                    (
                        self.current is not None
                        and self._generation == generation
                        and bool(self.queue)
                        and self.queue[0] is track
                    )
                    or self.current is track
                ) and self.prepared_next_track_id == track.id
                if valid:
                    self.prepared_next = prepared
                    return
                if self.prepared_next_track_id == track.id:
                    self.prepared_next_track_id = None
        except asyncio.CancelledError:
            await self._safe_cleanup_prepared(prepared, "music.prefetch.cancel_cleanup_failed")
            raise
        await self._safe_cleanup_prepared(prepared, "music.prefetch.invalid_cleanup_failed")

    async def _stop_locked(
        self,
        *,
        still_valid: Callable[[], bool] | None = None,
        deferred_cleanup: list[_CleanupBundle] | None = None,
    ) -> StopResult | None:
        if still_valid is not None and not still_valid():
            return None
        cancelled_current_prepare = await self._cancel_current_prepare_locked()
        prefetch_cleanup_error: ResourceCleanupError | None = None
        try:
            await self._cancel_prefetch_locked()
        except ResourceCleanupError as exc:
            prefetch_cleanup_error = exc
        if still_valid is not None and not still_valid():
            if (
                cancelled_current_prepare
                and self.current is not None
                and self.current.state is TrackState.PREPARING
            ):
                self._spawn(self._prepare_current(self.current, self._generation, attempts=2))
            self._ensure_prefetch_locked()
            return None
        current = self.current
        prepared = self.current_prepared
        queued = list(self.queue)
        next_prepared = self.prepared_next
        self.current = None
        self.current_prepared = None
        self.queue.clear()
        self.prepared_next = None
        self.prepared_next_track_id = None
        self._prefetch_task = None
        self._paused = False
        self._generation += 1
        voice = self._active_voice_client()
        if voice is not None and (voice.is_playing() or voice.is_paused()):
            voice.stop()
        cleanup_bundle = (current, prepared, queued, next_prepared)
        if deferred_cleanup is None:
            await self._cleanup_detached(*cleanup_bundle)
        else:
            deferred_cleanup.append(cleanup_bundle)
        if prefetch_cleanup_error is not None:
            raise prefetch_cleanup_error
        return StopResult((1 if current is not None else 0) + len(queued))

    async def _cancel_current_prepare_locked(self) -> bool:
        task = self._current_prepare_task
        if task is None or task is asyncio.current_task():
            return False
        cancelled = False
        if not task.done():
            cancelled = True
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._current_prepare_task is task:
            self._current_prepare_task = None
        return cancelled

    async def _cancel_prefetch_locked(self, *, track_id: str | None = None) -> None:
        if track_id is not None and self.prepared_next_track_id != track_id:
            return
        task = self._prefetch_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        prepared = self.prepared_next
        self.prepared_next = None
        self.prepared_next_track_id = None
        self._prefetch_task = None
        if prepared is not None:
            await self._safe_cleanup_prepared(prepared, "music.prefetch.cancel_cleanup_failed")

    async def _prepare_audio(self, track: Track, *, start_at: float = 0) -> PreparedAudio:
        memory_bytes = self.providers.preparation_memory_bytes(track)
        memory_reservation_id: str | None = None
        detached = False
        memory_transferred = False

        def observe(completion: asyncio.Future[None]) -> None:
            nonlocal detached
            detached = True
            self._track_detached_source_cleanup(completion, memory_reservation_id)

        async with asyncio.timeout(PROVIDER_OPERATION_TIMEOUT):
            try:
                await self._await_prepared_cleanup()
                await self._await_detached_source_cleanup()
                memory_reservation_id = await self._reserve_prepared_memory(memory_bytes)
                with observe_detached_work(observe):
                    prepared = await self.providers.prepare(track, start_at=start_at)
                if prepared.memory_bytes != memory_bytes:
                    prepared.memory_reservation_id = memory_reservation_id
                    memory_transferred = True
                    await self._safe_cleanup_prepared(
                        prepared, "music.prepare.memory_contract_cleanup_failed"
                    )
                    raise PlaybackError(f"Provider preparation memory mismatch: {track.provider}")
                prepared.memory_reservation_id = memory_reservation_id
                memory_transferred = True
                return prepared
            except BaseException:
                if memory_reservation_id is not None and not detached and not memory_transferred:
                    self._prepared_memory_reservations.pop(memory_reservation_id, None)
                raise

    async def _reserve_prepared_memory(self, memory_bytes: int) -> str | None:
        limit = self.config.max_queued_upload_bytes
        if not limit or not memory_bytes:
            return None
        async with self.lock:
            if self._closed:
                raise PlaybackError("Player is closed")
            if self._source_cleanup_failure is not None:
                raise self._source_cleanup_failure
            if self._total_upload_bytes_locked() + memory_bytes > limit:
                raise QueueLimitError()
            reservation_id = uuid.uuid4().hex
            self._prepared_memory_reservations[reservation_id] = memory_bytes
            return reservation_id

    def _track_detached_source_cleanup(
        self, completion: asyncio.Future[None], memory_reservation_id: str | None
    ) -> None:
        self._detached_source_cleanup[completion] = memory_reservation_id
        completion.add_done_callback(self._detached_source_cleanup_done)

    def _detached_source_cleanup_done(self, completion: asyncio.Future[None]) -> None:
        memory_reservation_id = self._detached_source_cleanup.pop(completion, None)
        try:
            error = asyncio.CancelledError() if completion.cancelled() else completion.exception()
        except asyncio.CancelledError as exc:
            error = exc
        if error is not None:
            self._record_source_cleanup_failure(error, "music.detached_cleanup.failed")
            return
        if memory_reservation_id is not None:
            self._prepared_memory_reservations.pop(memory_reservation_id, None)

    def _detached_upload_work_done(self, completion: asyncio.Future[None]) -> None:
        reservation_id = self._detached_upload_work.pop(completion, None)
        if reservation_id is not None:
            self._deferred_upload_reservations.discard(reservation_id)
            self._upload_reservations.pop(reservation_id, None)
        try:
            error = asyncio.CancelledError() if completion.cancelled() else completion.exception()
        except asyncio.CancelledError as exc:
            error = exc
        if error is not None:
            log_exception(
                self.log,
                error,
                event="music.upload.detached_inspection_failed",
                context={"guild_id": self.guild_id},
            )

    async def _await_detached_source_cleanup(self) -> None:
        while self._detached_source_cleanup:
            batch = tuple(self._detached_source_cleanup)
            pending = asyncio.gather(*batch, return_exceptions=True)
            results = await asyncio.shield(pending)
            for completion, result in zip(batch, results, strict=True):
                memory_reservation_id = self._detached_source_cleanup.pop(completion, None)
                if isinstance(result, BaseException):
                    self._record_source_cleanup_failure(result, "music.detached_cleanup.failed")
                elif memory_reservation_id is not None:
                    self._prepared_memory_reservations.pop(memory_reservation_id, None)
        if self._source_cleanup_failure is not None:
            raise self._source_cleanup_failure

    async def _drain_source_cleanup(self) -> None:
        try:
            async with asyncio.timeout(SHUTDOWN_DETACHED_CLEANUP_TIMEOUT):
                while (
                    self._detached_source_cleanup
                    or self._detached_upload_work
                    or self._prepared_cleanup_tasks
                ):
                    detached_batch = tuple(self._detached_source_cleanup)
                    upload_batch = tuple(self._detached_upload_work)
                    prepared_batch = tuple(self._prepared_cleanup_tasks)
                    batch = (*detached_batch, *upload_batch, *prepared_batch)
                    pending = asyncio.gather(*batch, return_exceptions=True)
                    results = await asyncio.shield(pending)
                    for index, (completion, result) in enumerate(zip(batch, results, strict=True)):
                        if isinstance(result, BaseException):
                            if index < len(detached_batch):
                                self._record_source_cleanup_failure(
                                    result,
                                    "music.shutdown.detached_cleanup_failed",
                                )
                            elif index < len(detached_batch) + len(upload_batch):
                                log_exception(
                                    self.log,
                                    result,
                                    event="music.shutdown.upload_inspection_failed",
                                    context={"guild_id": self.guild_id},
                                )
                            else:
                                self._record_source_cleanup_failure(
                                    result, "music.shutdown.prepared_cleanup_failed"
                                )
                        if completion in self._detached_source_cleanup:
                            memory_reservation_id = self._detached_source_cleanup.pop(completion)
                            if not isinstance(result, BaseException) and (
                                memory_reservation_id is not None
                            ):
                                self._prepared_memory_reservations.pop(memory_reservation_id, None)
                        if completion in self._detached_upload_work:
                            reservation_id = self._detached_upload_work.pop(completion)
                            if reservation_id is not None:
                                self._deferred_upload_reservations.discard(reservation_id)
                                self._upload_reservations.pop(reservation_id, None)
                        if completion in self._prepared_cleanup_tasks:
                            self._finalize_prepared_cleanup_task(completion, result)
        except TimeoutError as exc:
            log_exception(
                self.log,
                exc,
                event="music.shutdown.source_cleanup_timeout",
                context={"guild_id": self.guild_id},
            )

    def _assert_access_locked(
        self,
        access_check: Callable[[discord.abc.Connectable | None], bool] | None,
    ) -> None:
        if access_check is not None and not access_check(self.bot_channel):
            raise AccessDeniedError()

    async def _fail_current(self, track: Track, generation: int, error: BaseException) -> None:
        async with self.lock:
            if self.current is not track or self._generation != generation:
                return
            prepared = self.current_prepared
            self.current = None
            self.current_prepared = None
            self._paused = False
            self._generation += 1
        cleanup_error: ResourceCleanupError | None = None
        try:
            if prepared is not None:
                await self._safe_cleanup_prepared(prepared, "music.failure.cleanup_failed")
        except ResourceCleanupError as exc:
            cleanup_error = exc
        finally:
            self._safe_dispose_track(track, "music.failure.dispose_failed")
        if cleanup_error is not None:
            raise cleanup_error
        await self._notify(
            track.request_channel_id,
            render("music.play.retry_exhausted", title=self._safe_title(track.title)),
        )
        await self._report(error, "music.prepare.failed", track)
        if not isinstance(error, ResourceCleanupError):
            await self._advance()

    async def _discard_current_preserve_queue(self, track: Track, generation: int) -> None:
        async with self.lock:
            if self.current is not track or self._generation != generation:
                return
            prepared = self.current_prepared
            self.current = None
            self.current_prepared = None
            self._paused = False
            self._generation += 1
        cleanup_error: ResourceCleanupError | None = None
        try:
            if prepared is not None:
                await self._safe_cleanup_prepared(prepared, "music.cancel.cleanup_failed")
        except ResourceCleanupError as exc:
            cleanup_error = exc
        finally:
            self._safe_dispose_track(track, "music.cancel.dispose_failed")
        if cleanup_error is not None:
            raise cleanup_error

    def _enforce_limits(
        self,
        incoming: Track,
        *,
        upload_reservation: UploadReservation | None = None,
    ) -> None:
        if self.config.max_queue_tracks and (self.current is not None or self.queue):
            if len(self.queue) >= self.config.max_queue_tracks:
                raise QueueLimitError()
        limit = self.config.max_queued_upload_bytes
        if not limit:
            return
        if upload_reservation is not None:
            reserved = self._upload_reservations.get(upload_reservation.id)
            if reserved is None:
                raise PlaybackError("Upload reservation is no longer active")
            if reserved != incoming.upload_size:
                raise PlaybackError("Upload reservation does not match the track")
            if self._total_upload_bytes_locked() > limit:
                raise QueueLimitError()
            return
        if self._total_upload_bytes_locked() + incoming.upload_size > limit:
            raise QueueLimitError()

    def _owned_upload_bytes_locked(self) -> int:
        used = sum(track.upload_size for track in self.queue)
        if self.current is not None:
            used += self.current.upload_size
        return used

    def _total_upload_bytes_locked(self) -> int:
        return (
            self._owned_upload_bytes_locked()
            + sum(self._upload_reservations.values())
            + sum(self._prepared_memory_reservations.values())
        )

    def _active_voice_client(self) -> discord.VoiceClient | None:
        voice = self.voice_client
        if voice is not None and voice.is_connected():
            return voice
        guild = self.bot.get_guild(self.guild_id)
        guild_voice = getattr(guild, "voice_client", None)
        if guild_voice is not None and guild_voice.is_connected():
            self.voice_client = guild_voice
            return guild_voice
        return None

    def _playback_offset_locked(self) -> float:
        now = self._pause_started_at or asyncio.get_running_loop().time()
        return max(0.0, now - self._started_at - self._paused_total)

    async def _cleanup_detached(
        self,
        current: Track | None,
        prepared: PreparedAudio | None,
        queued: list[Track],
        next_prepared: PreparedAudio | None,
    ) -> None:
        cleanup_tasks: list[asyncio.Task[None]] = []
        for owned, event in (
            (prepared, "music.stop.cleanup_failed"),
            (next_prepared, "music.stop.cleanup_failed"),
        ):
            if owned is not None:
                cleanup_tasks.append(self._begin_prepared_cleanup(owned, event))
        try:
            results = (
                await asyncio.shield(asyncio.gather(*cleanup_tasks, return_exceptions=True))
                if cleanup_tasks
                else []
            )
        finally:
            if current is not None:
                self._safe_dispose_track(current, "music.stop.dispose_failed")
            for track in queued:
                self._safe_dispose_track(track, "music.stop.dispose_failed")
        error = next((result for result in results if isinstance(result, BaseException)), None)
        if error is not None:
            raise self._record_source_cleanup_failure(error, "music.stop.cleanup_failed")

    async def _safe_cleanup_prepared(self, prepared: PreparedAudio, event: str) -> None:
        cleanup = self._begin_prepared_cleanup(prepared, event)
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            raise self._record_source_cleanup_failure(exc, event) from exc

    def _begin_prepared_cleanup(self, prepared: PreparedAudio, event: str) -> asyncio.Task[None]:
        cleanup = asyncio.create_task(asyncio.to_thread(prepared.cleanup))
        self._prepared_cleanup_tasks[cleanup] = (event, prepared.memory_reservation_id)
        cleanup.add_done_callback(self._prepared_cleanup_done)
        return cleanup

    async def _await_prepared_cleanup(self) -> None:
        while self._prepared_cleanup_tasks:
            batch = tuple(self._prepared_cleanup_tasks)
            pending = asyncio.gather(*batch, return_exceptions=True)
            results = await asyncio.shield(pending)
            for task, result in zip(batch, results, strict=True):
                self._finalize_prepared_cleanup_task(task, result)
        if self._source_cleanup_failure is not None:
            raise self._source_cleanup_failure

    def _prepared_cleanup_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            result: BaseException | None = asyncio.CancelledError()
        else:
            result = task.exception()
        self._finalize_prepared_cleanup_task(task, result)

    def _finalize_prepared_cleanup_task(self, task: asyncio.Task[None], result: object) -> None:
        details = self._prepared_cleanup_tasks.pop(task, None)
        if details is None:
            return
        event, memory_reservation_id = details
        if isinstance(result, BaseException):
            self._record_source_cleanup_failure(result, event)
            return
        if memory_reservation_id is not None:
            self._prepared_memory_reservations.pop(memory_reservation_id, None)

    def _record_source_cleanup_failure(
        self, error: BaseException, event: str
    ) -> ResourceCleanupError:
        if self._source_cleanup_failure is None:
            failure = ResourceCleanupError("Audio source cleanup failed")
            failure.__cause__ = error
            self._source_cleanup_failure = failure
            log_exception(self.log, error, event=event)
        return self._source_cleanup_failure

    def _safe_dispose_track(self, track: Track, event: str) -> None:
        try:
            track.dispose()
            self.log.debug(
                "Track disposed",
                extra={"event": "track.disposed", "track_id": track.id},
            )
        except BaseException as exc:
            log_exception(self.log, exc, event=event)

    @staticmethod
    def _safe_title(title: str) -> str:
        return truncate(discord.utils.escape_markdown(title), 180)

    async def _notify(self, channel_id: int, message: str) -> None:
        channel = self.bot.get_channel(channel_id)
        if channel is None or not hasattr(channel, "send"):
            return
        try:
            await channel.send(message, allowed_mentions=discord.AllowedMentions.none())
        except Exception as exc:
            await self.reporter.report(
                exc,
                event="music.notification.failed",
                context=ErrorContext(guild_id=self.guild_id, channel_id=channel_id),
            )

    async def _report(self, error: BaseException, event: str, track: Track) -> None:
        await self.reporter.report(
            error,
            event=event,
            context=ErrorContext(
                guild_id=self.guild_id,
                channel_id=track.request_channel_id,
                provider=track.provider,
                track_id=track.id,
                requester_name=track.requester_display_name,
                requester_id=track.requester_id,
                url=track.canonical_url,
                filename=track.provider_data.get("filename"),
            ),
        )

    def _spawn(self, coroutine: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            report_task = asyncio.create_task(
                self.reporter.report(
                    error,
                    event="music.background_task.failed",
                    context=ErrorContext(guild_id=self.guild_id),
                )
            )
            self._report_tasks.add(report_task)
            report_task.add_done_callback(self._report_done)

    def _report_done(self, task: asyncio.Task[Any]) -> None:
        self._report_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            log_exception(self.log, error, event="music.error_report.failed")
