from __future__ import annotations

from types import SimpleNamespace

import pytest

from charlotte.errors import ExtensionOperationError
from charlotte.extensions.contract import ExtensionKind, ExtensionMetadata
from charlotte.extensions.manager import ExtensionManager


class Providers:
    def __init__(self, inflight=0):
        self.value = inflight
        self.begun = []

    def inflight(self, name):
        return self.value

    def begin_unload(self, name):
        self.begun.append(name)


def manager_with(*, active=False, inflight=0):
    bot = SimpleNamespace(
        players=SimpleNamespace(any_activity=lambda: active),
        providers=Providers(inflight),
    )
    return ExtensionManager(bot)


def test_runtime_protection_is_distinct_from_startup_required() -> None:
    manager = manager_with()
    source = ExtensionMetadata("youtube_source", ExtensionKind.SOURCE, provider_name="youtube")
    manager._check_runtime_change(source)
    protected = ExtensionMetadata("music_commands", ExtensionKind.COMMAND, runtime_protected=True)
    with pytest.raises(ExtensionOperationError, match="protected"):
        manager._check_runtime_change(protected)


@pytest.mark.parametrize(("active", "inflight"), [(True, 0), (False, 1)])
def test_source_change_rejected_for_activity_or_inflight(active, inflight) -> None:
    manager = manager_with(active=active, inflight=inflight)
    source = ExtensionMetadata("youtube_source", ExtensionKind.SOURCE, provider_name="youtube")
    with pytest.raises(ExtensionOperationError):
        manager._check_runtime_change(source)
