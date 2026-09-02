"""Feature extraction for the learned scorer.

One window in, one fixed-length vector out. The rule-based scorer and any model
consume THE SAME vector, so a comparison between them is a comparison of
decision rules and not of who got better inputs.

⚠ WHAT IS DELIBERATELY EXCLUDED, AND WHY

Signal-quality metrics -- SSQI, perfusion index, rail fraction -- are NOT
features, even though they are already computed and would be free to add.

WESAD showed that PPG quality degrades under stress (peripheral
vasoconstriction; heart-rate error rises from 2.25 to 10.01 bpm). So a
classifier given SSQI can learn "bad signal implies stress" and score well by
detecting SENSOR DEGRADATION rather than physiology. It would look good on
WESAD and fall apart on a subject whose sensor happened to sit badly at rest.

That is a label leak, it is invisible in the accuracy number, and it is exactly
the kind of shortcut this project exists not to take. Quality decides WHETHER
we score, never WHAT we score.

Also excluded: the raw heart rate in bpm. Only the personal sigma is a feature,
because absolute bpm would let the model learn population thresholds -- the
thing the entire product argues against.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal as sps

from .baseline import Deviation, PersonalBaseline
from .hr import Estimate
from .replay import Window
from .schema import SAMPLE_RATE_HZ

FS = SAMPLE_RATE_HZ

FEATURE_NAMES: tuple[str, ...] = (
    "hr_sigma",      # HR deviation in units of THIS person's variability
    "motion",        # std of |acceleration|, g
    "gsr_sigma",     # tonic skin conductance in this person's own units
    "gsr_slope",     # within-window EDA trend, counts/s
    "eda_peaks",     # SCR-like rises in the window
    "rmssd",         # HRV: root-mean-square of successive RR differences, ms
    "sdnn",          # HRV: std of RR intervals, ms
    "pnn50",         # HRV: fraction of successive RR diffs > 50 ms
)


@dataclass(slots=True)
class FeatureRow:
    values: np.ndarray          # aligned with FEATURE_NAMES
    label: str
    subject: str

    def as_dict(self) -> dict[str, float]:
        return dict(zip(FEATURE_NAMES, self.values.tolist()))


def _hrv(rr_ms: np.ndarray) -> tuple[float, float, float]:
    """RMSSD, SDNN, pNN50 from beat-to-beat intervals.

    These are the most established stress markers in the physiological
    literature, and `hr.py` has been computing rr_ms all along and throwing it
    away. Free signal.

    ⚠ A 10 s window holds only ~10 beats. RMSSD is conventionally computed over
    30 s or more, so these are NOISY SHORT-WINDOW ESTIMATES. They are useful as
    features; they should not be quoted as clinical HRV figures.
    """
    if rr_ms is None or rr_ms.size < 3:
        return 0.0, 0.0, 0.0
    d = np.diff(rr_ms)
    rmssd = float(np.sqrt(np.mean(d ** 2)))
    sdnn = float(np.std(rr_ms))
    pnn50 = float(np.mean(np.abs(d) > 50.0))
    return rmssd, sdnn, pnn50


def _eda(gsr_raw: np.ndarray) -> tuple[float, int]:
    """Within-window EDA trend and a count of skin-conductance responses.

    SCRs rise over ~1-3 s and decay over ~4 s, so a peak-finder with a minimum
    1 s separation and a prominence tied to the window's own scale picks them
    up without a fixed amplitude threshold (which would not transfer between
    people, for the same reason absolute EDA does not).
    """
    g = np.asarray(gsr_raw, dtype=float)
    t = np.arange(g.size) / FS
    slope = float(np.polyfit(t, g, 1)[0]) if g.size > 2 else 0.0
    prom = max(float(np.std(g)) * 0.5, 1.0)
    peaks, _ = sps.find_peaks(g, distance=int(FS * 1.0), prominence=prom)
    return slope, int(peaks.size)


def extract(
    window: Window,
    deviation: Deviation,
    estimate: Estimate,
    motion: float,
    baseline: PersonalBaseline,
) -> np.ndarray:
    """Build one feature vector. `deviation` is required, so a feature row can
    only exist for a window that already passed the quality gate."""
    gsr_sigma = baseline.gsr_deviation(window)
    slope, peaks = _eda(window.cols["gsr_raw"])
    rmssd, sdnn, pnn50 = _hrv(estimate.rr_ms)
    return np.array([
        deviation.personal_sigma,
        motion,
        0.0 if gsr_sigma is None else gsr_sigma,
        slope,
        float(peaks),
        rmssd,
        sdnn,
        pnn50,
    ], dtype=float)
