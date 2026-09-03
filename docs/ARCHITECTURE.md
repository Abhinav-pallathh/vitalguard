# VitalGuard — how the whole thing works

Written 2026-09-02. **Updated 2026-09-04** — the project became an interactive
pressure test, which added two more channels and a clock to reconcile them.

---

## The one idea

> **The model navigates. Determinism concludes.**

Every consumer wearable can tell you your heart rate went up. Two things make
this different, and both are about *refusing*:

1. **It refuses to show a number it doesn't trust.** Not a greyed-out stale
   value — no number at all, plus what you should do about it.
2. **It refuses to alarm when it knows why.** You went for a run: that's
   explained, not an emergency.

Everything below exists to serve those two refusals.

---

## The whole pipeline, end to end

```
 ┌──────────────────────── HARDWARE (ESP32, C++) ───────────────────────┐
 │                                                                      │
 │   MAX30102  ──I2C(21/22)──┐                                          │
 │   MPU6050   ──I2C(21/22)──┤                                          │
 │   AD8232    ──ADC1 GPIO34─┤   firmware = a DUMB RECORDER             │
 │   GSR       ──ADC1 GPIO35─┤   reads sensors, writes rows,            │
 │   LO+/LO−   ──GPIO 32/33──┘   computes NOTHING                       │
 │                                                                      │
 └────────────────────────────────┬─────────────────────────────────────┘
                                  │  14 fields @ 100 Hz
                                  │  t_ms, ppg_ir, ppg_red, ax..az,
                                  │  gx..gz, gsr_raw, ecg_raw,
                                  │  lead_off, btn, label
                                  ▼
                    ┌─────────────────────────────┐
                    │   schema.py  — the contract  │
                    │   ONE definition of a record │
                    └──────────────┬───────────────┘
                                   ▼
                    ┌─────────────────────────────┐
                    │   replay.py                  │
                    │   10-second windows, 1s hop  │
                    │   THE UNIT OF ANALYSIS       │
                    └──────────────┬───────────────┘
                                   ▼
        ╔══════════════════════════════════════════════════════╗
        ║  LAYER 1 — SIGNAL QUALITY GATE     gate.py+quality.py ║
        ║                                                      ║
        ║   measures: |skewness|, perfusion, motion,           ║
        ║             ADC rails, flatline, lead_off (hardware) ║
        ║                                                      ║
        ║   concludes:  TRUSTED  /  DEGRADED  /  UNSCORED      ║
        ╚══════════════════════════┬═══════════════════════════╝
                                   │  Verdict
                    UNSCORED ──────┼──────► ✋ STOP. No number
                                   │           ever leaves here.
                                   ▼
        ╔══════════════════════════════════════════════════════╗
        ║  LAYER 2 — HEART RATE                          hr.py ║
        ║                                                      ║
        ║   TWO estimators that fail DIFFERENTLY:              ║
        ║     spectral  — dominant frequency (robust)          ║
        ║     peaks     — count upstrokes (gives RR/HRV)       ║
        ║                                                      ║
        ║   disagree by >10 bpm  ──►  no number at all         ║
        ╚══════════════════════════┬═══════════════════════════╝
                                   │  Estimate
                                   ▼
        ╔══════════════════════════════════════════════════════╗
        ║  LAYER 3 — PERSONAL BASELINE             baseline.py ║
        ║                                                      ║
        ║   learns YOUR resting HR    (centre)                 ║
        ║   learns YOUR variability   (spread)  ◄── the point  ║
        ║   learns YOUR tonic GSR                              ║
        ║                                                      ║
        ║   only TRUSTED + agreeing + still windows may teach  ║
        ║   refuses to report until 60s of unique coverage     ║
        ╚══════════════════════════┬═══════════════════════════╝
                                   │  Deviation (in σ, not bpm)
                                   ▼
        ╔══════════════════════════════════════════════════════╗
        ║  LAYER 4 — SEVERITY SCORER                 scorer.py ║
        ║                                                      ║
        ║        is HR elevated? (> 2σ of YOUR normal)         ║
        ║               │                                      ║
        ║        no ────┴──── yes                              ║
        ║        │             │                               ║
        ║     NORMAL      moving? ──yes──► EXERTION (no alarm)  ║
        ║                      │                               ║
        ║                     no                               ║
        ║                      │                               ║
        ║                 GSR up? ──yes──► AROUSAL  (notice)   ║
        ║                      │                               ║
        ║                     no                               ║
        ║                      ▼                               ║
        ║                UNEXPLAINED  ──► ALERT ◄── the product ║
        ╚══════════════════════════┬═══════════════════════════╝
                                   │  Score
                                   ▼
                    ┌─────────────────────────────┐
                    │  SustainedScorer             │
                    │  3 consecutive windows must  │
                    │  agree before a full alarm   │
                    └──────────────┬───────────────┘
                                   ▼
                   OLED + buzzer  │  live dashboard
```

