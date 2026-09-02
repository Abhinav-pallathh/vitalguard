"""The Signal Quality Gate. This file JUDGES; `quality.py` measures.

    The scorer proposes. This gate concludes.

Nothing downstream may present a number for a window this gate marks UNSCORED.
Not a stale value, not an interpolation, not the last good reading held over.
UNSCORED is a state the user sees, not an error we hide.

Three states, not two:

    TRUSTED   -- report the number
    DEGRADED  -- report the number, say it is lower confidence, say why
    UNSCORED  -- report NO number, say why

DEGRADED exists because refusing to score during exercise would make the device
useless to exactly the group it is for -- people with cardiac risk who exercise.
Exercise genuinely degrades PPG; the honest response is to say so, not to
pretend the reading is pristine and not to go silent. `scored` stays binary, so
the product promise ("unscored rather than a guessed value") is exact.

THRESHOLDS ARE PROVISIONAL. Every number below was read off `calibrate.py`
against synthetic data, never invented and never taken from a paper. They MUST
be re-fit on real recordings before any performance number is quoted. The
constants are named and gathered here so re-fitting is a one-file change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from . import quality
from .replay import Window

# --- threshold profiles ---------------------------------------------------
#
# Thresholds are a property of the SENSOR, not of the algorithm. Decision D7
# said so; running the gate over WESAD on 2026-09-02 proved it -- constants fit
# on synthetic data rejected 93% of real human recordings. So they live in a
# named profile rather than as module globals, and every reported number has to
# say which profile produced it.

@dataclass(frozen=True, slots=True)
class GateProfile:
    """One sensor's quality thresholds.

    `perfusion_min = None` means the perfusion check is UNAVAILABLE for this
    source and is skipped -- not that it passes. Used for datasets whose
    hardware never exposed a raw DC pedestal, where any AC/DC figure we could
    compute would be measuring a constant we invented.
    """

    name: str
    ssqi_unscored: float
    ssqi_degraded: float
    perfusion_min: float | None
    motion_degraded: float
    rail_max: float = 0.02


SYNTHETIC = GateProfile(
    name="synthetic",
    # measured on synth.py 2026-09-02:
    #   |SSQI|     clean >= 0.75, corrupted median 0.10
    #   perfusion  loose 0.08-0.11 vs >= 1.08 everywhere else
    #   motion     exercise 0.46, corrupted ~0.39, quiet states <= 0.05
    ssqi_unscored=0.25,
    ssqi_degraded=0.70,
    perfusion_min=0.5,
    motion_degraded=0.15,
)

WESAD_E4 = GateProfile(
    name="wesad-e4",
    # measured on WESAD S2 2026-09-02. Real wrist PPG is markedly less peaked
    # than the synthetic generator's two-gaussian beat, so the same signal
    # quality yields a much lower |skew|. Re-fit, not reused.
    ssqi_unscored=0.12,
    ssqi_degraded=0.30,
    # UNAVAILABLE, not lenient: the Empatica E4 exposes no raw DC pedestal, so
    # the converter has to invent one and any perfusion number computed from it
    # is evidence about nothing. See wesad.INVENTED_PPG_DC.
    perfusion_min=None,
    motion_degraded=0.15,
)

# The profile that will actually ship. Deliberately absent until real ear-clip
# recordings exist -- there is no honest way to guess it, and a placeholder
# would get quoted.
EARCLIP_MAX30102 = None

DEFAULT_PROFILE = SYNTHETIC


class Trust(Enum):
    TRUSTED = "trusted"
    DEGRADED = "degraded"
    UNSCORED = "unscored"


@dataclass(slots=True)
class Verdict:
    """What the gate concluded about one window, and why.

    `reasons` is not decoration. A device that refuses to show a number has to
    tell the wearer what to fix -- "clip loose", "too much motion" -- or the
    refusal is indistinguishable from a broken device, and they take it off.
    """

    ppg: Trust
    ecg: Trust
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    profile: str = "unknown"   # which thresholds produced this verdict

    @property
    def scored(self) -> bool:
        """The product promise, in one boolean. False means show NO heart rate."""
        return self.ppg is not Trust.UNSCORED

    def __str__(self) -> str:
        why = f" ({'; '.join(self.reasons)})" if self.reasons else ""
        return f"ppg={self.ppg.value} ecg={self.ecg.value}{why}"


def assess(window: Window, profile: GateProfile = DEFAULT_PROFILE) -> Verdict:
    """Judge one window. Deterministic, no model, no state, no randomness."""
    ppg_raw = window.cols["ppg_ir"]
    ecg_raw = window.cols["ecg_raw"]

    m = {
        "ssqi": quality.ssqi(ppg_raw),
        "perfusion": quality.perfusion_index(ppg_raw),
        "motion": quality.motion_level(window.accel_mag),
        "ppg_rail": quality.rail_fraction(ppg_raw, quality.MAX30102_MAX),
        "ecg_rail": quality.rail_fraction(ecg_raw, quality.ESP32_ADC_MAX),
        "ecg_band": quality.ecg_band_fraction(ecg_raw),
    }
    reasons: list[str] = []

    # --- PPG -------------------------------------------------------------
    ppg = Trust.TRUSTED
    if quality.flatline(ppg_raw):
        ppg, _ = Trust.UNSCORED, reasons.append("PPG flatline - sensor disconnected")
    elif m["ppg_rail"] > profile.rail_max:
        ppg, _ = Trust.UNSCORED, reasons.append("PPG saturated at ADC rail")
    elif profile.perfusion_min is not None and m["perfusion"] < profile.perfusion_min:
        ppg, _ = Trust.UNSCORED, reasons.append("clip not making contact - reseat it")
    elif m["ssqi"] < profile.ssqi_unscored:
        ppg, _ = Trust.UNSCORED, reasons.append("pulse waveform destroyed by artifact")
    elif m["ssqi"] < profile.ssqi_degraded or m["motion"] > profile.motion_degraded:
        ppg = Trust.DEGRADED
        if m["motion"] > profile.motion_degraded:
            reasons.append("reading taken during motion")
        else:
            reasons.append("pulse waveform partially degraded")

    # --- ECG -------------------------------------------------------------
    # D3: the AD8232 lead-off pins are hardware ground truth about electrode
    # contact. We do not second-guess them with an inferred metric.
    ecg = Trust.TRUSTED
    if window.any_lead_off:
        ecg, _ = Trust.UNSCORED, reasons.append("ECG electrode detached")
    elif quality.flatline(ecg_raw):
        ecg, _ = Trust.UNSCORED, reasons.append("ECG flatline")
    elif m["ecg_rail"] > profile.rail_max:
        ecg, _ = Trust.UNSCORED, reasons.append("ECG saturated at ADC rail")

    return Verdict(ppg=ppg, ecg=ecg, reasons=reasons, metrics=m, profile=profile.name)
