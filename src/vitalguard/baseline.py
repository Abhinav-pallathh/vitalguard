"""The Personal Baseline Model -- differentiator #2.

The pitch's opening complaint is that consumer wearables apply one fixed
threshold to every user: 55 bpm is normal for a trained athlete and a warning
sign for a sedentary person. Learning a personal AVERAGE only half-fixes that.
If you then apply a fixed "+15 bpm is concerning" rule on top of it, you have
re-introduced exactly the same problem one level up.

So this model learns two things:

    resting_hr -- where this person sits
    spread     -- how much this person normally moves around that

and deviation is reported in units of the person's OWN variability. A +12 bpm
excursion is unremarkable for someone whose resting HR wanders by 8 bpm and is
a genuine signal for someone whose resting HR is stable to 2 bpm.

Two rules carried over from the rest of the system:

  1. Only windows that pass the quality gate AND have estimator agreement AND
     are genuinely at rest may contribute. A baseline built from readings we do
     not trust is worse than no baseline, because it is invisible.

  2. Until there is enough evidence, the baseline is None. Not a
     population average, not a partial estimate. The device says "calibrating"
     and shows no personal comparison, because a wrong baseline silently
     mislabels every subsequent reading.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from .gate import Trust, Verdict
from .hr import Estimate
from .replay import Window

# Seconds of UNIQUE qualifying data before a baseline is reported.
#
# Deliberately measured as coverage, not window count. Windows overlap 90% at a
# 1 s hop, so 60 s of recording yields 51 windows -- counting those as 51
# samples would let the model "calibrate" on about six seconds of independent
# evidence. The pitch promises a *brief* calibration; 60 s is brief and honest.
MIN_COVERAGE_S = 60.0

# Resting means resting. Stricter than the gate's DEGRADED motion threshold
# (0.15 g): a reading can be perfectly trustworthy while the wearer strolls
# across a room, and that reading is fine to display but must not teach the
# model what this person's resting rate is.
RESTING_MOTION_MAX = 0.05

# Bounded history so the baseline tracks genuine drift (fitness, illness,
# medication) instead of being anchored forever to calibration day.
HISTORY = 600

# MAD -> comparable-to-sigma scaling for normally distributed data.
MAD_TO_SIGMA = 1.4826

# Floor on the learned spread. Without it, an unnaturally steady calibration
# gives a spread near zero and every later reading becomes a 40-sigma event.
MIN_SPREAD_BPM = 1.5

# Same floor logic for skin conductance, in raw ADC counts.
MIN_GSR_SPREAD = 5.0


@dataclass(slots=True)
class Baseline:
    resting_hr: float | None
    spread: float | None
    coverage_s: float
    n_contributing: int
    # Tonic skin conductance is learned per person for the same reason heart
    # rate is: absolute EDA is meaningless across people. It depends on
    # electrode placement, skin hydration, temperature and individual
    # physiology, and varies by an order of magnitude between two healthy
    # subjects. A fixed "GSR above X means stress" rule is the SAME mistake as
    # a fixed "HR above 100 means concern" rule, one signal further along.
    resting_gsr: float | None = None
    gsr_spread: float | None = None

    @property
    def calibrated(self) -> bool:
        return self.resting_hr is not None

    def __str__(self) -> str:
        if not self.calibrated:
            return f"calibrating ({self.coverage_s:.0f}/{MIN_COVERAGE_S:.0f}s)"
        return f"{self.resting_hr:.1f} bpm +/- {self.spread:.1f}"


@dataclass(slots=True)
class Deviation:
    """How far a reading sits from this person's own normal."""

    hr_bpm: float
    baseline_bpm: float
    delta_bpm: float
    personal_sigma: float      # deviation in units of THIS person's variability

    def __str__(self) -> str:
        return (f"{self.hr_bpm:.0f} bpm ({self.delta_bpm:+.0f} vs "
                f"{self.baseline_bpm:.0f}, {self.personal_sigma:+.1f}sd)")


