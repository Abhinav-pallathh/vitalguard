"""Turn a recording into the windows every downstream layer consumes.

Everything -- the quality gate, the baseline model, the severity scorer --
operates on a fixed-length window of samples, never on a single sample. So the
window IS the unit of analysis, and this file is the only place that decides
what a window is.

Why 10 seconds by default: at 65 bpm that is ~11 beats, enough for a stable
heart-rate estimate and enough for the skewness SQI to mean anything. Shorter
windows make the quality index noisy; longer ones make the device slow to
react to a strap coming loose, which is the exact failure we exist to catch.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

from .schema import SAMPLE_RATE_HZ, Sample, accel_magnitude, read_csv, to_arrays

DEFAULT_WINDOW_S = 10.0
DEFAULT_HOP_S = 1.0


@dataclass(slots=True)
class Window:
    """One unit of analysis."""

    index: int
    t_start_ms: int
    t_end_ms: int
    cols: dict[str, np.ndarray]

    @property
    def n(self) -> int:
        return self.cols["t_ms"].size

    @property
    def duration_s(self) -> float:
        return self.n / SAMPLE_RATE_HZ

    @property
    def accel_mag(self) -> np.ndarray:
        return accel_magnitude(self.cols)

    @property
    def label(self) -> str:
        """The window's label, by majority vote.

        A window straddling a label change gets the dominant one. Windows that
        straddle are a real category and we do not silently drop them -- the
        scorer has to cope with transitions, because reality has transitions.
        """
        labels, counts = np.unique(self.cols["label"], return_counts=True)
        return str(labels[int(np.argmax(counts))])

    @property
    def any_lead_off(self) -> bool:
        """True if ANY sample in the window had a detached electrode.

        Deliberately `any`, not `mean` or `most`. A hardware signal saying the
        electrode came off is not something to average away.
        """
        return bool(np.any(self.cols["lead_off"] == 1))


def windows(
    samples: list[Sample],
    window_s: float = DEFAULT_WINDOW_S,
    hop_s: float = DEFAULT_HOP_S,
) -> Iterator[Window]:
    """Slide a window over a recording. Partial trailing windows are dropped."""
    if not samples:
        return
    size = int(round(window_s * SAMPLE_RATE_HZ))
    hop = int(round(hop_s * SAMPLE_RATE_HZ))
    if size <= 0 or hop <= 0:
        raise ValueError("window_s and hop_s must be positive")

    cols = to_arrays(samples)
    total = cols["t_ms"].size
    if total < size:
        return

    for i, start in enumerate(range(0, total - size + 1, hop)):
        sl = slice(start, start + size)
        yield Window(
            index=i,
            t_start_ms=int(cols["t_ms"][start]),
            t_end_ms=int(cols["t_ms"][start + size - 1]),
            cols={k: v[sl] for k, v in cols.items()},
        )


def from_csv(
    path: str | Path,
    window_s: float = DEFAULT_WINDOW_S,
    hop_s: float = DEFAULT_HOP_S,
) -> Iterator[Window]:
    """Replay a recording off disk. This is how we develop without hardware."""
    yield from windows(read_csv(path), window_s=window_s, hop_s=hop_s)
