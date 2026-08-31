from __future__ import annotations

import io
import threading
import wave

import pytest

from charlotte.errors import QueueLimitError, SourceUnavailableError, UserInputError
from charlotte.music.models import RequestContext
from charlotte.providers import upload as upload_module
from charlotte.providers.upload import UploadProvider


class Attachment:
    def __init__(self, data: bytes, *, content_type=None, filename="sample.wav") -> None:
        self.data = data
        self.content_type = content_type
        self.filename = filename
        self.size = len(data)
        self.read_called = False

    async def read(self) -> bytes:
        self.read_called = True
        return self.data


class FailingAttachment(Attachment):
    async def read(self) -> bytes:
        raise RuntimeError("network failed")


class FakeContent:
    def __init__(self, chunks) -> None:
        self.chunks = chunks

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    def __init__(self, chunks, *, content_length=None) -> None:
        self.content = FakeContent(chunks)
        self.content_length = content_length

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self, response) -> None:
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def get(self, url):
        return self.response


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
async def test_declared_oversized_attachment_is_rejected_before_read() -> None:
    provider = UploadProvider()
    attachment = Attachment(wav_bytes())
    request = RequestContext(1, 2, 3, "requester", max_upload_bytes=10)

    with pytest.raises(QueueLimitError):
        await provider.inspect_upload(request, attachment)

    assert not attachment.read_called


@pytest.mark.asyncio
async def test_bounded_cdn_read_stops_before_size_mismatch_can_allocate_more(monkeypatch) -> None:
    response = FakeResponse([b"123", b"456"])
    monkeypatch.setattr(
        upload_module.aiohttp,
        "ClientSession",
        lambda **kwargs: FakeSession(response),
    )

    with pytest.raises(QueueLimitError):
        await upload_module._read_cdn_bounded("https://cdn.example/audio", 5)


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
    assert prepared.memory_bytes == track.upload_size
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
