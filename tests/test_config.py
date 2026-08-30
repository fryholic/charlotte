from __future__ import annotations

import pytest

from charlotte.config import ConfigError, Environment, load_config


def test_production_defaults_without_default_file(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = load_config({"DISCORD_TOKEN": "secret"})
    assert config.environment is Environment.PRODUCTION
    assert config.command_prefix == "?"
    assert config.max_queue_tracks == 0
    assert "secret" not in repr(config.discord_token)


def test_development_defaults_and_token_are_independent(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = load_config({"DEV": "true", "DISCORD_TOKEN_DEV": "dev-secret"})
    assert config.environment is Environment.DEVELOPMENT
    assert config.command_prefix == "!"
    assert config.discord_token.reveal() == "dev-secret"


def test_toml_is_the_only_prefix_override(tmp_path) -> None:
    path = tmp_path / "instance.toml"
    path.write_text(
        """
[bot]
command_prefix = "$"
operator_user_ids = [42, 43]
[extensions]
startup_required = ["music_commands"]
startup_optional = ["youtube_source"]
[emoji]
enabled = false
allowed_channel_ids = [111, 222]
""".strip(),
        encoding="utf-8",
    )
    config = load_config(
        {
            "DISCORD_TOKEN": "secret",
            "CHARLOTTE_CONFIG": str(path),
            "MAX_QUEUE_TRACKS": "12",
            "MAX_QUEUED_UPLOAD_BYTES": "4096",
        }
    )
    assert config.command_prefix == "$"
    assert config.operator_user_ids == frozenset({42, 43})
    assert config.startup_required_extensions == frozenset({"music_commands"})
    assert config.emoji_allowed_channel_ids == frozenset({111, 222})
    assert not config.emoji_enabled
    assert config.max_queue_tracks == 12
    assert config.max_queued_upload_bytes == 4096


@pytest.mark.parametrize("value", ["maybe", "", "development"])
def test_invalid_dev_value_is_fatal(value) -> None:
    with pytest.raises(ConfigError, match="DEV"):
        load_config({"DEV": value, "DISCORD_TOKEN": "secret"})


def test_explicit_missing_config_file_is_fatal(tmp_path) -> None:
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(
            {
                "DISCORD_TOKEN": "secret",
                "CHARLOTTE_CONFIG": str(tmp_path / "missing.toml"),
            }
        )


def test_duplicate_or_invalid_discord_ids_are_rejected(tmp_path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("[bot]\noperator_user_ids = [42, 42]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="duplicate"):
        load_config({"DISCORD_TOKEN": "secret", "CHARLOTTE_CONFIG": str(path)})


@pytest.mark.parametrize(
    "content,location",
    [
        ("unexpected = true\n", "root"),
        ("[bot]\ncommand_prefx = '!'\n", "bot"),
        ("[extensions]\nstartup_require = []\n", "extensions"),
        ("[emoji]\nallowed_channel_id = [123]\n", "emoji"),
    ],
)
def test_unknown_toml_keys_are_fatal(tmp_path, content, location) -> None:
    path = tmp_path / "typo.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match=f"Unknown {location}"):
        load_config({"DISCORD_TOKEN": "secret", "CHARLOTTE_CONFIG": str(path)})


def test_legacy_environment_values_are_never_stored() -> None:
    config = load_config(
        {
            "DISCORD_TOKEN": "secret",
            "BLOCKED_USER_IDS": "sensitive-value",
        }
    )
    assert config.legacy_environment_variables == frozenset({"BLOCKED_USER_IDS"})
    assert "sensitive-value" not in repr(config)
