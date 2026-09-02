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

# --- provisional thresholds, fit on synthetic data 2026-09-02 -------------
# Measured separations that justify each number:
#   perfusion  loose 0.08-0.11  |  everything else >= 1.08   -> gap at 0.5
#   ssqi       corrupted median 0.10, min -0.61              -> floor at 0.25
#   ssqi       exercise 0.37-0.67 vs clean states >= 0.75    -> degrade at 0.70
#   motion     exercise 0.46, corrupted 0.37-0.41 vs <= 0.05 -> degrade at 0.15
PERFUSION_MIN = 0.5      # below this the sensor is not optically coupled
SSQI_UNSCORED = 0.25     # below this the pulse shape is destroyed
SSQI_DEGRADED = 0.70     # below this the shape is compromised but readable
MOTION_DEGRADED = 0.15   # g, std of |accel|; above this motion corrupts PPG
RAIL_MAX = 0.02          # fraction of samples allowed at an ADC rail


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

    @property
    def scored(self) -> bool:
        """The product promise, in one boolean. False means show NO heart rate."""
        return self.ppg is not Trust.UNSCORED

    def __str__(self) -> str:
        why = f" ({'; '.join(self.reasons)})" if self.reasons else ""
        return f"ppg={self.ppg.value} ecg={self.ecg.value}{why}"


def assess(window: Window) -> Verdict:
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
    elif m["ppg_rail"] > RAIL_MAX:
        ppg, _ = Trust.UNSCORED, reasons.append("PPG saturated at ADC rail")
    elif m["perfusion"] < PERFUSION_MIN:
        ppg, _ = Trust.UNSCORED, reasons.append("clip not making contact - reseat it")
    elif m["ssqi"] < SSQI_UNSCORED:
        ppg, _ = Trust.UNSCORED, reasons.append("pulse waveform destroyed by artifact")
    elif m["ssqi"] < SSQI_DEGRADED or m["motion"] > MOTION_DEGRADED:
        ppg = Trust.DEGRADED
        if m["motion"] > MOTION_DEGRADED:
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
    elif m["ecg_rail"] > RAIL_MAX:
        ecg, _ = Trust.UNSCORED, reasons.append("ECG saturated at ADC rail")

    return Verdict(ppg=ppg, ecg=ecg, reasons=reasons, metrics=m)
