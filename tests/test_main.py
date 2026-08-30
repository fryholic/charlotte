from __future__ import annotations

import asyncio
import signal

import pytest

from charlotte.__main__ import _run_bot


class SignalAwareBot:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.close_calls = 0
        self.token = None
        self.reconnect = None

    async def start(self, token, *, reconnect):
        self.token = token
        self.reconnect = reconnect
        self.started.set()
        await self.stopped.wait()

    async def close(self):
        self.close_calls += 1
        self.stopped.set()


@pytest.mark.asyncio
async def test_sigterm_requests_graceful_bot_close(monkeypatch) -> None:
    loop = asyncio.get_running_loop()
    handlers = {}
    removed = []
    monkeypatch.setattr(
        loop,
        "add_signal_handler",
        lambda requested_signal, callback: handlers.setdefault(requested_signal, callback),
    )
    monkeypatch.setattr(
        loop,
        "remove_signal_handler",
        lambda requested_signal: removed.append(requested_signal) or True,
    )
    bot = SignalAwareBot()
    running = asyncio.create_task(_run_bot(bot, "test-token"))
    await asyncio.wait_for(bot.started.wait(), 1)

    handlers[signal.SIGTERM]()
    await asyncio.wait_for(running, 1)

    assert bot.token == "test-token"
    assert bot.reconnect is True
    assert bot.close_calls >= 1
    assert signal.SIGTERM in removed
