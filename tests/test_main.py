from __future__ import annotations

import asyncio
import signal

import pytest

from charlotte import __main__ as main_module
from charlotte.__main__ import _run_bot


class SignalAwareBot:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.close_calls = 0
        self.token = None
        self.reconnect = None
        self.health_writer = RecordingHealthWriter()

    async def start(self, token, *, reconnect):
        self.token = token
        self.reconnect = reconnect
        self.started.set()
        await self.stopped.wait()

    async def close(self):
        self.close_calls += 1
        self.stopped.set()


class RecordingHealthWriter:
    def __init__(self) -> None:
        self.marked = False

    def mark_starting(self) -> None:
        self.marked = True


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
    assert bot.health_writer.marked


class CloseDoesNotWakeBot:
    def __init__(self, *, close_hangs: bool = False) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.close_hangs = close_hangs
        self.close_calls = 0

    async def start(self, token, *, reconnect):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

    async def close(self):
        self.close_calls += 1
        if self.close_hangs:
            await asyncio.Event().wait()


@pytest.mark.asyncio
@pytest.mark.parametrize("close_hangs", [False, True])
async def test_shutdown_cancels_start_task_even_when_close_does_not_wake_it(
    monkeypatch, close_hangs
) -> None:
    monkeypatch.setattr(main_module, "SHUTDOWN_RUNNER_TIMEOUT", 0.05)
    bot = CloseDoesNotWakeBot(close_hangs=close_hangs)
    requested = asyncio.Event()
    running = asyncio.create_task(_run_bot(bot, "test-token", shutdown_event=requested))
    await asyncio.wait_for(bot.started.wait(), 1)

    requested.set()
    await asyncio.wait_for(running, 0.3)

    assert bot.close_calls == 1
    assert bot.cancelled.is_set()
