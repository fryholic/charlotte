from __future__ import annotations

import json
from types import SimpleNamespace

from charlotte.health import HealthWriter


def test_mark_starting_invalidates_stale_ready_health_file(tmp_path) -> None:
    path = tmp_path / "charlotte-health.json"
    path.write_text(
        json.dumps(
            {
                "ready": True,
                "closed": False,
                "latency": 0.1,
                "heartbeat_at": 123,
                "updated_at": 123,
            }
        ),
        encoding="utf-8",
    )
    writer = HealthWriter(SimpleNamespace(), path=path)

    writer.mark_starting()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["ready"] is False
    assert payload["closed"] is False
    assert payload["latency"] is None
    assert payload["heartbeat_at"] is None
    assert payload["updated_at"] > 123
