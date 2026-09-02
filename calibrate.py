"""Measure every quality metric across every scenario.

Thresholds get read off THIS, not invented. Run it again on real recordings
before quoting any number.
"""
import numpy as np
from vitalguard import quality, synth
from vitalguard.replay import windows

rows = []
for sc in synth.SCENARIOS:
    for w in windows(synth.generate(sc, duration_s=60.0, seed=7)):
        rows.append((sc,
                     quality.ssqi(w.cols["ppg_ir"]),
                     quality.perfusion_index(w.cols["ppg_ir"]),
                     quality.motion_level(w.accel_mag),
                     quality.ecg_band_fraction(w.cols["ecg_raw"]),
                     float(w.any_lead_off)))

names = ["SSQI", "perfusion%", "motion σ(g)", "ecg_band", "lead_off"]
print(f"{'scenario':<13}" + "".join(f"{n:>26}" for n in names))
print(f"{'':13}" + "".join(f"{'min / median / max':>26}" for _ in names))
print("-" * (13 + 26 * len(names)))
for sc in synth.SCENARIOS:
    vals = [r for r in rows if r[0] == sc]
    line = f"{sc:<13}"
    for i in range(1, 6):
        col = np.array([v[i] for v in vals])
        line += f"{col.min():>8.2f} /{np.median(col):>7.2f} /{col.max():>7.2f}"
    print(line)
