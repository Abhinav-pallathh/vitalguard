# Recursion Edition II — Idea Submission content
**Project: VitalGuard · composure-training framing.** Copy-paste into
`RECURSION_EDITION_II_TEMPLATE .pptx` (9 frames, mapped 1:1 below).

Replaces the monitor-only version of this file (recoverable at commit `cba9396`).
The reframe came from Sujan's brief; the engine underneath it is unchanged.
Every number here is measured and lives in `README.md`. Anything not yet built
says so in the frame that mentions it.

**Deferred on purpose:** the game front-end (`game/index.html`, "The Gate") is
built but is NOT in this pitch. A fixed question ladder is the honest scope for
an idea submission; the game is roadmap (frame 05).

## The one thing this pitch is about
Everyone's body spikes under pressure — that is not the skill. **The skill is
recovery.** And a recovery number is only worth anything if the signal it was
computed from can be trusted, which is why the quality gate is in front of it
rather than bolted on afterwards.

---

# FRAME 01 — Cover

**PROJECT TITLE**
VitalGuard — a composure trainer that can say "I don't know"

**ONE-LINE PITCH**
A composure trainer that scores how fast you recover from pressure — and
refuses to score a signal it doesn't trust.

**TRACK**
[ fill — Edition II track list ]

**TEAM**
Munix
Abhinav Pallath
Sujan
Adesh

---

# FRAME 02 — Problem definition

**01 WHO & HOW OFTEN**
People who perform under a clock and never find out what the pressure actually
did to them — students in timed exams and vivas, candidates in online
assessments and interviews, anyone training for high-pressure work. It happens
every sitting, and the only record afterwards is a score and a vague memory of
feeling rushed.

**02 TODAY'S WORKAROUND**
Self-report after the fact — "I panicked", "I blanked" — recalled by the person
least able to observe themselves at the time. Or a smartwatch stress score: one
number off a fixed population threshold with no task attached to it, so it can
tell you your heart rate rose but not whether your answers got worse while it did.

**03 WHY IT FALLS SHORT**
Self-report is retrospective and unreliable. Wearable stress scores compare you
to a population instead of your own resting baseline, so the same +12 bpm is
noise in one person and a real event in another. And nothing measures
performance and physiology independently at the same time — which is the only
question worth asking: did your accuracy hold while your arousal climbed?

**04 EVIDENCE**
Our own measurements, not a slide. On WESAD (15 subjects, leave-one-subject-out)
heart-rate error is **10.01 bpm MAE under stress vs 2.25 at rest** — 4x worse at
exactly the moment you are trying to measure. And **11.9% of windows are
unreadable**, which every consumer device fills in silently instead of reporting.
A composure score built on those readings without a quality gate is measuring the
sensor, not the person.

---

# FRAME 03 — Proposed solution

**PRODUCT / SYSTEM NAME**
VitalGuard — a stress gym with a scoreboard.

**Plain-language description**
You sit a short, deliberately escalating test while an ESP32 wearable records
pulse, skin conductance and motion at 100 Hz. At the end you get one report:
how hard each question hit you, **how fast you came back to your own baseline**,
and whether your answers held while that was happening. Not a mood detector and
not a lie detector — a training signal that belongs to the person taking it.

**01 WHAT A USER DOES**
1. Strap on the wearable. **60 seconds of rest** — this is where the system
   learns your resting centre and, more importantly, your resting spread.
2. An untimed, unscored **practice question** — because nobody types while
   sitting still, and without it the response-time metrics have no reference.
3. A **fixed 20-question ladder**, 20 seconds each, difficulty rising
   monotonically. Pilot content is ER dispatch / 911 call-taking.
4. Read your report. Nobody else gets it.

**02 WHY IT BEATS THE WORKAROUND**
- It measures **recovery**, not calm — the thing that actually trains.
- Everything is in **your own sigma**, so it works on a person with a resting
  spread of +/-2 bpm and one with +/-8 bpm without a different threshold.
- It reports **coverage**: 88.1% of windows scored at 3.22 bpm MAE, 11.9% named
  as refusals rather than filled in. A claim with a denominator.
- Physiology and behaviour are measured **independently**, which is the only way
  "did accuracy hold while arousal climbed" is a real question and not a
  tautology.

**03 WHAT'S GENUINELY DIFFERENT**
**The refusal is a first-class output, and the refusal rate is published.** This
matters more here than it did for monitoring: a GSR electrode losing contact
mid-question looks *exactly* like a fast recovery. Without a quality gate in
front of it, a composure trainer posts its best scores when the sensor falls
off. We gate first, then score.

---

# FRAME 04 — System architecture

**FIG. 04-A — replace the template boxes with this flow**

