"""Synthetic recordings, so every layer is testable before hardware exists.

This is not a toy. It is the ONLY way to test the severity scorer against the
case that matters most -- an elevated heart rate with no physical and no
emotional explanation -- because you cannot ethically induce that in a subject
and it will not appear in any dataset you can download.

The five scenarios encode the discrimination the whole product claims to make:

    scenario       HR      motion   GSR      -> correct verdict
    ----------------------------------------------------------------
    rest           ~65     low      flat        normal
    exercise       ~130    HIGH     rising      elevated, EXPLAINED
    stress         ~95     low      RISING      elevated, arousal
    unexplained    ~110    low      flat        ALERT  <-- the product
    corrupted      --      any      any         UNSCORED (motion + lead-off)
    loose          --      low      flat        UNSCORED (weak coupling)

`loose` was added after the first calibration run: `corrupted` modelled motion
artifact, which ADDS amplitude, so perfusion index went UP. A clip that is
barely on does the opposite -- the pulsatile component collapses while the DC
pedestal stays. That is the likeliest real-world failure and the one the stage
demo depends on, and the generator did not have it.

Note the shape of it: `exercise` and `unexplained` differ ONLY in motion, and
`stress` and `unexplained` differ ONLY in GSR. That is deliberate. If the
scorer can separate these five, the three-signal architecture is justified. If
it can separate them without GSR, the GSR is decoration and we should say so.
"""
from __future__ import annotations

import numpy as np

from .schema import SAMPLE_RATE_HZ, Sample

FS = SAMPLE_RATE_HZ

# MAX30102 IR sits on a large DC pedestal; the pulsatile part is ~1% of it.
# Getting this ratio roughly right matters, because the perfusion index the
# quality gate uses is literally AC/DC.
PPG_DC = 80_000.0
PPG_AC_FRAC = 0.012

SCENARIOS = ("rest", "exercise", "stress", "unexplained", "corrupted", "loose")

# (hr_bpm, accel_g_rms, gsr_slope_counts_per_s, label)
_PROFILE = {
    "rest":        (65.0,  0.02,  0.0,  "rest"),
    "exercise":    (130.0, 0.85,  9.0,  "exercise"),
    "stress":      (95.0,  0.03, 22.0,  "stress"),
    "unexplained": (110.0, 0.03,  0.0,  "unknown"),
    "corrupted":   (75.0,  0.45,  0.0,  "unknown"),
    "loose":       (70.0,  0.05,  0.0,  "unknown"),
}


# Slow autonomic/respiratory drift of the mean rate, in bpm. Real resting heart
# rate is NOT a constant with jitter -- it wanders by several bpm over a minute.
# Added 2026-09-02 after the baseline model learned a spread of ~0 on the old
# generator and every later reading came out as a 40-sigma event. A generator
# that understates variability makes the personalisation look better than it is.
HR_DRIFT_BPM = 3.0


def _beat_times(hr_bpm: float, dur_s: float, rng: np.random.Generator) -> np.ndarray:
    """Beat instants with realistic RR jitter AND slow drift of the mean rate.

    Two separate effects, and they are not interchangeable:
      - fast jitter (~4% RR) stops peak detectors scoring perfectly for the
        wrong reason, because every beat would otherwise land on one phase
      - slow drift is what a personal baseline's SPREAD is actually measuring;
        without it the learned spread is zero and personalisation is a no-op
    """
    phase = rng.uniform(0, 2 * np.pi)
    period = rng.uniform(18.0, 32.0)          # seconds per drift cycle
    times, t = [], 60.0 / hr_bpm
    while t < dur_s:
        times.append(t)
        drift = HR_DRIFT_BPM * np.sin(2 * np.pi * t / period + phase)
        rr = 60.0 / (hr_bpm + drift)
        t += rr * (1.0 + rng.normal(0.0, 0.04))
    return np.array(times)


def _ppg_wave(t: np.ndarray, beats: np.ndarray) -> np.ndarray:
    """Systolic peak + dicrotic notch, one bump pair per beat."""
    y = np.zeros_like(t)
    for b in beats:
        y += np.exp(-0.5 * ((t - b) / 0.055) ** 2)              # systolic
        y += 0.35 * np.exp(-0.5 * ((t - b - 0.22) / 0.070) ** 2)  # dicrotic
    return y


def _ecg_wave(t: np.ndarray, beats: np.ndarray) -> np.ndarray:
    """Simplified P-QRS-T. R dominates, which is all we need for RR timing."""
    y = np.zeros_like(t)
    for b in beats:
        y += 0.12 * np.exp(-0.5 * ((t - b + 0.16) / 0.025) ** 2)   # P
        y -= 0.18 * np.exp(-0.5 * ((t - b - 0.020) / 0.008) ** 2)  # Q
        y += 1.00 * np.exp(-0.5 * ((t - b) / 0.008) ** 2)          # R
        y -= 0.25 * np.exp(-0.5 * ((t - b + -0.030) / 0.010) ** 2) # S
        y += 0.28 * np.exp(-0.5 * ((t - b - 0.180) / 0.045) ** 2)  # T
    return y


