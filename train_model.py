"""Train the shipped arousal model, and score it honestly.

D8 says rules conclude and a model may only navigate. That decision was made
when no model shipped at all; `evaluate.py` trained one, printed a comparison
and threw it away. This script is the other half: it produces the artifact the
architecture already describes -- a model that reports a PROBABILITY, and never
a verdict.

Three honesty rules are wired into the procedure, not just written down:

  1. EVERY number quoted here is leave-one-subject-out. A model that has seen
     any window from the subject it is scored on is measuring memory, not
     physiology, and 5,715 windows from 15 people would hide that completely
     under a random split (windows overlap; neighbours are near-duplicates).

  2. The decision threshold and the calibrator are fitted on OUT-OF-FOLD
     predictions only. Picking a threshold on the same predictions that are
     then reported is the quietest way to publish a fantasy.

  3. The manifest stores the LOSO numbers, not the fit-on-everything numbers,
     so anyone reading models/arousal_v1.json is reading what the model does on
     a stranger.

Run:  PYTHONPATH=src ./venv/bin/python train_model.py
"""
from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, brier_score_loss,
                             f1_score, roc_auc_score)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from vitalguard.features import FEATURE_NAMES

DATA = Path("data/wesad/wesad_features.npz")
OUT_MODEL = Path("models/arousal_v1.joblib")
OUT_CARD = Path("models/arousal_v1.json")
QUIET = ("rest", "meditation", "amusement")
MIN_STRESS_TEST = 20      # a fold with fewer positives cannot be scored at all


def hgb() -> HistGradientBoostingClassifier:
    """Boosted trees. class_weight because quiet outnumbers stress 8:1 and an
    unweighted fit buys 89% accuracy by never saying stress."""
    return HistGradientBoostingClassifier(
        max_iter=200, learning_rate=0.1, max_depth=4,
        class_weight="balanced", random_state=0)


def logreg():
    """The control arm. If trees do not beat a linear model on eight features,
    the trees are decoration and the linear model is the honest thing to ship."""
    return make_pipeline(StandardScaler(),
                         LogisticRegression(max_iter=2000, class_weight="balanced"))


@dataclass
class Fold:
    subject: str
    n_test: int
    n_stress: int


def loso_scores(X, y_bin, subj, make) -> tuple[np.ndarray, np.ndarray, list[Fold]]:
    """Out-of-fold P(stress) for every window, each produced by a model that
    never saw that window's subject. Returns (p, scored_mask, folds)."""
    p = np.full(len(X), np.nan)
    folds: list[Fold] = []
    for held in sorted(set(subj.tolist())):
        te = subj == held
        tr = ~te
        if y_bin[te].sum() < MIN_STRESS_TEST or y_bin[tr].sum() < MIN_STRESS_TEST:
            continue
        clf = make().fit(X[tr], y_bin[tr])
        p[te] = clf.predict_proba(X[te])[:, 1]
        folds.append(Fold(held, int(te.sum()), int(y_bin[te].sum())))
    return p, ~np.isnan(p), folds


def pick_threshold(p, y_bin, subj, folds) -> tuple[float, float]:
    """Threshold that maximises the MEAN per-subject F1, not the pooled F1.

    Pooled F1 lets one long, easy subject carry the number. The wearer is a
    single subject, so the per-subject mean is the unit that matches the claim.
    """
    best, best_f1 = 0.5, -1.0
    for t in np.arange(0.05, 0.96, 0.01):
        per = [f1_score(y_bin[subj == f.subject], p[subj == f.subject] >= t,
                        zero_division=0) for f in folds]
        m = float(np.mean(per))
        if m > best_f1:
            best, best_f1 = float(t), m
    return best, best_f1


def per_subject_table(name, p, y_bin, subj, folds, thr) -> list[dict]:
    rows = []
    for f in folds:
        m = subj == f.subject
        yt, pt = y_bin[m], p[m] >= thr
        rows.append({
            "subject": f.subject, "n": f.n_test,
            "sensitivity": float(pt[yt].mean()),
            "specificity": float(1 - pt[~yt].mean()),
            "f1": float(f1_score(yt, pt, zero_division=0)),
        })
    print(f"\n{name}  (threshold {thr:.2f}, leave-one-subject-out)")
    print(f"  {'subject':<9}{'n':>6}{'sens':>8}{'spec':>8}{'F1':>8}")
    for r in rows:
        print(f"  {r['subject']:<9}{r['n']:>6}{r['sensitivity']:>8.2f}"
              f"{r['specificity']:>8.3f}{r['f1']:>8.2f}")
    a = np.array([[r["sensitivity"], r["specificity"], r["f1"]] for r in rows])
    print(f"  {'MEAN':<9}{'':>6}{a[:,0].mean():>8.2f}{a[:,1].mean():>8.3f}{a[:,2].mean():>8.2f}")
    print(f"  {'WORST':<9}{'':>6}{a[:,0].min():>8.2f}{a[:,1].min():>8.3f}{a[:,2].min():>8.2f}")
    return rows


