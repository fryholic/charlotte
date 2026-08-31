from __future__ import annotations

import asyncio
import io
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from charlotte.errors import (
    AccessDeniedError,
    PlaybackError,
    ProviderError,
    QueueLimitError,
    ResourceCleanupError,
)
from charlotte.music.models import PreparedAudio
from charlotte.music.player import GuildPlayer
from charlotte.music.provider import ProviderRegistry
from charlotte.providers.ytdlp_common import run_blocking
from tests.fakes import (
    FakeBot,
    FakeGuild,
    FakeProvider,
    FakeReporter,
    FakeSource,
    FakeTextChannel,
    FakeVoiceChannel,
    FakeVoiceClient,
    make_track,
)


class TrackingSource(FakeSource):
    def __init__(self, provider) -> None:
        super().__init__()
        self.provider = provider

    def cleanup(self) -> None:
        if self.cleaned:
            return
        super().cleanup()
        self.provider.live_sources -= 1


class GatedPrefetchProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.prepare_calls = 0
        self.prefetch_created = asyncio.Event()
        self.release_prefetch = asyncio.Event()
        self.live_sources = 0
        self.max_live_sources = 0

    def _source(self):
        source = TrackingSource(self)
        self.sources.append(source)
        self.live_sources += 1
        self.max_live_sources = max(self.max_live_sources, self.live_sources)
        return source

    async def prepare(self, track, *, start_at=0):
        self.prepare_calls += 1
        source = self._source()
        if track.title == "next" and self.prepare_calls == 2:
            self.prefetch_created.set()
            try:
                await self.release_prefetch.wait()
            except asyncio.CancelledError:
                source.cleanup()
                raise
        return PreparedAudio(source=source, seekable=True)


class SlowCancelPrefetchProvider(GatedPrefetchProvider):
    def __init__(self) -> None:
        super().__init__()
        self.cancel_started = asyncio.Event()
        self.release_cancel = asyncio.Event()

    async def prepare(self, track, *, start_at=0):
        self.prepare_calls += 1
        source = self._source()
        if track.title == "next" and self.prepare_calls == 2:
            self.prefetch_created.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancel_started.set()
                await asyncio.shield(self.release_cancel.wait())
                source.cleanup()
                raise
        return PreparedAudio(source=source, seekable=True)


class SlowCancelCurrentProvider(GatedPrefetchProvider):
    def __init__(self) -> None:
        super().__init__()
        self.current_created = asyncio.Event()
        self.cancel_started = asyncio.Event()
        self.release_cancel = asyncio.Event()

    async def prepare(self, track, *, start_at=0):
        self.prepare_calls += 1
        source = self._source()
        if self.prepare_calls == 1:
            self.current_created.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancel_started.set()
                await asyncio.shield(self.release_cancel.wait())
                source.cleanup()
                raise
        return PreparedAudio(source=source, seekable=True)


class DetachedCurrentProvider(GatedPrefetchProvider):
    def __init__(self) -> None:
        super().__init__()
        self.current_started = threading.Event()
        self.release_current = threading.Event()

    async def prepare(self, track, *, start_at=0):
        self.prepare_calls += 1
        if self.prepare_calls == 1:

            def create():
                self.current_started.set()
                self.release_current.wait(timeout=2)
                return self._source()

            source = await run_blocking(
                create,
                cleanup_cancelled_result=lambda cancelled: cancelled.cleanup(),
            )
        else:
            source = self._source()
        return PreparedAudio(source=source, seekable=True)


class FailingDetachedCleanupProvider(GatedPrefetchProvider):
    def __init__(self) -> None:
        super().__init__()
        self.current_started = threading.Event()
        self.release_current = threading.Event()

    async def prepare(self, track, *, start_at=0):
        self.prepare_calls += 1

        def create():
            self.current_started.set()
            self.release_current.wait(timeout=2)
            return self._source()

        def fail_cleanup(source):
            source.cleanup()
            raise RuntimeError("detached cleanup failed")

        source = await run_blocking(create, cleanup_cancelled_result=fail_cleanup)
        return PreparedAudio(source=source, seekable=True)


class BlockingCleanupSource(TrackingSource):
    def __init__(self, provider) -> None:
        super().__init__(provider)
        self.cleanup_started = threading.Event()
        self.release_cleanup = threading.Event()

    def cleanup(self) -> None:
        self.cleanup_started.set()
        self.release_cleanup.wait(timeout=2)
        super().cleanup()


class BlockingCleanupProvider(GatedPrefetchProvider):
    def _source(self):
        source = BlockingCleanupSource(self)
        self.sources.append(source)
        self.live_sources += 1
        self.max_live_sources = max(self.max_live_sources, self.live_sources)
        return source


class TrackingProvider(GatedPrefetchProvider):
    async def prepare(self, track, *, start_at=0):
        self.prepare_calls += 1
        return PreparedAudio(source=self._source(), seekable=True)


class RecordingReconnectProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.start_offsets: list[float] = []

    async def prepare(self, track, *, start_at=0):
        self.start_offsets.append(start_at)
        return await super().prepare(track, start_at=start_at)


class PrefetchFailureProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls_by_title: dict[str, int] = {}

    async def prepare(self, track, *, start_at=0):
        self.calls_by_title[track.title] = self.calls_by_title.get(track.title, 0) + 1
        if track.title == "next":
            raise RuntimeError("prepare failed")
        return await super().prepare(track, start_at=start_at)


class FailingCleanupSource(FakeSource):
    def cleanup(self) -> None:
        raise RuntimeError("cleanup failed")


class FailingPreparedCleanupProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.prepare_calls = 0

    async def prepare(self, track, *, start_at=0):
        self.prepare_calls += 1
        return PreparedAudio(source=FailingCleanupSource(), seekable=True)


class UploadCopyProvider(FakeProvider):
    def preparation_memory_bytes(self, track) -> int:
        return track.upload_size

    async def prepare(self, track, *, start_at=0):
        buffer = io.BytesIO(b"x" * track.upload_size)
        source = FakeSource()
        self.sources.append(source)
        return PreparedAudio(
            source=source,
            seekable=True,
            owned_resources=(buffer,),
            memory_bytes=track.upload_size,
        )


class DelayedReconnectChannel(FakeVoiceChannel):
    def __init__(self, guild, channel_id, name) -> None:
        super().__init__(guild, channel_id, name)
        self.delay_reconnect = False
        self.reconnect_started = asyncio.Event()
        self.release_reconnect = asyncio.Event()

    async def connect(self, *, timeout, reconnect):  # noqa: ASYNC109
        if self.delay_reconnect:
            self.reconnect_started.set()
            await self.release_reconnect.wait()
        return await super().connect(timeout=timeout, reconnect=reconnect)


class InternallyTimedConnectChannel(FakeVoiceChannel):
    def __init__(self, guild, channel_id, name) -> None:
        super().__init__(guild, channel_id, name)
        self.attempts = 0

    async def connect(self, *, timeout, reconnect):  # noqa: ASYNC109
        self.attempts += 1
        if self.attempts == 1:
            ghost = FakeVoiceClient(self)
            self.guild.voice_client = ghost
            try:
                async with asyncio.timeout(timeout):
                    await asyncio.sleep(timeout * 2)
            except TimeoutError:
                await ghost.disconnect(force=True)
                raise
        if self.guild.voice_client is not None:
            raise RuntimeError("already connected")
        return await super().connect(timeout=timeout, reconnect=reconnect)


class CancelledConnectChannel(FakeVoiceChannel):
    def __init__(self, guild, channel_id, name) -> None:
        super().__init__(guild, channel_id, name)
        self.attempts = 0
        self.started = asyncio.Event()

    async def connect(self, *, timeout, reconnect):  # noqa: ASYNC109
        self.attempts += 1
        if self.attempts == 1:
            self.guild.voice_client = FakeVoiceClient(self)
            self.started.set()
            await asyncio.Event().wait()
        if self.guild.voice_client is not None:
            raise RuntimeError("already connected")
        return await super().connect(timeout=timeout, reconnect=reconnect)


class AdvancingReconnectChannel(FakeVoiceChannel):
    def __init__(self, guild, channel_id, name, player) -> None:
        super().__init__(guild, channel_id, name)
        self.player = player

    async def connect(self, *, timeout, reconnect):  # noqa: ASYNC109
        voice = await super().connect(timeout=timeout, reconnect=reconnect)
        await self.player._advance()
        return voice


def build_player(app_config, guild_id=1, *, provider_delay=0):
    bot = FakeBot()
    guild = FakeGuild(guild_id)
    channel = FakeVoiceChannel(guild, guild_id * 10, f"voice-{guild_id}")
    text = FakeTextChannel(guild_id * 100)
    bot.guilds[guild_id] = guild
    bot.channels[text.id] = text
    provider = FakeProvider(delay=provider_delay)
    providers = ProviderRegistry()
    providers.register(provider)
    reporter = FakeReporter()
    player = GuildPlayer(
        guild_id,
        bot=bot,
        config=app_config,
        providers=providers,
        reporter=reporter,
    )
    return player, channel, provider, reporter


@pytest.mark.asyncio
async def test_receipts_commit_in_command_arrival_order(app_config) -> None:
    player, _, _, _ = build_player(app_config)
    first = player.issue_receipt()
    second = player.issue_receipt()
    second_waiter = asyncio.create_task(player.wait_for_receipt(second))
    await asyncio.sleep(0)
    assert not second_waiter.done()
    await player.cancel_receipt(first)
    await asyncio.wait_for(second_waiter, 1)
    await player.finish_receipt(second)


@pytest.mark.asyncio
async def test_current_and_next_are_prepared_and_queue_view_is_capped(app_config) -> None:
    player, channel, provider, _ = build_player(app_config)
    await player.connect(channel)
    first = make_track("track-1")
    assert (await player.add(first)).started
    for index in range(2, 8):
        await player.add(make_track(f"track-{index}"))
    await asyncio.sleep(0.02)
    assert player.current is first
    assert player.prepared_next_track_id == player.queue[0].id
    assert player.prepared_next is not None
    assert len(provider.sources) == 2
    view = await player.queue_view()
    assert view.current and view.current.title == "track-1"
    assert [item.title for item in view.upcoming] == [
        "track-2",
        "track-3",
        "track-4",
        "track-5",
    ]
    await player.close()


