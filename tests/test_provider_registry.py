from __future__ import annotations

import asyncio
import threading
from urllib.parse import urlparse

import pytest

from charlotte.errors import ExtensionOperationError, ProviderError
from charlotte.music.models import RequestContext
from charlotte.music.provider import ProviderRegistry
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
async def test_cancelled_worker_stays_inflight_until_thread_really_stops() -> None:
    started = threading.Event()
    release = threading.Event()
    provider = BlockingProvider(started, release)
    registry = ProviderRegistry()
    registry.register(provider)
    request = RequestContext(1, 2, 3, "requester")
    track = await provider.inspect_url(
        request, urlparse("https://example.com/a"), "https://example.com/a"
    )
    operation = asyncio.create_task(registry.prepare(track))
    await asyncio.to_thread(started.wait, 1)
    operation.cancel()
    await asyncio.sleep(0)
    assert registry.inflight("fake") == 1
    with pytest.raises(ExtensionOperationError, match="inflight"):
        registry.begin_unload("fake")
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert registry.inflight("fake") == 0
