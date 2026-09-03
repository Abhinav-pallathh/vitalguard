"""The learned channel, and the guards that keep it a channel.

The model is allowed to be wrong. It is not allowed to be load-bearing, to
silently score a reordered vector, or to answer when its input is missing.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from vitalguard.features import FEATURE_NAMES
from vitalguard.model import (AGREE, DISAGREE, NO_OPINION, CARD_PATH,
                              MODEL_PATH, ArousalModel, summarise)
from vitalguard.scorer import Context

pytestmark = pytest.mark.skipif(not MODEL_PATH.exists(),
                                reason="no trained artifact -- run train_model.py")


@pytest.fixture(scope="module")
def m() -> ArousalModel:
    return ArousalModel.load()


def row(**over) -> np.ndarray:
    """A plausible quiet window; override a field to make it look aroused."""
    v = dict(hr_sigma=0.0, motion=0.005, gsr_sigma=0.0, gsr_slope=0.0,
             eda_peaks=0.0, rmssd=40.0, sdnn=50.0, pnn50=0.1) | over
    return np.array([v[n] for n in FEATURE_NAMES], dtype=float)


# --- it answers, in the right units -------------------------------------------

def test_a_probability_is_a_probability(m):
    p = m.p_arousal(row())
    assert p is not None and 0.0 <= p <= 1.0


def test_same_window_twice_gives_the_same_number(m):
    assert m.p_arousal(row(hr_sigma=3.0)) == m.p_arousal(row(hr_sigma=3.0))


def test_the_top_two_features_move_it_in_the_direction_the_data_says(m):
    """gsr_sigma then hr_sigma carry the model (permutation importance). This
    is a sanity check on the wiring, not a claim about physiology."""
    quiet = m.p_arousal(row())
    assert m.p_arousal(row(gsr_sigma=3.0, hr_sigma=4.0)) > quiet


# --- it refuses ----------------------------------------------------------------

def test_a_short_vector_returns_none_not_a_guess(m):
    assert m.p_arousal(np.zeros(len(FEATURE_NAMES) - 1)) is None


def test_a_nan_returns_none_rather_than_a_confident_zero(m):
    assert m.p_arousal(row(rmssd=float("nan"))) is None
    assert m.p_arousal(row(hr_sigma=float("inf"))) is None


def test_none_propagates_as_no_opinion_not_as_agreement(m):
    assert m.claims_arousal(None) is None
    assert m.agreement(None, Context.UNEXPLAINED) == NO_OPINION
    assert m.agreement(0.9, None) == NO_OPINION


def test_a_tampered_artifact_is_refused_at_load(tmp_path):
    bad = tmp_path / "arousal_v1.joblib"
    bad.write_bytes(MODEL_PATH.read_bytes() + b"\x00")
    with pytest.raises(Exception):
        ArousalModel.load(bad, CARD_PATH)


def test_a_reordered_feature_list_is_refused_at_load(tmp_path):
    import joblib
    blob = joblib.load(MODEL_PATH)
    blob["features"] = tuple(reversed(FEATURE_NAMES))
    p = tmp_path / "m.joblib"
    joblib.dump(blob, p)
    with pytest.raises(ValueError, match="feature order"):
        ArousalModel.load(p, CARD_PATH, verify=False)


def test_a_missing_artifact_degrades_to_no_model_instead_of_crashing():
    assert ArousalModel.load_or_none("models/does-not-exist.joblib") is None


# --- agreement is a word, never a verdict --------------------------------------

def test_agreement_is_reported_both_ways(m):
    thr = m.card.threshold
    assert m.agreement(thr + 0.05, Context.UNEXPLAINED) == AGREE
    assert m.agreement(thr + 0.05, Context.NORMAL) == DISAGREE
    assert m.agreement(0.0, Context.AROUSAL) == DISAGREE
    assert m.agreement(0.0, Context.EXERTION) == AGREE


def test_summarise_counts_the_windows_it_could_not_score(m):
    s = summarise([0.1, None, 0.9], [AGREE, NO_OPINION, DISAGREE])
    assert (s["n_windows"], s["n_scored"], s["n_unscored"]) == (3, 2, 1)
    assert s["n_disagree"] == 1 and s["p_max"] == 0.9


# --- the standing guards -------------------------------------------------------

def test_the_deterministic_path_does_not_import_the_model():
    """Determinism concludes. If scorer/gate/baseline ever read a probability,
    the model has stopped navigating and started deciding."""
    src = Path("src/vitalguard")
    for name in ("scorer.py", "gate.py", "baseline.py", "hr.py"):
        assert "model" not in _imports(src / name), f"{name} imports the model"


def _imports(path: Path) -> set[str]:
    import ast
    tree = ast.parse(path.read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module.split(".")[-1])
        elif isinstance(node, ast.Import):
            out.update(a.name.split(".")[-1] for a in node.names)
    return out


def test_the_module_exposes_no_severity_and_no_alarm():
    import vitalguard.model as mod
    assert not hasattr(mod, "Severity")
    assert not any(n.lower().startswith(("alarm", "alert", "severity")) for n in dir(mod))


def test_the_card_quotes_the_worst_subject_next_to_the_mean(m):
    h = m.card.headline
    assert "worst" in h and f"{m.card.mean_f1:.2f}" in h
    assert m.card.worst_sensitivity <= m.card.mean_sensitivity


def test_the_card_states_the_exercise_limit_in_words():
    """WESAD is a seated study. If this ever disappears from the card, someone
    has quoted these numbers for the EXERTION branch."""
    limits = " ".join(json.loads(CARD_PATH.read_text())["limits"]).lower()
    assert "exercise" in limits or "exertion" in limits
    assert "quality" in limits


def test_quality_metrics_never_became_features():
    for banned in ("ssqi", "perfusion", "rail", "trust", "quality"):
        assert not any(banned in n.lower() for n in FEATURE_NAMES)
