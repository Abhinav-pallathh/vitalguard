"""The behaviour channel. Tests are grouped by the design decision they defend."""
from __future__ import annotations

import pytest

from vitalguard.behaviour import (
    METRICS, MIN_KEYSTROKES, BehaviourBaseline, BehaviourEvent, Channel,
    Event, summarise,
)


def keystrokes(t0: int, n: int, gap: int) -> list[BehaviourEvent]:
    return [BehaviourEvent(t0 + i * gap, Event.KEYDOWN) for i in range(n)]


def question(t0: int, n_keys: int = 20, gap: int = 200, think: int = 500):
    """One answered question: shown, thought about, typed, committed."""
    ev = [BehaviourEvent(t0, Event.QUESTION_SHOWN)]
    ev += keystrokes(t0 + think, n_keys, gap)
    ev.append(BehaviourEvent(t0 + think + n_keys * gap, Event.ANSWER_COMMITTED))
    return ev


# --- summarise -----------------------------------------------------------

def test_hesitation_is_measured_from_question_to_first_key():
    s = summarise(question(1000, think=750))
    assert s.metrics["first_key_latency_ms"] == pytest.approx(750)


def test_steady_typing_has_near_zero_rhythm_irregularity():
    s = summarise(question(0, n_keys=30, gap=200))
    assert s.metrics["iki_cv"] < 0.01


def test_erratic_typing_has_higher_irregularity_than_steady():
    steady = summarise(question(0, n_keys=30, gap=200))
    ev = [BehaviourEvent(0, Event.QUESTION_SHOWN)]
    t = 500
    for i in range(30):                      # alternating fast/slow keys
        ev.append(BehaviourEvent(t, Event.KEYDOWN))
        t += 80 if i % 2 else 600
    erratic = summarise(ev)
    assert erratic.metrics["iki_cv"] > steady.metrics["iki_cv"]


def test_think_time_between_questions_is_not_counted_as_typing_rhythm():
    """A 9s gap because the next question appeared is not a slow keystroke.

    Without the question-boundary filter this dominates every rhythm metric,
    and the channel would be measuring the test's pacing, not the person.
    """
    ev = question(0, n_keys=20, gap=200) + question(13_000, n_keys=20, gap=200)
    s = summarise(ev)
    assert s.metrics["idle_max_ms"] < 1000


def test_typing_metrics_are_absent_below_the_evidence_floor():
    s = summarise(question(0, n_keys=MIN_KEYSTROKES - 1))
    assert "iki_cv" not in s.metrics
    assert "backspace_rate" not in s.metrics


def test_counts_are_reported_even_with_no_typing():
    s = summarise([BehaviourEvent(0, Event.FOCUS_LOST),
                   BehaviourEvent(50, Event.FOCUS_LOST)])
    assert s.metrics["focus_losses"] == 2


def test_fidget_is_tagged_as_a_motion_channel_metric():
    s = summarise(question(0), fidget=0.04)
    assert Channel.MOTION in s.channels_present()


def test_summary_without_a_device_is_still_valid():
    s = summarise(question(0))
    assert Channel.MOTION not in s.channels_present()
    assert s.metrics                       # input-only session still measures


# --- B3: no practice round means no reference ----------------------------

def test_uncalibrated_baseline_refuses_to_report_a_deviation():
    b = BehaviourBaseline()
    b.update(summarise(question(0)))       # one practice answer, not enough
    assert b.deviation(summarise(question(9000)), "iki_cv") is None


def test_deviation_is_reported_once_the_practice_round_is_long_enough():
    b = BehaviourBaseline()
    for i in range(4):
        b.update(summarise(question(i * 10_000, gap=200)))
    assert b.calibrated
    d = b.deviation(summarise(question(99_000, gap=200)), "first_key_latency_ms")
    assert d is not None


def test_slower_hesitation_under_load_reads_as_a_positive_deviation():
    b = BehaviourBaseline()
    for i in range(4):
        b.update(summarise(question(i * 10_000, think=500)))
    d = b.deviation(summarise(question(99_000, think=2500)), "first_key_latency_ms")
    assert d.personal_sigma > 1.0


def test_a_metric_missing_from_the_block_is_never_invented():
    b = BehaviourBaseline()
    for i in range(4):
        b.update(summarise(question(i * 10_000), fidget=0.02))
    assert b.deviation(summarise(question(99_000)), "fidget") is None


def test_a_perfectly_steady_practice_round_does_not_inflate_every_deviation():
    """The spread floor. Identical practice answers give MAD 0, and without a
    floor the next observation becomes a division by zero or a 40-sigma event.
    """
    b = BehaviourBaseline()
    for i in range(5):
        b.update(summarise(question(i * 10_000, think=500)))
    d = b.deviation(summarise(question(99_000, think=520)), "first_key_latency_ms")
    assert abs(d.personal_sigma) < 3.0


def test_report_is_ordered_by_size_of_change():
    b = BehaviourBaseline()
    for i in range(4):
        b.update(summarise(question(i * 10_000, think=500, gap=200)))
    rows = b.report(summarise(question(99_000, think=4000, gap=200)))
    assert rows
    assert abs(rows[0].personal_sigma) >= abs(rows[-1].personal_sigma)


# --- B1: the separation that makes the result meaningful -----------------

def test_module_exposes_no_way_to_feed_behaviour_into_the_scorer():
    """B1 is enforced by omission. If someone adds a feature-vector export
    here, this test fails and they have to read the docstring explaining why
    the test-difficulty clock would leak into the physiology model.
    """
    import vitalguard.behaviour as mod
    banned = {"to_features", "as_features", "feature_vector", "FEATURE_NAMES"}
    assert not banned & set(dir(mod))


def test_every_metric_can_be_described_without_naming_a_feeling():
    """The honesty check from the module docstring, kept executable."""
    feelings = {"stress", "anxious", "anxiety", "calm", "emotion", "mood",
                "nervous", "fear", "happy", "sad", "angry", "frustrated"}
    for name, (unit, what) in METRICS.items():
        assert not feelings & set(what.lower().split()), name
        assert unit and what