@pytest.mark.asyncio
async def test_paused_track_can_be_skipped_and_stop_cleans_everything(app_config) -> None:
    player, channel, provider, _ = build_player(app_config)
    await player.connect(channel)
    first = make_track("first")
    second = make_track("second")
    third = make_track("third")
    await player.add(first)
    await player.add(second)
    await player.add(third)
    await asyncio.sleep(0.02)
    assert await player.pause() is first
    result = await player.skip()
    assert result.skipped_title == "first"
    assert result.next_title == "second"
    await asyncio.sleep(0.02)
    assert player.current is second
    assert not player.is_paused
    stopped = await player.stop()
    assert stopped.removed_count == 2
    assert player.current is None and not player.queue
    assert all(source.cleaned for source in provider.sources)
    await player.close()


@pytest.mark.asyncio
async def test_two_guilds_prepare_concurrently_and_do_not_share_state(app_config) -> None:
    shared_provider = FakeProvider(delay=0.05)
    providers = ProviderRegistry()
    providers.register(shared_provider)
    bot = FakeBot()
    reporter = FakeReporter()
    players = []
    for guild_id in (1, 2):
        guild = FakeGuild(guild_id)
        channel = FakeVoiceChannel(guild, guild_id * 10, f"voice-{guild_id}")
        bot.guilds[guild_id] = guild
        player = GuildPlayer(
            guild_id,
            bot=bot,
            config=app_config,
            providers=providers,
            reporter=reporter,
        )
        await player.connect(channel)
        players.append(player)
    await asyncio.gather(
        players[0].add(make_track("guild-a")),
        players[1].add(make_track("guild-b")),
    )
    assert shared_provider.max_active == 2
    assert players[0].current.title == "guild-a"
    assert players[1].current.title == "guild-b"
    await asyncio.gather(*(player.close() for player in players))


@pytest.mark.asyncio
async def test_cancelled_initial_prepare_releases_current_track(app_config) -> None:
    player, channel, _, _ = build_player(app_config, provider_delay=0.2)
    await player.connect(channel)
    track = make_track("cancel-me")
    operation = asyncio.create_task(player.add(track))
    await asyncio.sleep(0.02)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert player.current is None
    assert track.state.value == "disposed"
    await player.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["finish", "skip"])
async def test_slow_prefetch_never_creates_a_third_source(app_config, transition) -> None:
    player, channel, _, reporter = build_player(app_config)
    provider = GatedPrefetchProvider()
    providers = ProviderRegistry()
    providers.register(provider)
    player.providers = providers
    await player.connect(channel)
    await player.add(make_track("current"))
    await player.add(make_track("next"))
    await asyncio.wait_for(provider.prefetch_created.wait(), 1)
    await player.add(make_track("after-next"))

    if transition == "finish":
        channel.guild.voice_client.finish()
        operation = None
    else:
        operation = asyncio.create_task(player.skip())
    await asyncio.sleep(0.02)
    assert provider.prepare_calls == 2
    assert provider.max_live_sources <= 2

    provider.release_prefetch.set()
    if operation is not None:
        await asyncio.wait_for(operation, 1)
    for _ in range(100):
        if player.current is not None and player.current.title == "next":
            break
        await asyncio.sleep(0.01)
    assert player.current is not None and player.current.title == "next"
    assert provider.max_live_sources <= 2
    assert reporter.reports == []
    await player.close()


@pytest.mark.asyncio
async def test_internal_connect_timeout_cleans_cache_before_consecutive_connect(
    app_config, monkeypatch
) -> None:
    player, original_channel, _, _ = build_player(app_config)
    channel = InternallyTimedConnectChannel(original_channel.guild, 99, "timed")
    monkeypatch.setattr("charlotte.music.player.VOICE_OPERATION_TIMEOUT", 0.02)

    with pytest.raises(TimeoutError):
        await player.connect(channel)

    assert channel.guild.voice_client is None
    assert not await player.connect(channel)
    assert channel.guild.voice_client is player.voice_client
    assert channel.attempts == 2
    await player.close()


@pytest.mark.asyncio
async def test_cancelled_connect_cleans_cache_before_consecutive_connect(app_config) -> None:
    player, original_channel, _, _ = build_player(app_config)
    channel = CancelledConnectChannel(original_channel.guild, 99, "cancelled")
    first = asyncio.create_task(player.connect(channel))
    await asyncio.wait_for(channel.started.wait(), 1)

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    assert channel.guild.voice_client is None
    assert not await player.connect(channel)
    assert channel.guild.voice_client is player.voice_client
    assert channel.attempts == 2
    await player.close()


@pytest.mark.asyncio
async def test_failed_move_disconnects_uncertain_voice_before_reconnect(
    app_config, monkeypatch
) -> None:
    player, old_channel, _, _ = build_player(app_config)
    new_channel = FakeVoiceChannel(old_channel.guild, 99, "new")
    await player.connect(old_channel)
    voice = player.voice_client
    assert voice is not None

    async def ignore_move(channel, *, timeout):  # noqa: ASYNC109
        return None

    monkeypatch.setattr(voice, "move_to", ignore_move)
    with pytest.raises(PlaybackError, match="did not reach"):
        await player.connect(new_channel)

    assert not voice.is_connected()
    assert old_channel.guild.voice_client is None
    assert not await player.connect(new_channel)
    assert player.bot_channel is new_channel
    await player.close()


