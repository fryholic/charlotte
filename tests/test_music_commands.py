from __future__ import annotations

import asyncio
import io
import logging
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest

from charlotte.extensions.music_commands import MusicCommandsCog
from charlotte.music.models import (
    AddResult,
    PlayCommitResult,
    PreparedAudio,
    RequestContext,
    Track,
    UploadReservation,
)
from charlotte.music.player import GuildPlayer
from charlotte.music.provider import ProviderRegistry
from tests.fakes import (
    FakeBot,
    FakeGuild,
    FakeReporter,
    FakeSource,
    FakeTextChannel,
    FakeVoiceChannel,
)


class Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class TextChannel:
    id = 500


class VoiceChannel:
    id = 600
    name = "music"

    def permissions_for(self, member):
        return SimpleNamespace(connect=True, speak=True)


class PermittedFakeVoiceChannel(FakeVoiceChannel):
    def permissions_for(self, member):
        return SimpleNamespace(connect=True, speak=True)


class RecoveryOwnedProvider:
    name = "fake"
    supports_upload = False

    def __init__(self) -> None:
        self.prepare_calls = 0
        self.first_started = asyncio.Event()
        self.first_cancelled = asyncio.Event()
        self.release_cancel = asyncio.Event()
        self.track: Track | None = None

    def supports_url(self, parsed_url) -> bool:
        return parsed_url.hostname == "example.com"

    async def inspect_url(self, request, parsed_url, raw_url):
        self.track = Track(
            provider=self.name,
            title="owned upload-like track",
            requester_id=request.requester_id,
            requester_display_name=request.requester_display_name,
            request_channel_id=request.text_channel_id,
            canonical_url=raw_url,
            owned_resource=io.BytesIO(b"owned"),
        )
        return self.track

    async def inspect_upload(self, request, attachment):
        raise NotImplementedError

    async def prepare(self, track, *, start_at=0):
        self.prepare_calls += 1
        if self.prepare_calls == 1:
            self.first_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.first_cancelled.set()
                await asyncio.shield(self.release_cancel.wait())
                raise
        return PreparedAudio(source=FakeSource(), seekable=True)


class Context:
    def __init__(self, *, author, attachments=()) -> None:
        self.author = author
        self.guild = SimpleNamespace(id=1, name="guild", me=object())
        self.channel = TextChannel()
        self.message = SimpleNamespace(attachments=list(attachments))
        self.command = SimpleNamespace(__str__=lambda self: "play")
        self.sent = []

    def typing(self):
        return Typing()

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


class Players:
    def __init__(self, player) -> None:
        self.player = player

    async def get(self, guild_id):
        return self.player


class Providers:
    async def inspect_url(self, request: RequestContext, parsed_url, raw_url) -> Track:
        return Track(
            provider="fake",
            title="track",
            requester_id=request.requester_id,
            requester_display_name=request.requester_display_name,
            request_channel_id=request.text_channel_id,
            canonical_url=raw_url,
        )

    async def inspect_upload(self, request: RequestContext, attachment) -> Track:
        return Track(
            provider="fake",
            title=attachment.filename,
            requester_id=request.requester_id,
            requester_display_name=request.requester_display_name,
            request_channel_id=request.text_channel_id,
            owned_resource=io.BytesIO(b"x" * attachment.size),
            provider_data={"upload_size": attachment.size},
        )


class CommandPlayer:
    def __init__(self, initial_channel, changed_channel=None) -> None:
        self.bot_channel = initial_channel
        self.changed_channel = changed_channel
        self.cancelled = False
        self.finished = False
        self.stopped = False
        self.connected_to = None
        self.added = None
        self.committed_reservation = None

    def issue_receipt(self):
        return 0

    async def wait_for_receipt(self, receipt):
        if self.changed_channel is not None:
            self.bot_channel = self.changed_channel

    async def finish_receipt(self, receipt):
        self.finished = True

    async def cancel_receipt(self, receipt):
        self.cancelled = True

    async def reserve_upload(self, declared_size):
        return None

    async def adjust_upload_reservation(self, reservation, actual_size):
        return None

    async def release_upload_reservation(self, reservation):
        return None

    def observe_upload_work(self, reservation):
        return nullcontext()

    async def commit_play(self, track, channel, *, access_check, upload_reservation=None):
        assert access_check(self.bot_channel)
        self.stopped = self.bot_channel is not None and self.bot_channel != channel
        self.connected_to = channel
        self.bot_channel = channel
        self.added = track
        self.committed_reservation = upload_reservation
        return PlayCommitResult(
            add_result=AddResult(started=True, queued_position=None),
            moved=True,
            remote_move=self.stopped,
            removed_count=2 if self.stopped else 0,
        )


class UploadCommandPlayer(CommandPlayer):
    def __init__(self, initial_channel) -> None:
        super().__init__(initial_channel)
        self.observed_reservation = None

    async def reserve_upload(self, declared_size):
        return UploadReservation("upload-test", declared_size)

    @contextmanager
    def observe_upload_work(self, reservation):
        self.observed_reservation = reservation
        yield


