from __future__ import annotations

import asyncio
import subprocess

import pytest

from charlotte.providers import ytdlp_common


class SuccessfulProcess:
    returncode = 0

    async def communicate(self):
        return b'{"id":"public"}', b""


class StuckProcess:
    def __init__(self) -> None:
        self.killed = False

    def poll(self):
        return None

    def kill(self) -> None:
        self.killed = True

    def wait(self, *, timeout):
        raise subprocess.TimeoutExpired("ffmpeg", timeout)


@pytest.mark.asyncio
async def test_extract_ignores_host_ytdlp_configuration(monkeypatch) -> None:
    captured: list[str] = []

    async def create_process(*command, **kwargs):
        captured.extend(command)
        assert kwargs["stdin"] is asyncio.subprocess.DEVNULL
        return SuccessfulProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    result = await ytdlp_common.extract("https://www.youtube.com/watch?v=public", playlist=False)

    assert result["id"] == "public"
    assert "--ignore-config" in captured
    assert captured.index("--ignore-config") < captured.index(
        "https://www.youtube.com/watch?v=public"
    )


def test_ffmpeg_cleanup_has_a_hard_process_wait_deadline() -> None:
    source = object.__new__(ytdlp_common.BoundedFFmpegOpusAudio)
    process = StuckProcess()
    source._process = process

    with pytest.raises(RuntimeError, match="cleanup deadline"):
        source._kill_process()

    assert process.killed
    source._process = None
