from __future__ import annotations

import io
import threading
import wave

import pytest

from charlotte.errors import SourceUnavailableError, UserInputError
from charlotte.music.models import RequestContext
from charlotte.providers import upload as upload_module
from charlotte.providers.upload import UploadProvider


class Attachment:
    def __init__(self, data: bytes, *, content_type=None, filename="sample.wav") -> None:
        self.data = data
        self.content_type = content_type
        self.filename = filename

    async def read(self) -> bytes:
        return self.data


class FailingAttachment(Attachment):
    async def read(self) -> bytes:
        raise RuntimeError("network failed")


def wav_bytes(*, frames: int = 800, rate: int = 8000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\x00\x00" * frames)
    return output.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", [None, "text/plain", "audio/wav"])
async def test_ffprobe_not_mime_is_authoritative(content_type) -> None:
    provider = UploadProvider()
    track = await provider.inspect_upload(
        RequestContext(1, 2, 3, "requester"),
        Attachment(wav_bytes(), content_type=content_type),
    )
    assert track.title == "sample.wav"
    # A pipe is non-seekable, so formats such as WAV may not expose a reliable
    # duration even though ffprobe authoritatively accepts the audio stream.
    assert track.provider_data["upload_size"] == len(wav_bytes())
    assert track.provider_data["content_type_hint"] == content_type
    buffer = track.owned_resource
    track.dispose()
    assert buffer is not None and buffer.closed


@pytest.mark.asyncio
async def test_invalid_bytes_are_rejected_without_a_temporary_file() -> None:
    provider = UploadProvider()
    with pytest.raises(UserInputError) as caught:
        await provider.inspect_upload(
            RequestContext(1, 2, 3, "requester"), Attachment(b"not audio")
        )
    assert caught.value.message_id == "music.play.invalid_attachment"


@pytest.mark.asyncio
async def test_attachment_transport_failure_is_reportable() -> None:
    provider = UploadProvider()
    with pytest.raises(SourceUnavailableError) as caught:
        await provider.inspect_upload(RequestContext(1, 2, 3, "requester"), FailingAttachment(b""))
    assert caught.value.message_id == "music.play.attachment_read_failed"


@pytest.mark.asyncio
async def test_probe_infrastructure_failure_is_reportable(monkeypatch) -> None:
    async def fail_probe(raw):
        raise RuntimeError("ffprobe unavailable")

    monkeypatch.setattr(upload_module, "_probe", fail_probe)
    provider = UploadProvider()
    with pytest.raises(SourceUnavailableError) as caught:
        await provider.inspect_upload(RequestContext(1, 2, 3, "requester"), Attachment(wav_bytes()))
    assert caught.value.message_id == "music.play.invalid_attachment"


@pytest.mark.asyncio
async def test_valid_upload_builds_and_cleans_a_pipe_ffmpeg_source() -> None:
    provider = UploadProvider()
    track = await provider.inspect_upload(
        RequestContext(1, 2, 3, "requester"), Attachment(wav_bytes())
    )
    prepared = await provider.prepare(track)
    playback_buffer = prepared.owned_resources[0]
    prepared.cleanup()
    track.dispose()
    assert playback_buffer.closed
    assert track.owned_resource is not None and track.owned_resource.closed


@pytest.mark.asyncio
async def test_immediate_upload_cleanup_has_no_pipe_writer_exception(monkeypatch) -> None:
    thread_errors: list[BaseException] = []
    monkeypatch.setattr(
        threading,
        "excepthook",
        lambda args: thread_errors.append(args.exc_value),
    )
    provider = UploadProvider()
    track = await provider.inspect_upload(
        RequestContext(1, 2, 3, "requester"),
        Attachment(wav_bytes(frames=48000, rate=48000)),
    )

    prepared = await provider.prepare(track)
    source = prepared.source
    playback_buffer = prepared.owned_resources[0]
    prepared.cleanup()
    track.dispose()

    writer = getattr(source, "_pipe_writer_thread", None)
    assert writer is None or not writer.is_alive()
    assert playback_buffer.closed
    assert thread_errors == []
