"""Provider protocol and concurrency-safe runtime registry."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import ParseResult

from charlotte.constants import PROVIDER_MAX_CONCURRENCY, PROVIDER_OPERATION_TIMEOUT
from charlotte.errors import ExtensionOperationError, ProviderError, UnsupportedSourceError
from charlotte.music.models import PreparedAudio, RequestContext, Track


class TrackProvider(Protocol):
    name: str
    supports_upload: bool

    def supports_url(self, parsed_url: ParseResult) -> bool: ...

    async def inspect_url(
        self, request: RequestContext, parsed_url: ParseResult, raw_url: str
    ) -> Track: ...

    async def inspect_upload(self, request: RequestContext, attachment: Any) -> Track: ...

    async def prepare(self, track: Track, *, start_at: float = 0) -> PreparedAudio: ...


class ProviderRegistry:
    def __init__(self, *, max_concurrency: int = PROVIDER_MAX_CONCURRENCY) -> None:
        self._providers: dict[str, TrackProvider] = {}
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._inflight: defaultdict[str, int] = defaultdict(int)
        self._unloading: set[str] = set()

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._providers)

    def register(self, provider: TrackProvider) -> None:
        if provider.name in self._providers:
            raise ExtensionOperationError(f"Provider already registered: {provider.name}")
        self._unloading.discard(provider.name)
        self._providers[provider.name] = provider

    def unregister(self, name: str) -> None:
        if self._inflight[name]:
            raise ExtensionOperationError(f"Provider has inflight operations: {name}")
        if self._providers.pop(name, None) is None:
            raise ExtensionOperationError(f"Provider is not registered: {name}")
        self._unloading.discard(name)

    def begin_unload(self, name: str) -> None:
        if name not in self._providers:
            raise ExtensionOperationError(f"Provider is not registered: {name}")
        if self._inflight[name]:
            raise ExtensionOperationError(f"Provider has inflight operations: {name}")
        self._unloading.add(name)

    def cancel_unload(self, name: str) -> None:
        self._unloading.discard(name)

    def inflight(self, name: str) -> int:
        return self._inflight[name]

    def provider_for_url(self, parsed_url: ParseResult) -> TrackProvider:
        matches = [
            provider
            for provider in self._providers.values()
            if provider.name not in self._unloading and provider.supports_url(parsed_url)
        ]
        if not matches:
            raise UnsupportedSourceError()
        if len(matches) > 1:
            names = ", ".join(sorted(provider.name for provider in matches))
            raise ExtensionOperationError(f"Provider URL capability collision: {names}")
        return matches[0]

    def upload_provider(self) -> TrackProvider:
        matches = [
            provider
            for provider in self._providers.values()
            if provider.name not in self._unloading and provider.supports_upload
        ]
        if len(matches) != 1:
            raise UnsupportedSourceError()
        return matches[0]

    async def inspect_url(
        self, request: RequestContext, parsed_url: ParseResult, raw_url: str
    ) -> Track:
        provider = self.provider_for_url(parsed_url)
        return await self._call(
            provider.name, lambda: provider.inspect_url(request, parsed_url, raw_url)
        )

    async def inspect_upload(self, request: RequestContext, attachment: Any) -> Track:
        provider = self.upload_provider()
        return await self._call(provider.name, lambda: provider.inspect_upload(request, attachment))

    async def prepare(self, track: Track, *, start_at: float = 0) -> PreparedAudio:
        provider = self._providers.get(track.provider)
        if track.provider in self._unloading:
            raise ProviderError(f"Provider is unloading: {track.provider}")
        if provider is None:
            raise ProviderError(f"Provider is not loaded: {track.provider}")
        return await self._call(provider.name, lambda: provider.prepare(track, start_at=start_at))

    async def _call(self, name: str, operation: Callable[[], Awaitable[Any]]) -> Any:
        if name in self._unloading:
            raise ProviderError(f"Provider is unloading: {name}")
        self._inflight[name] += 1
        try:
            async with asyncio.timeout(PROVIDER_OPERATION_TIMEOUT):
                async with self._semaphore:
                    if name in self._unloading or name not in self._providers:
                        raise ProviderError(f"Provider is unloading: {name}")
                    return await operation()
        except TimeoutError as exc:
            raise ProviderError(f"Provider operation timed out: {name}") from exc
        finally:
            self._inflight[name] -= 1
