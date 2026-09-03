"""The behaviour channel, after the test became multiple choice.

Typing metrics were removed, not ported: nobody types at a four-option question,
so every one of them read zero forever -- live code measuring something that no
longer happens. What replaced them is strictly more than a rename, because
multiple choice separates two things a typed answer conflated: the time before
you touch anything, and the time you sit on a choice before committing it.
"""
from __future__ import annotations

import pytest

from vitalguard.behaviour import (METRICS, MIN_ANSWERS, BehaviourBaseline,
                                  BehaviourEvent, Channel, Event, parse,
                                  summarise)


def ev(t, e, d=""):
    return BehaviourEvent(t_ms=t, event=e, detail=d)


def question(t0, qid, first="A", final=None, commit_after=1000, latency=900,
             switch_after=None, timed_out=False):
    """One question's worth of events, in the shape game/index.html emits."""
    final = final or first
    out = [ev(t0, Event.QUESTION_SHOWN, qid), ev(t0 + latency, Event.KEYDOWN, first)]
    last = t0 + latency
    if switch_after is not None:
        last = t0 + latency + switch_after
        out += [ev(last, Event.KEYDOWN, final),
                ev(last, Event.ANSWER_CHANGED, f"{first}->{final}")]
    detail = f"{qid}:none:timeout" if timed_out else f"{qid}:{final}"
    out.append(ev(last + commit_after, Event.ANSWER_COMMITTED, detail))
    return out


# --- the two clocks of a decision -------------------------------------------

def test_decision_latency_is_question_to_first_touch():
    m = summarise(question(0, "q1", latency=1400)).metrics
    assert m["decision_latency_ms"] == 1400


def test_commit_latency_is_the_doubt_window_not_the_whole_answer():
    """Chosen, but not yet committed. A typed answer has no equivalent, which
    is why this metric could not exist before the game became multiple choice."""
    m = summarise(question(0, "q1", latency=800, commit_after=2500)).metrics
    assert m["commit_latency_ms"] == 2500
    assert m["decision_latency_ms"] == 800


def test_a_switch_moves_the_doubt_window_not_the_decision_window():
    """Changing your mind restarts the sitting-on-it clock; it does not
    retroactively change how long you took to first react."""
    m = summarise(question(0, "q1", first="A", final="C",
                           latency=900, switch_after=1500, commit_after=400)).metrics
    assert m["decision_latency_ms"] == 900
    assert m["commit_latency_ms"] == 400


# --- changing your mind ------------------------------------------------------

def test_first_choice_kept_counts_going_back_on_yourself():
    evs = (question(0, "q1", first="A", final="A")
           + question(9_000, "q2", first="B", final="D", switch_after=700))
    m = summarise(evs).metrics
    assert m["first_choice_kept"] == 0.5
    assert m["switches_per_q"] == 0.5


def test_rates_are_absent_below_the_evidence_floor():
    """A fraction over one question is not a measurement. Same refusal as
    baseline.py under MIN_COVERAGE_S: a number from too little evidence looks
    identical to a real one, which makes it worse than no number."""
    m = summarise(question(0, "q1")).metrics
    assert MIN_ANSWERS == 2
    assert "first_choice_kept" not in m
    assert "switches_per_q" not in m
    assert "decision_latency_ms" in m          # per-question metrics still fine


# --- stopping ----------------------------------------------------------------

def test_a_timed_out_question_has_no_commit_latency_to_report():
    """The clock committed it, not the person. Reporting a doubt window there
    would attribute the machine's action to them."""
    recs = parse(question(0, "q1", timed_out=True))
    assert recs[0].timed_out is True
    assert recs[0].committed_key is None
    assert recs[0].commit_latency_ms is None


def test_timeout_fraction_counts_questions_the_clock_answered():
    evs = question(0, "q1", timed_out=True) + question(9_000, "q2")
    assert summarise(evs).metrics["timeout_fraction"] == 0.5


def test_focus_losses_are_counted():
    evs = question(0, "q1") + [ev(5_000, Event.FOCUS_LOST), ev(6_000, Event.FOCUS_REGAINED)]
    assert summarise(evs).metrics["focus_losses"] == 1.0


# --- parsing is strict, never inventive --------------------------------------

def test_an_unparseable_commit_does_not_invent_an_answer():
    """We do not know what was chosen. Guessing a key would put a fabricated
    answer into a behaviour report."""
    evs = [ev(0, Event.QUESTION_SHOWN, "q1"), ev(500, Event.KEYDOWN, "A"),
           ev(900, Event.ANSWER_COMMITTED, "garbage")]
    assert parse(evs)[0].committed_key is None


def test_events_before_any_question_are_ignored_not_attached():
    evs = [ev(0, Event.KEYDOWN, "A")] + question(1_000, "q1")
    recs = parse(evs)
    assert len(recs) == 1
    assert recs[0].first_key == "A" and recs[0].first_touch_ms == 1_900