def bot_for(player):
    return SimpleNamespace(
        players=Players(player),
        providers=Providers(),
        reporter=FakeReporter(),
        config=SimpleNamespace(
            command_prefix="!",
            operator_user_ids=frozenset(),
            max_queued_upload_bytes=0,
        ),
        log=logging.getLogger("test.music_commands"),
    )


@pytest.mark.asyncio
async def test_play_rechecks_voice_state_before_committing_remote_admin_move() -> None:
    target = VoiceChannel()
    initial = target
    changed = SimpleNamespace(id=700, name="other")
    player = CommandPlayer(initial, changed)
    bot = bot_for(player)
    author = SimpleNamespace(
        id=10,
        display_name="requester",
        voice=SimpleNamespace(channel=target),
        guild_permissions=SimpleNamespace(administrator=True),
    )
    ctx = Context(author=author)

    await MusicCommandsCog.play.callback(
        MusicCommandsCog(bot), ctx, url="https://example.com/track"
    )

    assert player.stopped
    assert player.connected_to is target
    assert player.added is not None
    assert player.finished and not player.cancelled
    assert "채널 이동 후 재생" in ctx.sent[0][0]


@pytest.mark.asyncio
async def test_upload_inspection_and_commit_share_one_memory_reservation() -> None:
    target = VoiceChannel()
    player = UploadCommandPlayer(target)
    bot = bot_for(player)
    author = SimpleNamespace(
        id=10,
        display_name="requester",
        voice=SimpleNamespace(channel=target),
        guild_permissions=SimpleNamespace(administrator=False),
    )
    attachment = SimpleNamespace(filename="sample.wav", size=6)
    ctx = Context(author=author, attachments=(attachment,))

    await MusicCommandsCog.play.callback(MusicCommandsCog(bot), ctx)

    assert player.observed_reservation is not None
    assert player.committed_reservation is player.observed_reservation
    assert player.added is not None and player.added.upload_size == 6


@pytest.mark.asyncio
async def test_play_transfers_track_ownership_when_recovery_cancels_prepare(app_config) -> None:
    discord_bot = FakeBot()
    guild = FakeGuild(1)
    target = PermittedFakeVoiceChannel(guild, 600, "music")
    discord_bot.guilds[guild.id] = guild
    discord_bot.channels[500] = FakeTextChannel(500)
    provider = RecoveryOwnedProvider()
    providers = ProviderRegistry()
    providers.register(provider)
    reporter = FakeReporter()
    player = GuildPlayer(
        guild.id,
        bot=discord_bot,
        config=app_config,
        providers=providers,
        reporter=reporter,
    )
    bot = SimpleNamespace(
        players=Players(player),
        providers=providers,
        reporter=reporter,
        config=app_config,
        log=logging.getLogger("test.music_commands.recovery"),
    )
    author = SimpleNamespace(
        id=10,
        display_name="requester",
        voice=SimpleNamespace(channel=target),
        guild_permissions=SimpleNamespace(administrator=False),
    )
    ctx = Context(author=author)

    command = asyncio.create_task(
        MusicCommandsCog.play.callback(MusicCommandsCog(bot), ctx, url="https://example.com/track")
    )
    await asyncio.wait_for(provider.first_started.wait(), 1)
    disconnected = player.voice_client
    assert disconnected is not None
    disconnected.connected = False
    guild.voice_client = None
    recovery = asyncio.create_task(
        player.recover_voice(target, expected_track_id=provider.track.id)
    )
    await asyncio.wait_for(provider.first_cancelled.wait(), 1)
    provider.release_cancel.set()

    await asyncio.wait_for(asyncio.gather(command, recovery), 1)
    assert provider.track is player.current
    assert provider.track.owned_resource is not None
    assert not provider.track.owned_resource.closed
    assert provider.prepare_calls == 2
    await player.close()
    assert provider.track.owned_resource.closed


@pytest.mark.asyncio
async def test_play_rejects_multiple_attachments_before_voice_changes() -> None:
    player = CommandPlayer(None)
    bot = bot_for(player)
    author = SimpleNamespace(id=10, display_name="requester")
    ctx = Context(
        author=author,
        attachments=(SimpleNamespace(filename="a"), SimpleNamespace(filename="b")),
    )

    await MusicCommandsCog.play.callback(MusicCommandsCog(bot), ctx, url=None)

    assert player.cancelled
    assert not player.stopped
    assert ctx.sent[0][0] == "오디오 파일은 하나만 보내 주세요!"


@pytest.mark.asyncio
async def test_help_uses_the_instance_prefix_and_hides_operator_commands() -> None:
    bot = bot_for(CommandPlayer(None))
    author = SimpleNamespace(id=10, display_name="requester")
    ctx = Context(author=author)

    await MusicCommandsCog.help_command.callback(MusicCommandsCog(bot), ctx)

    content = ctx.sent[0][0]
    assert "!play" in content
    assert "extension" not in content
