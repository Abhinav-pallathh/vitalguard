"""Heart-rate estimation, and the claim that gating is what makes it accurate."""
import numpy as np
import pytest

from vitalguard import hr, synth
from vitalguard.gate import Trust, assess
from vitalguard.replay import windows

CLEAN = ("rest", "exercise", "stress", "unexplained")


def estimates(scenario, dur=60.0, seed=7):
    return [(w, hr.estimate(w.cols["ppg_ir"]))
            for w in windows(synth.generate(scenario, duration_s=dur, seed=seed))]


# --- THE claim ------------------------------------------------------------

def test_gating_is_what_makes_the_heart_rate_accurate():
    """The number is only good because we refuse to produce most of them.

    Ungated, a corrupted recording yields a wildly wrong heart rate. Gated, the
    survivors are accurate to about a beat. This test is the argument for the
    whole product: the accuracy does not come from a better estimator, it comes
    from declining to answer.
    """
    truth = synth.true_hr("corrupted")
    rows = estimates("corrupted")

    ungated = [abs(e.hr_bpm - truth) for _, e in rows if e.hr_bpm is not None]
    gated = [abs(e.hr_bpm - truth) for w, e in rows
             if e.hr_bpm is not None and assess(w).ppg is not Trust.UNSCORED]

    assert gated, "the gate must not reject everything, or there is no product"
    assert np.median(gated) < 3.0, f"gated error {np.median(gated):.1f} bpm"
    assert np.mean(ungated) > 10 * np.median(gated) + 1, (
        f"gating must materially improve accuracy: "
        f"ungated mean {np.mean(ungated):.1f} vs gated median {np.median(gated):.1f}"
    )


@pytest.mark.parametrize("scenario", CLEAN)
def test_accurate_within_two_bpm_on_clean_signal(scenario):
    truth = synth.true_hr(scenario)
    errs = [abs(e.hr_bpm - truth) for _, e in estimates(scenario) if e.hr_bpm is not None]
    assert errs, f"{scenario}: no usable estimate at all"
    assert np.mean(errs) < 2.0, f"{scenario}: MAE {np.mean(errs):.2f} bpm"


@pytest.mark.parametrize("scenario", CLEAN)
def test_both_estimators_agree_on_clean_signal(scenario):
    assert all(e.agree for _, e in estimates(scenario)), scenario


# --- the disagreement check earns its keep --------------------------------

def test_disagreement_alone_catches_a_loose_clip():
    """Independent of the quality gate. Two estimators that fail differently
    catch what neither would catch alone: on a loose clip the spectral estimate
    is right and peak-counting doubles it, so agreement collapses and no number
    is produced."""
    rows = estimates("loose")
    assert not any(e.agree for _, e in rows)
    assert all(e.hr_bpm is None for _, e in rows), "disagreement must yield no number"


def test_disagreement_never_produces_a_number():
    for sc in synth.SCENARIOS:
        for _, e in estimates(sc, dur=20.0):
            if e.spectral_bpm is not None and e.peak_bpm is not None and not e.agree:
                assert e.hr_bpm is None


def test_usable_matches_hr_being_present():
    for sc in synth.SCENARIOS:
        for _, e in estimates(sc, dur=15.0):
            assert e.usable == (e.hr_bpm is not None)


# --- edges ----------------------------------------------------------------

def test_flat_signal_yields_no_estimate():
    e = hr.estimate(np.full(1000, 80_000.0))
    assert e.hr_bpm is None and e.n_beats == 0


def test_window_shorter_than_two_seconds_yields_no_estimate():
    e = hr.estimate(synth.generate("rest", duration_s=1.0)[0:100] and
                    np.array([s.ppg_ir for s in synth.generate("rest", duration_s=1.0)]))
    assert e.hr_bpm is None


def test_rr_intervals_are_returned_for_hrv_later():
    _, e = estimates("rest", dur=15.0)[0]
    assert e.rr_ms.size >= 5
    assert 300 < np.median(e.rr_ms) < 2000, "RR should be a plausible interval in ms"