@pytest.mark.asyncio
async def test_stale_empty_channel_event_cannot_disconnect_remote_move(app_config) -> None:
    player, old_channel, _, _ = build_player(app_config)
    new_channel = FakeVoiceChannel(old_channel.guild, 99, "new-channel")
    await player.connect(old_channel)
    await player.add(make_track("old"))
    await player.stop()

    await player._connection_lock.acquire()
    move = asyncio.create_task(player.connect(new_channel))
    await asyncio.sleep(0)
    stale_leave = asyncio.create_task(player.leave_if_empty(old_channel))
    await asyncio.sleep(0)
    player._connection_lock.release()
    await asyncio.wait_for(move, 1)
    await player.add(make_track("new"))
    assert not await asyncio.wait_for(stale_leave, 1)
    assert player.bot_channel is new_channel
    assert player.current is not None and player.current.title == "new"
    await player.close()


@pytest.mark.asyncio
async def test_human_join_during_empty_leave_cancels_disconnect(app_config) -> None:
    player, channel, _, _ = build_player(app_config)
    provider = SlowCancelPrefetchProvider()
    providers = ProviderRegistry()
    providers.register(provider)
    player.providers = providers
    await player.connect(channel)
    await player.add(make_track("current"))
    await player.add(make_track("next"))
    await asyncio.wait_for(provider.prefetch_created.wait(), 1)

    leave = asyncio.create_task(player.leave_if_empty(channel))
    await asyncio.wait_for(provider.cancel_started.wait(), 1)
    channel.members.append(SimpleNamespace(bot=False))
    provider.release_cancel.set()
    assert not await asyncio.wait_for(leave, 1)
    assert player.bot_channel is channel
    assert player.current is not None and player.current.title == "current"
    await player.close()


@pytest.mark.asyncio
async def test_stop_waits_for_owned_current_prepare_before_new_sources(app_config) -> None:
    player, channel, _, _ = build_player(app_config)
    provider = SlowCancelCurrentProvider()
    providers = ProviderRegistry()
    providers.register(provider)
    player.providers = providers
    await player.connect(channel)

    first_add = asyncio.create_task(player.add(make_track("first")))
    await asyncio.wait_for(provider.current_created.wait(), 1)
    stopping = asyncio.create_task(player.stop())
    await asyncio.wait_for(provider.cancel_started.wait(), 1)
    second_add = asyncio.create_task(player.add(make_track("second")))
    await asyncio.sleep(0.02)
    assert not stopping.done()
    assert not second_add.done()

    provider.release_cancel.set()
    await asyncio.wait_for(stopping, 1)
    assert not (await first_add).started
    await asyncio.wait_for(second_add, 1)
    await player.add(make_track("third"))
    await asyncio.sleep(0.02)
    assert provider.max_live_sources <= 2
    assert provider.sources[0].cleaned
    await player.close()


@pytest.mark.asyncio
async def test_new_prepare_waits_for_detached_constructor_cleanup(app_config) -> None:
    player, channel, _, _ = build_player(app_config)
    provider = DetachedCurrentProvider()
    providers = ProviderRegistry()
    providers.register(provider)
    player.providers = providers
    await player.connect(channel)

    first_add = asyncio.create_task(player.add(make_track("first")))
    assert await asyncio.to_thread(provider.current_started.wait, 1)
    await asyncio.wait_for(player.stop(), 1)
    assert not (await first_add).started

    second_add = asyncio.create_task(player.add(make_track("second")))
    await asyncio.sleep(0.02)
    assert not second_add.done()
    assert provider.prepare_calls == 1

    provider.release_current.set()
    await asyncio.wait_for(second_add, 1)
    await player.add(make_track("third"))
    await asyncio.sleep(0.02)
    assert provider.max_live_sources <= 2
    assert provider.sources[0].cleaned
    await player.close()


@pytest.mark.asyncio
async def test_detached_constructor_does_not_block_another_guild(app_config) -> None:
    first_player, first_channel, _, _ = build_player(app_config, guild_id=1)
    second_player, second_channel, _, _ = build_player(app_config, guild_id=2)
    provider = DetachedCurrentProvider()
    providers = ProviderRegistry()
    providers.register(provider)
    first_player.providers = providers
    second_player.providers = providers
    await first_player.connect(first_channel)
    await second_player.connect(second_channel)

    first_add = asyncio.create_task(first_player.add(make_track("first")))
    assert await asyncio.to_thread(provider.current_started.wait, 1)
    await asyncio.wait_for(first_player.stop(), 1)
    assert not (await first_add).started

    await asyncio.wait_for(second_player.add(make_track("independent")), 1)
    assert second_player.current is not None
    assert second_player.current.title == "independent"

    provider.release_current.set()
    await first_player.close()
    await second_player.close()


