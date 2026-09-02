"""The gate's contract. The first test is the product thesis; the rest support it."""
import numpy as np
import pytest

from vitalguard import quality, synth
from vitalguard.gate import Trust, Verdict, assess
from vitalguard.replay import windows

BAD = ("corrupted", "loose")
CLEAN = ("rest", "stress", "unexplained")


def verdicts(scenario, dur=60.0, seed=7):
    return [assess(w) for w in windows(synth.generate(scenario, duration_s=dur, seed=seed))]


# --- THE headline claim ---------------------------------------------------

@pytest.mark.parametrize("seed", [7, 11, 23])
def test_no_untrustworthy_window_is_ever_marked_trusted(seed):
    """A false CONFIRM is the only unrecoverable error this device can make.

    Marking a corrupted reading TRUSTED is worse than refusing to read at all:
    the wearer acts on a number that means nothing. Missing a window is
    recoverable -- another arrives one second later.
    """
    for scenario in BAD:
        for v in verdicts(scenario, seed=seed):
            assert v.ppg is not Trust.TRUSTED, (
                f"{scenario} seed={seed}: bad signal marked TRUSTED -- "
                f"metrics={v.metrics}"
            )


def test_false_confirm_rate_with_its_denominator():
    """Report the number the way Residual Zero taught us: with a denominator.

    Never '0.00%'. Zero events in N trials is an upper bound, not a point
    estimate -- one-sided rule of three, 3/N at 95% confidence.
    """
    bad = [v for s in BAD for seed in (7, 11, 23) for v in verdicts(s, seed=seed)]
    confirms = sum(v.ppg is Trust.TRUSTED for v in bad)
    n = len(bad)
    assert confirms == 0
    assert n >= 300, "denominator too small to claim anything"
    bound = 3.0 / n
    assert bound < 0.01, f"0 in {n}, below {bound:.2%} at 95% confidence"


# --- per-scenario behaviour ----------------------------------------------

def test_clean_resting_signal_is_trusted():
    assert all(v.ppg is Trust.TRUSTED for v in verdicts("rest"))


def test_loose_clip_is_unscored_and_says_what_to_do():
    vs = verdicts("loose")
    assert all(v.ppg is Trust.UNSCORED for v in vs)
    assert all(not v.scored for v in vs)
    assert any("contact" in r or "reseat" in r for r in vs[0].reasons)


def test_exercise_is_degraded_not_refused():
    """The product exists for people with cardiac risk WHO EXERCISE.

    Refusing to score during motion would make it useless to them. Exercise
    genuinely degrades PPG; the honest answer is to say so, not go silent.
    """
    vs = verdicts("exercise")
    assert all(v.ppg is Trust.DEGRADED for v in vs)
    assert all(v.scored for v in vs), "degraded still produces a number"
    assert any("motion" in r for r in vs[0].reasons)


def test_the_alarm_case_stays_scoreable():
    """`unexplained` is the whole point. If the gate refuses it, we cannot alarm."""
    vs = verdicts("unexplained")
    assert all(v.ppg is Trust.TRUSTED for v in vs)
    assert all(v.scored for v in vs)


def test_stress_is_not_mistaken_for_corruption():
    """Stress is low-motion and clean. Only GSR distinguishes it from normal --
    so the gate must NOT reject it, or the arousal layer never runs."""
    assert all(v.ppg is Trust.TRUSTED for v in verdicts("stress"))


def test_lead_off_makes_ecg_unscored_but_leaves_ppg_alone():
    """Channels fail independently. A peeled electrode says nothing about the
    ear clip, and must not take the PPG down with it."""
    vs = [v for v in verdicts("corrupted") if "ECG electrode detached" in v.reasons]
    assert vs, "the corrupted scenario should contain lead-off windows"
    assert all(v.ecg is Trust.UNSCORED for v in vs)


# --- regressions ----------------------------------------------------------

def test_rail_fraction_requires_an_explicit_range():
    """Regression: a default adc_max silently applied the ESP32's 12-bit range
    to the MAX30102's 18-bit counts, so every PPG sample read as saturated and
    every window came back UNSCORED. The parameter is now required."""
    with pytest.raises(TypeError):
        quality.rail_fraction(np.array([1, 2, 3]))


def test_ppg_and_ecg_use_their_own_converter_ranges():
    ppg = np.full(1000, 80_000.0)
    assert quality.rail_fraction(ppg, quality.MAX30102_MAX) == 0.0
    assert quality.rail_fraction(ppg, quality.ESP32_ADC_MAX) == 1.0


def test_flatline_channel_is_unscored():
    s = synth.generate("rest", duration_s=12.0)
    for smp in s:
        smp.ppg_ir = 80_000
    v = assess(next(iter(windows(s, window_s=10.0))))
    assert v.ppg is Trust.UNSCORED
    assert any("flatline" in r for r in v.reasons)


def test_scored_is_exactly_not_unscored():
    for scenario in CLEAN + BAD + ("exercise",):
        for v in verdicts(scenario, dur=15.0):
            assert v.scored == (v.ppg is not Trust.UNSCORED)


def test_verdict_reports_the_metrics_it_judged_on():
    """A refusal we cannot audit later is a refusal we cannot defend on stage."""
    v = verdicts("rest", dur=15.0)[0]
    assert {"ssqi", "perfusion", "motion", "ppg_rail", "ecg_rail"} <= set(v.metrics)
