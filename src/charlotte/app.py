"""Discord bot composition and lifecycle."""

from __future__ import annotations

import logging
import sys

import discord
from discord.ext import commands

from charlotte.config import AppConfig
from charlotte.extensions.manager import ExtensionManager
from charlotte.health import HealthWriter
from charlotte.messages import render
from charlotte.music.provider import ProviderRegistry
from charlotte.music.registry import PlayerRegistry
from charlotte.observability import ErrorContext, ErrorReporter, log_exception


def required_intents() -> discord.Intents:
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True
    intents.message_content = True
    intents.voice_states = True
    return intents


class CharlotteBot(commands.Bot):
    def __init__(self, config: AppConfig) -> None:
        super().__init__(
            command_prefix=config.command_prefix,
            intents=required_intents(),
            help_command=None,
            allowed_mentions=discord.AllowedMentions.none(),
            case_insensitive=True,
        )
        self.config = config
        self.providers = ProviderRegistry()
        self.reporter = ErrorReporter(config)
        self.players = PlayerRegistry(
            bot=self,
            config=config,
            providers=self.providers,
            reporter=self.reporter,
        )
        self.extension_manager = ExtensionManager(self)
        self.health_writer = HealthWriter(self)
        self._owner_resolved = False
        self._closing = False
        self.log = logging.getLogger("charlotte.app")

    async def setup_hook(self) -> None:
        self.extension_manager.discover()
        try:
            await self.extension_manager.load_startup()
        except Exception:
            for module in reversed(tuple(self.extensions)):
                try:
                    await self.unload_extension(module)
                except Exception as error:
                    log_exception(
                        self.log,
                        error,
                        event="extension.rollback_failed",
                        context={"extension": module},
                    )
            raise

    async def on_ready(self) -> None:
        if not self._owner_resolved:
            self._owner_resolved = await self.reporter.resolve_owner(self)
        self.health_writer.start()
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name=f"{self.config.command_prefix}help",
            )
        )
        self.log.info(
            "Discord bot ready",
            extra={
                "event": "app.ready",
                "guild_count": len(self.guilds),
                "environment": self.config.environment.value,
            },
        )

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        await self.players.remove(guild.id)

    async def on_error(self, event_method: str, *args: object, **kwargs: object) -> None:
        error = sys.exc_info()[1]
        if error is None:
            error = RuntimeError(f"Discord event failed without exception: {event_method}")
        await self.reporter.report(error, event=f"discord.{event_method}.failed")

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        underlying = getattr(error, "original", error)
        if isinstance(error, commands.NoPrivateMessage):
            self.reporter.expected(
                "command.guild_only",
                context=ErrorContext(
                    channel_id=getattr(ctx.channel, "id", None),
                    command=str(ctx.command) if ctx.command else None,
                    requester_name=getattr(ctx.author, "display_name", None),
                    requester_id=getattr(ctx.author, "id", None),
                ),
                error=underlying,
            )
            await ctx.send(
                render("common.command_failed"),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        if isinstance(error, (commands.UserInputError, commands.CheckFailure)):
            self.reporter.expected(
                "command.usage_failed",
                context=ErrorContext(
                    guild_name=ctx.guild.name if ctx.guild else None,
                    guild_id=ctx.guild.id if ctx.guild else None,
                    channel_name=getattr(ctx.channel, "name", None),
                    channel_id=getattr(ctx.channel, "id", None),
                    command=str(ctx.command) if ctx.command else None,
                    requester_name=getattr(ctx.author, "display_name", None),
                    requester_id=getattr(ctx.author, "id", None),
                ),
                error=underlying,
            )
            await ctx.send(render("common.command_failed"))
            return
        await self.reporter.report(
            underlying,
            event="command.unhandled",
            context=ErrorContext(
                guild_name=ctx.guild.name if ctx.guild else None,
                guild_id=ctx.guild.id if ctx.guild else None,
                channel_name=getattr(ctx.channel, "name", None),
                channel_id=getattr(ctx.channel, "id", None),
                command=str(ctx.command) if ctx.command else None,
                requester_name=getattr(ctx.author, "display_name", None),
                requester_id=getattr(ctx.author, "id", None),
            ),
        )
        try:
            await ctx.send(render("common.command_failed"))
        except Exception as send_error:
            log_exception(
                self.log,
                send_error,
                event="command.error_response_failed",
                context={"origin_event": "command.unhandled"},
            )

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.log.info("Charlotte stopping", extra={"event": "app.stopping"})
        cleanup_steps = (
            ("health", self.health_writer.stop),
            ("players", self.players.close),
            ("discord", super().close),
        )
        for component, cleanup in cleanup_steps:
            try:
                await cleanup()
            except Exception as error:
                log_exception(
                    self.log,
                    error,
                    event="app.shutdown_failed",
                    context={"component": component},
                )
        self.log.info("Charlotte stopped", extra={"event": "app.stopped"})


def create_bot(config: AppConfig) -> CharlotteBot:
    return CharlotteBot(config)