@pytest.mark.asyncio
async def test_recovery_cancels_initial_prepare_without_third_source(app_config) -> None:
    player, channel, _, _ = build_player(app_config)
    provider = DetachedCurrentProvider()
    providers = ProviderRegistry()
    providers.register(provider)
    player.providers = providers
    await player.connect(channel)

    first = make_track("first")
    first_add = asyncio.create_task(player.add(first))
    assert await asyncio.to_thread(provider.current_started.wait, 1)
    await player.add(make_track("second"))
    await player.add(make_track("third"))
    await asyncio.sleep(0.02)

    disconnected = player.voice_client
    assert disconnected is not None
    disconnected.connected = False
    channel.guild.voice_client = None
    recovery = asyncio.create_task(player.recover_voice(channel, expected_track_id=first.id))
    await asyncio.sleep(0.02)
    provider.release_current.set()

    await asyncio.wait_for(recovery, 1)
    assert not (await first_add).started
    assert player.current is first
    assert provider.max_live_sources <= 2
    assert provider.sources[1].cleaned
    await player.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("paused", [False, True])
async def test_voice_recovery_restores_the_previous_pause_state(app_config, paused) -> None:
    player, channel, _, _ = build_player(app_config)
    provider = RecordingReconnectProvider()
    providers = ProviderRegistry()
    providers.register(provider)
    player.providers = providers
    await player.connect(channel)
    current = make_track("current")
    await player.add(current)
    if paused:
        await player.pause()

    disconnected = player.voice_client
    assert disconnected is not None
    disconnected.connected = False
    channel.guild.voice_client = None
    await player.recover_voice(channel, expected_track_id=current.id)

    recovered = channel.guild.voice_client
    assert recovered is not None
    assert player.is_paused is paused
    assert recovered.is_paused() is paused
    assert recovered.is_playing() is (not paused)
    assert len(provider.start_offsets) == 2
    await player.close()


@pytest.mark.asyncio
async def test_advance_preserves_queue_until_voice_recovery(app_config) -> None:
    player, channel, _, _ = build_player(app_config)
    await player.connect(channel)
    current = make_track("current")
    following = make_track("following")
    await player.add(current)
    await player.add(following)
    await asyncio.sleep(0.02)
    generation = player._generation
    disconnected = player.voice_client
    assert disconnected is not None
    disconnected.connected = False
    channel.guild.voice_client = None

    await player._on_finished(current.id, generation, None)

    assert player.current is None
    assert list(player.queue) == [following]
    await player.recover_voice(channel, expected_track_id=None)
    assert player.current is following
    assert channel.guild.voice_client is not None
    assert channel.guild.voice_client.is_playing()
    await player.close()


@pytest.mark.asyncio
async def test_recovery_adopts_candidate_before_queue_advance_can_use_it(app_config) -> None:
    player, channel, _, _ = build_player(app_config)
    await player.connect(channel)
    current = make_track("current")
    following = make_track("following")
    await player.add(current)
    await player.add(following)
    await asyncio.sleep(0.02)
    generation = player._generation
    disconnected = player.voice_client
    assert disconnected is not None
    disconnected.connected = False
    channel.guild.voice_client = None
    await player._on_finished(current.id, generation, None)
    recovery_channel = AdvancingReconnectChannel(channel.guild, 99, "recovery", player)

    await player.recover_voice(recovery_channel, expected_track_id=None)

    assert player.current is following
    assert player.bot_channel is recovery_channel
    assert player.voice_client is recovery_channel.guild.voice_client
    assert player.voice_client is not None and player.voice_client.is_playing()
    await player.close()


@pytest.mark.asyncio
async def test_consecutive_skip_owns_promoted_prefetch(app_config) -> None:
    player, channel, _, _ = build_player(app_config)
    provider = SlowCancelPrefetchProvider()
    providers = ProviderRegistry()
    providers.register(provider)
    player.providers = providers
    await player.connect(channel)
    await player.add(make_track("current"))
    await player.add(make_track("next"))
    await player.add(make_track("third"))
    await player.add(make_track("fourth"))
    await asyncio.wait_for(provider.prefetch_created.wait(), 1)

    first_skip = asyncio.create_task(player.skip())
    await asyncio.sleep(0.02)
    second_skip = asyncio.create_task(player.skip())
    await asyncio.wait_for(provider.cancel_started.wait(), 1)
    provider.release_cancel.set()

    await asyncio.wait_for(first_skip, 1)
    await asyncio.wait_for(second_skip, 1)
    await asyncio.sleep(0.02)
    assert player.current is not None and player.current.title == "third"
    assert provider.max_live_sources <= 2
    await player.close()


@pytest.mark.asyncio
async def test_commit_play_rechecks_access_after_connect(app_config) -> None:
    bot = FakeBot()
    guild = FakeGuild(1)
    channel = DelayedReconnectChannel(guild, 10, "voice")
    text = FakeTextChannel(500)
    bot.guilds[guild.id] = guild
    bot.channels[text.id] = text
    provider = FakeProvider()
    providers = ProviderRegistry()
    providers.register(provider)
    player = GuildPlayer(
        guild.id,
        bot=bot,
        config=app_config,
        providers=providers,
        reporter=FakeReporter(),
    )
    channel.delay_reconnect = True
    allowed = True
    committing = asyncio.create_task(
        player.commit_play(
            make_track("stale"),
            channel,
            access_check=lambda _: allowed,
        )
    )
    await asyncio.wait_for(channel.reconnect_started.wait(), 1)
    allowed = False
    channel.release_reconnect.set()

    with pytest.raises(AccessDeniedError):
        await asyncio.wait_for(committing, 1)
    assert player.current is None
    assert player.bot_channel is None
    await player.close()


