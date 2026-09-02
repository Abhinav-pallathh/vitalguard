"""These tests assert the CLAIM in synth.py's docstring is actually true.

If the five scenarios are not separable by the signals we say separate them,
the three-sensor architecture is not justified and we need to know that now,
not on stage.
"""
import numpy as np
import pytest

from vitalguard import synth
from vitalguard.schema import SAMPLE_RATE_HZ, accel_magnitude, to_arrays


def cols(scenario, dur=60.0, seed=7):
    return to_arrays(synth.generate(scenario, duration_s=dur, seed=seed))


def test_length_matches_sample_rate():
    assert len(synth.generate("rest", duration_s=30.0)) == 30 * SAMPLE_RATE_HZ


def test_same_seed_is_deterministic():
    a = synth.generate("stress", duration_s=5.0, seed=42)
    b = synth.generate("stress", duration_s=5.0, seed=42)
    assert a == b


def test_different_seed_differs():
    a = synth.generate("stress", duration_s=5.0, seed=1)
    b = synth.generate("stress", duration_s=5.0, seed=2)
    assert a != b


def test_unknown_scenario_raises():
    with pytest.raises(ValueError, match="unknown scenario"):
        synth.generate("meltdown")


@pytest.mark.parametrize("scenario", synth.SCENARIOS)
def test_timestamps_are_monotonic_and_evenly_spaced(scenario):
    t = cols(scenario, dur=5.0)["t_ms"].astype(int)
    d = np.diff(t)
    assert np.all(d == 10), "100 Hz means exactly 10 ms between samples"


# --- the discrimination the product claims ------------------------------

def test_exercise_is_the_only_high_motion_state():
    quiet = {s: accel_magnitude(cols(s)).std() for s in ("rest", "stress", "unexplained")}
    moving = accel_magnitude(cols("exercise")).std()
    assert moving > 10 * max(quiet.values()), (
        f"exercise motion {moving:.3f} must dominate {quiet}"
    )


def test_gsr_rises_under_arousal_and_is_flat_otherwise():
    def slope(s):
        g = cols(s)["gsr_raw"].astype(float)
        t = np.arange(g.size) / SAMPLE_RATE_HZ
        return np.polyfit(t, g, 1)[0]

    assert slope("stress") > 10.0
    assert abs(slope("rest")) < 2.0
    assert abs(slope("unexplained")) < 2.0


def test_unexplained_differs_from_exercise_only_by_motion():
    """The alarm case. Both have elevated HR; only motion separates them."""
    assert synth.true_hr("exercise") > 90 and synth.true_hr("unexplained") > 90
    assert accel_magnitude(cols("exercise")).std() > 0.3
    assert accel_magnitude(cols("unexplained")).std() < 0.1


def test_unexplained_differs_from_stress_only_by_gsr():
    g_stress = cols("stress")["gsr_raw"].astype(float)
    g_unexp = cols("unexplained")["gsr_raw"].astype(float)
    assert g_stress.max() - g_stress.min() > 5 * (g_unexp.max() - g_unexp.min())


def test_corrupted_asserts_lead_off_and_others_never_do():
    assert cols("corrupted")["lead_off"].astype(int).sum() > 0
    for s in ("rest", "exercise", "stress", "unexplained"):
        assert cols(s)["lead_off"].astype(int).sum() == 0


def test_corrupted_ppg_is_visibly_worse_than_clean_ppg():
    """Whatever quality metric we pick must be able to see this difference."""
    clean = cols("rest")["ppg_ir"].astype(float)
    dirty = cols("corrupted")["ppg_ir"].astype(float)
    a, b = int(0.35 * dirty.size), int(0.60 * dirty.size)
    assert dirty[a:b].std() > 3 * clean.std()