---

## Why the order matters

**Motion is tested before skin conductance.** Exercise raises skin conductance
too — you sweat. Test arousal first and every workout is labelled "stress",
which is exactly the false alarm that teaches someone to ignore the device.

**Quality is tested before everything.** A bad reading that reaches the scorer
becomes a confident wrong answer, which is worse than no answer.

---

## The chain is enforced by types, not discipline

```
    assess(window)            ─►  Verdict
    baseline.deviation(est, VERDICT)   ◄── verdict is REQUIRED
    score(DEVIATION, ...)              ◄── deviation is REQUIRED
```

You **cannot** compute a severity for a reading that failed quality, because
`score()` needs a `Deviation`, which only `PersonalBaseline` produces, which
requires a gate `Verdict`.

This is not decoration. An earlier version took a bare float, and within an
hour of writing the gate we fed it an UNSCORED window and got back a confident
**"217 bpm, +51.5σ"**. The gate was working perfectly; the number simply walked
around it. A rule you have to remember is a rule you forget.

---

## Why everything is in σ and never in bpm

This is the whole product in one line.

```
    Person A: resting 65, wanders ±8    +12 bpm = +1.5σ  = noise
    Person B: resting 65, steady ±2     +12 bpm = +6.0σ  = event
```

A threshold in bpm is a fixed threshold wearing a disguise. Same for skin
conductance — absolute EDA varies by an order of magnitude between two healthy
people, so a fixed "GSR above X" rule repeats the exact mistake one signal
further along.

---

## The other two channels, and the clock that lets them be combined

The diagram above is the *physiology* pipeline, and until 2026-09-03 it was the
whole product. The project is now an interactive pressure test, which means two
more channels — and the moment you have three channels you have a clock problem.

```
   BROWSER (game/index.html)          PYTHON (live.py)           PHONE
   The Gate: 4 options, a clock       the pipeline above         MJPEG stream
        │                                    │                       │
        │ one POST per event,                │ 100 Hz rows           │ ~14 fps
        │ NEVER batched                      │                       │
        ▼                                    ▼                       ▼
   ┌────────────────────────────────────────────────────────────────────┐
   │  bridge.py — the ONLY place the three meet                         │
   │                                                                    │
   │  · serves the game (so it is same-origin, not file://)             │
   │  · stamps every browser event with the DEVICE clock on arrival     │
   │  · CameraRunner is handed a `stamp` and a `sink` — it never         │
   │    becomes a second source of truth about time                     │
   │  · /state  — live personal_sigma, so the door reads the real body   │
   │  · /report — every act vs the person's OWN practice round           │
   └────────────────────────────────┬───────────────────────────────────┘
                                    ▼
                        one timeline, or an honest refusal
```

### The clock is measured, never assumed

The browser runs on `performance.now()`; the device runs on `millis()`. Every
event therefore carries **both** clocks, and the audit reports the measured
offset spread. A session that cannot demonstrate agreement is reported as **not
alignable**, and the report is then forbidden from laying the channels on one
timeline.

This is not defensive decoration — it caught its own first bug. Stamping once
per 1 s analysis hop produced an 882 ms spread that was pure quantisation, not
clock disagreement. The clock now ticks per sample; measured spread is ~25 ms.

**The one rule that keeps it honest: events are POSTed one at a time, as they
happen.** If the browser ever batches, arrival time stops meaning event time and
every alignment silently gains the batch interval as error.

### Two walls, both enforced by tests