@pytest.mark.asyncio
async def test_close_drains_detached_constructor_cleanup(app_config) -> None:
    player, channel, _, _ = build_player(app_config)
    provider = DetachedCurrentProvider()
    providers = ProviderRegistry()
    providers.register(provider)
    player.providers = providers
    await player.connect(channel)

    first_add = asyncio.create_task(player.add(make_track("first")))
    assert await asyncio.to_thread(provider.current_started.wait, 1)
    closing = asyncio.create_task(player.close())
    await asyncio.sleep(0.02)
    assert not closing.done()
    provider.release_current.set()

    await asyncio.wait_for(closing, 1)
    assert not (await first_add).started
    assert provider.live_sources == 0
    assert provider.sources[0].cleaned


@pytest.mark.asyncio
async def test_async_failure_notification_escapes_and_truncates_title(app_config) -> None:
    player, _, _, _ = build_player(app_config)
    track = make_track("**" + "x" * 3000)
    text_channel = next(iter(player.bot.channels.values()))
    track.request_channel_id = text_channel.id
    async with player.lock:
        player.current = track
        player._generation += 1
        generation = player._generation
    await player._fail_current(track, generation, RuntimeError("failed"))

    content = text_channel.messages[0][0]
    assert len(content) < 1900
    assert "\\*\\*" in content
    await player.close()


@pytest.mark.asyncio
async def test_recovery_serializes_with_remote_move_and_play(app_config) -> None:
    bot = FakeBot()
    guild = FakeGuild(1)
    old_channel = DelayedReconnectChannel(guild, 10, "old")
    new_channel = FakeVoiceChannel(guild, 11, "new")
    text = FakeTextChannel(500)
    bot.guilds[guild.id] = guild
    bot.channels[text.id] = text
    provider = FakeProvider()
    providers = ProviderRegistry()
    providers.register(provider)
    player = GuildPlayer(
        guild.id,
        bot=bot,
        config=app_config,
        providers=providers,
        reporter=FakeReporter(),
    )
    await player.connect(old_channel)
    first = make_track("first")
    await player.add(first)
    disconnected = player.voice_client
    assert disconnected is not None
    disconnected.connected = False
    guild.voice_client = None
    old_channel.delay_reconnect = True

    recovery = asyncio.create_task(player.recover_voice(old_channel, expected_track_id=first.id))
    await asyncio.wait_for(old_channel.reconnect_started.wait(), 1)

    async def remote_play():
        await player.stop()
        await player.connect(new_channel)
        await player.add(make_track("remote"))

    remote = asyncio.create_task(remote_play())
    await asyncio.sleep(0.02)
    assert not remote.done()
    old_channel.release_reconnect.set()
    await asyncio.wait_for(recovery, 1)
    await asyncio.wait_for(remote, 1)
    assert player.bot_channel is new_channel
    assert player.current is not None and player.current.title == "remote"
    assert guild.voice_client is player.voice_client
    await player.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["skip", "pause", "resume", "stop", "leave"])
async def test_control_rechecks_access_at_mutation_boundary(app_config, transition) -> None:
    player, channel, _, _ = build_player(app_config)
    await player.connect(channel)
    await player.add(make_track("current"))
    if transition == "resume":
        await player.pause()

    allowed = True
    await player.lock.acquire()
    operation = asyncio.create_task(getattr(player, transition)(access_check=lambda _: allowed))
    await asyncio.sleep(0)
    allowed = False
    player.lock.release()
    with pytest.raises(AccessDeniedError):
        await asyncio.wait_for(operation, 1)
    assert player.current is not None and player.current.title == "current"
    assert player.bot_channel is channel
    await player.close()


@pytest.mark.asyncio
async def test_pending_track_is_rejected_after_its_provider_begins_unloading(app_config) -> None:
    player, channel, _, _ = build_player(app_config)
    await player.connect(channel)
    track = make_track("inspected-before-unload")
    player.providers.begin_unload("fake")

    with pytest.raises(ProviderError, match="unloading or unavailable"):
        await player.add(track)

    assert player.current is None
    assert not player.queue
    track.dispose()
    await player.close()


@pytest.mark.asyncio
async def test_human_join_during_current_prepare_restores_cancelled_playback(app_config) -> None:
    player, channel, _, _ = build_player(app_config)
    provider = SlowCancelCurrentProvider()
    providers = ProviderRegistry()
    providers.register(provider)
    player.providers = providers
    await player.connect(channel)

    track = make_track("current")
    adding = asyncio.create_task(player.add(track))
    await asyncio.wait_for(provider.current_created.wait(), 1)
    leaving = asyncio.create_task(player.leave_if_empty(channel))
    await asyncio.wait_for(provider.cancel_started.wait(), 1)
    channel.members.append(SimpleNamespace(bot=False))
    provider.release_cancel.set()

    assert not await asyncio.wait_for(leaving, 1)
    assert not (await asyncio.wait_for(adding, 1)).started
    for _ in range(100):
        if player.current_prepared is not None:
            break
        await asyncio.sleep(0.01)
    assert player.current is track
    assert player.current_prepared is not None
    assert player.bot_channel is channel
    assert provider.sources[0].cleaned
    assert provider.max_live_sources <= 2
    await player.close()


