"""Rules vs model, leave-one-subject-out. The honest comparison.

Both are evaluated on IDENTICAL feature vectors and IDENTICAL splits, so this
compares decision rules, not inputs.

Leave-one-subject-out is not optional here. The rule thresholds were originally
fitted on S2-S4 and then evaluated on S2-S6 -- three of five test subjects had
trained the thresholds. Every number in this file is produced on a subject the
scorer has never seen, for BOTH arms.

Reported in two units:
  - the benchmark unit (sensitivity / specificity), comparable to the WESAD
    literature
  - the unit a WEARER experiences (false alarms per 16-hour day), because a
    1% per-window rate sounds fine and is roughly 130 alarms a day
"""
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import f1_score

from vitalguard.baseline import Deviation
from vitalguard.features import FEATURE_NAMES
from vitalguard.scorer import Score
from vitalguard.scorer import (AlarmPolicy, Context, Severity,
                               SustainedScorer, score)

HOP_S = 5.0
QUIET = ("rest", "meditation", "amusement")
F = {n: i for i, n in enumerate(FEATURE_NAMES)}


def rules_predict(X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Run the existing rule scorer over a feature matrix.

    Returns (alarm, says_stress). The SustainedScorer is applied so the rules
    get the same benefit of the doubt the product gives them.
    """
    ss = SustainedScorer()
    ap = AlarmPolicy()
    alarm, stress, notify = [], [], []
    for i, row in enumerate(X):
        sig = row[F["hr_sigma"]]
        d = Deviation(hr_bpm=65 + sig * 3, baseline_bpm=65, delta_bpm=sig * 3,
                      personal_sigma=sig)
        s = ss.push(score(d, row[F["motion"]], row[F["gsr_sigma"]]))
        alarm.append(s.severity in (Severity.CONCERN, Severity.ALERT))
        notify.append(ap.should_notify(s, i * HOP_S))
        stress.append(s.context is Context.AROUSAL)
    return np.array(alarm), np.array(stress), np.array(notify)


def main():
    d = np.load("data/wesad/wesad_features.npz", allow_pickle=True)
    X, y, subj = d["X"], d["y"], d["subject"]
    is_stress = (y == "stress")
    subjects = sorted(set(subj.tolist()))
    print(f"{len(X)} windows, {len(subjects)} subjects, {len(FEATURE_NAMES)} features")
    print(f"stress {is_stress.sum()} / quiet {np.isin(y, QUIET).sum()}\n")

    rows = {"rules": [], "model": []}
    for held in subjects:
        te = subj == held
        tr = ~te
        if is_stress[tr].sum() < 20 or te.sum() < 20:
            continue

        r_alarm, r_stress, r_notify = rules_predict(X[te])

        clf = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.1,
                                             max_depth=4, random_state=0)
        clf.fit(X[tr], is_stress[tr])
        m_stress = clf.predict(X[te])
        m_alarm = m_stress            # model's "alarm" = it claims stress
        # Same episode policy applied to the model, so the comparison is fair.
        ap = AlarmPolicy(); m_notify = []
        for i, p_i in enumerate(m_stress):
            sev = Severity.CONCERN if p_i else Severity.NONE
            m_notify.append(ap.should_notify(
                Score(Context.UNEXPLAINED if p_i else Context.NORMAL, sev,
                      0.0, 0.0, None, ""), i * HOP_S))
        m_notify = np.array(m_notify)

        quiet = np.isin(y[te], QUIET)
        for name, alarm, pred, notif in (("rules", r_alarm, r_stress, r_notify),
                                         ("model", m_alarm, m_stress, m_notify)):
            fa = int(alarm[quiet].sum()); n_q = int(quiet.sum())
            fn_ = int(notif[quiet].sum())
            sens = float(pred[is_stress[te]].mean()) if is_stress[te].any() else np.nan
            spec = float(1 - pred[quiet].mean())
            f1 = f1_score(is_stress[te], pred, zero_division=0)
            hours = n_q * HOP_S / 3600
            rows[name].append((held, sens, spec, f1, fa / hours * 16 if hours else 0,
                               fn_ / hours * 16 if hours else 0))

    print(f"{'':<8}{'subject':<9}{'sensitivity':>12}{'specificity':>12}{'F1':>8}"
          f"{'raw/day':>10}{'NOTIFIED/day':>14}")
    print("-" * 76)
    for name in ("rules", "model"):
        for held, sens, spec, f1, per_day, notif_day in rows[name]:
            print(f"{name:<8}{held:<9}{sens:>12.2f}{spec:>12.3f}{f1:>8.2f}"
                  f"{per_day:>10.0f}{notif_day:>14.1f}")
        a = np.array([r[1:] for r in rows[name]], dtype=float)
        print(f"{name.upper():<8}{'MEAN':<9}{a[:,0].mean():>12.2f}{a[:,1].mean():>12.3f}"
              f"{a[:,2].mean():>8.2f}{a[:,3].mean():>10.0f}{a[:,4].mean():>14.1f}")
        print(f"{'':<8}{'worst':<9}{a[:,0].min():>12.2f}{a[:,1].min():>12.3f}"
              f"{a[:,2].min():>8.2f}{a[:,3].max():>10.0f}{a[:,4].max():>14.1f}")
        print("-" * 76)

    clf = HistGradientBoostingClassifier(max_iter=200, max_depth=4, random_state=0).fit(X, is_stress)
    from sklearn.inspection import permutation_importance
    imp = permutation_importance(clf, X, is_stress, n_repeats=5, random_state=0)
    print("\nfeature importance (permutation, whole set):")
    for i in np.argsort(imp.importances_mean)[::-1]:
        print(f"  {FEATURE_NAMES[i]:<12} {imp.importances_mean[i]:+.4f}")


if __name__ == "__main__":
    main()
