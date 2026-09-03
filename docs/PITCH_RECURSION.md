# Recursion Edition II — Idea Submission content
**Project: VitalGuard · no game, monitor version only.** Copy-paste into
`RECURSION_EDITION_II_TEMPLATE .pptx`. Every number below is measured and lives
in `README.md`; nothing here is estimated except where it says so.

## The one thing this pitch is about
Continuous monitors have a **base-rate problem**. 96.8% specificity sounds
excellent and is 123x short of usable. VitalGuard is built around two refusals —
**don't show a number you don't trust, don't alarm when you already know why** —
and it is the only version of this that can report its own coverage.

---

# FRAME 01 — Cover

**PROJECT TITLE**
VitalGuard

**ONE-LINE PITCH**
A wearable vitals monitor that refuses: it won't show a heart rate it doesn't
trust, and it won't alarm when it already knows why you're elevated.

**TRACK**
[ fill — I don't know Edition II's track list ]

**TEAM**
BioForge
Abhinav Pallath
Sujan
[ others / blank ]

---

# FRAME 02 — Problem definition

**Header line (2-3 lines, readable outside the track)**
People wearing continuous heart monitors are told a number every second of the
day, and never told when that number is garbage. So the alarms get ignored, and
the ones that mattered get ignored with them.

**01 WHO & HOW OFTEN**
Anyone on continuous monitoring — cardiac and post-op patients at home, elderly
living alone, and ordinary smartwatch wearers. Continuous means the failure is
continuous too: every 10 seconds, all day, for years.

