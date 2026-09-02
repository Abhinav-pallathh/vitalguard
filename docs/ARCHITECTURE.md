# VitalGuard — how the whole thing works

Written 2026-09-02 so both of us are looking at the same picture.

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
 │   GSR       ──ADC1 GPIO34─┤   firmware = a DUMB RECORDER             │
 │   AD8232    ──ADC1 GPIO35─┤   reads sensors, writes rows,            │
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

## What is validated, and what is not

| claim | evidence | status |
|---|---|---|
| HR accuracy 3.22 bpm MAE | WESAD, 7 subjects, 2517 windows, vs ECG | real, but the ECG reference is our own unvalidated detector |
| gate false-confirm 0/306 | our own synthetic data | **circular — we wrote the data and the detector** |
| scorer false alarms 1.15% | WESAD, 5 subjects | **≈133 alarms/simulated day. Unusable.** |
| stress accuracy collapse | WESAD, 7 subjects | real and consistent |
| EXERTION branch works | synthetic only | **unvalidated — WESAD is a seated study** |

Read `../README.md` for the numbers and `DECISIONS.md` for why each choice
was made.
