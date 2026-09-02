# VitalGuard

Personalized, context-aware, signal-honest vitals monitoring on an ESP32.

> **The scorer proposes. A deterministic gate concludes.**
> No inferred number reaches a user without passing the quality gate. A reading
> we do not trust is reported as `UNSCORED` — never a value, never a guess,
> never the last good number held over.

## Run it

```bash
cd ~/vitalguard
PYTHONPATH=src ./venv/bin/python -m pytest -q      # 43 passed
PYTHONPATH=src ./venv/bin/python report_gate.py    # what the gate concludes
PYTHONPATH=src ./venv/bin/python calibrate.py     # the measured thresholds
```

`PYTHONPATH=src` is required — there is no installed package.

## What exists (Phase 0)

| File | Role |
|---|---|
| `src/vitalguard/schema.py` | **The record format. One source of truth.** Firmware writes it, everything reads it. |
| `src/vitalguard/synth.py` | Five synthetic scenarios, so every layer is testable before hardware. |
| `src/vitalguard/replay.py` | Recording → windows. The window is the unit of analysis. |
| `docs/FIRMWARE_CONTRACT.md` | **For Sujan.** Pin map, row format, the ADC2/WiFi trap. |
| `docs/DECISIONS.md` | Decision ledger — every choice, its alternative, and why. |

## The five scenarios

| Scenario | HR | Motion | GSR | Correct verdict |
|---|---|---|---|---|
| `rest` | ~65 | low | flat | normal |
| `exercise` | ~130 | **high** | rising | elevated, **explained** |
| `stress` | ~95 | low | **rising** | elevated, arousal |
| `unexplained` | ~110 | low | flat | **ALERT** ← the product |
| `corrupted` | — | any | any | **UNSCORED**, never a number |

`exercise` and `unexplained` differ *only* in motion. `stress` and `unexplained`
differ *only* in GSR. That is deliberate: if the scorer separates all five, the
three-signal architecture is justified. If it separates them without GSR, the
GSR is decoration and we should say so out loud.

## Phases

| Phase | Owner | What | Status |
|---|---|---|---|
| 0 | Abhi | Repo, record schema, replay harness, synthetic data | ✅ **done** |
| 1a | Sujan | Firmware: sensors → timestamped CSV. Dumb, no logic. | firmware contract ready |
| 1b | Abhi | Signal Quality Gate (SSQI + perfusion + accel + lead-off) | ✅ **done** |
| 2 | Both | Collect own labelled session (button = "exercising now") | blocked on 1a |
| 3 | Abhi | Personal Baseline + Severity Scorer, rule-based first | blocked on 2 |
| 4 | Both | On-device inference → OLED + buzzer | |
| 5 | Both | Dashboard + demo script | |

## Measured results

**PPG heart rate vs chest-ECG ground truth — WESAD, 7 subjects (S2–S8), gate-passing
windows only, `WESAD_E4` profile.**

| | |
|---|---|
| MAE | **3.22 bpm** |
| Median error | **1.60 bpm** |
| Within 5 bpm | 84.4% |
| Within 10 bpm | 94.0% |
| Worst single subject | 4.65 bpm |
| n | 2,517 windows |

**Gate false-confirm rate — synthetic adversarial windows, `SYNTHETIC` profile.**
0 in 306, below 0.98% at 95% confidence (one-sided rule of three).

### Severity scorer — WESAD, 5 subjects (S2–S6), 1,729 scored windows

| true state | n | normal | exertion | arousal | unexplained | alarms |
|---|---|---|---|---|---|---|
| rest | 818 | 731 | 0 | 32 | 55 | 14 (1.7%) |
| meditation | 529 | 480 | 0 | 30 | 19 | 4 (0.8%) |
| amusement | 212 | 186 | 0 | 17 | 9 | 0 (0.0%) |
| stress | 170 | 57 | 13 | 90 | 10 | 31 (18.2%) |

**False alarms on non-stress states: 18 in 1,559 windows (1.15%).**
**Stress recognised as elevated: 113/170 (66.5%)** — a third of stress windows are missed.

#### Ablation: does the GSR sensor earn its place?

| configuration | false alarms (quiet states) | stress alarms |
|---|---|---|
| HR + motion + GSR | 18 / 1559 = **1.15%** | 31 / 170 = 18.2% |
| HR + motion only | 26 / 1559 = **1.67%** | 44 / 170 = 25.9% |

Removing GSR multiplies false alarms by **1.4×**. That is real but modest — smaller
than the ablation was expected to show, and it is reported as measured rather than
framed up.

