"""Extract features for every WESAD subject. Cached to a single .npz.

Each pickle is ~930 MB, so subjects are processed one at a time and deleted
immediately. Run once; everything downstream reads the cache.
"""
import subprocess, sys
from pathlib import Path
import numpy as np
from vitalguard import features, hr, wesad
from vitalguard.baseline import PersonalBaseline
from vitalguard.gate import WESAD_E4, Trust, assess
from vitalguard.replay import windows

ROOT = Path("data/wesad")
OUT = ROOT / "wesad_features.npz"
SUBJECTS = ["S2","S3","S4","S5","S6","S7","S8","S9","S10","S11","S13","S14","S15","S16","S17"]

X, y, subj = [], [], []
for i, sid in enumerate(SUBJECTS, 1):
    pkl = ROOT / f"WESAD/{sid}/{sid}.pkl"
    if not pkl.exists():
        subprocess.run(["unzip","-o","-q",str(ROOT/"WESAD.zip"),f"WESAD/{sid}/{sid}.pkl","-d",str(ROOT)], check=True)
    try:
        ws = list(windows(wesad.to_samples(wesad.load_subject(pkl)), window_s=10.0, hop_s=5.0))
    except Exception as exc:
        print(f"[{i}/15] {sid}: FAILED {exc}", flush=True); pkl.unlink(missing_ok=True); continue

    # Calibrate on this subject's OWN baseline block, exactly as the device would.
    pb = PersonalBaseline()
    for w in ws:
        if w.label == "rest":
            pb.update(w, assess(w, WESAD_E4), hr.estimate(w.cols["ppg_ir"]))
    if not pb.snapshot().calibrated:
        print(f"[{i}/15] {sid}: never calibrated, skipped", flush=True); pkl.unlink(); continue

    n = 0
    for w in ws:
        if w.label == "unknown":
            continue
        v = assess(w, WESAD_E4)
        e = hr.estimate(w.cols["ppg_ir"])
        d = pb.deviation(e, v)
        if d is None:            # failed the gate -- no features, by design
            continue
        X.append(features.extract(w, d, e, v.metrics["motion"], pb))
        y.append(w.label); subj.append(sid); n += 1
    print(f"[{i}/15] {sid}: {n} scored windows", flush=True)
    pkl.unlink()

np.savez_compressed(OUT, X=np.array(X), y=np.array(y), subject=np.array(subj),
                    names=np.array(features.FEATURE_NAMES))
print(f"\nsaved {OUT}  X={np.array(X).shape}  subjects={len(set(subj))}")