# --- one report, three channels ---------------------------------------------

def test_camera_metrics_ride_the_same_report():
    s = summarise(question(0, "q1"), fidget=0.03,
                  camera={"head_motion_px_s": 41.9, "turn_fraction": 0.53,
                          "face_absent_fraction": 0.04, "head_tilt_range_deg": 12.0})
    assert s.metrics["head_motion_px_s"] == 41.9
    assert Channel.CAMERA in s.channels_present()
    assert Channel.MOTION in s.channels_present()


def test_an_undeclared_camera_metric_cannot_arrive_unannounced():
    """A new camera metric must be added to METRICS deliberately, so nothing
    reaches the report without a phrasing that passed the feelings check."""
    s = summarise(question(0, "q1"), camera={"mystery_score": 9.9})
    assert "mystery_score" not in s.metrics


def test_summary_without_a_device_or_phone_is_still_valid():
    s = summarise(question(0, "q1"))
    assert s.channels_present() == {Channel.INPUT}
    assert "fidget" not in s.metrics


# --- the personal baseline (unchanged machinery, new metrics) ----------------

def practice(n=4, latency=900):
    evs = []
    for i in range(n):
        evs += question(i * 9_000, f"p{i}", latency=latency)
    return summarise(evs)


def test_uncalibrated_baseline_refuses_to_report_a_deviation():
    b = BehaviourBaseline()
    b.update(summarise(question(0, "p0")))
    assert b.calibrated is False
    assert b.deviation(summarise(question(0, "q1")), "decision_latency_ms") is None


def test_slower_decisions_under_load_read_as_a_positive_deviation():
    b = BehaviourBaseline()
    for i in range(4):
        b.update(summarise(question(i * 9_000, f"p{i}", latency=900 + i * 60)))
    assert b.calibrated
    d = b.deviation(summarise(question(0, "q1", latency=3_000)), "decision_latency_ms")
    assert d is not None and d.personal_sigma > 0
    assert d.channel is Channel.INPUT


def test_a_metric_missing_from_the_block_is_never_invented():
    b = BehaviourBaseline()
    for i in range(4):
        b.update(summarise(question(i * 9_000, f"p{i}"), fidget=0.02))
    assert b.deviation(summarise(question(0, "q1")), "fidget") is None


def test_report_is_ordered_by_size_of_change():
    b = BehaviourBaseline()
    for i in range(4):
        b.update(summarise(question(i * 9_000, f"p{i}", latency=900 + i * 40,
                                    commit_after=1000 + i * 40)))
    rep = b.report(summarise(question(0, "q1", latency=4_000, commit_after=1_100)))
    assert rep, "expected at least one deviation"
    assert abs(rep[0].personal_sigma) >= abs(rep[-1].personal_sigma)


# --- the standing guards -----------------------------------------------------

def test_module_exposes_no_way_to_feed_behaviour_into_the_scorer():
    """The test gets harder over time by construction, so behaviour drifts by
    construction. A model given both learns to score the clock while appearing
    to score the body."""
    import vitalguard.behaviour as b
    assert not hasattr(b, "FEATURE_NAMES")
    assert not hasattr(b, "features")
    assert not any("feature" in n.lower() for n in dir(b))


def test_every_metric_can_be_described_without_naming_a_feeling():
    banned = ("stress", "anxious", "anxiety", "nervous", "calm", "panic", "fear",
              "confident", "confidence", "emotion", "mood", "engaged", "bored",
              "attention", "attentive", "distract", "frustrat", "doubt")
    for name, (unit, phrasing) in METRICS.items():
        blob = f"{name} {unit} {phrasing}".lower()
        assert not [w for w in banned if w in blob], f"{name}: {phrasing}"


def test_no_typing_metric_survived_the_change_to_multiple_choice():
    """They read zero forever at a four-option question. If one comes back,
    the game went back to typed answers and this test should be updated then."""
    for gone in ("iki_cv", "backspace_rate", "first_key_latency_ms", "answer_changes"):
        assert gone not in METRICS


def test_a_perfectly_steady_practice_round_says_so_instead_of_claiming_precision():
    """The spread floor stops a uniform practice round producing 40-sigma
    events, but then the sigma is a lower bound, not a z-score. The report has
    to be able to tell the difference."""
    b = BehaviourBaseline()
    for i in range(4):
        b.update(summarise(question(i * 9_000, f"p{i}", latency=900)))   # identical
    assert b.spread_is_floored("decision_latency_ms") is True

    b2 = BehaviourBaseline()
    for i, lat in enumerate((600, 1500, 900, 2100)):                     # human-ish
        b2.update(summarise(question(i * 9_000, f"p{i}", latency=lat)))
    assert b2.spread_is_floored("decision_latency_ms") is False
