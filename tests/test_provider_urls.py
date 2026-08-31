from urllib.parse import urlparse

import pytest

from charlotte.errors import UnsupportedContentError, UserInputError
from charlotte.providers.soundcloud import _normalize as normalize_soundcloud
from charlotte.providers.youtube import _normalize as normalize_youtube


@pytest.mark.parametrize(
    ("url", "playlist"),
    [
        ("https://www.youtube.com/watch?v=abc&list=ignored", False),
        ("https://youtu.be/abc", False),
        ("https://www.youtube.com/shorts/abc", False),
        ("https://www.youtube.com/playlist?list=PL123", True),
    ],
)
def test_youtube_accepts_only_video_shorts_or_playlist(url, playlist) -> None:
    normalized, is_playlist = normalize_youtube(urlparse(url))
    assert normalized.startswith("https://")
    assert is_playlist is playlist


@pytest.mark.parametrize(
    "url",
    [
        "https://music.youtube.com/watch?v=abc",
        "https://www.youtube.com/results?search_query=abc",
        "ytsearch:query",
    ],
)
def test_youtube_rejects_music_and_search_inputs(url) -> None:
    with pytest.raises(UserInputError):
        normalize_youtube(urlparse(url))


def test_youtube_live_path_is_rejected() -> None:
    with pytest.raises(UnsupportedContentError):
        normalize_youtube(urlparse("https://youtube.com/live/abc"))


@pytest.mark.parametrize(
    "url",
    [
        "https://user@example.com/watch?v=abc",
        "https://user:password@youtube.com/watch?v=abc",
        "https://token@soundcloud.com/user/track",
    ],
)
def test_embedded_url_credentials_are_rejected(url) -> None:
    parsed = urlparse(url)
    normalizer = normalize_soundcloud if parsed.hostname == "soundcloud.com" else normalize_youtube
    with pytest.raises(UserInputError):
        normalizer(parsed)


def test_soundcloud_rejects_sets_but_keeps_secret_track_url() -> None:
    with pytest.raises(UnsupportedContentError):
        normalize_soundcloud(urlparse("https://soundcloud.com/user/sets/collection"))
    normalized = normalize_soundcloud(
        urlparse("https://soundcloud.com/user/track/s-secret?si=share")
    )
    assert "s-secret" in normalized
