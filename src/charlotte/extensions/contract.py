"""Metadata used without coupling Extension implementations together."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ExtensionKind(StrEnum):
    COMMAND = "command"
    FEATURE = "feature"
    SOURCE = "source"


@dataclass(frozen=True, slots=True)
class ExtensionMetadata:
    name: str
    kind: ExtensionKind
    runtime_protected: bool = False
    provider_name: str | None = None
    load_order: int = 100
