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

## Honesty rules for any number we report

Carried over from Residual Zero, because they were right there:

- **Never quote a metric without its scope.** Synthetic-generator results and
  real-recording results are different claims and get labelled as such.
- **Never report a zero rate as `0.00%`.** Use the one-sided rule-of-three
  upper bound with its denominator: *"0 in 340, below 0.88% at 95% confidence."*
- **Always give the denominator.**
