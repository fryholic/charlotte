from __future__ import annotations

import asyncio
import io
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


class ExitedProcess:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def wait(self, *, timeout):
        return self.returncode


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


def monitored_source(*, returncode: int, packets: list[bytes], duration: float | None):
    source = object.__new__(ytdlp_common.BoundedFFmpegOpusAudio)
    source._process = ExitedProcess(returncode)
    source._packet_iter = iter(packets)
    source._current_error = None
    source._stopped = False
    source._stderr = None
    diagnostics = ytdlp_common.BoundedDiagnosticBuffer(
        secrets=("secret-header",),
    )
    source.configure_monitoring(diagnostics, expected_duration=duration, start_at=0)
    return source, diagnostics


def test_ffmpeg_eof_before_first_packet_is_a_playback_error() -> None:
    source, diagnostics = monitored_source(returncode=0, packets=[b""], duration=3600)
    diagnostics.write(
        b"HTTP 403 https://rr.example.googlevideo.com/private/path?signature=secret "
        b"Cookie: secret-header"
    )

    with pytest.raises(Exception) as caught:
        source.read()

    rendered = str(caught.value)
    assert "after 0.00s" in rendered
    assert "private/path" not in rendered
    assert "secret-header" not in rendered


def test_ffmpeg_nonzero_exit_after_packets_is_a_playback_error() -> None:
    source, _ = monitored_source(returncode=1, packets=[b"opus", b""], duration=None)

    assert source.read() == b"opus"
    with pytest.raises(Exception, match="status 1"):
        source.read()


def test_ffmpeg_normal_eof_near_expected_duration_is_not_an_error() -> None:
    source, _ = monitored_source(returncode=0, packets=[b""], duration=60)
    source._charlotte_packets_read = 2_750

    assert source.read() == b""


def test_diagnostic_buffer_keeps_only_a_bounded_tail() -> None:
    diagnostics = ytdlp_common.BoundedDiagnosticBuffer(max_bytes=8)

    diagnostics.write(b"0123456789")

    assert diagnostics.tail() == "23456789"


@pytest.mark.asyncio
async def test_stream_audio_forwards_sanitized_headers_to_ffmpeg(monkeypatch) -> None:
    captured = {}

    class Source:
        def __init__(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)

        def configure_monitoring(self, diagnostics, **kwargs):
            captured["monitoring"] = kwargs

        def cleanup(self):
            return None

        def read(self):
            return b""

        def is_opus(self):
            return True

    monkeypatch.setattr(ytdlp_common, "BoundedFFmpegOpusAudio", Source)
    prepared = await ytdlp_common.stream_audio(
        "https://media.example/audio",
        headers={"User-Agent": "Charlotte Test", "X-Bad\nHeader": "ignored"},
        expected_duration=3600,
    )

    assert "-headers" in captured["before_options"]
    assert "User-Agent: Charlotte Test" in captured["before_options"]
    assert "X-Bad" not in captured["before_options"]
    assert captured["monitoring"]["expected_duration"] == 3600
    assert prepared.confirm_first_packet
    prepared.cleanup()


@pytest.mark.asyncio
async def test_ytdlp_pipe_fallback_owns_and_cleans_both_processes(monkeypatch) -> None:
    captured = {}

    class Process:
        def __init__(self, command, **kwargs):
            captured["command"] = command
            captured["process_kwargs"] = kwargs
            self.stdout = io.BytesIO(b"media")
            self.stderr = io.BytesIO(b"")
            self.pid = 123
            self.returncode = None
            self.killed = False

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9

        def wait(self, *, timeout):
            return self.returncode

    class Source:
        def __init__(self, input_stream, **kwargs):
            captured["input_stream"] = input_stream
            captured["ffmpeg_kwargs"] = kwargs
            self.cleaned = False

        def configure_monitoring(self, diagnostics, **kwargs):
            captured["monitoring"] = kwargs

        def cleanup(self):
            self.cleaned = True

        def read(self):
            return b""

        def is_opus(self):
            return True

    process = None

    def popen(command, **kwargs):
        nonlocal process
        process = Process(command, **kwargs)
        return process

    monkeypatch.setattr(ytdlp_common.subprocess, "Popen", popen)
    monkeypatch.setattr(ytdlp_common, "BoundedFFmpegOpusAudio", Source)

    prepared = await ytdlp_common.stream_ytdlp_audio(
        "https://www.youtube.com/watch?v=public",
        expected_duration=3600,
    )
    prepared.cleanup()

    assert process is not None and process.killed
    assert captured["command"][-2:] == [
        "-",
        "https://www.youtube.com/watch?v=public",
    ]
    assert captured["ffmpeg_kwargs"]["pipe"] is True
