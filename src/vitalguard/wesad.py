"""Convert WESAD into our record schema, so the pipeline meets real humans.

Why this exists: the synthetic generator and the detectors were written by the
same person. Six scenarios were designed to be separable and then confirmed to
be separable, which tests that the code does what was intended -- not that the
approach works on a body. WESAD is 15 real people, real induced stress, labelled
by researchers who have never heard of this project. It breaks the circle.

    WESAD source rates          ->  our schema (100 Hz, schema.D1)
    wrist BVP        64 Hz      ->  ppg_ir / ppg_red
    wrist ACC        32 Hz      ->  ax, ay, az      (E4 reports in 1/64 g)
    wrist EDA         4 Hz      ->  gsr_raw
    chest ECG       700 Hz      ->  ecg_raw         (the ground-truth reference)
    label           700 Hz      ->  label           (decimated, NEVER filtered)

WHAT DOES NOT TRANSFER, and this matters:

  PERFUSION INDEX IS NOT PHYSICALLY MEANINGFUL ON WESAD DATA. Our gate computes
  perfusion as AC/DC on raw MAX30102 counts. The Empatica E4 does not expose a
  raw DC pedestal -- BVP arrives AC-coupled and centred near zero. To produce a
  well-formed record we add a plausible DC offset, which means any perfusion
  number computed from WESAD is measuring a constant WE invented. It is not
  evidence about anything.

  SSQI, motion level and estimator agreement DO transfer -- all three are
  computed on the bandpassed signal, where the DC pedestal is removed anyway.

  So: WESAD validates the shape of the gate, the heart-rate estimator, and the
  severity scorer. It cannot validate the perfusion threshold, and it cannot
  supply thresholds we ship (schema.D7 -- different sensor, different site,
  different noise; training on it and deploying to an ear clip is the UCI-HAR
  mistake and it fails silently).

  There is no exercise condition in WESAD. That half is ours to record.
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
from scipy import signal as sps

from .schema import SAMPLE_RATE_HZ, Sample, write_csv

FS = SAMPLE_RATE_HZ

CHEST_HZ, BVP_HZ, ACC_HZ, EDA_HZ = 700, 64, 32, 4

# WESAD's own label key. 0 is transient/undefined, 5-7 are "should be ignored"
# per the dataset documentation -- all become `unknown`, which in our schema
# explicitly does NOT mean rest.
WESAD_LABELS = {1: "rest", 2: "stress", 3: "amusement", 4: "meditation"}

# A plausible MAX30102 IR pedestal, so records are well-formed. See the module
# docstring: this number is INVENTED and perfusion computed from it is not
# evidence. Named loudly so nobody quotes a perfusion figure off WESAD.
FAKE_DC_PEDESTAL = 80_000.0
INVENTED_PPG_DC = True

ADC_MAX = 4095


def _resample(x: np.ndarray, src_hz: int) -> np.ndarray:
    """Rational resample to FS. Anti-aliased, which is why labels never use it."""
    x = np.asarray(x, dtype=float).ravel()
    if src_hz == FS:
        return x
    g = np.gcd(int(FS), int(src_hz))
    return sps.resample_poly(x, up=FS // g, down=src_hz // g)


def _to_counts(x: np.ndarray, lo_pct=0.5, hi_pct=99.5) -> np.ndarray:
    """Map a physical signal onto the 0-4095 ADC range our schema stores.

    Percentile-based rather than min/max so a single spike cannot compress the
    entire signal into three counts.
    """
    lo, hi = np.percentile(x, [lo_pct, hi_pct])
    if hi - lo < 1e-12:
        return np.full(x.size, ADC_MAX // 2, dtype=float)
    return np.clip((x - lo) / (hi - lo) * ADC_MAX, 0, ADC_MAX)


def load_subject(pkl_path: str | Path) -> dict:
    """WESAD pickles were written under Python 2 -- latin1 or it fails cryptically."""
    with open(pkl_path, "rb") as fh:
        return pickle.load(fh, encoding="latin1")


def to_samples(raw: dict, limit_s: float | None = None) -> list[Sample]:
    """One WESAD subject -> our records, at 100 Hz."""
    wrist, chest = raw["signal"]["wrist"], raw["signal"]["chest"]

    bvp = _resample(wrist["BVP"], BVP_HZ)
    acc = np.column_stack([_resample(wrist["ACC"][:, i], ACC_HZ) for i in range(3)])
    acc /= 64.0                                  # E4 reports acceleration in 1/64 g
    eda = _resample(wrist["EDA"], EDA_HZ)
    ecg = _resample(chest["ECG"], CHEST_HZ)

    # Labels are decimated by plain slicing. Running them through an
    # anti-aliasing filter would interpolate BETWEEN class ids and invent
    # states that never happened -- label 1.5 is not a thing.
    lab_ids = np.asarray(raw["label"]).ravel()[:: CHEST_HZ // FS]

    n = min(bvp.size, acc.shape[0], eda.size, ecg.size, lab_ids.size)
    if limit_s is not None:
        n = min(n, int(limit_s * FS))

    # BVP is AC-coupled around zero. Scale it to a realistic pulsatile amplitude
    # (~1.2% of DC, matching a real MAX30102) and sit it on the invented pedestal.
    b = bvp[:n]
    scale = np.percentile(np.abs(b - b.mean()), 99)
    pulsatile = (b - b.mean()) / (scale if scale > 1e-12 else 1.0)
    ppg = FAKE_DC_PEDESTAL * (1.0 + 0.012 * pulsatile)

    gsr_counts = _to_counts(eda[:n])
    ecg_counts = _to_counts(ecg[:n])

    return [
        Sample(
            t_ms=int(i * 1000 / FS),
            ppg_ir=int(ppg[i]),
            ppg_red=int(ppg[i] * 0.72),
            ax=float(acc[i, 0]), ay=float(acc[i, 1]), az=float(acc[i, 2]),
            gx=0.0, gy=0.0, gz=0.0,        # WESAD has no gyroscope
            gsr_raw=int(gsr_counts[i]),
            ecg_raw=int(ecg_counts[i]),
            lead_off=0,                    # chest ECG, no lead-off channel exposed
            btn=0,
            label=WESAD_LABELS.get(int(lab_ids[i]), "unknown"),
        )
        for i in range(n)
    ]


def convert(pkl_path: str | Path, out_csv: str | Path,
            limit_s: float | None = None) -> Path:
    samples = to_samples(load_subject(pkl_path), limit_s=limit_s)
    return write_csv(out_csv, samples)