class PersonalBaseline:
    """Learns one wearer's resting heart rate and their normal variability."""

    def __init__(self, min_coverage_s: float = MIN_COVERAGE_S) -> None:
        self._hr: deque[float] = deque(maxlen=HISTORY)
        self._gsr: deque[float] = deque(maxlen=HISTORY)
        self._covered_s: set[int] = set()
        self._min_coverage_s = min_coverage_s

    # --- learning --------------------------------------------------------

    @staticmethod
    def qualifies(window: Window, verdict: Verdict, est: Estimate) -> bool:
        """Three independent gates. All must pass.

        TRUSTED already implies low motion, but the resting threshold is
        stricter than the gate's, so it is checked separately rather than
        assumed -- if the gate's constant is ever re-tuned this must not
        silently loosen with it.
        """
        return (
            verdict.ppg is Trust.TRUSTED
            and est.hr_bpm is not None
            and est.agree
            and verdict.metrics.get("motion", 1.0) <= RESTING_MOTION_MAX
        )

    def update(self, window: Window, verdict: Verdict, est: Estimate) -> bool:
        """Offer one window to the model. Returns whether it contributed."""
        if not self.qualifies(window, verdict, est):
            return False
        self._hr.append(float(est.hr_bpm))
        self._gsr.append(float(np.median(window.cols["gsr_raw"])))
        # Unique whole-second buckets, so overlapping windows cannot inflate
        # coverage. This is the line that makes MIN_COVERAGE_S mean something.
        start_s, end_s = window.t_start_ms // 1000, window.t_end_ms // 1000
        self._covered_s.update(range(start_s, end_s + 1))
        return True

    # --- reporting -------------------------------------------------------

    @property
    def coverage_s(self) -> float:
        return float(len(self._covered_s))

    def snapshot(self) -> Baseline:
        if self.coverage_s < self._min_coverage_s or len(self._hr) < 2:
            return Baseline(None, None, self.coverage_s, len(self._hr))

        hr = np.fromiter(self._hr, dtype=float)
        centre = float(np.median(hr))
        # MAD rather than std: one artifact that slipped every other check
        # cannot drag the learned spread.
        mad = float(np.median(np.abs(hr - centre)))
        spread = max(mad * MAD_TO_SIGMA, MIN_SPREAD_BPM)

        gsr = np.fromiter(self._gsr, dtype=float)
        g_centre = float(np.median(gsr))
        g_mad = float(np.median(np.abs(gsr - g_centre)))
        g_spread = max(g_mad * MAD_TO_SIGMA, MIN_GSR_SPREAD)

        return Baseline(centre, spread, self.coverage_s, len(self._hr),
                        resting_gsr=g_centre, gsr_spread=g_spread)

    def gsr_deviation(self, window: Window) -> float | None:
        """This window's tonic skin conductance, in units of the person's own
        variability. None until calibrated."""
        base = self.snapshot()
        if base.resting_gsr is None:
            return None
        level = float(np.median(window.cols["gsr_raw"]))
        return (level - base.resting_gsr) / base.gsr_spread

    def deviation(self, est: Estimate, verdict: Verdict) -> Deviation | None:
        """Compare a reading to this person's normal. None unless it is safe.

        `verdict` is REQUIRED and has no default. An earlier version took a bare
        `hr_bpm: float` and had no way to know whether that number had passed
        the quality gate -- and within an hour of the gate being written, a
        demo script fed it an UNSCORED window and got back a confident
        "217 bpm, +51.5sd". The gate was working perfectly; the number simply
        walked around it.

        A rule that says "always check the verdict first" is a rule someone
        forgets. A required parameter is one they cannot. This signature is the
        enforcement mechanism, and it is deliberately inconvenient.
        """
        base = self.snapshot()
        if not verdict.scored or est.hr_bpm is None or not base.calibrated:
            return None
        delta = float(est.hr_bpm) - base.resting_hr
        return Deviation(
            hr_bpm=float(est.hr_bpm),
            baseline_bpm=base.resting_hr,
            delta_bpm=delta,
            personal_sigma=delta / base.spread,
        )
