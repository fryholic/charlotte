"""Unauthenticated YouTube video, Shorts, and first-playlist-entry provider."""

from __future__ import annotations

from typing import Any
from urllib.parse import ParseResult, parse_qs, urlencode, urlunparse

from charlotte.errors import SourceUnavailableError, UnsupportedContentError, UserInputError
from charlotte.music.models import PreparedAudio, RequestContext, Track
from charlotte.providers.ytdlp_common import YtdlpError, extract, first_entry, stream_audio

_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


class YouTubeProvider:
    name = "youtube"
    supports_upload = False

    def supports_url(self, parsed_url: ParseResult) -> bool:
        return (parsed_url.hostname or "").lower() in _YOUTUBE_HOSTS

    async def inspect_url(
        self, request: RequestContext, parsed_url: ParseResult, raw_url: str
    ) -> Track:
        normalized, playlist = _normalize(parsed_url)
        try:
            data = await extract(normalized, playlist=playlist)
        except YtdlpError as exc:
            message_id = "music.youtube.empty_playlist" if playlist else "music.youtube.unavailable"
            raise SourceUnavailableError(message_id, str(exc)) from exc
        info = first_entry(data)
        if info is None:
            raise SourceUnavailableError("music.youtube.empty_playlist")
        _reject_live(info)
        if "youtube" not in str(info.get("extractor_key", info.get("extractor", ""))).lower():
            raise SourceUnavailableError("music.youtube.unavailable")
        canonical = info.get("webpage_url") or info.get("original_url")
        title = info.get("title")
        if not isinstance(canonical, str) or not isinstance(title, str) or not title.strip():
            raise SourceUnavailableError("music.youtube.unavailable")
        duration = _duration(info.get("duration"))
        return Track(
            provider=self.name,
            title=title.strip(),
            requester_id=request.requester_id,
            requester_display_name=request.requester_display_name,
            request_channel_id=request.text_channel_id,
            canonical_url=canonical,
            duration=duration,
            provider_data={"source_url": canonical},
        )

    async def inspect_upload(self, request: RequestContext, attachment: Any) -> Track:
        raise UserInputError("music.play.invalid_attachment")

    async def prepare(self, track: Track, *, start_at: float = 0) -> PreparedAudio:
        source_url = str(track.provider_data["source_url"])
        try:
            data = await extract(source_url, playlist=False)
        except YtdlpError as exc:
            raise SourceUnavailableError("music.youtube.unavailable", str(exc)) from exc
        info = first_entry(data)
        if info is None:
            raise SourceUnavailableError("music.youtube.unavailable")
        _reject_live(info)
        direct_url = info.get("url")
        if not isinstance(direct_url, str) or not direct_url:
            raise SourceUnavailableError("music.youtube.unavailable")
        return await stream_audio(direct_url, start_at=start_at)


def _normalize(parsed: ParseResult) -> tuple[str, bool]:
    if parsed.scheme not in {"http", "https"}:
        raise UserInputError("music.play.invalid_url")
    if parsed.username is not None or parsed.password is not None:
        raise UserInputError("music.play.invalid_url")
    host = (parsed.hostname or "").lower()
    if host not in _YOUTUBE_HOSTS:
        raise UserInputError("music.play.invalid_url")
    query = parse_qs(parsed.query)
    path = parsed.path.rstrip("/")
    if host == "youtu.be":
        video_id = path.lstrip("/").split("/", 1)[0]
        if not video_id:
            raise UserInputError("music.play.invalid_url")
        return f"https://www.youtube.com/watch?{urlencode({'v': video_id})}", False
    if path == "/watch" and query.get("v", [""])[0]:
        return f"https://www.youtube.com/watch?{urlencode({'v': query['v'][0]})}", False
    if path.startswith("/shorts/"):
        video_id = path.split("/", 3)[2]
        if not video_id:
            raise UserInputError("music.play.invalid_url")
        return f"https://www.youtube.com/shorts/{video_id}", False
    if path == "/playlist" and query.get("list", [""])[0]:
        return urlunparse(
            ("https", "www.youtube.com", "/playlist", "", urlencode({"list": query["list"][0]}), "")
        ), True
    if path.startswith("/live"):
        raise UnsupportedContentError("music.youtube.live_not_supported")
    raise UserInputError("music.play.invalid_url")


def _reject_live(info: dict[str, Any]) -> None:
    live_status = str(info.get("live_status", "")).lower()
    if info.get("is_live") or live_status in {"is_live", "is_upcoming"}:
        raise UnsupportedContentError("music.youtube.live_not_supported")


def _duration(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return None