def _gsr(t: np.ndarray, slope: float, rng: np.random.Generator) -> np.ndarray:
    """Tonic drift plus phasic SCR bursts (fast rise, slow exponential decay)."""
    tonic = 1800.0 + slope * t + 30.0 * np.sin(2 * np.pi * 0.01 * t)
    phasic = np.zeros_like(t)
    if slope > 5.0:                      # arousal states fire SCRs
        n = max(1, int(t[-1] / 12))
        for onset in rng.uniform(0, t[-1], n):
            m = t >= onset
            phasic[m] += 120.0 * (1 - np.exp(-(t[m] - onset) / 0.6)) * np.exp(-(t[m] - onset) / 4.0)
    return tonic + phasic + rng.normal(0, 4.0, t.size)


def generate(
    scenario: str,
    duration_s: float = 60.0,
    seed: int = 7,
    t0_ms: int = 0,
) -> list[Sample]:
    """Build one synthetic recording. Deterministic for a given seed."""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}, expected one of {SCENARIOS}")

    rng = np.random.default_rng(seed)
    n = int(duration_s * FS)
    t = np.arange(n) / FS
    hr, accel_rms, gsr_slope, label = _PROFILE[scenario]

    beats = _beat_times(hr, duration_s, rng)

    # --- motion -----------------------------------------------------------
    if scenario == "exercise":
        # ~2.5 Hz gait/limb cadence, phase-offset per axis
        step = 2 * np.pi * 2.5 * t
        ax = accel_rms * np.sin(step)
        ay = accel_rms * np.sin(step + 2.1)
        az = 1.0 + accel_rms * np.sin(step + 4.2)
    else:
        ax = rng.normal(0, accel_rms, n)
        ay = rng.normal(0, accel_rms, n)
        az = 1.0 + rng.normal(0, accel_rms, n)
    accel_mag = np.sqrt(ax**2 + ay**2 + az**2)

    # --- PPG --------------------------------------------------------------
    cardiac = _ppg_wave(t, beats)
    resp = 0.25 * np.sin(2 * np.pi * 0.23 * t)           # respiratory wander
    ac_frac = PPG_AC_FRAC * (0.06 if scenario == 'loose' else 1.0)
    ppg = PPG_DC * (1.0 + ac_frac * (cardiac + resp))
    ppg += rng.normal(0, PPG_DC * 0.0004, n)

    # Motion couples into PPG proportionally to how much the limb is moving.
    ppg += PPG_DC * 0.004 * (accel_mag - accel_mag.mean()) * rng.normal(1.0, 0.3, n)

    # --- ECG + lead-off ---------------------------------------------------
    ecg = _ecg_wave(t, beats)
    lead_off = np.zeros(n, dtype=int)

    if scenario == "corrupted":
        # Two failures at once, because that is what actually happens: the
        # strap slips (PPG destroyed) and an electrode peels (ECG rails).
        a, b = int(0.30 * n), int(0.65 * n)
        ppg[a:b] += PPG_DC * 0.05 * rng.normal(0, 1, b - a).cumsum() / np.sqrt(b - a)
        ppg[a:b] += PPG_DC * 0.02 * np.sin(2 * np.pi * 3.7 * t[a:b])
        c, d = int(0.50 * n), int(0.80 * n)
        lead_off[c:d] = 1
        ecg[c:d] = 0.0
        ecg[c:d] += rng.normal(0, 0.02, d - c)

    ecg_counts = np.clip(2048 + ecg * 900 + rng.normal(0, 12, n), 0, 4095)

    # --- GSR --------------------------------------------------------------
    gsr = np.clip(_gsr(t, gsr_slope, rng), 0, 4095)

    # --- label button: held down for the middle third of a labelled run ----
    btn = np.zeros(n, dtype=int)
    if label != "unknown":
        btn[int(n / 3): int(2 * n / 3)] = 1

    return [
        Sample(
            t_ms=int(t0_ms + i * 1000 / FS),
            ppg_ir=int(ppg[i]),
            ppg_red=int(ppg[i] * 0.72),      # red channel tracks IR, lower amplitude
            ax=float(ax[i]), ay=float(ay[i]), az=float(az[i]),
            gx=float(rng.normal(0, 2.0)), gy=float(rng.normal(0, 2.0)), gz=float(rng.normal(0, 2.0)),
            gsr_raw=int(gsr[i]),
            ecg_raw=int(ecg_counts[i]),
            lead_off=int(lead_off[i]),
            btn=int(btn[i]),
            label=label,
        )
        for i in range(n)
    ]


def true_hr(scenario: str) -> float:
    """The HR the generator actually used. Ground truth for testing detectors."""
    return _PROFILE[scenario][0]
