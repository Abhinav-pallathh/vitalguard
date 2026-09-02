"""The record format. ONE source of truth.

Firmware writes it, the quality gate reads it, the baseline model reads it, the
scorer reads it, the dashboard reads it, and WESAD gets converted INTO it. If
this file and the firmware ever disagree, everything downstream is quietly
wrong, so the field list lives here and nowhere else.

Design decisions that are load-bearing (see docs/DECISIONS.md for the why):

  D1. ONE sample rate for every channel: 100 Hz, one row per sample instant.
      Real sensors want different rates (ECG ~250Hz, GSR ~4Hz) but a single
      rate means no resampling, no interpolation, and no alignment bugs -- and
      alignment bugs are silent. 100 Hz is enough for R-peak *timing* (not
      morphology) and oversamples GSR harmlessly.

  D2. RAW values only. No heart rate, no filtering, no smoothing on-device.
      The firmware is a dumb recorder. Every derived number is computed in
      Python where it is testable and reproducible from the same input.

  D3. `lead_off` is a HARDWARE honesty signal and it is free. The AD8232
      exposes LO+ / LO- digital pins that go high when an electrode detaches.
      That is ground truth about signal validity that costs us nothing, and it
      is the only quality input in this schema that is not inferred.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, fields, astuple
from pathlib import Path

import numpy as np

SAMPLE_RATE_HZ = 100
"""D1. Every channel is logged at this rate. Firmware and analysis both assume it."""

# Label vocabulary. `unknown` is the default and is NOT a training class -- it
# means nobody pressed the button, not that the subject was at rest.
LABELS = ("unknown", "rest", "exercise", "stress")


@dataclass(slots=True)
class Sample:
    """One 10ms slice of the world, exactly as the device saw it."""

    t_ms: int          # device millis(), monotonic. THE clock for alignment.
    ppg_ir: int        # MAX30102 IR channel, raw ADC counts
    ppg_red: int       # MAX30102 red channel, raw ADC counts
    ax: float          # accel g
    ay: float
    az: float
    gx: float          # gyro deg/s
    gy: float
    gz: float
    gsr_raw: int       # ESP32 ADC1 counts 0-4095, GPIO 34
    ecg_raw: int       # ESP32 ADC1 counts 0-4095, GPIO 35
    lead_off: int      # 0 = electrodes attached, 1 = detached (LO+ | LO-)
    btn: int           # 0/1 momentary label button
    label: str         # one of LABELS, applied post-hoc or by button

    def __post_init__(self) -> None:
        if self.label not in LABELS:
            raise ValueError(f"label {self.label!r} not in {LABELS}")


FIELDS: tuple[str, ...] = tuple(f.name for f in fields(Sample))
"""Canonical column order. The firmware MUST emit this order."""

CSV_HEADER = ",".join(FIELDS)


def write_csv(path: str | Path, samples: list[Sample]) -> Path:
    path = Path(path)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDS)
        w.writerows(astuple(s) for s in samples)
    return path


def read_csv(path: str | Path) -> list[Sample]:
    """Read a recording. Fails loudly on a column mismatch rather than
    silently shifting every channel by one -- which is exactly what a bare
    positional read would do if the firmware added a field."""
    path = Path(path)
    with path.open(newline="") as fh:
        r = csv.reader(fh)
        header = next(r)
        if tuple(header) != FIELDS:
            missing = set(FIELDS) - set(header)
            extra = set(header) - set(FIELDS)
            raise ValueError(
                f"{path} header does not match schema.\n"
                f"  missing: {sorted(missing) or 'none'}\n"
                f"  extra:   {sorted(extra) or 'none'}\n"
                f"  expected: {CSV_HEADER}"
            )
        types = {f.name: f.type for f in fields(Sample)}
        out = []
        for row in r:
            kw = {}
            for name, val in zip(FIELDS, row):
                t = types[name]
                kw[name] = int(val) if t == "int" else float(val) if t == "float" else val
            out.append(Sample(**kw))
    return out


def to_arrays(samples: list[Sample]) -> dict[str, np.ndarray]:
    """Columnar view. Everything downstream works on arrays, not objects."""
    cols: dict[str, np.ndarray] = {}
    for name in FIELDS:
        vals = [getattr(s, name) for s in samples]
        cols[name] = np.array(vals, dtype=object if name == "label" else None)
    return cols


def accel_magnitude(cols: dict[str, np.ndarray]) -> np.ndarray:
    """Vector magnitude of acceleration, in g.

    Used by the quality gate (motion corrupts PPG) AND by the severity scorer
    (motion explains an elevated heart rate). Same number, two very different
    jobs -- which is the whole idea behind the arousal-context layer.
    """
    return np.sqrt(cols["ax"] ** 2 + cols["ay"] ** 2 + cols["az"] ** 2)