```
 MAX30102 (PPG) ┐
 MPU6050 (motion)├─I2C / ADC1─> ESP32 "dumb recorder"   core 1: hard 100 Hz sampler
 GSR (finger)   ┤              computes NOTHING         core 0: SD + serial + OLED
 AD8232 + LO+/- ┘              14 fields @ 100 Hz
                                     │ USB serial 230400
   webcam ─> OpenCV (fidget, gaze)   │            question events
        └──────────────┬─────────────┴──────────────────┘
                       v
            ONE CLOCK: host-arrival time.monotonic() stamped on every
            sample, frame and event the instant it arrives
                       v
   ┌──────────── Python pipeline (the only source of truth) ────────────┐
   │ quality gate  (|skew| SQI, perfusion, motion, rails, lead-off)     │
   │      -> UNSCORED: stop. no number leaves here.                     │
   │ two HR estimators + disagreement check (>10 bpm = no number)       │
   │ features  (RMSSD / SDNN / pNN50 / eda_peaks / gsr_sigma)           │
   │ personal baseline  (your own sigma, from the 60 s rest block)      │
   │ RECOVERY ENGINE: time to return within 1 sigma of resting GSR      │
   └───────────────────────────┬────────────────────────────────────────┘
                               │
        behaviour channel ─────┤ (reported ALONGSIDE, never fed in)
        response latency,      │
        corrections, fidget,   v
        gaze-off          session report  +  OLED / buzzer for the cool-down
```

**01 KEY TECHNICAL DECISION**
**The model navigates; determinism concludes** — and the firmware computes
nothing at all. Every derived number must be reproducible from the raw
recording, so HR is never computed on-device even though the sensor library
offers it. Reimplementing the gate in C would create a second source of truth
for the one decision the product rests on. The honest cost, said on stage rather
than hidden: **untethered, the device records but cannot score.**

Second decision, equally load-bearing: **behaviour metrics never feed the
physiology scorer.** The ladder gets harder over time by construction, so
response times slow over time by construction; a model given both would learn
"slow answers = stress" and score the *clock* while looking like it scored the
body. Enforced by omission, with a test that fails if anyone adds the feature
back.

**02 HARDEST PART TO BUILD**
A quality gate that doesn't cheat.
- Quality metrics must **never** reach the classifier. WESAD shows signal
  quality degrades under stress, so a model given both learns "bad signal =
  stress" and scores the sensor while looking like it scored the body.
- The SQI had to be **absolute** skewness. Signed skew rejected 93% of real
  human data because sensor polarity differs between rigs — it would have looked
  exactly like dead hardware.

**03 HOW YOU KNOW IT WORKS**
- **103 automated tests**, green (`PYTHONPATH=src pytest -q`).
- **Leave-one-subject-out** across all 15 WESAD subjects — every number comes
  from a subject the scorer never saw. Learned model F1 **0.67 vs 0.41** for
  rules, and the rules failed outright on 3 of 15 subjects (sensitivity 0.00), a
  failure the earlier pooled 5-subject figure had hidden completely.
- An **ablation** rather than an assertion: removing the GSR channel multiplies
  false attributions by 1.4x.

---

# FRAME 05 — Feasibility & scope

**01 SCOPE**
**In:** one person at a time; PPG + GSR + accelerometer + webcam behaviour;
10 s windows, 1 s hop; explicit 60 s calibration; a fixed 20-question ER-dispatch
ladder; recovery time and behaviour reported side by side; laptop-tethered
scoring; fully offline.
**Out:** any diagnosis or medical claim; arrhythmia / ECG morphology (the AD8232
is a reference instrument for validating our PPG, not a shown feature); facial
emotion recognition; cloud, accounts, multi-user; on-device inference; and
**any use of this to assess a person on someone else's behalf** — see frame 06.

**Recovery, defined precisely so it can be argued with:** the time from a
question's onset spike until GSR returns to within **1 sigma of that person's own
resting spread**, measured in the 60 s calibration block, and only counted over
windows that passed the quality gate. HR is displayed alongside but does not gate
the event. If the next question starts first, the time is capped at "time until
the next question began" and labelled **did not fully recover** — never silently
implied. Sigma rather than a fixed percentage band for the same reason the rest
of the system uses it: absolute skin conductance varies by an order of magnitude
between two healthy people.

