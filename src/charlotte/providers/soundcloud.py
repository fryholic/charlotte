"""Unauthenticated SoundCloud single-track provider."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import ParseResult, urlunparse

from charlotte.constants import STREAM_DESCRIPTOR_MAX_AGE
from charlotte.errors import SourceUnavailableError, UnsupportedContentError, UserInputError
from charlotte.music.models import PreparedAudio, RequestContext, Track
from charlotte.providers.ytdlp_common import (
    StreamDescriptor,
    YtdlpError,
    extract,
    stream_audio,
    stream_descriptor,
)

_SOUNDCLOUD_HOSTS = {
    "soundcloud.com",
    "www.soundcloud.com",
    "m.soundcloud.com",
    "on.soundcloud.com",
}


class SoundCloudProvider:
    name = "soundcloud"
    supports_upload = False

    def supports_url(self, parsed_url: ParseResult) -> bool:
        return (parsed_url.hostname or "").lower() in _SOUNDCLOUD_HOSTS

    async def inspect_url(
        self, request: RequestContext, parsed_url: ParseResult, raw_url: str
    ) -> Track:
        normalized = _normalize(parsed_url)
        try:
            data = await extract(normalized, playlist=False)
        except YtdlpError as exc:
            raise SourceUnavailableError("music.soundcloud.unavailable", str(exc)) from exc
        _require_single_track(data)
        extractor = str(data.get("extractor_key", data.get("extractor", ""))).lower()
        if "soundcloud" not in extractor:
            raise SourceUnavailableError("music.soundcloud.unavailable")
        canonical = data.get("webpage_url") or data.get("original_url") or normalized
        title = data.get("title")
        if not isinstance(canonical, str) or not isinstance(title, str) or not title.strip():
            raise SourceUnavailableError("music.soundcloud.unavailable")
        duration = data.get("duration")
        reliable_duration = (
            float(duration)
            if isinstance(duration, (int, float))
            and not isinstance(duration, bool)
            and duration >= 0
            else None
        )
        descriptor = stream_descriptor(data)
        return Track(
            provider=self.name,
            title=title.strip(),
            requester_id=request.requester_id,
            requester_display_name=request.requester_display_name,
            request_channel_id=request.text_channel_id,
            canonical_url=canonical,
            duration=reliable_duration,
            provider_data={"source_url": canonical},
            playback_hint=descriptor,
            prefetchable=False,
        )

    async def inspect_upload(self, request: RequestContext, attachment: Any) -> Track:
        raise UserInputError("music.play.invalid_attachment")

    async def prepare(self, track: Track, *, start_at: float = 0) -> PreparedAudio:
        source_url = str(track.provider_data["source_url"])
        descriptor = track.playback_hint
        track.playback_hint = None
        if not isinstance(descriptor, StreamDescriptor) or (
            time.monotonic() - descriptor.extracted_at > STREAM_DESCRIPTOR_MAX_AGE
        ):
            try:
                data = await extract(source_url, playlist=False)
            except YtdlpError as exc:
                raise SourceUnavailableError("music.soundcloud.unavailable", str(exc)) from exc
            _require_single_track(data)
            descriptor = stream_descriptor(data)
        if descriptor is None:
            raise SourceUnavailableError("music.soundcloud.unavailable")
        return await stream_audio(
            descriptor.url,
            start_at=start_at,
            headers=descriptor.headers,
            expected_duration=track.duration,
        )


def _normalize(parsed: ParseResult) -> str:
    if parsed.scheme not in {"http", "https"}:
        raise UserInputError("music.play.invalid_url")
    if parsed.username is not None or parsed.password is not None:
        raise UserInputError("music.play.invalid_url")
    path = parsed.path.rstrip("/")
    if not path or path in {"/discover", "/stream", "/charts"} or "/sets/" in path:
        raise UnsupportedContentError("music.soundcloud.collection_not_supported")
    return urlunparse(("https", parsed.netloc.lower(), parsed.path, "", parsed.query, ""))


def _require_single_track(data: dict[str, Any]) -> None:
    if data.get("entries") is not None or str(data.get("_type", "")).lower() == "playlist":
        raise UnsupportedContentError("music.soundcloud.collection_not_supported")
