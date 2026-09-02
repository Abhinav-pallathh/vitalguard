"""Heart rate from a PPG window. Two independent estimators, on purpose.

A single estimator gives you a number and no way to know if it is nonsense.
Two estimators that work on completely different principles give you a number
AND a consistency check -- and when they disagree, that disagreement is itself
evidence the window is bad, independent of anything the quality gate measured.

    spectral  -- dominant frequency in the cardiac band. Robust to a few
                 missed or spurious beats; blind to which beat went where.
    peaks     -- count actual systolic upstrokes. Gives beat-to-beat RR
                 intervals (so, HRV later); fooled by artifact that looks
                 peak-shaped.

They fail differently. That is the entire point of running both.

Nothing here decides anything. `estimate` returns an Estimate with `hr_bpm`
possibly None; the gate and the scorer decide what that means.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal as sps

from .quality import PPG_BAND, bandpass
from .schema import SAMPLE_RATE_HZ

FS = SAMPLE_RATE_HZ

HR_MIN_BPM = 30.0
HR_MAX_BPM = 220.0

# Zero-pad the FFT so the spectral peak can be located finely. A bare 10 s
# window gives 0.1 Hz resolution -- 6 bpm -- which is far too coarse to detect
# a meaningful deviation from a personal baseline. Padding to 8192 gives
# ~0.73 bpm.
NFFT = 8192

# Above this the two estimators are telling different stories and neither
# should be trusted. Chosen to be wider than normal RR jitter (~4%, a couple of
# bpm) and narrower than a halving/doubling error (~30+ bpm).
AGREEMENT_TOL_BPM = 10.0


@dataclass(slots=True)
class Estimate:
    hr_bpm: float | None          # None when no defensible estimate exists
    spectral_bpm: float | None
    peak_bpm: float | None
    n_beats: int
    agree: bool
    rr_ms: np.ndarray             # beat-to-beat intervals, for HRV later

    @property
    def usable(self) -> bool:
        return self.hr_bpm is not None


def _is_flat(raw: np.ndarray) -> bool:
    """True if the raw channel carries no signal at all.

    Checked on the RAW input, BEFORE filtering, and relative to the channel's
    own scale. An absolute post-filter threshold does not work: `filtfilt` on a
    constant 80,000-count signal leaves a tiny edge transient, the spectral
    estimator finds a "peak" in that numerical noise, and a dead sensor reports
    a confident 31 bpm. Caught by a test; it is the exact failure this product
    claims never to make.
    """
    x = np.asarray(raw, dtype=float)
    scale = max(abs(float(np.mean(x))), 1.0)
    return float(np.std(x)) < 1e-6 * scale


def _spectral_bpm(ac: np.ndarray) -> float | None:
    """Dominant frequency in the cardiac band, in bpm."""
    if ac.size < FS * 2 or ac.std() < 1e-9:
        return None
    win = ac * np.hanning(ac.size)
    spec = np.abs(np.fft.rfft(win, n=NFFT))
    freqs = np.fft.rfftfreq(NFFT, d=1.0 / FS)
    band = (freqs >= HR_MIN_BPM / 60.0) & (freqs <= HR_MAX_BPM / 60.0)
    if not band.any():
        return None
    idx = np.argmax(spec[band])
    return float(freqs[band][idx] * 60.0)


def _peak_bpm(ac: np.ndarray) -> tuple[float | None, int, np.ndarray]:
    """Count systolic upstrokes; return bpm, beat count, and RR intervals in ms.

    `distance` enforces a physiological refractory period so one broad
    artifact cannot be counted as several beats.
    """
    if ac.size < FS * 2 or ac.std() < 1e-9:
        return None, 0, np.array([])
    min_gap = int(FS * 60.0 / HR_MAX_BPM)
    peaks, _ = sps.find_peaks(ac, distance=min_gap, prominence=0.5 * ac.std())
    if peaks.size < 2:
        return None, int(peaks.size), np.array([])
    rr_ms = np.diff(peaks) * (1000.0 / FS)
    # Median RR, not mean: one missed beat doubles a single interval, and a
    # mean would drag the whole estimate with it. A median shrugs it off.
    return float(60_000.0 / np.median(rr_ms)), int(peaks.size), rr_ms


def estimate(ppg_raw: np.ndarray) -> Estimate:
    """Estimate heart rate from one window of raw PPG."""
    raw = np.asarray(ppg_raw, dtype=float)
    if raw.size < FS * 2 or _is_flat(raw):
        return Estimate(None, None, None, 0, False, np.array([]))

    ac = bandpass(raw, *PPG_BAND)

    spectral = _spectral_bpm(ac)
    peak, n_beats, rr_ms = _peak_bpm(ac)

    if spectral is None and peak is None:
        return Estimate(None, None, None, n_beats, False, rr_ms)

    if spectral is not None and peak is not None:
        agree = abs(spectral - peak) <= AGREEMENT_TOL_BPM
        # Average the two when they agree -- neither is privileged, and the
        # mean of two independent estimates beats either alone.
        hr = (spectral + peak) / 2.0 if agree else None
        return Estimate(hr, spectral, peak, n_beats, agree, rr_ms)

    # Exactly one estimator produced a number. Report it, but never claim
    # agreement we did not verify.
    only = spectral if spectral is not None else peak
    return Estimate(only, spectral, peak, n_beats, False, rr_ms)
