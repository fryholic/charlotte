"""Lazy, multi-guild player ownership."""

from __future__ import annotations

import asyncio
import logging

import discord

from charlotte.config import AppConfig
from charlotte.music.player import GuildPlayer
from charlotte.music.provider import ProviderRegistry
from charlotte.observability import ErrorReporter, log_exception


class PlayerRegistry:
    def __init__(
        self,
        *,
        bot: discord.Client,
        config: AppConfig,
        providers: ProviderRegistry,
        reporter: ErrorReporter,
    ) -> None:
        self.bot = bot
        self.config = config
        self.providers = providers
        self.reporter = reporter
        self._players: dict[int, GuildPlayer] = {}
        self._lock = asyncio.Lock()
        self.log = logging.getLogger("charlotte.players")

    async def get(self, guild_id: int) -> GuildPlayer:
        player = self._players.get(guild_id)
        if player is not None:
            return player
        async with self._lock:
            player = self._players.get(guild_id)
            if player is None:
                player = GuildPlayer(
                    guild_id,
                    bot=self.bot,
                    config=self.config,
                    providers=self.providers,
                    reporter=self.reporter,
                )
                self._players[guild_id] = player
            return player

    def peek(self, guild_id: int) -> GuildPlayer | None:
        return self._players.get(guild_id)

    def any_activity(self) -> bool:
        return any(player.has_activity for player in self._players.values())

    async def remove(self, guild_id: int) -> None:
        async with self._lock:
            player = self._players.pop(guild_id, None)
        if player is not None:
            await player.close()

    async def close(self) -> None:
        async with self._lock:
            players = list(self._players.values())
            self._players.clear()
        results = await asyncio.gather(
            *(player.close() for player in players), return_exceptions=True
        )
        for player, result in zip(players, results, strict=True):
            if isinstance(result, BaseException):
                log_exception(
                    self.log,
                    result,
                    event="player.shutdown_failed",
                    context={"guild_id": player.guild_id},
                )
