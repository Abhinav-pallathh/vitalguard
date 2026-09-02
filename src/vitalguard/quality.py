"""Signal quality metrics. Pure functions over one window. No decisions here.

Split deliberately from `gate.py`: this file MEASURES, the gate JUDGES. Keeping
them apart means we can re-fit every threshold without touching a line of
measurement code, and it means the measurements are testable independently of
whatever thresholds we happen to believe today.

The primary index is skewness (SSQI), which the PPG signal-quality literature
identifies as the optimal single index of the eight commonly tested (perfusion,
kurtosis, skewness, relative power, non-stationarity, zero-crossing, entropy,
systolic-wave matching), and which is explicitly characterised as low-cost
enough for real-time use on a wearable.

The intuition: a clean PPG is quasi-periodic with sharp systolic upstrokes and
long diastolic decays, which makes its amplitude distribution positively
skewed. Motion artifact destroys that asymmetry before it destroys the
amplitude -- so skewness degrades *earlier* than anything you would notice by
eye, which is exactly what we want from an early warning.
"""
from __future__ import annotations

import numpy as np
from scipy import signal as sps
from scipy import stats

from .schema import SAMPLE_RATE_HZ

FS = SAMPLE_RATE_HZ

# Heart rate 30-240 bpm is 0.5-4 Hz; keep headroom for the dicrotic notch.
PPG_BAND = (0.5, 8.0)
# Standard diagnostic-ish ECG band. We only need R-peak timing, not morphology.
ECG_BAND = (0.5, 40.0)

# Two different converters live in one record and they do NOT share a range.
# Conflating them made every PPG sample look saturated -- caught by running the
# gate, not by reading the code.
ESP32_ADC_MAX = 4095       # GSR (GPIO 34) and ECG (GPIO 35): 12-bit ESP32 ADC
MAX30102_MAX = 262_143     # PPG: the sensor's own 18-bit I2C counts


def bandpass(x: np.ndarray, lo: float, hi: float, order: int = 3) -> np.ndarray:
    """Zero-phase Butterworth bandpass.

    `filtfilt`, not `lfilter`: we are analysing a stored window, so we can
    afford non-causal filtering, and it avoids the phase delay that would shift
    every peak timing estimate by a constant we would then have to correct for.
    """
    x = np.asarray(x, dtype=float)
    nyq = FS / 2.0
    b, a = sps.butter(order, [lo / nyq, min(hi / nyq, 0.99)], btype="band")
    return sps.filtfilt(b, a, x)


def ssqi(ppg_raw: np.ndarray) -> float:
    """Skewness signal quality index. Higher is cleaner.

    Computed on the bandpassed signal so the large DC pedestal and respiratory
    wander do not dominate the third moment.
    """
    ac = bandpass(ppg_raw, *PPG_BAND)
    if ac.std() < 1e-9:
        return 0.0
    return float(stats.skew(ac))


def perfusion_index(ppg_raw: np.ndarray) -> float:
    """AC/DC ratio as a percentage. Low PI means the sensor is not coupled.

    This is the metric that catches an ear clip hanging off, which skewness
    alone can miss -- a weak signal can still be beautifully shaped.
    """
    x = np.asarray(ppg_raw, dtype=float)
    dc = float(np.mean(x))
    if abs(dc) < 1e-9:
        return 0.0
    ac = bandpass(x, *PPG_BAND)
    return float(100.0 * (np.percentile(ac, 95) - np.percentile(ac, 5)) / abs(dc))


def motion_level(accel_mag: np.ndarray) -> float:
    """Standard deviation of |acceleration|, in g.

    Deliberately the *variability*, not the mean: gravity contributes a
    constant ~1 g that says nothing about whether the wearer is moving.
    """
    return float(np.std(np.asarray(accel_mag, dtype=float)))


def rail_fraction(raw: np.ndarray, adc_max: int) -> float:
    """Fraction of samples stuck at a converter rail (0 or full scale).

    `adc_max` is REQUIRED, with no default, on purpose: the record carries two
    converters with different ranges (ESP32 12-bit for GSR/ECG, MAX30102 18-bit
    for PPG) and a default would silently pick the wrong one. It did exactly
    that in the first version -- every PPG sample read as saturated.

    A railed channel is a dead or saturated sensor. It is not noise, and it must
    never be filtered into looking like a signal.
    """
    x = np.asarray(raw)
    return float(np.mean((x <= 0) | (x >= adc_max)))


def flatline(raw: np.ndarray, eps: float = 1e-6) -> bool:
    """True if the channel never changes -- a disconnected or frozen sensor."""
    return bool(np.std(np.asarray(raw, dtype=float)) < eps)


def ecg_band_fraction(ecg_raw: np.ndarray) -> float:
    """Fraction of ECG power falling in the QRS band (5-40 Hz). Range 0-1.

    An earlier version of this returned a raw in-band/out-of-band RATIO, and
    calibration caught it measuring the wrong thing: QRS energy scales with the
    number of beats in the window, so a resting subject scored 20 and an
    exercising subject scored 400 -- the metric was reading heart rate, not
    quality. Normalising by TOTAL power removes the beat-count dependence.

    Not a true SNR: we have no clean reference. Named for what it actually is.
    """
    x = np.asarray(ecg_raw, dtype=float)
    if x.std() < 1e-9:
        return 0.0
    f, pxx = sps.welch(x - x.mean(), fs=FS, nperseg=min(256, x.size))
    total = pxx.sum()
    if total < 1e-12:
        return 0.0
    return float(pxx[(f >= 5.0) & (f <= 40.0)].sum() / total)
