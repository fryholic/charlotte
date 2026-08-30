"""Prefix music commands and operator-only Extension controls."""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import discord
from discord.ext import commands

from charlotte.errors import (
    ExtensionOperationError,
    SourceUnavailableError,
    UserError,
    UserInputError,
)
from charlotte.extensions.contract import ExtensionKind, ExtensionMetadata
from charlotte.messages import queue_embed, render, truncate
from charlotte.music.access import AccessReason, decide_control, decide_play
from charlotte.music.models import RequestContext, Track
from charlotte.observability import ErrorContext

EXTENSION_META = ExtensionMetadata(
    name="music_commands",
    kind=ExtensionKind.COMMAND,
    runtime_protected=True,
    load_order=10,
)


class MusicCommandsCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    @commands.command(name="play")
    @commands.guild_only()
    async def play(self, ctx: commands.Context, *, url: str | None = None) -> None:
        player = await self.bot.players.get(ctx.guild.id)
        receipt = player.issue_receipt()
        track: Track | None = None
        owned_by_player = False
        normalized_url: str | None = None
        filename: str | None = None
        try:
            attachments = tuple(ctx.message.attachments)
            normalized_url = url.strip() if url else None
            filename = str(getattr(attachments[0], "filename", "")) if attachments else None
            if len(attachments) > 1:
                raise UserInputError("music.play.multiple_attachments")
            if normalized_url and attachments:
                raise UserInputError("music.play.ambiguous_input")
            if not normalized_url and not attachments:
                raise UserInputError("music.play.missing_input")
            if normalized_url and any(character.isspace() for character in normalized_url):
                raise UserInputError("music.play.invalid_url")

            decision = decide_play(
                ctx.author, player.bot_channel, self.bot.config.operator_user_ids
            )
            if not decision.allowed:
                if decision.reason is AccessReason.USER_NOT_IN_VOICE and decision.privileged:
                    raise UserInputError("access.admin_play_requires_voice")
                raise UserInputError(_access_message_id(decision.reason))
            target_channel = ctx.author.voice.channel
            _check_voice_permissions(ctx.guild.me, target_channel)

            request = RequestContext(
                guild_id=ctx.guild.id,
                text_channel_id=ctx.channel.id,
                requester_id=ctx.author.id,
                requester_display_name=ctx.author.display_name,
            )
            async with ctx.typing():
                if attachments:
                    track = await self.bot.providers.inspect_upload(request, attachments[0])
                else:
                    parsed = urlparse(normalized_url)
                    if not parsed.scheme or not parsed.hostname:
                        raise UserInputError("music.play.invalid_url")
                    track = await self.bot.providers.inspect_url(request, parsed, normalized_url)
            self.bot.log.info(
                "Track inspected",
                extra={
                    "event": "track.inspected",
                    "guild_id": ctx.guild.id,
                    "track_id": track.id,
                    "provider": track.provider,
                },
            )

            await player.wait_for_receipt(receipt)
            removed_count = 0
            moved = False
            remote_move = False
            try:
                decision = decide_play(
                    ctx.author, player.bot_channel, self.bot.config.operator_user_ids
                )
                if not decision.allowed:
                    if decision.reason is AccessReason.USER_NOT_IN_VOICE and decision.privileged:
                        raise UserInputError("access.admin_play_requires_voice")
                    raise UserInputError(_access_message_id(decision.reason))
                target_channel = ctx.author.voice.channel
                _check_voice_permissions(ctx.guild.me, target_channel)
                remote_move = (
                    player.bot_channel is not None and player.bot_channel != target_channel
                )
                if remote_move:
                    removed_count = (await player.stop()).removed_count
                moved = await player.connect(target_channel)
                result = await player.add(track)
                owned_by_player = True
            finally:
                await player.finish_receipt(receipt)

            title = _safe(track.title, 180)
            requester = _safe(track.requester_display_name, 80)
            if remote_move or moved:
                if result.started:
                    await self._send(
                        ctx,
                        render(
                            "music.play.moved_and_started",
                            title=title,
                            channel=_safe(target_channel.name, 80),
                            requester=requester,
                            removed_count=removed_count,
                        ),
                    )
            elif result.started:
                await self._send(
                    ctx, render("music.play.started", title=title, requester=requester)
                )
            elif result.queued_position is not None:
                await self._send(
                    ctx,
                    render(
                        "music.play.queued",
                        title=title,
                        requester=requester,
                        position=result.queued_position,
                    ),
                )
        except asyncio.CancelledError:
            await player.cancel_receipt(receipt)
            if track is not None and not owned_by_player:
                track.dispose()
            raise
        except Exception as error:
            await player.cancel_receipt(receipt)
            if track is not None and not owned_by_player:
                track.dispose()
            await self._handle_error(
                ctx,
                error,
                "music.play.failed",
                track,
                url=normalized_url,
                filename=filename,
            )

    @commands.command(name="skip")
    @commands.guild_only()
    async def skip(self, ctx: commands.Context) -> None:
        player = self.bot.players.peek(ctx.guild.id)
        if player is None or player.current is None:
            await self._send(ctx, render("music.skip.empty"))
            return
        if not await self._control_allowed(ctx, player):
            return
        result = await player.skip()
        if result.skipped_title is None:
            await self._send(ctx, render("music.skip.empty"))
            return
        await self._send(
            ctx,
            render(
                "music.skip.success",
                skipped_title=_safe(result.skipped_title, 180),
                next_title_or_none=_safe(result.next_title or "없음", 180),
            ),
        )

    @commands.command(name="queue")
    @commands.guild_only()
    async def show_queue(self, ctx: commands.Context) -> None:
        player = self.bot.players.peek(ctx.guild.id)
        if player is None:
            await self._send(ctx, render("music.queue.empty"))
            return
        view = await player.queue_view()
        if view.current is None and not view.upcoming:
            await self._send(ctx, render("music.queue.empty"))
            return
        await ctx.send(
            embed=queue_embed(view.current, view.upcoming),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.command(name="pause")
    @commands.guild_only()
    async def pause(self, ctx: commands.Context) -> None:
        player = self.bot.players.peek(ctx.guild.id)
        if player is None or player.current is None:
            await self._send(ctx, render("music.pause.empty"))
            return
        if not await self._control_allowed(ctx, player):
            return
        if player.is_paused:
            await self._send(ctx, render("music.pause.already_paused"))
            return
        track = await player.pause()
        if track is None:
            await self._send(ctx, render("music.pause.empty"))
            return
        await self._send(ctx, render("music.pause.success", title=_safe(track.title, 180)))

    @commands.command(name="resume")
    @commands.guild_only()
    async def resume(self, ctx: commands.Context) -> None:
        player = self.bot.players.peek(ctx.guild.id)
        if player is None or player.current is None:
            await self._send(ctx, render("music.resume.empty"))
            return
        if not await self._control_allowed(ctx, player):
            return
        if not player.is_paused:
            await self._send(ctx, render("music.resume.not_paused"))
            return
        track = await player.resume()
        if track is None:
            await self._send(ctx, render("music.resume.not_paused"))
            return
        await self._send(ctx, render("music.resume.success", title=_safe(track.title, 180)))

    @commands.command(name="stop")
    @commands.guild_only()
    async def stop(self, ctx: commands.Context) -> None:
        player = self.bot.players.peek(ctx.guild.id)
        if player is None or not player.has_activity:
            await self._send(ctx, render("music.stop.empty"))
            return
        if not await self._control_allowed(ctx, player):
            return
        result = await player.stop()
        if result.removed_count == 0:
            await self._send(ctx, render("music.stop.empty"))
            return
        await self._send(ctx, render("music.stop.success", removed_count=result.removed_count))

    @commands.command(name="leave")
    @commands.guild_only()
    async def leave(self, ctx: commands.Context) -> None:
        player = self.bot.players.peek(ctx.guild.id)
        if player is None or player.bot_channel is None:
            await self._send(ctx, render("music.leave.not_connected"))
            return
        if not await self._control_allowed(ctx, player):
            return
        channel_name, result = await player.leave()
        await self._send(
            ctx,
            render(
                "music.leave.success",
                channel=_safe(channel_name or "음성 채널", 80),
                removed_count=result.removed_count,
            ),
        )

    @commands.command(name="help")
    async def help_command(self, ctx: commands.Context) -> None:
        prefix = self.bot.config.command_prefix
        lines = [
            "Charlotte 음악 명령",
            "",
            f"{prefix}play <YouTube/SoundCloud URL>",
            f"{prefix}play + 오디오 파일 1개",
            f"{prefix}skip",
            f"{prefix}queue",
            f"{prefix}stop",
            f"{prefix}leave",
            f"{prefix}pause",
            f"{prefix}resume",
            f"{prefix}help",
            "",
            "URL과 파일은 한 번에 하나만 사용할 수 있습니다.",
        ]
        await self._send(ctx, "\n".join(lines))

    @commands.group(name="extension", hidden=True, invoke_without_command=True)
    @commands.guild_only()
    async def extension_group(self, ctx: commands.Context) -> None:
        if await self._operator_allowed(ctx):
            await self._extension_list(ctx)

    @extension_group.command(name="list")
    async def extension_list(self, ctx: commands.Context) -> None:
        if await self._operator_allowed(ctx):
            await self._extension_list(ctx)

    @extension_group.command(name="load")
    async def extension_load(self, ctx: commands.Context, name: str) -> None:
        if not await self._operator_allowed(ctx):
            return
        await self._extension_action(ctx, "load", name)

    @extension_group.command(name="unload")
    async def extension_unload(self, ctx: commands.Context, name: str) -> None:
        if not await self._operator_allowed(ctx):
            return
        await self._extension_action(ctx, "unload", name)

    @extension_group.command(name="reload")
    async def extension_reload(self, ctx: commands.Context, name: str) -> None:
        if not await self._operator_allowed(ctx):
            return
        await self._extension_action(ctx, "reload", name)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        player = self.bot.players.peek(member.guild.id)
        if player is None:
            return
        if (
            self.bot.user is not None
            and member.id == self.bot.user.id
            and before.channel is not None
            and after.channel is None
            and player.current is not None
        ):
            await player.recover_voice(before.channel)
            return
        channel = player.bot_channel
        if channel is not None and not any(not item.bot for item in channel.members):
            await player.leave()

    async def _control_allowed(self, ctx: commands.Context, player) -> bool:
        decision = decide_control(ctx.author, player.bot_channel, self.bot.config.operator_user_ids)
        if decision.allowed:
            return True
        await self._send(ctx, render(_access_message_id(decision.reason)))
        return False

    async def _operator_allowed(self, ctx: commands.Context) -> bool:
        if ctx.author.id in self.bot.config.operator_user_ids:
            return True
        await self._send(ctx, render("access.operator_only"))
        return False

    async def _extension_list(self, ctx: commands.Context) -> None:
        statuses = self.bot.extension_manager.statuses()
        loaded = sum(status.loaded for status in statuses)
        lines = [f"Extension 상태 ({loaded}/{len(statuses)} 로드됨)"]
        for status in statuses:
            requirement = "시작 필수" if status.startup_required else "선택"
            if status.loaded:
                marker, state = "✅", "로드됨"
            elif status.failed:
                marker, state = "⚠️", "로드 실패"
            else:
                marker, state = "○", "로드 안 됨"
            lines.append(f"{marker} {status.metadata.name} — {state} · {requirement}")
        await self._send(ctx, "\n".join(lines))

    async def _extension_action(self, ctx: commands.Context, action: str, name: str) -> None:
        manager = self.bot.extension_manager
        try:
            method = getattr(manager, action)
            await method(name)
        except ExtensionOperationError as error:
            detail = str(error).lower()
            if "unknown" in detail:
                await self._send(ctx, render("extension.not_found", name=name))
            elif "protected" in detail:
                await self._send(ctx, render("extension.protected", name=name))
            elif "active" in detail or "inflight" in detail:
                await self._send(ctx, render("extension.active_music"))
            else:
                await self._handle_error(
                    ctx,
                    error,
                    "extension.operation_failed",
                    fallback_message_id="extension.failed",
                )
            return
        except Exception as error:
            await self._handle_error(
                ctx,
                error,
                "extension.operation_failed",
                fallback_message_id="extension.failed",
            )
            return
        message_id = {
            "load": "extension.loaded",
            "unload": "extension.unloaded",
            "reload": "extension.reloaded",
        }[action]
        await self._send(ctx, render(message_id, name=name))

    async def _handle_error(
        self,
        ctx: commands.Context,
        error: BaseException,
        event: str,
        track: Track | None = None,
        *,
        url: str | None = None,
        filename: str | None = None,
        fallback_message_id: str = "common.command_failed",
    ) -> None:
        context = ErrorContext(
            guild_name=ctx.guild.name if ctx.guild else None,
            guild_id=ctx.guild.id if ctx.guild else None,
            channel_name=getattr(ctx.channel, "name", None),
            channel_id=getattr(ctx.channel, "id", None),
            command=str(ctx.command) if ctx.command else None,
            provider=track.provider if track else None,
            track_id=track.id if track else None,
            requester_name=getattr(ctx.author, "display_name", None),
            requester_id=getattr(ctx.author, "id", None),
            url=track.canonical_url if track else url,
            filename=track.provider_data.get("filename") if track else filename,
        )
        if isinstance(error, UserError):
            self.bot.reporter.expected(event, context=context, error=error)
            await self._send(ctx, render(error.message_id))
            return
        if isinstance(error, SourceUnavailableError):
            await self.bot.reporter.report(error, event=event, context=context)
            await self._send(ctx, render(error.message_id))
            return
        await self.bot.reporter.report(error, event=event, context=context)
        await self._send(ctx, render(fallback_message_id))

    async def _send(self, ctx: commands.Context, content: str) -> None:
        await ctx.send(
            truncate(content, 1900),
            allowed_mentions=discord.AllowedMentions.none(),
        )


def _access_message_id(reason: AccessReason) -> str:
    if reason is AccessReason.USER_NOT_IN_VOICE:
        return "access.user_not_in_voice"
    return "access.different_voice_channel"


def _check_voice_permissions(member: discord.Member | None, channel) -> None:
    if member is None:
        raise UserInputError("access.bot_cannot_connect")
    permissions = channel.permissions_for(member)
    if not permissions.connect:
        raise UserInputError("access.bot_cannot_connect")
    if not permissions.speak:
        raise UserInputError("access.bot_cannot_speak")


def _safe(value: str, limit: int) -> str:
    return truncate(discord.utils.escape_markdown(value), limit)


async def setup(bot) -> None:
    await bot.add_cog(MusicCommandsCog(bot))
