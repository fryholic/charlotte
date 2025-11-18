"""Helpers for expanding Discord custom emoji messages."""

from __future__ import annotations

from typing import Optional

import discord

from .regex import SINGLE_EMOJI_REGEX


def _resolve_embed_color(member: discord.Member) -> discord.Colour:
    """Return a consistent embed color for the member."""
    color = getattr(member, "color", discord.Colour.default())
    if color == discord.Colour.default():
        return discord.Colour.greyple()
    return color


def build_emoji_embed(message: discord.Message) -> Optional[discord.Embed]:
    """Return an embed for single custom emoji messages, otherwise None."""
    match = SINGLE_EMOJI_REGEX.match(message.content)
    if not match:
        return None

    emoji_id = match.group(3)
    extension = ".gif" if match.group(1) else ".png"

    embed = discord.Embed(color=_resolve_embed_color(message.author))
    embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar)
    embed.set_image(url=f"https://cdn.discordapp.com/emojis/{emoji_id}{extension}")
    return embed
