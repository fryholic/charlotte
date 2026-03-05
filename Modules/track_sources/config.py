"""Centralised configuration for media download/transcode options."""

COMMON_DOWNLOAD_OPTIONS = {
    "restrictfilenames": True,
    "ignoreerrors": False,
    "quiet": False,
    "no_warnings": False,
    "postprocessors": [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "opus",
            "preferredquality": "320",
        }
    ],
}

YTDL_FORMAT_OPTIONS = {
    **COMMON_DOWNLOAD_OPTIONS,
    "format": "bestaudio/best",
    "noplaylist": True,
    "extract_flat": "in_playlist",
    # "impersonate": "chrome", # Removed due to yt-dlp AssertionError (expects ImpersonateTarget object)
    "concurrent_fragment_downloads": 5,
}

SOUNDCLOUD_FORMAT_OPTIONS = {
    **COMMON_DOWNLOAD_OPTIONS,
    "format": "bestaudio/best",
    "noplaylist": False,
    "extract_flat": False,
    "default_search": "scsearch1",
}

FFMPEG_STREAM_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -thread_queue_size 4096",
    "options": "-vn -b:a 320k -ac 2 -ar 48000 -bufsize 128k",
}

FFMPEG_MEMORY_OPTIONS = {
    "before_options": "-vn -loglevel warning ",
    "options": "-c:a libopus -b:a 320k -ar 48000 ",
}
