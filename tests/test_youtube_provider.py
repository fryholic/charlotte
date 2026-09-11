from __future__ import annotations

from unittest.mock import AsyncMock
from urllib.parse import urlparse

import pytest

from charlotte.errors import NonRetryableSourceError
from charlotte.music.models import PreparedAudio, RequestContext, Track
from charlotte.providers import youtube
from charlotte.providers.ytdlp_common import YtdlpError
from tests.fakes import FakeSource


def metadata() -> dict[str, object]:
    return {
        "extractor_key": "Youtube",
        "webpage_url": "https://www.youtube.com/watch?v=public",
        "title": "One hour",
        "duration": 3600,
        "url": "https://media.example/audio?signature=secret",
        "http_headers": {"User-Agent": "Charlotte Test"},
        "protocol": "https",
        "format_id": "251",
    }


@pytest.mark.asyncio
async def test_immediate_youtube_play_reuses_fresh_inspection_descriptor(monkeypatch) -> None:
    extract = AsyncMock(return_value=metadata())
    stream = AsyncMock(return_value=PreparedAudio(source=FakeSource(), seekable=True))
    monkeypatch.setattr(youtube, "extract", extract)
    monkeypatch.setattr(youtube, "stream_audio", stream)
    provider = youtube.YouTubeProvider()
    request = RequestContext(1, 500, 100, "requester")
    url = "https://www.youtube.com/watch?v=public"

    track = await provider.inspect_url(request, urlparse(url), url)
    await provider.prepare(track)

    assert extract.await_count == 1
    assert not track.prefetchable
    stream.assert_awaited_once_with(
        "https://media.example/audio?signature=secret",
        start_at=0,
        headers={"User-Agent": "Charlotte Test"},
        expected_duration=3600.0,
    )


@pytest.mark.asyncio
async def test_youtube_retry_switches_to_stdout_streaming_fallback(monkeypatch) -> None:
    extract = AsyncMock(return_value=metadata())
    fallback = AsyncMock(return_value=PreparedAudio(source=FakeSource(), seekable=True))
    direct = AsyncMock()
    monkeypatch.setattr(youtube, "extract", extract)
    monkeypatch.setattr(youtube, "stream_ytdlp_audio", fallback)
    monkeypatch.setattr(youtube, "stream_audio", direct)
    provider = youtube.YouTubeProvider()
    track = Track(
        provider="youtube",
        title="One hour",
        requester_id=100,
        requester_display_name="requester",
        request_channel_id=500,
        duration=3600,
        provider_data={"source_url": "https://www.youtube.com/watch?v=public"},
        failure_retries=1,
    )

    await provider.prepare(track, start_at=12.5)

    fallback.assert_awaited_once_with(
        "https://www.youtube.com/watch?v=public",
        start_at=12.5,
        expected_duration=3600,
    )
    direct.assert_not_awaited()


@pytest.mark.asyncio
async def test_queued_youtube_track_reextracts_before_direct_playback(monkeypatch) -> None:
    extract = AsyncMock(return_value=metadata())
    stream = AsyncMock(return_value=PreparedAudio(source=FakeSource(), seekable=True))
    monkeypatch.setattr(youtube, "extract", extract)
    monkeypatch.setattr(youtube, "stream_audio", stream)
    provider = youtube.YouTubeProvider()
    track = Track(
        provider="youtube",
        title="One hour",
        requester_id=100,
        requester_display_name="requester",
        request_channel_id=500,
        duration=3600,
        provider_data={"source_url": "https://www.youtube.com/watch?v=public"},
        playback_hint=None,
        prefetchable=False,
    )

    await provider.prepare(track)

    extract.assert_awaited_once_with(
        "https://www.youtube.com/watch?v=public",
        playlist=False,
    )
    stream.assert_awaited_once()


@pytest.mark.asyncio
async def test_youtube_bot_challenge_is_classified_as_non_retryable(monkeypatch) -> None:
    extract = AsyncMock(side_effect=YtdlpError("Sign in to confirm you're not a bot"))
    monkeypatch.setattr(youtube, "extract", extract)
    provider = youtube.YouTubeProvider()
    request = RequestContext(1, 500, 100, "requester")
    url = "https://www.youtube.com/watch?v=public"

    with pytest.raises(NonRetryableSourceError):
        await provider.inspect_url(request, urlparse(url), url)

    assert extract.await_count == 1
