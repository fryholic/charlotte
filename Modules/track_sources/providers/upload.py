"""Upload driven track source (Discord attachments)."""

from __future__ import annotations

from Modules.track_sources.base import BaseUploadSource, UploadPayload
from Modules.track_sources.providers.memory import MemoryAudioSource


class UploadSource(BaseUploadSource):
    name = "upload"

    @classmethod
    async def create_tracks(cls, payload: UploadPayload):
        source = await MemoryAudioSource.from_upload(payload.file)
        return [source]
