"""Reusable plotting helpers for the Eternal Return feature."""

from __future__ import annotations

import io
from typing import Sequence, Tuple

import matplotlib.pyplot as plt

from .constants import (
    PLOT_DEFAULT_FIGSIZE,
    PLOT_FACE_COLOR,
    PLOT_LINE_COLOR,
    PLOT_TEXT_COLOR,
)

MmrPoint = Tuple[str, int]


def build_mmr_plot(points: Sequence[MmrPoint]) -> io.BytesIO:
    """Create an in-memory PNG containing the MMR history chart."""
    fig, ax = plt.subplots(figsize=PLOT_DEFAULT_FIGSIZE)
    fig.patch.set_facecolor(PLOT_FACE_COLOR)
    ax.set_facecolor(PLOT_FACE_COLOR)

    ax.tick_params(colors=PLOT_TEXT_COLOR)
    ax.spines["bottom"].set_color(PLOT_TEXT_COLOR)
    ax.spines["left"].set_color(PLOT_TEXT_COLOR)
    ax.spines["top"].set_color(PLOT_FACE_COLOR)
    ax.spines["right"].set_color(PLOT_FACE_COLOR)

    if points:
        x_values = list(range(len(points)))[::-1]
        labels = [label for label, _ in points][::-1]
        values = [value for _, value in points][::-1]
        ax.plot(x_values, values, color=PLOT_LINE_COLOR, marker="o")
        ax.set_xticks(x_values)
        ax.set_xticklabels(labels, rotation=45)
        ax.invert_xaxis()
    else:
        ax.text(
            0.5,
            0.5,
            "RP 데이터 없음",
            ha="center",
            va="center",
            transform=ax.transAxes,
            color=PLOT_TEXT_COLOR,
        )

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=100)
    buf.seek(0)
    plt.close(fig)
    return buf
