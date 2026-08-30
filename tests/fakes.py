from __future__ import annotations

import asyncio
from types import SimpleNamespace
from urllib.parse import ParseResult

from charlotte.music.models import PreparedAudio, RequestContext, Track


class FakeSource:
    def __init__(self) -> None:
        self.cleaned = False

    def cleanup(self) -> None:
        self.cleaned = True

    def is_opus(self) -> bool:
        return True

    def read(self) -> bytes:
        return b""


class FakeProvider:
    name = "fake"
    supports_upload = False

    def __init__(self, delay: float = 0) -> None:
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.sources: list[FakeSource] = []

    def supports_url(self, parsed_url: ParseResult) -> bool:
        return parsed_url.hostname == "example.com"

    async def inspect_url(
        self, request: RequestContext, parsed_url: ParseResult, raw_url: str
    ) -> Track:
        if self.delay:
            await asyncio.sleep(self.delay)
        return make_track(raw_url, request=request)

    async def inspect_upload(self, request, attachment):
        raise NotImplementedError

    async def prepare(self, track: Track, *, start_at: float = 0) -> PreparedAudio:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            source = FakeSource()
            self.sources.append(source)
            return PreparedAudio(source=source, seekable=True)
        finally:
            self.active -= 1


class FakeReporter:
    def __init__(self) -> None:
        self.reports = []
        self.expected_events = []

    async def report(self, error, *, event, context=None, notify_owner=True):
        self.reports.append((error, event, context, notify_owner))
        return "error-id"

    def expected(self, event, *, context=None, error=None):
        self.expected_events.append((event, context, error))


class FakeTextChannel:
    def __init__(self, channel_id: int = 500) -> None:
        self.id = channel_id
        self.messages = []

    async def send(self, content=None, **kwargs):
        self.messages.append((content, kwargs))


class FakeVoiceClient:
    def __init__(self, channel) -> None:
        self.channel = channel
        self.connected = True
        self.playing = False
        self.paused = False
        self.after = None
        self.source = None

    def is_connected(self) -> bool:
        return self.connected

    def is_playing(self) -> bool:
        return self.playing

    def is_paused(self) -> bool:
        return self.paused

    def play(self, source, *, after) -> None:
        if self.playing or self.paused:
            raise RuntimeError("already playing")
        self.source = source
        self.after = after
        self.playing = True
        self.paused = False

    def pause(self) -> None:
        self.playing = False
        self.paused = True

    def resume(self) -> None:
        self.playing = True
        self.paused = False

    def stop(self) -> None:
        callback = self.after
        self.playing = False
        self.paused = False
        self.after = None
        if callback is not None:
            callback(None)

    def finish(self, error=None) -> None:
        callback = self.after
        self.playing = False
        self.paused = False
        self.after = None
        if callback is not None:
            callback(error)

    async def move_to(self, channel) -> None:
        self.channel = channel

    async def disconnect(self, *, force=False) -> None:
        self.connected = False
        self.playing = False
        self.paused = False
        self.channel.guild.voice_client = None


class FakeVoiceChannel:
    def __init__(self, guild, channel_id: int, name: str) -> None:
        self.guild = guild
        self.id = channel_id
        self.name = name
        self.members = []

    async def connect(self, *, timeout, reconnect) -> FakeVoiceClient:  # noqa: ASYNC109
        voice = FakeVoiceClient(self)
        self.guild.voice_client = voice
        return voice


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self.voice_client = None


class FakeBot:
    def __init__(self) -> None:
        self.guilds = {}
        self.channels = {}
        self.user = SimpleNamespace(id=999)

    def get_guild(self, guild_id: int):
        return self.guilds.get(guild_id)

    def get_channel(self, channel_id: int):
        return self.channels.get(channel_id)


def make_track(title: str, *, request: RequestContext | None = None) -> Track:
    context = request or RequestContext(1, 500, 100, "requester")
    return Track(
        provider="fake",
        title=title,
        requester_id=context.requester_id,
        requester_display_name=context.requester_display_name,
        request_channel_id=context.text_channel_id,
    )
