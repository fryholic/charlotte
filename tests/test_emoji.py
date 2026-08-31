from __future__ import annotations

import logging
from types import SimpleNamespace

import discord
import pytest

from charlotte.extensions import emoji_enlarger as emoji_module
from charlotte.extensions.emoji_enlarger import EmojiEnlargerCog, parse_custom_emoji


class Permissions:
    view_channel = True
    read_message_history = True
    send_messages = True
    send_messages_in_threads = True
    embed_links = True
    manage_messages = True


class Channel:
    def __init__(self, channel_id=10, *, parent_id=None) -> None:
        self.id = channel_id
        self.name = "emoji-test"
        self.parent_id = parent_id
        self.sent = []
        self.send_error: BaseException | None = None
        self.permissions = Permissions()

    def permissions_for(self, member):
        return self.permissions

    async def send(self, content=None, **kwargs):
        if self.send_error is not None:
            error = self.send_error
            self.send_error = None
            raise error
        self.sent.append((content, kwargs))
        return SimpleNamespace(id=1000 + len(self.sent))


class Message:
    def __init__(self, channel: Channel, *, message_id=100, content="<:hello:12345678901234567>"):
        self.id = message_id
        self.content = content
        self.channel = channel
        self.reference = SimpleNamespace(message_id=77)
        self.webhook_id = None
        self.author = SimpleNamespace(
            id=42,
            bot=False,
            display_name="member",
            display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
            colour=discord.Colour.blue(),
        )
        self.guild = SimpleNamespace(id=1, name="guild", me=SimpleNamespace(id=999))
        self.deleted = False
        self.delete_error: BaseException | None = None

    def is_system(self) -> bool:
        return False

    async def delete(self) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted = True


class Reporter:
    def __init__(self) -> None:
        self.reports = []

    async def report(self, error, *, event, context=None, notify_owner=True):
        self.reports.append((error, event, context, notify_owner))
        return "error-id"


def build_cog(app_config, *, allowed=frozenset()):
    config = SimpleNamespace(
        emoji_enabled=True,
        emoji_allowed_channel_ids=allowed,
    )
    reporter = Reporter()
    bot = SimpleNamespace(config=config, reporter=reporter, log=logging.getLogger("test.emoji"))
    return EmojiEnlargerCog(bot), reporter


def test_parses_one_static_or_animated_custom_emoji() -> None:
    static = parse_custom_emoji("  <:hello:12345678901234567> ")
    animated = parse_custom_emoji("<a:hello:123456789012345678>")
    assert static and not static.animated
    assert animated and animated.animated


def test_rejects_unicode_text_and_multiple_custom_emoji() -> None:
    assert parse_custom_emoji("😀") is None
    assert parse_custom_emoji("x <:hello:12345678901234567>") is None
    assert parse_custom_emoji("<:hello:12345678901234567><:world:22345678901234567>") is None


@pytest.mark.asyncio
async def test_effectful_replacement_preserves_reply_and_disables_mentions(app_config) -> None:
    cog, reporter = build_cog(app_config)
    channel = Channel()
    message = Message(channel)
    await cog.on_message(message)
    assert message.deleted
    assert len(channel.sent) == 1
    content, kwargs = channel.sent[0]
    assert content is None
    assert kwargs["reference"] is message.reference
    assert kwargs["mention_author"] is False
    assert kwargs["allowed_mentions"].everyone is False
    assert kwargs["allowed_mentions"].users is False
    assert kwargs["embed"].image.url.endswith(".png?quality=lossless")
    assert reporter.reports == []


@pytest.mark.asyncio
async def test_permission_failure_is_a_noop(app_config) -> None:
    cog, reporter = build_cog(app_config)
    channel = Channel()
    channel.permissions.manage_messages = False
    message = Message(channel)
    await cog.on_message(message)
    assert not message.deleted
    assert channel.sent == []
    assert reporter.reports == []


@pytest.mark.asyncio
async def test_thread_parent_id_controls_allowlist(app_config) -> None:
    cog, _ = build_cog(app_config, allowed=frozenset({20}))
    allowed_channel = Channel(21, parent_id=20)
    blocked_channel = Channel(22, parent_id=999)
    allowed = Message(allowed_channel, message_id=101)
    blocked = Message(blocked_channel, message_id=102)
    await cog.on_message(allowed)
    await cog.on_message(blocked)
    assert allowed.deleted
    assert not blocked.deleted


@pytest.mark.asyncio
async def test_delete_failure_keeps_original_and_reports_context(app_config) -> None:
    cog, reporter = build_cog(app_config)
    channel = Channel()
    message = Message(channel)
    message.delete_error = RuntimeError("delete failed")
    await cog.on_message(message)
    assert not message.deleted
    assert channel.sent[0][0]
    assert len(reporter.reports) == 1
    _, event, context, notify_owner = reporter.reports[0]
    assert event == "emoji.delete_failed"
    assert context.message_id == message.id
    assert context.message_content == message.content
    assert context.author_name == "member"
    assert notify_owner is True


@pytest.mark.asyncio
async def test_send_failure_reports_original_loss_without_mentions(app_config) -> None:
    cog, reporter = build_cog(app_config)
    channel = Channel()
    channel.send_error = RuntimeError("embed send failed")
    message = Message(channel)
    await cog.on_message(message)
    assert message.deleted
    assert len(channel.sent) == 1
    notice, kwargs = channel.sent[0]
    assert "원본 메시지가 삭제" in notice
    assert kwargs["allowed_mentions"].everyone is False
    assert reporter.reports[0][1] == "emoji.send_failed"


@pytest.mark.asyncio
async def test_duplicate_event_is_claimed_only_once(app_config) -> None:
    cog, _ = build_cog(app_config)
    channel = Channel()
    message = Message(channel)
    await cog.on_message(message)
    await cog.on_message(message)
    assert len(channel.sent) == 1


@pytest.mark.asyncio
async def test_embed_build_failure_keeps_original_and_reports(monkeypatch, app_config) -> None:
    cog, reporter = build_cog(app_config)
    channel = Channel()
    message = Message(channel)

    def fail_build(message, emoji):
        raise RuntimeError("avatar lookup failed")

    monkeypatch.setattr(emoji_module, "build_embed", fail_build)
    await cog.on_message(message)

    assert not message.deleted
    assert channel.sent[0][0]
    assert reporter.reports[0][1] == "emoji.prepare_failed"
    assert reporter.reports[0][2].message_id == message.id
