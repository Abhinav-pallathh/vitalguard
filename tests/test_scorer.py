"""The severity scorer. The first two tests are the product's two promises."""
import pytest

from vitalguard import hr, synth
from vitalguard.baseline import Deviation, PersonalBaseline
from vitalguard.gate import SYNTHETIC, Trust, assess
from vitalguard.replay import windows
from vitalguard.scorer import (DEFAULT, Context, ScoreProfile, Score, Severity,
                               SustainedScorer, score)


def dev(sigma: float, baseline: float = 65.0) -> Deviation:
    return Deviation(baseline + sigma * 3.0, baseline, sigma * 3.0, sigma)


# --- the two promises -----------------------------------------------------

def test_exercise_never_raises_an_alarm():
    """Promise one: exertion is explained, not alarmed on.

    A device that alarms during exercise is a device its owner disables, and
    the first named user group is people with cardiac risk WHO EXERCISE.
    """
    for sigma in (2.5, 5.0, 10.0, 20.0):
        s = score(dev(sigma), motion=0.45, gsr_sigma=3.0)
        assert s.context is Context.EXERTION
        assert s.severity is Severity.NONE, f"alarmed during exercise at {sigma}sd"


def test_unexplained_elevation_is_the_only_thing_that_alerts():
    """Promise two: the alarm fires when nothing explains the elevation."""
    s = score(dev(5.0), motion=0.01, gsr_sigma=0.1)
    assert s.context is Context.UNEXPLAINED
    assert s.severity is Severity.ALERT


# --- ordering of the explanations ----------------------------------------

def test_motion_is_checked_before_arousal():
    """Exercise raises skin conductance too -- sweat. Testing arousal first
    would label every workout as stress, which is the false alarm that trains
    people to ignore the device."""
    s = score(dev(6.0), motion=0.45, gsr_sigma=5.0)
    assert s.context is Context.EXERTION


def test_arousal_is_distinguished_from_unexplained_by_gsr_alone():
    """Same heart rate, same stillness. Only skin conductance differs -- and it
    changes the verdict from 'stress' to 'alarm'. This is what the third sensor
    buys, and if it did not change the answer the GSR would be decoration."""
    a = score(dev(5.0), motion=0.02, gsr_sigma=2.6)
    b = score(dev(5.0), motion=0.02, gsr_sigma=0.1)
    assert a.context is Context.AROUSAL
    assert b.context is Context.UNEXPLAINED
    assert b.severity.value > a.severity.value


def test_missing_gsr_never_silently_becomes_calm():
    """An absent sensor must not read as 'no arousal'. Unknown is not zero."""
    s = score(dev(5.0), motion=0.02, gsr_sigma=None)
    assert s.context is Context.UNEXPLAINED
    assert "unavailable" in s.explanation


# --- thresholds are personal ---------------------------------------------

def test_the_same_bpm_means_different_things_for_different_people():
    """The premise of the whole product. +12 bpm is noise for a person whose
    resting rate wanders, and an event for a person who is metronomic."""
    wobbly = Deviation(hr_bpm=77, baseline_bpm=65, delta_bpm=12, personal_sigma=12 / 8.0)
    steady = Deviation(hr_bpm=77, baseline_bpm=65, delta_bpm=12, personal_sigma=12 / 2.0)
    assert score(wobbly, 0.01, 0.1).context is Context.NORMAL
    assert score(steady, 0.01, 0.1).context is Context.UNEXPLAINED


def test_below_threshold_is_normal_regardless_of_context():
    for motion, gsr in [(0.0, 0.0), (0.45, 5.0), (0.0, 5.0)]:
        assert score(dev(0.5), motion, gsr).context is Context.NORMAL


def test_severity_escalates_with_magnitude():
    mild = score(dev(2.5), 0.01, 0.1)
    big = score(dev(9.0), 0.01, 0.1)
    assert mild.severity is Severity.CONCERN
    assert big.severity is Severity.ALERT


# --- persistence ----------------------------------------------------------

def test_a_single_window_cannot_raise_a_full_alarm():
    """Devices that cry wolf do it by alarming on one sample."""
    ss = SustainedScorer()
    first = ss.push(score(dev(9.0), 0.01, 0.1))
    assert first.severity is Severity.NOTICE
    assert "confirming" in first.explanation


def test_a_sustained_event_does_escalate():
    ss = SustainedScorer()
    out = [ss.push(score(dev(9.0), 0.01, 0.1)) for _ in range(int(DEFAULT.sustain_s))]
    assert out[-1].severity is Severity.ALERT
    assert "confirming" not in out[-1].explanation


def test_context_is_never_suppressed_only_the_alarm_is():
    ss = SustainedScorer()
    s = ss.push(score(dev(9.0), 0.01, 0.1))
    assert s.context is Context.UNEXPLAINED, "the user always sees what we think"


def test_a_changing_context_restarts_confirmation():
    ss = SustainedScorer()
    ss.push(score(dev(9.0), 0.01, 0.1))
    ss.push(score(dev(9.0), 0.45, 0.1))          # became exertion
    s = ss.push(score(dev(9.0), 0.01, 0.1))      # back to unexplained
    assert s.severity is Severity.NOTICE


# --- end to end on synthetic data ----------------------------------------

def _run(scenario, pb):
    out = []
    for w in windows(synth.generate(scenario, duration_s=40.0, seed=11)):
        v = assess(w, SYNTHETIC)
        e = hr.estimate(w.cols["ppg_ir"])
        d = pb.deviation(e, v)
        if d is None:
            continue
        out.append(score(d, v.metrics["motion"], pb.gsr_deviation(w)))
    return out


@pytest.fixture(scope="module")
def calibrated():
    pb = PersonalBaseline()
    for w in windows(synth.generate("rest", duration_s=150.0, seed=7)):
        pb.update(w, assess(w, SYNTHETIC), hr.estimate(w.cols["ppg_ir"]))
    assert pb.snapshot().calibrated
    return pb


def test_end_to_end_exercise_is_exertion(calibrated):
    ctx = {s.context for s in _run("exercise", calibrated)}
    assert ctx == {Context.EXERTION}


def test_end_to_end_unexplained_alarms(calibrated):
    scores = _run("unexplained", calibrated)
    assert scores
    assert all(s.context is Context.UNEXPLAINED for s in scores)


def test_end_to_end_rest_never_alarms(calibrated):
    scores = _run("rest", calibrated)
    assert scores
    assert all(s.severity is Severity.NONE for s in scores)


@pytest.mark.parametrize("scenario", ["corrupted", "loose"])
def test_untrustworthy_signal_produces_no_score_at_all(calibrated, scenario):
    """A score cannot exist for a reading that failed quality -- the chain
    gate -> baseline -> scorer is enforced by type signatures."""
    for w in windows(synth.generate(scenario, duration_s=30.0, seed=11)):
        v = assess(w, SYNTHETIC)
        if v.ppg is Trust.UNSCORED:
            assert calibrated.deviation(hr.estimate(w.cols["ppg_ir"]), v) is None
