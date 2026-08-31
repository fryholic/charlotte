from pathlib import Path

import pytest

from charlotte.config import AppConfig, Environment, Secret


@pytest.fixture
def app_config() -> AppConfig:
    return AppConfig(
        environment=Environment.DEVELOPMENT,
        discord_token=Secret("test-token"),
        command_prefix="!",
        operator_user_ids=frozenset({42}),
        startup_required_extensions=frozenset(
            {
                "music_commands",
                "youtube_source",
                "soundcloud_source",
                "upload_source",
                "emoji_enlarger",
            }
        ),
        startup_optional_extensions=frozenset(),
        emoji_enabled=True,
        emoji_allowed_channel_ids=frozenset(),
        max_queue_tracks=0,
        max_queued_upload_bytes=0,
        config_path=Path("config.dev.toml"),
        legacy_environment_variables=frozenset(),
    )
