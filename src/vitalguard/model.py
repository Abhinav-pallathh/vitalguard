"""The learned arousal channel -- the model that NAVIGATES.

    > The model navigates. Determinism concludes.

This module is the first half of that sentence made real, and it is written to
make the second half impossible to violate by accident:

  - It returns a PROBABILITY and a disagreement flag. There is no code path in
    here that produces a Severity, a Context, or an alarm.
  - Nothing in the deterministic path imports it. scorer.py, gate.py and
    baseline.py do not know this file exists, and a test fails if that changes.
  - It REFUSES rather than guesses. A malformed vector, a NaN, a model whose
    feature order does not match the code -- all return None or raise at load,
    never a plausible number. Same rule the quality gate already follows: an
    absent answer is honest, a fabricated one is not.

What it is FOR, given it decides nothing: it is a second opinion that fails
differently from the rules. hr.py already runs two heart-rate estimators and
reports their disagreement instead of averaging it away; this is that pattern
applied one level up. When the rules and the model disagree about a window,
that window is worth a human looking at -- which is navigation, not a verdict.

Trained by train_model.py; every number in the card is leave-one-subject-out.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .features import FEATURE_NAMES
from .scorer import Context

MODEL_PATH = Path("models/arousal_v1.joblib")
CARD_PATH = Path("models/arousal_v1.json")

# What the model may say about a window, in relation to what the rules said.
AGREE = "agree"
DISAGREE = "disagree"
NO_OPINION = "no opinion"      # the rules never got a window to judge


@dataclass(frozen=True, slots=True)
class ModelCard:
    """The claims the artifact is allowed to make about itself."""

    name: str
    kind: str
    threshold: float
    features: tuple[str, ...]
    mean_sensitivity: float
    mean_specificity: float
    mean_f1: float
    worst_sensitivity: float
    worst_specificity: float
    limits: tuple[str, ...]
    sha256: str

    @property
    def headline(self) -> str:
        """Deliberately quotes the WORST subject alongside the mean. A mean
        alone is the number a wearer never experiences."""
        return (f"{self.kind}, leave-one-subject-out: mean F1 {self.mean_f1:.2f}, "
                f"sensitivity {self.mean_sensitivity:.2f} "
                f"(worst subject {self.worst_sensitivity:.2f}), "
                f"specificity {self.mean_specificity:.3f} "
                f"(worst {self.worst_specificity:.3f})")


class ArousalModel:
    """P(the TSST stress condition) for one feature window. Nothing more."""

    def __init__(self, base, calibrator, card: ModelCard) -> None:
        self._base = base
        self._cal = calibrator
        self.card = card

    # -- construction -------------------------------------------------------
    @classmethod
    def load(cls, model_path: str | Path = MODEL_PATH,
             card_path: str | Path = CARD_PATH, *, verify: bool = True) -> "ArousalModel":
        """Load the artifact, refusing anything that does not match this code.

        The feature-order check is the important one. A silently reordered
        vector produces confident nonsense that no test of the model itself
        would ever catch -- the numbers stay in range, they are just about the
        wrong columns.
        """
        import joblib

        model_path, card_path = Path(model_path), Path(card_path)
        card_raw = json.loads(card_path.read_text())
        blob = joblib.load(model_path)

        features = tuple(blob["features"])
        if features != FEATURE_NAMES:
            raise ValueError(f"model feature order {features} != code {FEATURE_NAMES}")
        if tuple(card_raw["features"]) != FEATURE_NAMES:
            raise ValueError("model card feature order does not match the code")
        if verify:
            digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
            if digest != card_raw["sha256"]:
                raise ValueError(f"{model_path} does not match its card "
                                 f"({digest[:12]} vs {card_raw['sha256'][:12]})")

        loso = card_raw["loso"]
        card = ModelCard(
            name=card_raw["name"], kind=card_raw["kind"],
            threshold=float(card_raw["threshold"]), features=features,
            mean_sensitivity=loso["mean_sensitivity"],
            mean_specificity=loso["mean_specificity"],
            mean_f1=loso["mean_f1"],
            worst_sensitivity=loso["worst_sensitivity"],
            worst_specificity=loso["worst_specificity"],
            limits=tuple(card_raw["limits"]), sha256=card_raw["sha256"])
        return cls(blob["base"], blob["calibrator"], card)

    @classmethod
    def load_or_none(cls, *a, **kw) -> "ArousalModel | None":
        """For the live path: a missing or broken artifact must not take the
        deterministic pipeline down with it. The model is optional BY DESIGN --
        the product still works with no model at all, which is the whole point
        of it not being allowed to conclude."""
        try:
            return cls.load(*a, **kw)
        except Exception:
            return None

    # -- inference ----------------------------------------------------------
    def p_arousal(self, vector) -> float | None:
        """Calibrated P(stress) for one window, or None if it cannot be scored.

        None is returned -- not raised, not 0.0 -- for a vector that is the
        wrong length or contains a non-finite value. 0.0 would read as
        'confidently calm' downstream, which is a lie about a missing input.
        """
        v = np.asarray(vector, dtype=float).ravel()
        if v.size != len(FEATURE_NAMES) or not np.isfinite(v).all():
            return None
        raw = float(self._base.predict_proba(v.reshape(1, -1))[0, 1])
        return float(self._cal.predict_proba([[raw]])[0, 1])

    def claims_arousal(self, p: float | None) -> bool | None:
        if p is None:
            return None
        return p >= self.card.threshold

    def agreement(self, p: float | None, context: Context | None) -> str:
        """How this window's model opinion sits against the rules' verdict.

        Returned as a WORD, never a severity. The caller may print it, count it
        or log it; nothing in this project is permitted to escalate on it.
        """
        claims = self.claims_arousal(p)
        if claims is None or context is None:
            return NO_OPINION
        rules_elevated = context in (Context.AROUSAL, Context.UNEXPLAINED)
        return AGREE if claims == rules_elevated else DISAGREE


def summarise(ps: list[float | None], agreements: list[str]) -> dict:
    """One block of windows, summarised for the report.

    Reports n_unscored explicitly. A mean over 3 of 40 windows and a mean over
    40 of 40 are different claims, and the difference is invisible if the
    refusals are dropped silently.
    """
    scored = [p for p in ps if p is not None]
    out = {
        "n_windows": len(ps),
        "n_scored": len(scored),
        "n_unscored": len(ps) - len(scored),
        "p_mean": None, "p_max": None,
        "n_disagree": sum(1 for a in agreements if a == DISAGREE),
        "n_agree": sum(1 for a in agreements if a == AGREE),
    }
    if scored:
        out["p_mean"] = float(np.mean(scored))
        out["p_max"] = float(np.max(scored))
    return out
