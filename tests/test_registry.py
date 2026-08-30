from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from charlotte.errors import PlaybackError
from charlotte.music import registry as registry_module
from charlotte.music.registry import PlayerRegistry


class SlowClosePlayer:
    def __init__(self, guild_id, **kwargs) -> None:
        self.guild_id = guild_id
        self.has_activity = False
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()

    async def close(self) -> None:
        self.close_started.set()
        await self.release_close.wait()


@pytest.mark.asyncio
async def test_registry_rejects_new_players_once_shutdown_begins(monkeypatch) -> None:
    monkeypatch.setattr(registry_module, "GuildPlayer", SlowClosePlayer)
    registry = PlayerRegistry(
        bot=SimpleNamespace(),
        config=SimpleNamespace(),
        providers=SimpleNamespace(),
        reporter=SimpleNamespace(),
    )
    existing = await registry.get(1)
    closing = asyncio.create_task(registry.close())
    await asyncio.wait_for(existing.close_started.wait(), 1)

    with pytest.raises(PlaybackError, match="registry is closed"):
        await registry.get(2)

    assert registry.peek(1) is None
    existing.release_close.set()
    await asyncio.wait_for(closing, 1)
