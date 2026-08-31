"""Exit non-zero when the diagnostic health file is stale or disconnected."""

from __future__ import annotations

import json
import sys
import time

from charlotte.constants import HEALTH_MAX_AGE
from charlotte.health import HEALTH_PATH


def healthy() -> bool:
    try:
        payload = json.loads(HEALTH_PATH.read_text(encoding="utf-8"))
        heartbeat_at = float(payload["heartbeat_at"])
    except OSError, KeyError, TypeError, ValueError, json.JSONDecodeError:
        return False
    return bool(payload.get("ready")) and time.time() - heartbeat_at <= HEALTH_MAX_AGE


def main() -> int:
    return 0 if healthy() else 1


if __name__ == "__main__":
    sys.exit(main())
