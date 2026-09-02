"""The Personal Baseline Model, and the rule that a number cannot bypass the gate."""
import numpy as np
import pytest

from vitalguard import hr, synth
from vitalguard.baseline import (MIN_COVERAGE_S, RESTING_MOTION_MAX,
                                 PersonalBaseline)
from vitalguard.gate import Trust, assess
from vitalguard.replay import windows


def feed(pb, scenario, dur=120.0, seed=7):
    used = 0
    for w in windows(synth.generate(scenario, duration_s=dur, seed=seed)):
        used += pb.update(w, assess(w), hr.estimate(w.cols["ppg_ir"]))
    return used


def one(scenario, dur=30.0, seed=11):
    ws = list(windows(synth.generate(scenario, duration_s=dur, seed=seed)))
    w = ws[len(ws) // 2]
    return w, assess(w), hr.estimate(w.cols["ppg_ir"])


# --- the structural rule --------------------------------------------------

def test_an_unscored_window_can_never_produce_a_deviation():
    """Regression. `deviation` once took a bare float and happily reported
    '217 bpm, +51.5sd' for a window the gate had marked UNSCORED. The verdict
    is now a required argument."""
    pb = PersonalBaseline()
    feed(pb, "rest")
    assert pb.snapshot().calibrated

    for scenario in ("corrupted", "loose"):
        for w in windows(synth.generate(scenario, duration_s=30.0, seed=11)):
            v, e = assess(w), hr.estimate(w.cols["ppg_ir"])
            if v.ppg is Trust.UNSCORED:
                assert pb.deviation(e, v) is None, f"{scenario}: number escaped the gate"


def test_deviation_cannot_be_called_without_a_verdict():
    pb = PersonalBaseline()
    with pytest.raises(TypeError):
        pb.deviation(hr.estimate(np.zeros(1000)))


# --- calibration ----------------------------------------------------------

def test_refuses_to_report_a_baseline_before_enough_coverage():
    pb = PersonalBaseline()
    feed(pb, "rest", dur=30.0)
    snap = pb.snapshot()
    assert not snap.calibrated and snap.resting_hr is None
    assert "calibrating" in str(snap)


def test_coverage_counts_unique_time_not_overlapping_windows():
    """51 windows from 60 s at a 1 s hop are ~6 s of independent evidence.
    Counting windows would let the model calibrate on almost nothing."""
    pb = PersonalBaseline()
    used = feed(pb, "rest", dur=60.0)
    assert used > 45, "most windows should qualify on a clean resting signal"
    assert pb.coverage_s <= 61.0, f"coverage {pb.coverage_s} inflated by overlap"


def test_learns_the_right_resting_rate():
    pb = PersonalBaseline()
    feed(pb, "rest")
    snap = pb.snapshot()
    assert abs(snap.resting_hr - synth.true_hr("rest")) < 2.5


def test_learns_a_plausible_personal_spread():
    """The spread is the point: personalising only the centre re-introduces a
    fixed threshold one level up."""
    pb = PersonalBaseline()
    feed(pb, "rest")
    spread = pb.snapshot().spread
    assert 1.5 <= spread < 3 * synth.HR_DRIFT_BPM, f"implausible spread {spread}"


# --- what may teach the model --------------------------------------------

def test_exercise_never_teaches_the_baseline():
    """Resting means resting. A baseline that learned from exercise would
    silently raise itself and stop alarming on the thing it exists to catch."""
    pb = PersonalBaseline()
    assert feed(pb, "exercise") == 0
    assert not pb.snapshot().calibrated


@pytest.mark.parametrize("scenario", ["corrupted", "loose"])
def test_untrusted_signal_never_teaches_the_baseline(scenario):
    pb = PersonalBaseline()
    assert feed(pb, scenario) == 0


def test_qualifying_requires_all_three_conditions():
    w, v, e = one("rest")
    assert PersonalBaseline.qualifies(w, v, e)
    assert v.ppg is Trust.TRUSTED
    assert e.agree
    assert v.metrics["motion"] <= RESTING_MOTION_MAX


# --- deviation ------------------------------------------------------------

def test_resting_reading_sits_near_zero_sigma():
    pb = PersonalBaseline()
    feed(pb, "rest")
    w, v, e = one("rest")
    d = pb.deviation(e, v)
    assert d is not None and abs(d.personal_sigma) < 3.0


def test_elevated_readings_are_flagged_as_far_from_normal():
    pb = PersonalBaseline()
    feed(pb, "rest")
    for scenario in ("stress", "unexplained"):
        w, v, e = one(scenario)
        d = pb.deviation(e, v)
        assert d is not None, scenario
        assert d.delta_bpm > 15, scenario
        assert d.personal_sigma > 3.0, scenario