**1. Behaviour and camera never reach the scorer.** The test gets harder over
time *by construction*, so any behaviour signal drifts by construction. A model
given both would learn "slow answers = stress" and score **the clock** while
appearing to score the body. A test fails if either module exports a feature.

**2. No metric may name a feeling.** Facial emotion recognition was proposed and
rejected — the facial-movement→emotion mapping is not consistent across people
or contexts, and a contested black box inside an honesty-first system hands a
judge the question that ends the pitch. A test scans every metric description
for feeling-words. `mouth_width_ratio` is a distance; "smiling" is an inference.

### The third wall: the model may navigate, and may not conclude

`model.py` is the learned channel, and it ships — `models/arousal_v1.joblib`,
trained by `train_model.py` on 5,715 WESAD windows from 15 subjects. It answers
one question, `p_arousal(window) -> float | None`, and it is structurally unable
to answer any other:

- it returns a probability and one word — agree, disagree, or no opinion — about
  what the rules already concluded. There is no code path in it that produces a
  `Severity` or a `Context`.
- **nothing in the deterministic path imports it.** A test parses the imports of
  `scorer.py`, `gate.py`, `baseline.py` and `hr.py` and fails if `model` appears.
- it **refuses** rather than guesses: a NaN, a short vector, or an artifact whose
  feature order does not match `FEATURE_NAMES` returns `None` or raises at load.
  `0.0` would read downstream as *confidently calm*, which is a lie about a
  missing input.
- `live.py` degrades to rules-only if the artifact is missing. The product works
  with no model at all, which is the honest test of the claim that it decides
  nothing.

That is what buys the freedom to tune it for sensitivity (0.83 mean, threshold
0.27) rather than for alarm rate: **a channel that cannot alarm cannot false-alarm
at the wearer.** Its disagreements are counted per act in `/report`, which is the
navigation — a window where the rules and the model differ is a window worth a
human looking at.

### Everything is compared to the person's own practice round

Act 1 of the game is untimed and unscored, and exists solely to be the reference
— the same role `baseline.py` plays for heart rate, one layer up. Without it
every behaviour metric would need a population threshold, which is the
cross-person scoring this project refuses. No practice round means no
deviations and a stated reason, never a fallback to fixed numbers.

⚠ **A floored spread is not a z-score.** If the practice round is too uniform to
measure spread, the baseline uses a floor — and the resulting sigma is then a
*lower bound on how unusual something was*, not a calibrated score. The report
says which, and prints "beyond practice" rather than a number it did not earn.

---

## What is validated, and what is not

| claim | evidence | status |
|---|---|---|
| HR accuracy 3.22 bpm MAE | WESAD, 7 subjects, 2517 windows, vs ECG | real, but the ECG reference is our own unvalidated detector |
| gate false-confirm 0/306 | our own synthetic data | **circular — we wrote the data and the detector** |
| scorer false alarms 1.15% | WESAD, 5 subjects | **≈133 alarms/simulated day — unusable per-window.** A 60 s sustain rule closes this to **2.3/day at unchanged sensitivity**. The fix was never a better model. |
| stress accuracy collapse | WESAD, 7 subjects | real and consistent |
| EXERTION branch works | synthetic only | **unvalidated — WESAD is a seated study** |
| behaviour + camera on one timeline | measured, live | real — 144 ms spread on a full run, and reported when it fails |
| camera face detection | live, phone, 553 frames | real — 95.7%, after fixing a 90° rotation that had it at 2% |
| rPPG (heart rate from the face) | probed 2026-09-04 | **rejected for now — 90 bpm at a 3.7% spectral peak is barely above noise.** Not shipped, because shipping it would contradict the thesis |
| shipped model (`arousal_v1`) | WESAD, leave-one-subject-out, 14 scorable subjects | real — mean per-subject F1 **0.74**, sensitivity 0.83, specificity 0.938, ROC-AUC 0.962. ⚠ **worst subject: sensitivity 0.45, specificity 0.648.** Threshold and calibrator fitted out-of-fold on this same dataset |
| the model on exertion | none | **it has never seen exercise.** WESAD is seated; that branch is the rules' alone |
| any recording from OUR hardware | none yet | **the I2C bus is still being rewired. Every threshold below is provisional.** |

Read `../README.md` for the numbers and `DECISIONS.md` for why each choice
was made.
