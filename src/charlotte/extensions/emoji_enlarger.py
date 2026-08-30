"""Delete-first custom emoji enlargement with instance-specific channel policy."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass

import discord
from discord.ext import commands

from charlotte.extensions.contract import ExtensionKind, ExtensionMetadata
from charlotte.messages import render
from charlotte.observability import ErrorContext

EXTENSION_META = ExtensionMetadata(
    name="emoji_enlarger",
    kind=ExtensionKind.FEATURE,
    load_order=30,
)

_EMOJI = re.compile(r"^<(?P<animated>a?):(?P<name>[A-Za-z0-9_]{2,32}):(?P<id>[0-9]{17,20})>$")


@dataclass(frozen=True, slots=True)
class ParsedEmoji:
    animated: bool
    name: str
    emoji_id: int


def parse_custom_emoji(content: str) -> ParsedEmoji | None:
    match = _EMOJI.fullmatch(content.strip())
    if match is None:
        return None
    return ParsedEmoji(
        animated=bool(match.group("animated")),
        name=match.group("name"),
        emoji_id=int(match.group("id")),
    )


def build_embed(message: discord.Message, emoji: ParsedEmoji) -> discord.Embed:
    member_colour = getattr(message.author, "colour", discord.Colour.default())
    colour = (
        member_colour if member_colour != discord.Colour.default() else discord.Colour.greyple()
    )
    extension = "gif" if emoji.animated else "png"
    embed = discord.Embed(colour=colour)
    embed.set_author(
        name=message.author.display_name,
        icon_url=str(message.author.display_avatar.url),
    )
    embed.set_image(
        url=f"https://cdn.discordapp.com/emojis/{emoji.emoji_id}.{extension}?quality=lossless"
    )
    return embed


class EmojiEnlargerCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self._recent: dict[int, float] = {}
        self._lock = asyncio.Lock()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not self.bot.config.emoji_enabled or not _eligible_author(message):
            return
        emoji = parse_custom_emoji(message.content)
        if emoji is None or not self._allowed_channel(message):
            return
        if not _has_permissions(message):
            return
        if not await self._claim(message.id):
            return
        context = _fallback_error_context(message, emoji)
        try:
            context = _error_context(message, emoji)
            embed = build_embed(message, emoji)
        except Exception as exc:
            await self._notice(message.channel, render("emoji.replace_failed"), context)
            await self.bot.reporter.report(
                exc,
                event="emoji.prepare_failed",
                context=context,
            )
            return
        try:
            await message.delete()
        except discord.NotFound:
            return
        except Exception as exc:
            await self._notice(message.channel, render("emoji.replace_failed"), context)
            await self.bot.reporter.report(exc, event="emoji.delete_failed", context=context)
            return
        try:
            await message.channel.send(
                embed=embed,
                reference=message.reference,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            self.bot.log.info(
                "Custom emoji enlarged",
                extra={
                    "event": "emoji.replaced",
                    "guild_id": message.guild.id,
                    "channel_id": message.channel.id,
                    "message_id": message.id,
                    "emoji_id": emoji.emoji_id,
                },
            )
        except Exception as exc:
            await self._notice(message.channel, render("emoji.original_lost"), context)
            await self.bot.reporter.report(exc, event="emoji.send_failed", context=context)

    def _allowed_channel(self, message: discord.Message) -> bool:
        allowed = self.bot.config.emoji_allowed_channel_ids
        if not allowed:
            return True
        channel_id = getattr(message.channel, "parent_id", None) or message.channel.id
        return channel_id in allowed

    async def _claim(self, message_id: int) -> bool:
        async with self._lock:
            now = time.monotonic()
            cutoff = now - 300
            if len(self._recent) > 2048:
                self._recent = {
                    key: value for key, value in self._recent.items() if value >= cutoff
                }
            if message_id in self._recent:
                return False
            self._recent[message_id] = now
            return True

    async def _notice(self, channel, content: str, context: ErrorContext) -> None:
        try:
            await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
        except Exception as error:
            await self.bot.reporter.report(
                error,
                event="emoji.notice_failed",
                context=context,
                notify_owner=False,
            )


def _eligible_author(message: discord.Message) -> bool:
    return bool(
        message.guild is not None
        and not message.author.bot
        and message.webhook_id is None
        and not message.is_system()
    )


def _has_permissions(message: discord.Message) -> bool:
    member = message.guild.me if message.guild is not None else None
    if member is None:
        return False
    permissions = message.channel.permissions_for(member)
    can_send = (
        permissions.send_messages_in_threads
        if isinstance(message.channel, discord.Thread)
        else permissions.send_messages
    )
    return all(
        (
            permissions.view_channel,
            permissions.read_message_history,
            can_send,
            permissions.embed_links,
            permissions.manage_messages,
        )
    )


def _error_context(message: discord.Message, emoji: ParsedEmoji) -> ErrorContext:
    return ErrorContext(
        guild_name=message.guild.name if message.guild else None,
        guild_id=message.guild.id if message.guild else None,
        channel_name=getattr(message.channel, "name", None),
        channel_id=message.channel.id,
        message_content=message.content[:500],
        author_name=message.author.display_name,
        message_id=message.id,
        emoji_id=emoji.emoji_id,
    )


def _fallback_error_context(message: discord.Message, emoji: ParsedEmoji) -> ErrorContext:
    guild = _safe_attribute(message, "guild")
    channel = _safe_attribute(message, "channel")
    author = _safe_attribute(message, "author")
    content = _safe_attribute(message, "content")
    return ErrorContext(
        guild_name=_safe_attribute(guild, "name"),
        guild_id=_safe_attribute(guild, "id"),
        channel_name=_safe_attribute(channel, "name"),
        channel_id=_safe_attribute(channel, "id"),
        message_content=content[:500] if isinstance(content, str) else None,
        author_name=_safe_attribute(author, "display_name"),
        message_id=_safe_attribute(message, "id"),
        emoji_id=emoji.emoji_id,
    )


def _safe_attribute(value: object, name: str) -> object | None:
    try:
        return getattr(value, name, None)
    except Exception:
        return None


async def setup(bot) -> None:
    await bot.add_cog(EmojiEnlargerCog(bot))
