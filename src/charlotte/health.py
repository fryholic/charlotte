"""File-backed Gateway readiness diagnostic for Docker HEALTHCHECK."""

from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path
from typing import Any

HEALTH_PATH = Path("/tmp/charlotte-health.json")


class HealthWriter:
    def __init__(self, bot: Any, *, path: Path = HEALTH_PATH) -> None:
        self.bot = bot
        self.path = path
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self._write)
            except Exception as error:
                await self.bot.reporter.report(
                    error,
                    event="health.write_failed",
                )
            await asyncio.sleep(15)

    def _write(self) -> None:
        latency = float(getattr(self.bot, "latency", math.inf))
        ready = bool(self.bot.is_ready() and not self.bot.is_closed() and math.isfinite(latency))
        now = time.time()
        payload = {
            "ready": ready,
            "closed": self.bot.is_closed(),
            "latency": latency if math.isfinite(latency) else None,
            "heartbeat_at": now if ready else None,
            "updated_at": now,
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temporary.replace(self.path)
