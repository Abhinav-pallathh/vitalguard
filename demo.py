"""What VitalGuard would show on the device, end to end, with no hardware.

    PYTHONPATH=src ./venv/bin/python demo.py            # the full walkthrough
    PYTHONPATH=src ./venv/bin/python demo.py stress     # one scenario, live
    PYTHONPATH=src ./venv/bin/python demo.py --list

Runs the real pipeline -- the same gate, estimator and baseline model the test
suite covers -- over synthetic recordings. Swap `synth.generate(...)` for
`read_csv(...)` and this is the device.
"""
import sys
import time

from vitalguard import hr, synth
from vitalguard.baseline import MIN_COVERAGE_S, PersonalBaseline
from vitalguard.gate import Trust, assess
from vitalguard.replay import windows
from vitalguard.scorer import Context, Severity, SustainedScorer, score

BAR = "=" * 68
MARK = {Trust.TRUSTED: "[ OK ]", Trust.DEGRADED: "[ ~~ ]", Trust.UNSCORED: "[ !! ]"}


def calibrate(pb, seconds=120.0, live=False):
    print(f"{BAR}\nCALIBRATION -- sit still, {MIN_COVERAGE_S:.0f}s of clean resting signal needed\n{BAR}")
    for w in windows(synth.generate("rest", duration_s=seconds, seed=7)):
        used = pb.update(w, assess(w), hr.estimate(w.cols["ppg_ir"]))
        snap = pb.snapshot()
        if w.index % 10 == 0 or (snap.calibrated and w.index % 10 == 0):
            state = "learning" if not snap.calibrated else "CALIBRATED"
            print(f"  t={w.t_end_ms/1000:6.1f}s  used={'y' if used else 'n'}  {state:<11} {snap}")
            if live:
                time.sleep(0.05)
    print(f"\n  -> personal baseline: {pb.snapshot()}")
    print("     (spread is THIS person's normal variability -- not a fixed rule)\n")


def show(pb, scenario, seconds=25.0, live=False):
    print(f"{BAR}\nSCENARIO: {scenario.upper()}\n{BAR}")
    print(f"  {'time':>6}  {'':6}  {'reading':<26} {'verdict':<12} {'':8} why")
    print(f"  {'-'*6}  {'-'*6}  {'-'*26} {'-'*12} {'-'*8} {'-'*34}")
    ss = SustainedScorer()
    for w in windows(synth.generate(scenario, duration_s=seconds, seed=11)):
        if w.index % 4:
            continue
        v = assess(w)
        e = hr.estimate(w.cols["ppg_ir"])
        d = pb.deviation(e, v)

        if d is None:
            reading = "  -- no reading --" if not v.scored else "  -- calibrating --"
            ctx, sev, why = "", "", (v.reasons[0] if v.reasons else "")
        else:
            reading = f"{d.hr_bpm:5.0f} bpm  {d.delta_bpm:+5.0f}  {d.personal_sigma:+5.1f}sd"
            s = ss.push(score(d, v.metrics["motion"], pb.gsr_deviation(w)))
            ctx, sev, why = s.context.value.upper(), s.severity.name, s.explanation

        print(f"  {w.t_end_ms/1000:5.1f}s  {MARK[v.ppg]}  {reading:<26} {ctx:<12} {sev:<8} {why}")
        if live:
            time.sleep(0.12)
    print()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--list" in sys.argv:
        print("scenarios:", ", ".join(synth.SCENARIOS))
        return

    pb = PersonalBaseline()
    calibrate(pb, live=bool(args))

    for scenario in (args or list(synth.SCENARIOS)):
        if scenario not in synth.SCENARIOS:
            print(f"unknown scenario {scenario!r}; try --list")
            return
        show(pb, scenario, live=bool(args))

    print(BAR)
    print("Every number above passed the quality gate before it reached the")
    print("baseline, and the baseline before it reached the scorer. The rows with")
    print("no reading are the product working, not the product failing.")
    print(BAR)


if __name__ == "__main__":
    main()