The stronger case for the third sensor is attribution, not alarm count: **of the
100 detected stress windows that were not exertion, 90 were correctly labelled
AROUSAL rather than UNEXPLAINED.** Without GSR all 100 present as unexplained
alarms. Reducing false alarms is what GSR does second; explaining *why* the heart
rate rose is what it does first, and that is the actual product claim.

#### ⚠ What this data cannot tell us

- **The EXERTION branch is unvalidated on real data.** WESAD is a seated lab
  study; motion is 0.004–0.022 g in every state. Only our own recordings can
  test it.
- **WESAD's amusement condition barely raises heart rate** (+0.03σ, same as
  rest), so it does not meaningfully test arousal-without-stress at an
  *elevated* rate. The scorer scores well on amusement for the wrong reason.

### ⚠ Known limitation: accuracy collapses under stress

| state | MAE | median | n |
|---|---|---|---|
| rest | 2.25 | 1.24 | 1163 |
| amusement | 2.47 | 1.40 | 342 |
| meditation | 2.98 | 1.92 | 783 |
| **stress** | **10.01** | **5.86** | 229 |

Stress is **4× worse than every other state, on windows that PASSED the gate.**
Peripheral vasoconstriction shunts blood from the extremities, so wrist PPG
degrades exactly when the reading matters most — and our quality gate does not
currently detect that degradation. The estimator-agreement check catches part of
it (agreement falls to 29% under stress) but the windows that survive are still
four times less accurate.

This is stated rather than hidden because it is the single most important
weakness in the system: the product exists to catch physiological events, and
its measurement is least reliable during one. Two mitigations, neither yet
validated:

  - the **ear clip** is far better perfused than the wrist, which is the
    placement already chosen for the hardware — but WESAD is wrist data, so
    this remains a hypothesis until our own recordings exist
  - stress-state readings could be forced to DEGRADED by policy

## Learned scorer vs rules — leave-one-subject-out, all 15 WESAD subjects

Both arms on identical feature vectors and identical splits. Every number is
from a subject the scorer never saw.

| | sensitivity | specificity | F1 | worst-subject sensitivity |
|---|---|---|---|---|
| rules | 0.44 | 0.930 | 0.41 | **0.00** |
| learned model | **0.68** | **0.965** | **0.67** | 0.22 |

**Decision D8 is falsified.** It assumed rules would hold unless a model clearly
beat them. A model clearly beats them, and the rules fail outright on 3 of 15
subjects (sensitivity 0.00, 0.04, 0.04) — a failure completely hidden by the
earlier pooled 5-subject figure.

`gsr_sigma` is the **most important single feature**, ahead of heart-rate
deviation. That is a far stronger justification for the third sensor than the
1.4x ablation reported earlier. HRV features (RMSSD, SDNN, pNN50) contribute
almost nothing at 10-second windows — as predicted in `features.py`, the window
is too short for them to mean much.

### The alarm rate was never a classifier problem

Per-window alarming, out-of-fold:

| | alarms / 16 h day | worst subject |
|---|---|---|
| rules | 305 | 2235 |
| learned model | **400** | 2717 |

The *better* classifier produced *more* alarms. Higher sensitivity means more
firing. Requiring continuous evidence fixes it almost for free:

| sustain required | alarms/day | per-window sensitivity |
|---|---|---|
| 5 s | 46.8 | 0.68 |
| 15 s | 15.0 | 0.68 |
| **60 s** | **2.3** | **0.68** |
| 120 s | 0.0 | 0.68 (never fires) |

Real physiological episodes last minutes; false positives are isolated. So
duration costs essentially nothing and buys a 20x reduction.

**The number worth remembering:**

```
per-window specificity achieved   0.9679
needed for ~3 alarms/day          0.99974   -> a 123x reduction
```

96.8% specificity sounds excellent and is two orders of magnitude short of
usable. Continuous monitoring has a base-rate problem that per-window metrics
hide, which is why every alarm figure here is also quoted per day.

⚠ **Not yet measured: episode-level detection rate.** The sensitivity column is
per-window classifier sensitivity and is independent of the notification
policy. At 120 s sustain the system fires zero alarms while still showing 0.68
— which proves the column does not measure whether real episodes get caught.
Until that is measured, 60 s is an operating point chosen on alarm rate alone.

## Honesty rules for any number we report

Carried over from Residual Zero, because they were right there:

- **Never quote a metric without its scope.** Synthetic-generator results and
  real-recording results are different claims and get labelled as such.
- **Never report a zero rate as `0.00%`.** Use the one-sided rule-of-three
  upper bound with its denominator: *"0 in 340, below 0.88% at 95% confidence."*
- **Always give the denominator.**
