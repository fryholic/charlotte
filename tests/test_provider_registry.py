from __future__ import annotations

import asyncio
import threading
import time
from urllib.parse import urlparse

import pytest

from charlotte.errors import ExtensionOperationError, ProviderError
from charlotte.music.models import RequestContext
from charlotte.music.provider import ProviderRegistry, observe_detached_work
from charlotte.providers.ytdlp_common import run_blocking
from tests.fakes import FakeProvider


def test_provider_registration_collision_is_rejected() -> None:
    registry = ProviderRegistry()
    registry.register(FakeProvider())
    with pytest.raises(ExtensionOperationError, match="already registered"):
        registry.register(FakeProvider())


@pytest.mark.asyncio
async def test_begin_unload_rejects_new_operations_and_waits_for_no_inflight() -> None:
    registry = ProviderRegistry()
    provider = FakeProvider(delay=0.05)
    registry.register(provider)
    request = RequestContext(1, 2, 3, "requester")
    operation = asyncio.create_task(
        registry.inspect_url(request, urlparse("https://example.com/a"), "https://example.com/a")
    )
    await asyncio.sleep(0)
    with pytest.raises(ExtensionOperationError, match="inflight"):
        registry.begin_unload("fake")
    await operation
    registry.begin_unload("fake")
    with pytest.raises(ProviderError, match="unloading"):
        await registry.prepare(
            await provider.inspect_url(
                request, urlparse("https://example.com/a"), "https://example.com/a"
            )
        )
    registry.cancel_unload("fake")
    assert (
        await registry.prepare(
            await provider.inspect_url(
                request, urlparse("https://example.com/a"), "https://example.com/a"
            )
        )
    ).source is not None


@pytest.mark.asyncio
async def test_operations_waiting_for_global_capacity_count_as_inflight() -> None:
    registry = ProviderRegistry(max_concurrency=1)
    provider = FakeProvider(delay=0.05)
    registry.register(provider)
    request = RequestContext(1, 2, 3, "requester")
    operations = [
        asyncio.create_task(
            registry.inspect_url(
                request,
                urlparse(f"https://example.com/{index}"),
                f"https://example.com/{index}",
            )
        )
        for index in range(2)
    ]
    await asyncio.sleep(0)
    assert registry.inflight("fake") == 2
    with pytest.raises(ExtensionOperationError, match="inflight"):
        registry.begin_unload("fake")
    await asyncio.gather(*operations)


class BlockingProvider(FakeProvider):
    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        super().__init__()
        self.started = started
        self.release = release

    async def prepare(self, track, *, start_at=0):
        def block():
            self.started.set()
            self.release.wait(timeout=2)
            return super(BlockingProvider, self)

        await run_blocking(block)
        return await super().prepare(track, start_at=start_at)


@pytest.mark.asyncio
async def test_provider_timeout_releases_command_and_capacity_before_thread_stops() -> None:
    started = threading.Event()
    release = threading.Event()
    provider = BlockingProvider(started, release)
    registry = ProviderRegistry(operation_timeout=0.05)
    registry.register(provider)
    request = RequestContext(1, 2, 3, "requester")
    track = await provider.inspect_url(
        request, urlparse("https://example.com/a"), "https://example.com/a"
    )
    started_at = time.monotonic()
    operation = asyncio.create_task(registry.prepare(track))
    await asyncio.to_thread(started.wait, 1)
    with pytest.raises(ProviderError, match="timed out"):
        await operation
    assert time.monotonic() - started_at < 0.2
    assert registry.inflight("fake") == 0
    registry.begin_unload("fake")
    registry.cancel_unload("fake")
    release.set()


@pytest.mark.asyncio
async def test_cancelled_source_constructor_returns_before_late_cleanup() -> None:
    started = threading.Event()
    release = threading.Event()
    cleaned = asyncio.Event()
    loop = asyncio.get_running_loop()

    def construct():
        started.set()
        release.wait(timeout=2)
        return object()

    def cleanup(result):
        loop.call_soon_threadsafe(cleaned.set)

    operation = asyncio.create_task(run_blocking(construct, cleanup_cancelled_result=cleanup))
    await asyncio.to_thread(started.wait, 1)
    started_at = time.monotonic()
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert time.monotonic() - started_at < 0.1
    release.set()
    await asyncio.wait_for(cleaned.wait(), 1)


@pytest.mark.asyncio
async def test_detached_cleanup_does_not_block_the_event_loop() -> None:
    constructor_started = threading.Event()
    release_constructor = threading.Event()
    cleanup_started = threading.Event()
    release_cleanup = threading.Event()
    completions = []

    def construct():
        constructor_started.set()
        release_constructor.wait(timeout=2)
        return object()

    def cleanup(result):
        cleanup_started.set()
        release_cleanup.wait(timeout=2)

    with observe_detached_work(completions.append):
        operation = asyncio.create_task(run_blocking(construct, cleanup_cancelled_result=cleanup))
        assert await asyncio.to_thread(constructor_started.wait, 1)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation

    release_constructor.set()
    assert await asyncio.to_thread(cleanup_started.wait, 1)
    ticked = False

    async def ticker():
        nonlocal ticked
        await asyncio.sleep(0)
        ticked = True

    await asyncio.wait_for(ticker(), 0.1)
    assert ticked
    assert completions and not completions[0].done()
    release_cleanup.set()
    await asyncio.wait_for(completions[0], 1)
