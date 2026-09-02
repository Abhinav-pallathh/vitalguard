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

## Honesty rules for any number we report

Carried over from Residual Zero, because they were right there:

- **Never quote a metric without its scope.** Synthetic-generator results and
  real-recording results are different claims and get labelled as such.
- **Never report a zero rate as `0.00%`.** Use the one-sided rule-of-three
  upper bound with its denominator: *"0 in 340, below 0.88% at 95% confidence."*
- **Always give the denominator.**