**02 RUNNING COST**
**Zero marginal cost.** No cloud, no API calls, no storage bill — numpy + scipy
on a laptop, and the firmware toolchain is pinned so it builds with the venue
network down. Hardware is a one-off BOM: ESP32 DevKit v1, MAX30102, MPU6050, GSR
module, AD8232, SSD1306 OLED, buzzer, microSD, battery.
[ total BOM cost — fill from receipts, don't estimate ]

**03 RISKS & FALLBACK**
- **The real risk: it has never been worn.** The firmware compiles (46 KB RAM /
  375 KB flash) but is untested on hardware, and every threshold is provisional
  until one real labelled recording exists.
- **Four modules are still to write** — recovery engine, question orchestrator,
  camera module, report. The pipeline they sit on (gate, HR, features, baseline,
  behaviour) is built and tested.
- **Fallback for a live demo:** `live.py` runs the identical pipeline from
  `--synth` or a WESAD replay, so nothing depends on the perfboard surviving
  the day.
- **Duty of care.** This deliberately induces stress in a real person. Failing
  to recover on two consecutive questions triggers a mandatory pause — no new
  question — and the session resumes one difficulty tier down or ends. Reuses
  the recovery definition; it is not a second metric.

**04 AFTER THE HACKATHON**
1. One labelled recording: rest, load, recovery. That single hour refits every
   provisional threshold.
2. Measure whether recovery time itself is stable within a person across
   sessions — the number the whole product rests on, and currently unvalidated.
3. Swap the fixed ladder for the **built biofeedback front-end** (`game/`): rooms
   whose door only opens once arousal drops, so the person trains the recovery
   directly instead of only being scored on it.
4. Move inference on-device so it scores untethered.

---

# FRAME 06 — Impact metrics

**BIG NUMBER**
**11.9%**
of readings a composure score would otherwise be silently built on — we name
them instead of filling them in.

**HOW IT WAS MEASURED**
WESAD, subjects S2–S4, 3,794 windows of 10 s at 1 s hop. Each window passes the
quality gate before any heart rate is computed. **88.1% were scored, at 3.22 bpm
MAE against chest ECG; 11.9% were refused**, all of them "pulse waveform
destroyed by artifact". Every consumer wearable answers 100% by construction, so
its accuracy figure is quoted over the readings it chose to keep and has no
denominator.

**01 WHAT MEASURABLY IMPROVES**
- A recovery number that comes with the share of the session it was computed
  over — instead of a stress score with no denominator at all.
- Personal-baseline scoring: the same +12 bpm reads as noise for one person and
  an event for another, which a fixed population threshold cannot do.
- Measurement quality where it matters most: HR error is **10.01 bpm MAE under
  stress vs 2.25 at rest**, so the readings a naive composure score would trust
  most are the ones that are worst.

**02 WHO ELSE COULD USE IT**
Interview and viva preparation; public speaking and performance training;
anxiety self-tracking, where "elevated, and here's how fast you came back" is
the whole reassurance. More broadly the **coverage ledger** transfers to any
continuous-sensing system that reports accuracy without a denominator.

**03 WHAT COULD GO WRONG / BE MISUSED**
- **Someone using it to assess another person** — screening, hiring, "is this
  candidate composed." This is the serious one. Beta blockers flatten the
  physiological response entirely, and arousal is not performance.
- **Mistaken for a medical device.** It is not one and cannot diagnose.
- **Recovery time may not be a stable trait.** We have not yet shown it is
  repeatable within one person across sessions.
- Accuracy collapses under stress (10.01 vs 2.25 bpm MAE) and the gate does not
  yet detect that specific degradation.

**04 HOW YOU'D HANDLE THAT**
- **The report belongs to the person who sat the test, and there is no export
  path to a third party.** There is also no cross-person score to rank anyone
  with — every number is in that person's own sigma by construction.
- Non-diagnostic labelling, no clinical claim anywhere in the product.
- Report repeatability before recovery time is presented as a trait rather than
  a session measurement.
- Better-perfused sensor placement, plus forcing DEGRADED when the
  estimator-agreement check drops (it falls to 29% under stress — the system
  already sees the problem it cannot yet fully gate).

---

# FRAME 07 — References & attribution

| TYPE | SOURCE | WHAT IT GAVE YOU | BORROWED / YOURS | LICENCE |
|---|---|---|---|---|
| Dataset | Schmidt, Reiss, Duerichen, Marberger, Van Laerhoven, "Introducing WESAD, a Multimodal Dataset for Wearable Stress and Affect Detection," ICMI 2018 | All evaluation data: PPG, chest ECG reference, stress/amusement/meditation labels, 15 subjects | Borrowed | Academic/non-commercial — [ verify exact terms on the dataset page ] |
| Paper | Elgendi, "Optimal Signal Quality Index for Photoplethysmogram Signals," Bioengineering, 2016 | The skewness SQI (SSQI) used as our primary quality index | Borrowed (method) | CC BY 4.0 — [ verify ] |
| Paper | Barrett, Adolphs, Marsella, Martinez, Pollak, "Emotional Expressions Reconsidered," Psychological Science in the Public Interest, 2019 | The evidence on which we rejected facial emotion recognition | Borrowed (argument) | — |
| Repository | SparkFun MAX3010x Pulse and Proximity Sensor Library v1.1.2 | MAX30102 sensor driver | Borrowed | MIT |
| Repository | Adafruit MPU6050 / SSD1306 / GFX / BusIO / Unified Sensor | IMU + OLED drivers | Borrowed | BSD / MIT |
| Documentation | Espressif Arduino-ESP32 core (platform espressif32@6.9.0) | Dual-core task pinning, ADC1-vs-ADC2 constraint | Borrowed | LGPL / Apache 2.0 |
| Library | NumPy, SciPy | Signal processing and numerics | Borrowed | BSD-3-Clause |
| Library | OpenCV | Frame capture and face detection for the camera behaviour channel | Borrowed | Apache 2.0 |
| Model weights | YuNet `face_detection_yunet_2023mar.onnx` (OpenCV Zoo) | Face landmarks for head motion / tilt / turn. **Geometry only — never emotion.** | Borrowed | MIT |
| Model weights | No *emotion* or *stress* model weights used | — | — | — |
| Code | All of `src/vitalguard/`, `firmware/src/main.cpp`, tests, evaluation scripts | Quality gate, HR estimation, personal baseline, behaviour + camera channels, the bridge, firmware, 153 tests | **Ours** | — |

> No pretrained model weights are used anywhere. The scorer is trained by us on
> WESAD features, and per decision D7 WESAD is used for algorithm *shape* only —
> never as deployed weights, because the sensor domain shift from an Empatica E4
> at 64 Hz to a MAX30102 at 100 Hz fails silently.

---

# FRAME 08 — Extra slide

- One screenshot of `live.py` mid-run showing an **UNSCORED** verdict beside a
  normal one — the refusal is the product, so show the refusal.
- The coverage table (88.1% scored / 11.9% refused) — the one slide judges
  remember.
- Demo with no hardware and no network:
  `live.py --calibrate-synth rest --synth unexplained`
- **GitHub: https://github.com/Abhinav-pallathh/vitalguard** (public).
  Resolved 2026-09-04 — `WESAD.zip` and an `S2.pkl` had been committed into
  history and GitHub hard-rejects blobs over 100 MB, so `git filter-repo` took
  `.git` from 2.6 GB to 1.4 MB with all 22 commits and their dates intact.

---

# FRAME 09 — Team & contact

[ Team name: Munix ]

| NAME | PHONE | EMAIL |
|---|---|---|
| Abhinav Pallath | [ ] | abhinavpallath14@gmail.com |
| Sujan | [ ] | [ ] |
| Adesh | [ ] | [ ] |

---

## Blanks only you can fill
1. **Track** (frame 01).
2. **BOM total** (frame 05) — from receipts, not an estimate.
3. **Team members + contacts** (frames 01, 09).
4. **Whether a public repo URL is required** (frame 08) — decides whether
   `git filter-repo` has to happen.
5. **Whether the track requires a cloud/AWS component** — the template's
   architecture placeholder says "MODEL / AWS SERVICE" and this system is
   deliberately 100% local. Defensible (works with the venue network down, no
   per-reading cost, no physiological data leaves the room), but worth knowing.

## Where this deviates from Sujan's brief, and why
- **Quality gate kept in front of the recovery engine.** His brief has no signal
  quality layer; a GSR electrode losing contact reads as a perfect recovery.
- **Recovery band in personal sigma, not a fixed ±12%.** A percentage is a fixed
  threshold in disguise, which is the mistake the baseline module exists to
  prevent.
- **100 Hz / 14-field serial kept** (his brief specifies 50 Hz / 5 fields). At
  50 Hz an RR interval quantises to ±20 ms and RMSSD is typically 20–50 ms.
- **OpenCV Haar instead of MediaPipe** — already cached locally; MediaPipe is
  ~150 MB with dependencies and buys nothing for fidget and gaze.
- **MPU6050 kept, not dropped.** Already on the I2C bus and in the firmware, and
  it is what separates "GSR rose from stress" from "GSR rose from moving".
- **Institutional / pilots-surgeons-traders framing dropped.** Kept ER dispatch
  as scenario *content*; the user and the owner of the report is the individual.
- **Adopted from his brief unchanged:** composure framing, recovery as the
  headline metric, the fixed non-adaptive ladder, the ER-dispatch pilot vertical,
  the safety cool-down, and the one-clock host-timestamp rule — which also
  happens to fix our open behaviour/physiology timeline problem.
