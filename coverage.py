"""Coverage -- the metric no consumer wearable reports.

    PYTHONPATH=src ./venv/bin/python coverage.py S2 S3 S4

Every always-on wearable answers "what fraction of the time did you show the
wearer a number you could stand behind?" with 100%, by construction: it always
shows something. The question is therefore never asked, and the accuracy figure
on the box has no denominator.

This asks it. Over real recordings, per window:

    TRUSTED   we showed a number
    DEGRADED  we showed a number and said it was lower confidence, and why
    UNSCORED  we showed NO number, and said why

`coverage = 1 - UNSCORED` is the honest denominator that belongs next to the
3.22 bpm MAE. It also makes the refusal falsifiable in the other direction: a
gate quietly tuned until it refuses everything would post a perfect error rate,
and would be caught here instantly.
"""
import subprocess
import sys
from collections import Counter
from pathlib import Path

from vitalguard import wesad
from vitalguard.gate import WESAD_E4, Trust, assess
from vitalguard.replay import windows

ROOT = Path("data/wesad")
DEFAULT = ["S2", "S3", "S4"]


def subject(sid: str):
    pkl = ROOT / f"WESAD/{sid}/{sid}.pkl"
    if not pkl.exists():
        subprocess.run(["unzip", "-o", "-q", str(ROOT / "WESAD.zip"),
                        f"WESAD/{sid}/{sid}.pkl", "-d", str(ROOT)], check=True)
    ws = windows(wesad.to_samples(wesad.load_subject(pkl)), window_s=10.0, hop_s=5.0)
    trust, why = Counter(), Counter()
    for w in ws:
        v = assess(w, WESAD_E4)
        trust[v.ppg] += 1
        if v.ppg is Trust.UNSCORED and v.reasons:
            why[v.reasons[0]] += 1
    return trust, why


def main() -> None:
    sids = sys.argv[1:] or DEFAULT
    total, all_why = Counter(), Counter()

    print(f"{'subject':<9} {'n':>6} {'trusted':>9} {'degraded':>9} {'refused':>9} {'coverage':>9}")
    print("-" * 58)
    for sid in sids:
        t, w = subject(sid)
        n = sum(t.values())
        if not n:
            print(f"{sid:<9} {'no windows':>6}")
            continue
        total.update(t)
        all_why.update(w)
        cov = 100.0 * (n - t[Trust.UNSCORED]) / n
        print(f"{sid:<9} {n:>6} {t[Trust.TRUSTED]:>9} {t[Trust.DEGRADED]:>9} "
              f"{t[Trust.UNSCORED]:>9} {cov:>8.1f}%")

    n = sum(total.values())
    if not n:
        return
    cov = 100.0 * (n - total[Trust.UNSCORED]) / n
    print("-" * 58)
    print(f"{'ALL':<9} {n:>6} {total[Trust.TRUSTED]:>9} {total[Trust.DEGRADED]:>9} "
          f"{total[Trust.UNSCORED]:>9} {cov:>8.1f}%")
    print(f"\nWe were willing to show a number {cov:.1f}% of the time, at 3.22 bpm MAE.")
    print("The other side of that trade, itemised:")
    for reason, k in all_why.most_common():
        print(f"  {100.0*k/n:5.1f}%  {reason}")
    print("\nProfile: WESAD_E4. Wrist PPG on an Empatica E4, not our ear clip --")
    print("this is the shape of the number, not the number we will ship.")


if __name__ == "__main__":
    main()
