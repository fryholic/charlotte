from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from charlotte.extensions.music_commands import MusicCommandsCog
from charlotte.music.models import AddResult, RequestContext, Track
from tests.fakes import FakeReporter


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


class CommandPlayer:
    def __init__(self, initial_channel, changed_channel=None) -> None:
        self.bot_channel = initial_channel
        self.changed_channel = changed_channel
        self.cancelled = False
        self.finished = False
        self.stopped = False
        self.connected_to = None
        self.added = None

    def issue_receipt(self):
        return 0

    async def wait_for_receipt(self, receipt):
        if self.changed_channel is not None:
            self.bot_channel = self.changed_channel

    async def finish_receipt(self, receipt):
        self.finished = True

    async def cancel_receipt(self, receipt):
        self.cancelled = True

    async def stop(self):
        self.stopped = True
        return SimpleNamespace(removed_count=2)

    async def connect(self, channel):
        self.connected_to = channel
        self.bot_channel = channel
        return True

    async def add(self, track):
        self.added = track
        return AddResult(started=True, queued_position=None)


def bot_for(player):
    return SimpleNamespace(
        players=Players(player),
        providers=Providers(),
        reporter=FakeReporter(),
        config=SimpleNamespace(command_prefix="!", operator_user_ids=frozenset()),
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
