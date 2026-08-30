from __future__ import annotations

import io

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