def main() -> None:
    d = np.load(DATA, allow_pickle=True)
    X, y, subj = d["X"], d["y"], d["subject"]
    names = tuple(str(n) for n in d["names"])
    if names != FEATURE_NAMES:
        raise SystemExit(f"feature order drifted: cache {names} vs code {FEATURE_NAMES}")
    y_bin = (y == "stress")
    keep = np.isin(y, QUIET) | y_bin       # everything is already one of these
    X, y_bin, subj = X[keep], y_bin[keep], subj[keep]
    print(f"{len(X)} windows, {len(set(subj.tolist()))} subjects, {X.shape[1]} features")
    print(f"stress {int(y_bin.sum())} / quiet {int((~y_bin).sum())}")

    arms = {}
    for label, make in (("logreg (control)", logreg), ("hist-gradient-boosting", hgb)):
        p, m, folds = loso_scores(X, y_bin, subj, make)
        thr, mean_f1 = pick_threshold(p[m], y_bin[m], subj[m], folds)
        rows = per_subject_table(label, p[m], y_bin[m], subj[m], folds, thr)
        auc = roc_auc_score(y_bin[m], p[m])
        ap = average_precision_score(y_bin[m], p[m])
        brier = brier_score_loss(y_bin[m], p[m])
        print(f"  ROC-AUC {auc:.3f}   PR-AUC {ap:.3f}   Brier {brier:.4f}")
        arms[label] = dict(p=p, mask=m, folds=folds, thr=thr, rows=rows,
                           auc=float(auc), ap=float(ap), brier=float(brier),
                           mean_f1=float(mean_f1))

    win = "hist-gradient-boosting"
    ctrl = arms["logreg (control)"]
    a = arms[win]
    print(f"\ntrees vs linear, mean per-subject F1: {a['mean_f1']:.3f} vs {ctrl['mean_f1']:.3f}")
    if a["mean_f1"] <= ctrl["mean_f1"]:
        print("  -> the control wins. Shipping the LINEAR model; trees are decoration here.")
        win = "logreg (control)"
        a = ctrl

    # Platt scaling fitted on OUT-OF-FOLD scores only. The base model is then
    # refit on everything; the calibrator is not refit, because there are no
    # honest scores left to fit it on once the model has seen every subject.
    m = a["mask"]
    cal = LogisticRegression().fit(a["p"][m].reshape(-1, 1), y_bin[m])
    p_cal = cal.predict_proba(a["p"][m].reshape(-1, 1))[:, 1]
    print(f"\ncalibration (out-of-fold): Brier {a['brier']:.4f} -> {brier_score_loss(y_bin[m], p_cal):.4f}")

    make = hgb if win == "hist-gradient-boosting" else logreg
    base = make().fit(X, y_bin)
    joblib.dump({"base": base, "calibrator": cal, "features": FEATURE_NAMES},
                OUT_MODEL)

    imp = permutation_importance(base, X, y_bin, n_repeats=5, random_state=0,
                                 scoring="average_precision")
    importance = {FEATURE_NAMES[i]: float(imp.importances_mean[i])
                  for i in np.argsort(imp.importances_mean)[::-1]}
    print("\npermutation importance (whole set, PR-AUC drop):")
    for k, v in importance.items():
        print(f"  {k:<12}{v:+.4f}")

    rows = a["rows"]
    arr = np.array([[r["sensitivity"], r["specificity"], r["f1"]] for r in rows])
    card = {
        "name": "arousal_v1",
        "kind": win,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "features": list(FEATURE_NAMES),
        "trained_on": {"dataset": "WESAD", "windows": int(len(X)),
                       "subjects": sorted(set(subj.tolist())),
                       "positive_label": "stress (TSST)",
                       "negative_labels": list(QUIET)},
        "threshold": a["thr"],
        "loso": {
            "per_subject": rows,
            "mean_sensitivity": float(arr[:, 0].mean()),
            "mean_specificity": float(arr[:, 1].mean()),
            "mean_f1": float(arr[:, 2].mean()),
            "worst_sensitivity": float(arr[:, 0].min()),
            "worst_specificity": float(arr[:, 1].min()),
            "roc_auc": a["auc"], "pr_auc": a["ap"],
            "brier_raw": a["brier"],
            "brier_calibrated": float(brier_score_loss(y_bin[m], p_cal)),
        },
        "permutation_importance": importance,
        "environment": {"python": platform.python_version(),
                        "sklearn": sklearn.__version__,
                        "numpy": np.__version__},
        "limits": [
            "Trained on a seated lab study. WESAD has NO exercise condition, so this "
            "model has never seen exertion and cannot tell it from stress. The EXERTION "
            "branch stays with the rules.",
            "The positive class is the TSST condition, not a person's felt stress. It is "
            "a label for an imposed situation.",
            "The threshold and the calibrator were fitted on out-of-fold predictions of "
            "this same dataset, so both carry a mild optimism no held-out subject removes.",
            "The calibrator was fitted on scores from models trained on 14 subjects and is "
            "applied to a model trained on 15. The shift is small and unmeasured.",
            "Signal-quality features are excluded on purpose (see features.py). Adding them "
            "would raise every number here and measure sensor degradation instead.",
            "This model reports a probability. It never sets a severity, and nothing in "
            "the deterministic path reads it.",
        ],
    }
    card["sha256"] = hashlib.sha256(OUT_MODEL.read_bytes()).hexdigest()
    OUT_CARD.write_text(json.dumps(card, indent=1) + "\n")
    print(f"\nsaved {OUT_MODEL} ({OUT_MODEL.stat().st_size/1024:.0f} KB)"
          f"\nsaved {OUT_CARD}")


if __name__ == "__main__":
    main()
