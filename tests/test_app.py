from unittest.mock import AsyncMock

import pytest

from charlotte.app import create_bot, required_intents


def test_bot_requests_only_required_intents() -> None:
    intents = required_intents()
    assert intents.guilds
    assert intents.guild_messages
    assert intents.message_content
    assert intents.voice_states
    assert not intents.members
    assert not intents.presences


@pytest.mark.asyncio
async def test_startup_discovers_and_loads_the_approved_extensions(app_config) -> None:
    bot = create_bot(app_config)
    await bot.setup_hook()
    assert set(bot.extension_manager.discovered) == {
        "music_commands",
        "youtube_source",
        "soundcloud_source",
        "upload_source",
        "emoji_enlarger",
    }
    assert bot.providers.names == frozenset({"youtube", "soundcloud", "upload"})
    assert len(bot.extension_manager.statuses()) == 5
    await bot.close()


@pytest.mark.asyncio
async def test_ready_retries_transient_owner_lookup_failure(app_config) -> None:
    bot = create_bot(app_config)
    bot.reporter.resolve_owner = AsyncMock(side_effect=[False, True])
    bot.health_writer.start = lambda: None
    bot.change_presence = AsyncMock()
    await bot.on_ready()
    assert not bot._owner_resolved
    await bot.on_ready()
    assert bot._owner_resolved
    assert bot.reporter.resolve_owner.await_count == 2
    await bot.close()
