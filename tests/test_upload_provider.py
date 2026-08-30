from __future__ import annotations

import io
import wave

import pytest

from charlotte.errors import UserInputError
from charlotte.music.models import RequestContext
from charlotte.providers.upload import UploadProvider


class Attachment:
    def __init__(self, data: bytes, *, content_type=None, filename="sample.wav") -> None:
        self.data = data
        self.content_type = content_type
        self.filename = filename

    async def read(self) -> bytes:
        return self.data


def wav_bytes() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 800)
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
