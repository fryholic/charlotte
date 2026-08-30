"""Directory discovery and safe operator-driven Extension lifecycle."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from dataclasses import dataclass
from typing import Any

from charlotte.errors import ExtensionOperationError
from charlotte.extensions.contract import ExtensionKind, ExtensionMetadata
from charlotte.observability import log_exception


@dataclass(frozen=True, slots=True)
class ExtensionStatus:
    metadata: ExtensionMetadata
    loaded: bool
    startup_required: bool
    failed: bool


class ExtensionManager:
    def __init__(self, bot: Any) -> None:
        self.bot = bot
        self.discovered: dict[str, tuple[str, ExtensionMetadata]] = {}
        self.failures: dict[str, BaseException] = {}
        self.log = logging.getLogger("charlotte.extensions")

    def discover(self) -> None:
        package = importlib.import_module("charlotte.extensions")
        for module_info in pkgutil.iter_modules(package.__path__, f"{package.__name__}."):
            short_name = module_info.name.rsplit(".", 1)[-1]
            if short_name.startswith("_") or short_name in {"contract", "manager"}:
                continue
            try:
                module = importlib.import_module(module_info.name)
            except Exception as exc:
                self.failures[short_name] = exc
                continue
            metadata = getattr(module, "EXTENSION_META", None)
            if metadata is None:
                continue
            if not isinstance(metadata, ExtensionMetadata):
                raise ExtensionOperationError(f"Invalid Extension metadata: {module_info.name}")
            if metadata.name in self.discovered:
                raise ExtensionOperationError(f"Duplicate Extension name: {metadata.name}")
            if metadata.kind is ExtensionKind.SOURCE and not metadata.provider_name:
                raise ExtensionOperationError(
                    f"Source Extension must name its provider: {metadata.name}"
                )
            self.discovered[metadata.name] = (module_info.name, metadata)

    async def load_startup(self) -> None:
        required = self.bot.config.startup_required_extensions
        optional = self.bot.config.startup_optional_extensions
        unknown_required = required - self.discovered.keys() - self.failures.keys()
        if unknown_required:
            names = ", ".join(sorted(unknown_required))
            raise ExtensionOperationError(f"Unknown required Extensions: {names}")
        for name in sorted(required, key=self._load_order):
            if name in self.failures:
                raise ExtensionOperationError(
                    f"Required Extension import failed: {name}"
                ) from self.failures[name]
            await self.load(name)
        for name in sorted(optional, key=self._load_order):
            if name not in self.discovered:
                import_error = self.failures.get(name)
                if import_error is not None:
                    log_exception(
                        self.log,
                        import_error,
                        event="extension.optional_failed",
                        context={"extension": name, "stage": "import"},
                    )
                else:
                    self.log.error(
                        "unknown optional Extension",
                        extra={"event": "extension.optional_unknown", "extension": name},
                    )
                continue
            try:
                await self.load(name)
            except Exception as exc:
                self.failures[name] = exc
                log_exception(
                    self.log,
                    exc,
                    event="extension.optional_failed",
                    context={"extension": name},
                )

    async def load(self, name: str) -> None:
        module, _ = self._entry(name)
        if module in self.bot.extensions:
            raise ExtensionOperationError(f"Extension is already loaded: {name}")
        try:
            await self.bot.load_extension(module)
            self.failures.pop(name, None)
            self.log.info(
                "Extension loaded",
                extra={"event": "extension.loaded", "extension": name},
            )
        except Exception as exc:
            self.failures[name] = exc
            raise

    async def unload(self, name: str) -> None:
        module, metadata = self._entry(name)
        self._check_runtime_change(metadata)
        if module not in self.bot.extensions:
            raise ExtensionOperationError(f"Extension is not loaded: {name}")
        provider_name = self._begin_source_unload(metadata)
        try:
            await self.bot.unload_extension(module)
            self.log.info(
                "Extension unloaded",
                extra={"event": "extension.unloaded", "extension": name},
            )
        except Exception:
            if provider_name is not None:
                self.bot.providers.cancel_unload(provider_name)
            raise

    async def reload(self, name: str) -> None:
        module, metadata = self._entry(name)
        self._check_runtime_change(metadata)
        if module not in self.bot.extensions:
            raise ExtensionOperationError(f"Extension is not loaded: {name}")
        provider_name = self._begin_source_unload(metadata)
        try:
            await self.bot.reload_extension(module)
            self.failures.pop(name, None)
            self.log.info(
                "Extension reloaded",
                extra={"event": "extension.reloaded", "extension": name},
            )
        except Exception as exc:
            if provider_name is not None:
                self.bot.providers.cancel_unload(provider_name)
            self.failures[name] = exc
            raise

    def statuses(self) -> list[ExtensionStatus]:
        required = self.bot.config.startup_required_extensions
        return [
            ExtensionStatus(
                metadata=metadata,
                loaded=module in self.bot.extensions,
                startup_required=name in required,
                failed=name in self.failures,
            )
            for name, (module, metadata) in sorted(self.discovered.items())
        ]

    def _entry(self, name: str) -> tuple[str, ExtensionMetadata]:
        try:
            return self.discovered[name]
        except KeyError as exc:
            raise ExtensionOperationError(f"Unknown Extension: {name}") from exc

    def _load_order(self, name: str) -> tuple[int, str]:
        entry = self.discovered.get(name)
        return ((entry[1].load_order if entry else 1000), name)

    def _check_runtime_change(self, metadata: ExtensionMetadata) -> None:
        if metadata.runtime_protected:
            raise ExtensionOperationError(f"Runtime-protected Extension: {metadata.name}")
        if metadata.kind is ExtensionKind.SOURCE and self.bot.players.any_activity():
            raise ExtensionOperationError("Music is active")
        if metadata.provider_name and self.bot.providers.inflight(metadata.provider_name):
            raise ExtensionOperationError("Provider operation is inflight")

    def _begin_source_unload(self, metadata: ExtensionMetadata) -> str | None:
        if metadata.kind is not ExtensionKind.SOURCE or metadata.provider_name is None:
            return None
        self.bot.providers.begin_unload(metadata.provider_name)
        return metadata.provider_name
