"""Centralised configuration for media download/transcode options."""

YTDL_FORMAT_OPTIONS = {
    "format": "bestaudio/best",
    "restrictfilenames": True,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "quiet": False,
    "no_warnings": False,
    "extract_flat": "in_playlist",
    "http_headers": {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    },
    "postprocessors": [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "opus",
            "preferredquality": "320",
        }
    ],
}

SOUNDCLOUD_FORMAT_OPTIONS = {
    **YTDL_FORMAT_OPTIONS,
    "noplaylist": False,
    "extract_flat": False,
    "default_search": "scsearch1",
}

FFMPEG_STREAM_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn -b:a 320k -ac 2 -ar 48000",
}

FFMPEG_MEMORY_OPTIONS = {
    "before_options": "-vn -loglevel warning ",
    "options": "-c:a libopus -b:a 320k -ar 48000 ",
}