@pytest.mark.asyncio
async def test_playback_callback_error_retries_same_track_only_once(app_config) -> None:
    player, channel, _, reporter = build_player(app_config)
    provider = TrackingProvider()
    providers = ProviderRegistry()
    providers.register(provider)
    player.providers = providers
    await player.connect(channel)

    current = make_track("current")
    following = make_track("following")
    await player.add(current)
    await player.add(following)
    await asyncio.sleep(0.02)
    voice = channel.guild.voice_client
    voice.finish(RuntimeError("decoder failed once"))

    for _ in range(100):
        if player.current is current and current.failure_retries == 1 and voice.is_playing():
            break
        await asyncio.sleep(0.01)
    assert player.current is current
    assert current.failure_retries == 1
    assert voice.is_playing()
    assert provider.sources[0].cleaned
    assert reporter.reports == []
    assert provider.max_live_sources <= 2

    voice.finish(RuntimeError("decoder failed twice"))
    for _ in range(100):
        if player.current is following and voice.is_playing():
            break
        await asyncio.sleep(0.01)
    assert player.current is following
    assert current.state.value == "disposed"
    assert any(event == "music.playback.failed" for _, event, _, _ in reporter.reports)
    assert provider.max_live_sources <= 2
    await player.close()


@pytest.mark.asyncio
async def test_prefetch_failure_consumes_the_track_retry_budget(app_config) -> None:
    player, channel, _, _ = build_player(app_config)
    provider = PrefetchFailureProvider()
    providers = ProviderRegistry()
    providers.register(provider)
    player.providers = providers
    await player.connect(channel)
    current = make_track("current")
    following = make_track("next")
    await player.add(current)
    await player.add(following)
    for _ in range(100):
        if provider.calls_by_title.get("next") == 1:
            break
        await asyncio.sleep(0.01)
    assert following.failure_retries == 1

    await player.add(make_track("third"))
    await asyncio.sleep(0.02)
    await player.add(make_track("fourth"))
    await asyncio.sleep(0.02)
    assert provider.calls_by_title["next"] == 1

    channel.guild.voice_client.finish()
    for _ in range(100):
        if following.state.value == "disposed":
            break
        await asyncio.sleep(0.01)

    assert provider.calls_by_title["next"] == 2
    assert following.state.value == "disposed"
    await player.close()


@pytest.mark.asyncio
async def test_prepared_cleanup_never_blocks_other_event_loop_work(app_config) -> None:
    player, channel, _, _ = build_player(app_config)
    provider = BlockingCleanupProvider()
    providers = ProviderRegistry()
    providers.register(provider)
    player.providers = providers
    await player.connect(channel)
    await player.add(make_track("current"))
    source = provider.sources[0]

    stopping = asyncio.create_task(player.stop())
    assert await asyncio.to_thread(source.cleanup_started.wait, 1)
    ticked = asyncio.Event()
    asyncio.get_running_loop().call_soon(ticked.set)
    await asyncio.wait_for(ticked.wait(), 0.1)
    assert not stopping.done()

    source.release_cleanup.set()
    await asyncio.wait_for(stopping, 1)
    assert source.cleaned
    await player.close()


@pytest.mark.asyncio
async def test_cancelled_cleanup_returns_within_the_callers_timeout(app_config) -> None:
    player, channel, _, _ = build_player(app_config)
    provider = BlockingCleanupProvider()
    providers = ProviderRegistry()
    providers.register(provider)
    player.providers = providers
    await player.connect(channel)
    await player.add(make_track("current"))
    source = provider.sources[0]
    loop = asyncio.get_running_loop()
    started_at = loop.time()

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(player.stop(), 0.05)

    assert loop.time() - started_at < 0.2
    assert source.cleanup_started.is_set()
    source.release_cleanup.set()
    await asyncio.sleep(0.02)
    await player.close()


@pytest.mark.asyncio
async def test_failed_prepared_cleanup_prevents_another_provider_prepare(app_config) -> None:
    player, channel, _, _ = build_player(app_config)
    provider = FailingPreparedCleanupProvider()
    providers = ProviderRegistry()
    providers.register(provider)
    player.providers = providers
    await player.connect(channel)
    await player.add(make_track("current"))

    with pytest.raises(ResourceCleanupError):
        await player.stop()
    blocked = make_track("blocked")
    with pytest.raises(ResourceCleanupError):
        await player.add(blocked)

    assert provider.prepare_calls == 1
    assert player.current is None
    blocked.dispose()
    await player.close()


