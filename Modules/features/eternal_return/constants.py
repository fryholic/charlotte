"""Constants and configuration knobs for the Eternal Return feature."""

from __future__ import annotations

import os
from typing import Dict

DEFAULT_API_BASE = "https://er.dakgg.io/api/v1"
TIERS_ENDPOINT = os.getenv("ER_TIERS_ENDPOINT", f"{DEFAULT_API_BASE}/data/tiers?hl=ko")
PROFILE_ENDPOINT_TEMPLATE = os.getenv(
    "ER_PROFILE_ENDPOINT_TEMPLATE",
    f"{DEFAULT_API_BASE}/players/{{player_id}}/profile",
)
REQUEST_TIMEOUT = float(os.getenv("ER_API_TIMEOUT", "10"))
MAX_MMR_POINTS = int(os.getenv("ER_MAX_MMR_POINTS", "15"))

SEASON_ID_MAP: Dict[str, int] = {
    "SEASON_15": 29,
}

PLOT_TEXT_COLOR = os.getenv("ER_PLOT_TEXT_COLOR", "white")
PLOT_DEFAULT_FIGSIZE = (6, 4)
PLOT_FACE_COLOR = os.getenv("ER_PLOT_FACE_COLOR", "none")
PLOT_LINE_COLOR = os.getenv("ER_PLOT_LINE_COLOR", PLOT_TEXT_COLOR)
