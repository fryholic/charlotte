"""SoundCloud specific YoutubeDL helpers."""

from __future__ import annotations

from functools import lru_cache

import yt_dlp as youtube_dl

from Modules.track_sources.config import SOUNDCLOUD_FORMAT_OPTIONS


@lru_cache(maxsize=1)
def soundcloud_client() -> youtube_dl.YoutubeDL:
    return youtube_dl.YoutubeDL(SOUNDCLOUD_FORMAT_OPTIONS)