**02 TODAY'S WORKAROUND**
Consumer wearables and ward monitors both do the same two things: show a number
100% of the time, and alarm on a fixed population threshold ("high heart rate
while inactive"). If the sensor slips, they show the last plausible value
instead of admitting the gap.

**03 WHY IT FALLS SHORT**
Two failures, both about refusing.
- **They never say "I don't know."** A device that always answers has no
  denominator — its accuracy figure is quoted over the readings it chose to keep.
- **They don't know why.** A 130 bpm from a staircase and a 130 bpm from nothing
  look identical, so the device alarms on both. Users mute it, and the alarm that
  mattered is muted too.

**04 EVIDENCE**
- We measured it on ourselves, not from a slide. Our own learned scorer, run
  out-of-fold across all 15 WESAD subjects, produced **305 alarms per 16-hour
  day** at 96.8% per-window specificity. The better classifier produced *more*
  alarms (400/day) than the rule-based one.
- The arithmetic: **0.9679 specificity achieved vs 0.99974 needed** for ~3
  alarms/day. **A 123x gap** that per-window metrics hide completely.
- And the reading is worst when it matters: on windows that *passed* our quality
  gate, heart-rate error under stress is **10.01 bpm MAE vs 2.25 at rest** —
  4x worse, because peripheral vasoconstriction wrecks wrist PPG during exactly
  the event you're monitoring for.

---

# FRAME 03 — Proposed solution

**PRODUCT / SYSTEM NAME**
VitalGuard — an ESP32 wearable with a quality gate that is allowed to say no.

**Plain-language description**
It records pulse, motion and skin conductance at 100 Hz, learns *your* resting
baseline instead of a population threshold, and returns one of five honest
outputs: **NORMAL**, **EXERTION** (elevated, explained by movement), **AROUSAL**
(elevated, explained by stress response), **UNEXPLAINED** (the alarm — elevated
with no explanation), or **UNSCORED** (we do not trust this signal, here is what
to go and check).

**01 WHAT A USER DOES**
Strap it on. Sit still for a 5-minute calibration — this is separate and
deliberate, because a device that learns from whatever it sees first will decide
111 bpm is your resting rate if you put it on mid-episode. Then wear it. The OLED
shows the current verdict; the buzzer fires for UNEXPLAINED only.

**02 WHY IT BEATS THE WORKAROUND**
- It reports **coverage**: 88.1% of windows got a number, 11.9% were refused —
  at 3.22 bpm MAE. A claim with a denominator.
- It attributes instead of just triggering: of 100 detected stress windows,
  **90 were correctly labelled AROUSAL rather than UNEXPLAINED** — without the
  GSR channel all 100 would have fired as unexplained alarms.
- Requiring 60 seconds of continuous evidence cut alarms from **305/day to
  2.3/day** with no loss of per-window sensitivity.

**03 WHAT'S GENUINELY DIFFERENT**
**The refusal is a first-class output, and the refusal rate is published.**
Every wearable answers 100% by construction, so nobody ever asks the question.
Publishing coverage also makes us falsifiable in the other direction: a gate
quietly tuned until it refuses everything would post a perfect error rate and
get caught in one line of the same table.

---

# FRAME 04 — System architecture

**FIG. 04-A — replace the template boxes with this flow**

```
MAX30102 (PPG) ┐
MPU6050 (accel)├─I2C/ADC1─> ESP32  "dumb recorder"       core 1: hard 100 Hz sampler
GSR            ┤            computes NOTHING            core 0: SD + serial + OLED
AD8232 + LO+/- ┘            14 fields @ 100 Hz
                                   │  serial 230400
                                   v
        ┌──────── Python pipeline (the only source of truth) ────────┐
        │ quality gate (SSQI + perfusion + accel + lead-off)         │
        │      -> UNSCORED, or:                                      │
        │ two HR estimators + disagreement check                     │
        │ features (RMSSD/SDNN/pNN50/eda_peaks/gsr_sigma)            │
        │ personal baseline (your own sigma, not a population)       │
        │ learned scorer -> 60 s sustain policy                      │
        └──────────────────────┬─────────────────────────────────────┘
                               │  one verdict line back down
                               v
                    OLED + buzzer  (verdict expires after 3 s)
```

**01 KEY TECHNICAL DECISION**
**The model navigates; determinism concludes** — and the firmware computes
nothing at all. Every derived number must be reproducible from the raw
recording, so HR is never computed on-device even though the sensor library
offers it. Reimplementing the gate in C would create a second source of truth
for the one decision the product rests on, with no tests to keep it honest.
The honest cost, said on stage rather than hidden: **untethered, the device
records but cannot score.**

**02 HARDEST PART TO BUILD**
A quality gate that doesn't cheat. Two things nearly broke it:
- Quality metrics must **never** be fed to the classifier. WESAD shows signal
  quality degrades under stress, so a model given both learns "bad signal =
  stress" and scores the sensor while looking like it scored the body. Enforced
  by omission, with a test that fails if anyone adds the feature back.
- The SQI had to be **absolute** skewness. Signed skew rejected 93% of real
  human data, because sensor polarity differs between rigs — it would have
  looked exactly like dead hardware.

**03 HOW YOU KNOW IT WORKS**
- **103 automated tests**, green (`PYTHONPATH=src pytest -q`).
- **Leave-one-subject-out** evaluation on all 15 WESAD subjects — every number
  comes from a subject the scorer never saw. Learned model F1 **0.67 vs 0.41**
  for rules, and the rules failed outright on 3 of 15 subjects (sensitivity
  0.00) — a failure the earlier pooled 5-subject figure had completely hidden.
- An **ablation** on the third sensor rather than an assertion: removing GSR
  multiplies false alarms by 1.4x. Reported as measured, not framed up.

---

# FRAME 05 — Feasibility & scope

**01 SCOPE**
**In:** one wearer at a time; PPG + accelerometer + GSR; 10 s windows, 1 s hop;
five verdicts; personal baseline via explicit calibration; laptop-tethered
scoring; fully offline.
**Out:** any diagnosis or medical claim; arrhythmia/ECG morphology (the AD8232 is
a reference instrument for validating our PPG, not a shown feature); cloud,
accounts, multi-user; on-device inference (Phase 4); and **any screening or
assessment use of a person by someone else.**

**02 RUNNING COST**
**Zero marginal cost.** No cloud, no API calls, no storage bill — the whole
pipeline is numpy + scipy on a laptop, and the firmware toolchain is pinned so
it builds with the venue network down. Hardware is a one-off BOM:
ESP32 DevKit v1, MAX30102, MPU6050, GSR module, AD8232, SSD1306 OLED, buzzer,
microSD, battery. [ total BOM cost — fill from your actual receipts, don't
estimate it ]

**03 RISKS & FALLBACK**
- **The real risk: it has never been worn.** The firmware compiles (46 KB RAM /
  375 KB flash) but is untested on hardware, and every threshold is provisional
  until one real labelled recording exists.
- **Fallback for the demo:** `live.py` runs the identical pipeline from
  `--synth` or from a WESAD replay, so the demo does not depend on the
  perfboard surviving the day.
- The EXERTION branch has no real evidence — WESAD is a seated lab study with
  motion of 0.004-0.022 g in every condition. We say so rather than claim it.

**04 AFTER THE HACKATHON**
1. One labelled recording: 5 min rest + 2 min walking. That single hour closes
   the EXERTION branch and refits every provisional threshold.
2. Measure **episode-level** detection rate — the number we're currently missing.
3. Move inference on-device so it scores untethered.
4. [ optional, only if you want the hook: turn the calibration protocol into an
   interactive stress test, so the device reports your *response* and recovery
   slope rather than a resting number. ]

---

# FRAME 06 — Impact metrics

**BIG NUMBER**
**99.2%**
fewer false alarms per day — 305 down to 2.3 — with no loss of per-window
sensitivity.

**HOW IT WAS MEASURED**
All 15 WESAD subjects, leave-one-subject-out, so every window is scored by a
model that never saw that person. An alarm = the scorer calling UNEXPLAINED.
Per-window alarms were scaled to a 16-hour waking day: **305/day** for rules,
**400/day** for the better learned model. Requiring 60 seconds of *continuous*
evidence before firing drops it to **2.3/day** while per-window sensitivity
stays at 0.68 — real physiological episodes last minutes, false positives are
isolated, so duration costs almost nothing.

**01 WHAT MEASURABLY IMPROVES**
- Alarms per day: **305 -> 2.3** (20x+ reduction).
- Alarms that come with a cause attached: **90 of 100** detected stress windows
  labelled AROUSAL rather than an unexplained alert.
- Readings the user can trust: **88.1% coverage at 3.22 bpm MAE**, with the
  other 11.9% named as refusals instead of quietly filled in.

**02 WHO ELSE COULD USE IT**
Elderly and post-op home monitoring; anxiety and panic self-tracking (knowing
"elevated, explained" is the whole reassurance); athletes tracking recovery.
More broadly, the **coverage ledger** transfers to any continuous-sensing system
that currently reports accuracy without a denominator.

**03 WHAT COULD GO WRONG / BE MISUSED**
- **Mistaken for a medical device.** It is not one and cannot diagnose.
- **The 60 s sustain could be silencing real events.** We have measured
  per-window sensitivity, not episode-level detection — at 120 s sustain the
  system fires zero alarms while still reporting 0.68, which proves that column
  does not measure what we need it to.
- **Used to assess someone else** — screening, hiring, "is this person stressed."
  Beta blockers flatten the response entirely and arousal is not performance.
- Accuracy collapses under stress (10.01 vs 2.25 bpm MAE) and the gate does not
  yet detect that specific degradation.

**04 HOW YOU'D HANDLE THAT**
- Non-diagnostic labelling, and no clinical claim anywhere in the product.
- Measure episode-level detection before the sustain policy ships; 60 s is
  currently an operating point chosen on alarm rate alone and is stated as such.
- Personal-baseline-only by design: there is no cross-person score to rank
  people with, and self-knowledge is the only pitched use.
- Better-perfused sensor placement, plus forcing DEGRADED when the
  estimator-agreement check drops (it falls to 29% under stress — it already
  sees the problem it can't yet fully gate).

---

# FRAME 07 — References & attribution

| TYPE | SOURCE | WHAT IT GAVE YOU | BORROWED / YOURS | LICENCE |
|---|---|---|---|---|
| Dataset | Schmidt, Reiss, Duerichen, Marberger, Van Laerhoven, "Introducing WESAD, a Multimodal Dataset for Wearable Stress and Affect Detection," ICMI 2018 | All evaluation data: PPG, chest ECG reference, stress/amusement/meditation labels, 15 subjects | Borrowed | Academic/non-commercial — [ verify the exact terms on the dataset page before submitting ] |
| Paper | Elgendi, "Optimal Signal Quality Index for Photoplethysmogram Signals," Bioengineering, 2016 | The skewness SQI (SSQI) used as our primary quality index | Borrowed (method) | CC BY 4.0 — [ verify ] |
| Repository | SparkFun MAX3010x Pulse and Proximity Sensor Library v1.1.2 | MAX30102 sensor driver | Borrowed | MIT |
| Repository | Adafruit MPU6050 / SSD1306 / GFX / BusIO / Unified Sensor | IMU + OLED drivers | Borrowed | BSD / MIT |
| Documentation | Espressif Arduino-ESP32 core (platform espressif32@6.9.0) | Dual-core task pinning, ADC1-vs-ADC2 constraint | Borrowed | LGPL / Apache 2.0 |
| Library | NumPy, SciPy | Signal processing and numerics | Borrowed | BSD-3-Clause |
| Model weights | None used | — | — | — |
| Code | All of `src/vitalguard/`, `firmware/src/main.cpp`, tests, evaluation scripts | The quality gate, HR estimation, baseline, scorer, sustain policy, firmware, 103 tests | **Ours** | — |

> Note: no pretrained model weights are used anywhere. The scorer is trained by
> us on WESAD features, and per decision D7 WESAD is used for algorithm *shape*
> only — never as deployed weights, because the sensor domain shift from an
> Empatica E4 at 64 Hz to a MAX30102 at 100 Hz fails silently.

---

# FRAME 08 — Extra slide

**Suggested content**
- One screenshot of `live.py` mid-run, showing an UNSCORED verdict next to a
  NORMAL one — the refusal is the product, so show the refusal.
- The coverage table (88.1% / 11.9%) — the one slide judges will remember.
- Demo: `live.py --calibrate-synth rest --synth unexplained` runs with no
  hardware and no network.
- [ GitHub link — **currently not possible.** The repo's `.git` is 2.6 GB
  (WESAD.zip and an S2.pkl are committed into history) and GitHub hard-rejects
  it. If the submission requires a repo URL, say so and we'll run
  `git filter-repo` — it's about 20 minutes. ]

---

# FRAME 09 — Team & contact

[ Team name: BioForge ]

| NAME | PHONE | EMAIL |
|---|---|---|
| Abhinav Pallath | [ ] | abhinavpallath14@gmail.com |
| Sujan | [ ] | [ ] |
| [ ] | [ ] | [ ] |

---

## Blanks only you can fill
1. **Track** (frame 01).
2. **BOM total** (frame 05) — from receipts, not an estimate.
3. **Team members + contacts** (frames 01, 09).
4. **Whether a public repo URL is required** (frame 08) — that decides whether we
   need `git filter-repo` today.
5. **Whether the hackathon expects an AWS/cloud component** — the template's
   architecture placeholder says "MODEL / AWS SERVICE" and our system is
   deliberately 100% local. That's defensible (works with the venue network
   down, no per-reading cost, no health data leaves the device), but if the
   track *requires* a cloud service we need to know now.