@pytest.mark.asyncio
async def test_upload_reservations_include_pending_and_owned_bytes(app_config) -> None:
    limited = replace(app_config, max_queued_upload_bytes=10)
    player, channel, _, _ = build_player(limited)
    await player.connect(channel)
    current = make_track("current")
    current.provider_data["upload_size"] = 4
    await player.add(current)
    reservation = await player.reserve_upload(6)

    with pytest.raises(QueueLimitError):
        await player.reserve_upload(1)

    await player.release_upload_reservation(reservation)
    accepted = await player.reserve_upload(6)
    assert accepted is not None
    await player.release_upload_reservation(accepted)
    await player.close()


@pytest.mark.asyncio
async def test_concurrent_upload_reservations_cannot_overcommit_memory(app_config) -> None:
    limited = replace(app_config, max_queued_upload_bytes=10)
    player, _, _, _ = build_player(limited)

    results = await asyncio.gather(
        player.reserve_upload(6),
        player.reserve_upload(6),
        return_exceptions=True,
    )

    reservations = [result for result in results if not isinstance(result, BaseException)]
    errors = [result for result in results if isinstance(result, BaseException)]
    assert len(reservations) == 1
    assert len(errors) == 1 and isinstance(errors[0], QueueLimitError)
    await player.release_upload_reservation(reservations[0])
    await player.close()


@pytest.mark.asyncio
async def test_upload_reservation_transfers_atomically_to_accepted_track(app_config) -> None:
    limited = replace(app_config, max_queued_upload_bytes=10)
    player, channel, _, _ = build_player(limited)
    track = make_track("upload")
    track.provider_data["upload_size"] = 6
    reservation = await player.reserve_upload(6)
    assert reservation is not None

    result = await player.commit_play(
        track,
        channel,
        access_check=lambda _: True,
        upload_reservation=reservation,
    )
    assert result.add_result.started
    remaining = await player.reserve_upload(4)
    assert remaining is not None

    await player.release_upload_reservation(remaining)
    await player.close()


@pytest.mark.asyncio
async def test_cancelled_upload_worker_keeps_its_memory_reserved(app_config) -> None:
    limited = replace(app_config, max_queued_upload_bytes=10)
    player, _, _, _ = build_player(limited)
    reservation = await player.reserve_upload(10)
    assert reservation is not None
    payload = b"x" * 10
    started = threading.Event()
    release = threading.Event()

    def metadata() -> int:
        started.set()
        release.wait(timeout=2)
        return len(payload)

    async def inspect() -> None:
        with player.observe_upload_work(reservation):
            await run_blocking(metadata)

    inspection = asyncio.create_task(inspect())
    assert await asyncio.to_thread(started.wait, 1)
    inspection.cancel()
    with pytest.raises(asyncio.CancelledError):
        await inspection
    await player.release_upload_reservation(reservation)

    with pytest.raises(QueueLimitError):
        await player.reserve_upload(1)

    release.set()
    for _ in range(100):
        try:
            replacement = await player.reserve_upload(10)
        except QueueLimitError:
            await asyncio.sleep(0.01)
        else:
            break
    else:
        pytest.fail("detached upload reservation was not released")
    assert replacement is not None
    await player.release_upload_reservation(replacement)
    await player.close()


@pytest.mark.asyncio
async def test_current_and_prefetched_upload_copies_count_toward_memory_limit(
    app_config,
) -> None:
    limited = replace(app_config, max_queued_upload_bytes=24)
    player, channel, _, _ = build_player(limited)
    provider = UploadCopyProvider()
    providers = ProviderRegistry()
    providers.register(provider)
    player.providers = providers
    await player.connect(channel)
    first = make_track("first upload")
    first.provider_data["upload_size"] = 6
    first.owned_resource = io.BytesIO(b"a" * 6)
    second = make_track("second upload")
    second.provider_data["upload_size"] = 6
    second.owned_resource = io.BytesIO(b"b" * 6)

    await player.add(first)
    await player.add(second)
    for _ in range(100):
        if player.prepared_next is not None:
            break
        await asyncio.sleep(0.01)

    assert player.current_prepared is not None
    assert player.prepared_next is not None
    assert player._total_upload_bytes_locked() == 24
    with pytest.raises(QueueLimitError):
        await player.reserve_upload(1)

    await player.close()
    assert not player._prepared_memory_reservations


@pytest.mark.asyncio
async def test_failed_detached_cleanup_prevents_another_source(app_config) -> None:
    player, channel, _, reporter = build_player(app_config)
    provider = FailingDetachedCleanupProvider()
    providers = ProviderRegistry()
    providers.register(provider)
    player.providers = providers
    await player.connect(channel)

    first_add = asyncio.create_task(player.add(make_track("first")))
    assert await asyncio.to_thread(provider.current_started.wait, 1)
    await asyncio.wait_for(player.stop(), 1)
    assert not (await first_add).started

    second = make_track("second")
    second_add = asyncio.create_task(player.add(second))
    await asyncio.sleep(0.02)
    assert not second_add.done()
    provider.release_current.set()

    result = await asyncio.wait_for(second_add, 1)
    assert not result.started
    assert provider.prepare_calls == 1
    assert player.current is None
    assert second.state.value == "disposed"
    assert any(event == "music.prepare.failed" for _, event, _, _ in reporter.reports)
    third = make_track("third")
    with pytest.raises(ResourceCleanupError):
        await player.add(third)
    assert provider.prepare_calls == 1
    third.dispose()
    await player.close()
