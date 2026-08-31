from __future__ import annotations

import io
import threading

import pytest

from charlotte.music.models import PreparedAudio


class FailingSource:
    def cleanup(self) -> None:
        raise RuntimeError("source cleanup failed")


def test_prepared_audio_closes_all_owned_resources_when_source_cleanup_fails() -> None:
    first = io.BytesIO(b"first")
    second = io.BytesIO(b"second")
    prepared = PreparedAudio(
        source=FailingSource(),  # type: ignore[arg-type]
        seekable=True,
        owned_resources=(first, second),
    )

    with pytest.raises(RuntimeError, match="source cleanup failed"):
        prepared.cleanup()

    assert first.closed
    assert second.closed
    prepared.cleanup()


class CountingSource:
    def __init__(self) -> None:
        self.cleanup_calls = 0
        self.cleanup_started = threading.Event()
        self.release_cleanup = threading.Event()

    def read(self) -> bytes:
        return b""

    def is_opus(self) -> bool:
        return True

    def cleanup(self) -> None:
        self.cleanup_calls += 1
        self.cleanup_started.set()
        self.release_cleanup.wait(timeout=1)


class CountingResource:
    def __init__(self) -> None:
        self.closed = False
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True


def test_discord_and_player_cleanup_share_one_thread_safe_owner() -> None:
    source = CountingSource()
    resource = CountingResource()
    prepared = PreparedAudio(
        source=source,  # type: ignore[arg-type]
        seekable=True,
        owned_resources=(resource,),
    )
    discord_cleanup = threading.Thread(target=prepared.source.cleanup)
    player_cleanup = threading.Thread(target=prepared.cleanup)

    discord_cleanup.start()
    assert source.cleanup_started.wait(timeout=1)
    player_cleanup.start()
    source.release_cleanup.set()
    discord_cleanup.join(timeout=1)
    player_cleanup.join(timeout=1)

    assert not discord_cleanup.is_alive()
    assert not player_cleanup.is_alive()
    assert source.cleanup_calls == 1
    assert resource.close_calls == 1
