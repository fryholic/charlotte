"""Helpers responsible for downloading Spotify audio into memory."""

from __future__ import annotations

import asyncio
import io
import os
import tempfile
import traceback
from typing import Dict, Tuple

from dotenv import load_dotenv

from deezspot.libutils.utils import get_ids, link_is_valid
from deezspot.models.download.preferences import Preferences
from deezspot.spotloader.__download__ import DW_TRACK
from deezspot.spotloader.__init__ import SpoLogin
from deezspot.spotloader.__spo_api__ import tracking


load_dotenv()


class SpotifyDownloadError(Exception):
    pass


def _find_credentials_file() -> str:
    possible_paths = [
        os.getenv("SPOTIFY_CREDENTIALS_PATH"),
        "/app/credentials.json",
        "/home/pi/charlotte/credentials.json",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "credentials.json"),
    ]
    for path in possible_paths:
        if path and os.path.exists(path):
            return path
    raise SpotifyDownloadError("Spotify credentials.json 경로를 찾을 수 없습니다")


def _blocking_download_spotify(spotify_url: str) -> Tuple[io.BytesIO, Dict]:
    try:
        link_is_valid(spotify_url)
        track_id = get_ids(spotify_url)

        client_id = os.getenv("SPOTIFY_CLIENT_ID")
        client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise SpotifyDownloadError("SPOTIFY_CLIENT_ID/SECRET 환경 변수를 설정하세요")

        credentials_path = _find_credentials_file()
        spo_login = SpoLogin(
            credentials_path=credentials_path,
            spotify_client_id=client_id,
            spotify_client_secret=client_secret,
        )

        song_metadata = tracking(track_id)
        if not song_metadata:
            raise SpotifyDownloadError("Spotify 메타데이터를 가져오지 못했습니다")

        preferences = Preferences()
        preferences.link = spotify_url
        preferences.ids = track_id
        preferences.song_metadata = song_metadata
        preferences.quality_download = "NORMAL"
        preferences.output_dir = tempfile.mkdtemp()
        preferences.recursive_quality = False
        preferences.recursive_download = False
        preferences.not_interface = True
        preferences.method_save = 0
        preferences.is_episode = False
        preferences.convert_to = None
        preferences.initial_retry_delay = 10
        preferences.retry_delay_increase = 5
        preferences.max_retries = 3

        track = DW_TRACK(preferences).dw()
        if not track or not track.success or not track.song_path:
            raise SpotifyDownloadError("트랙 다운로드에 실패했습니다")

        buffer = io.BytesIO()
        with open(track.song_path, "rb") as handle:
            buffer.write(handle.read())
        buffer.seek(0)

        try:
            os.remove(track.song_path)
            os.rmdir(os.path.dirname(track.song_path))
        except Exception:
            pass

        metadata = {
            "title": getattr(song_metadata, "title", "Unknown"),
            "artist": " & ".join(
                [getattr(artist, "name", "Unknown") for artist in getattr(song_metadata, "artists", [])]
            ),
            "duration": getattr(song_metadata, "duration_ms", 0),
        }
        return buffer, metadata

    except Exception as exc:
        traceback.print_exc()
        if isinstance(exc, SpotifyDownloadError):
            raise
        raise SpotifyDownloadError(str(exc)) from exc


async def download_spotify_to_buffer(spotify_url: str):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _blocking_download_spotify, spotify_url)
