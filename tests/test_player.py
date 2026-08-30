from __future__ import annotations

import asyncio

import pytest

from charlotte.music.models import PreparedAudio
from charlotte.music.player import GuildPlayer
from charlotte.music.provider import ProviderRegistry
from tests.fakes import (
    FakeBot,
    FakeGuild,
    FakeProvider,
    FakeReporter,
    FakeSource,
    FakeTextChannel,
    FakeVoiceChannel,
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
