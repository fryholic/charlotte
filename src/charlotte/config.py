"""Typed application configuration and the environment/TOML boundary."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class ConfigError(RuntimeError):
    """Raised before Discord login when configuration is invalid."""


class Environment(StrEnum):
    PRODUCTION = "production"
    DEVELOPMENT = "development"


class Secret:
    """A string wrapper whose repr and str never reveal the value."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not value.strip():
            raise ConfigError("Discord token cannot be empty")
        self._value = value.strip()

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret('********')"

    def __str__(self) -> str:
        return "********"


DEFAULT_REQUIRED_EXTENSIONS = frozenset(
    {
        "music_commands",
        "youtube_source",
        "soundcloud_source",
        "upload_source",
        "emoji_enlarger",
    }
)

LEGACY_ENVIRONMENT_VARIABLES = frozenset(
    {
        "BLOCKED_USER_IDS",
        "ER_API_KEY",
        "SPOTIFY_USERNAME",
        "SPOTIFY_PASSWORD",
        "SPOTIFY_CLIENT_ID",
        "SPOTIFY_CLIENT_SECRET",
    }
)


@dataclass(frozen=True, slots=True)
class AppConfig:
    environment: Environment
    discord_token: Secret
    command_prefix: str
    operator_user_ids: frozenset[int]
    startup_required_extensions: frozenset[str]
    startup_optional_extensions: frozenset[str]
    emoji_enabled: bool
    emoji_allowed_channel_ids: frozenset[int]
    max_queue_tracks: int
    max_queued_upload_bytes: int
    config_path: Path | None
    legacy_environment_variables: frozenset[str]

    @property
    def log_level(self) -> str:
        return "DEBUG" if self.environment is Environment.DEVELOPMENT else "INFO"


def _parse_bool(raw: str | None, *, name: str, default: bool) -> bool:
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean value")


def _parse_non_negative_int(raw: str | None, *, name: str) -> int:
    if raw is None or not raw.strip():
        return 0
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a non-negative integer") from exc
    if value < 0:
        raise ConfigError(f"{name} must be a non-negative integer")
    return value


def _table(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, Mapping):
        raise ConfigError(f"[{name}] must be a TOML table")
    return value


def _string_set(value: Any, *, name: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"{name} must be an array of strings")
    normalized = [item.strip() for item in value]
    if any(not item for item in normalized):
        raise ConfigError(f"{name} cannot contain an empty name")
    if len(set(normalized)) != len(normalized):
        raise ConfigError(f"{name} cannot contain duplicate names")
    return frozenset(normalized)


def _snowflake_set(value: Any, *, name: str) -> frozenset[int]:
    if value is None:
        return frozenset()
    if not isinstance(value, list):
        raise ConfigError(f"{name} must be an array of Discord IDs")
    ids: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise ConfigError(f"{name} must contain positive integer Discord IDs")
        ids.append(item)
    if len(set(ids)) != len(ids):
        raise ConfigError(f"{name} cannot contain duplicate IDs")
    return frozenset(ids)


def _read_toml(path: Path | None, *, explicitly_configured: bool) -> Mapping[str, Any]:
    if path is None:
        return {}
    if not path.is_file():
        if explicitly_configured:
            raise ConfigError(f"Configuration file does not exist: {path}")
        return {}
    try:
        with path.open("rb") as stream:
            parsed = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Cannot read configuration file: {path}") from exc
    if not isinstance(parsed, Mapping):
        raise ConfigError("The TOML root must be a table")
    return parsed


def load_config(environ: Mapping[str, str] | None = None) -> AppConfig:
    """Load all environment values once and validate the TOML schema."""

    env = os.environ if environ is None else environ
    development = _parse_bool(env.get("DEV"), name="DEV", default=False)
    environment = Environment.DEVELOPMENT if development else Environment.PRODUCTION
    token_key = "DISCORD_TOKEN_DEV" if development else "DISCORD_TOKEN"
    token = env.get(token_key)
    if token is None:
        raise ConfigError(f"Missing required environment variable: {token_key}")

    configured_path = env.get("CHARLOTTE_CONFIG")
    default_path = Path("config.dev.toml" if development else "config.toml")
    config_path = Path(configured_path) if configured_path else default_path
    data = _read_toml(config_path, explicitly_configured=configured_path is not None)
    bot = _table(data, "bot")
    extensions = _table(data, "extensions")
    emoji = _table(data, "emoji")

    default_prefix = "!" if development else "?"
    prefix = bot.get("command_prefix", default_prefix)
    if not isinstance(prefix, str) or not prefix or prefix.isspace() or len(prefix) > 8:
        raise ConfigError("bot.command_prefix must be a non-empty string up to 8 characters")
    if "<@" in prefix:
        raise ConfigError("bot.command_prefix cannot be a Discord mention prefix")

    required = _string_set(
        extensions.get("startup_required", list(DEFAULT_REQUIRED_EXTENSIONS)),
        name="extensions.startup_required",
    )
    optional = _string_set(
        extensions.get("startup_optional", []), name="extensions.startup_optional"
    )
    overlap = required & optional
    if overlap:
        names = ", ".join(sorted(overlap))
        raise ConfigError(f"Extensions cannot be both required and optional: {names}")

    enabled = emoji.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError("emoji.enabled must be a boolean")

    return AppConfig(
        environment=environment,
        discord_token=Secret(token),
        command_prefix=prefix,
        operator_user_ids=_snowflake_set(
            bot.get("operator_user_ids", []), name="bot.operator_user_ids"
        ),
        startup_required_extensions=required,
        startup_optional_extensions=optional,
        emoji_enabled=enabled,
        emoji_allowed_channel_ids=_snowflake_set(
            emoji.get("allowed_channel_ids", []), name="emoji.allowed_channel_ids"
        ),
        max_queue_tracks=_parse_non_negative_int(
            env.get("MAX_QUEUE_TRACKS"), name="MAX_QUEUE_TRACKS"
        ),
        max_queued_upload_bytes=_parse_non_negative_int(
            env.get("MAX_QUEUED_UPLOAD_BYTES"), name="MAX_QUEUED_UPLOAD_BYTES"
        ),
        config_path=config_path if config_path.is_file() else None,
        legacy_environment_variables=frozenset(LEGACY_ENVIRONMENT_VARIABLES & env.keys()),
    )
