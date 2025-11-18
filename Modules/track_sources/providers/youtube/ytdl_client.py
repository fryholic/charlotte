"""YouTube specific YoutubeDL client helpers."""

from __future__ import annotations

from functools import lru_cache

import yt_dlp as youtube_dl

from Modules.track_sources.config import YTDL_FORMAT_OPTIONS


@lru_cache(maxsize=1)
def youtube_client() -> youtube_dl.YoutubeDL:
    return youtube_dl.YoutubeDL(YTDL_FORMAT_OPTIONS)
