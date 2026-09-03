# VitalGuard — The Gate

**A composure trainer that refuses to lie to you.**
Team Munix · Recursion Edition II

You sit a short, timed, deliberately grim test while a wrist-worn ESP32 records
your body. Afterwards it tells you what your body did while you decided — and
how fast you came back down — measured against *your own* resting baseline,
never against anyone else's.

> ### The thesis
> **The model navigates. Determinism concludes.**
>
> Two refusals hold the whole system together:
> 1. **Never show a number we don't trust.** A degraded signal is reported as
>    `UNSCORED` — not the last good value, not an estimate, not a graceful
>    degradation into a plausible lie.
> 2. **Never alarm when we know why.** An elevated heart rate with motion is
>    exercise, and saying so is more useful than an alert.

---

## Why this exists

Every consumer wearable can tell you your heart rate went up. None of them will
tell you when they don't know.

Our headline finding is a base-rate problem the industry's own metrics hide.
Measured on WESAD (leave-one-subject-out, 7 subjects):

| | |
|---|---|
| Per-window specificity | **0.9679** |
| Specificity actually needed for ~3 alarms/day | **0.99974** |
| Gap | **123×** |

A 96.8% specificity sounds excellent and produces **305 false alarms a day**.
Worse, the *better* classifier produced *more* alarms (400/day), because a
per-window metric is not the unit a wearer experiences. A 60-second sustain rule
closes it to **2.3/day at unchanged sensitivity** — the fix was never a better
model.

That is why this is a training tool you opt into for ten minutes, not a monitor
that watches you all day.

---

## Repository structure

```
src/vitalguard/        the library — every number on screen comes through here
  schema.py            the 14-field row contract, shared with the firmware
  quality.py           the signal-quality gate (SSQI, perfusion, flatline)
  gate.py              trusted / degraded / unscored, and why
  hr.py                heart-rate estimation with a two-estimator disagreement rule
  baseline.py          the person's own resting reference, learned not assumed
  features.py          RMSSD, SDNN, pNN50, EDA peaks, gsr_sigma
  scorer.py            NORMAL / EXERTION / AROUSAL / UNEXPLAINED — rules, explainable
  behaviour.py         what you DID while deciding: latency, doubt, switching
  camera.py            observable face geometry from a phone. Never emotion.
  bridge.py            the one place all three channels meet on one clock
  wesad.py, synth.py, replay.py

firmware/src/main.cpp  ESP32 recorder. 100 Hz, dual-core, boot self-test
game/                  "The Gate" — the test itself. Zero dependencies, runs offline
  index.html           the game
  questions.json       the question set
models/                YuNet face detector, vendored (works with no network)
docs/                  ARCHITECTURE · DECISIONS · FIRMWARE_CONTRACT · PITCH
tests/                 153 tests
```

---

## Run it

No hardware needed — the whole system rehearses on synthetic data.

```bash
python -m venv venv && ./venv/bin/pip install -r requirements.txt

# the test suite
PYTHONPATH=src ./venv/bin/python -m pytest -q                # 153 passed

# the full experience: pipeline + game + phone camera
PYTHONPATH=src ./venv/bin/python live.py \
    --synth rest --calibrate-synth rest --bridge --camera <phone-url>
# then open http://127.0.0.1:8765/index.html
```

`PYTHONPATH=src` is required — there is no installed package.

**The phone is the camera.** Any MJPEG source works (we use IP Webcam). Check it
before trusting it:

```bash
./venv/bin/python camcheck.py http://<phone-ip>:8080/video
```

---

## How the three channels stay honest

**Physiology** — PPG, ECG, GSR and motion at 100 Hz from the ESP32.

**Behaviour** — how long before you touched an option, how long you sat on it
before committing (*the doubt window*), whether you went back on yourself.

**Camera** — head motion, tilt, turning away, face distance. Geometry only.

Three rules make those safe to combine:

**1. One clock, and it is audited, never assumed.**
The game runs in a browser on `performance.now()`; the device runs on `millis()`.
Every event carries *both* clocks, and the bridge reports the measured offset
spread. A session that cannot prove alignment says so, and the report is then
forbidden from laying the channels on one timeline.

**2. Behaviour and camera never reach the scorer.**
The test gets harder over time *by construction*, so every behaviour signal
drifts by construction. A model given both would learn "slow answers = stress"
and score **the clock** while appearing to score the body. Enforced by tests.

**3. No metric may name a feeling.**
Facial emotion recognition was proposed and rejected: the mapping from facial
movement to emotion isn't consistent across people or contexts, and a contested
black box inside an honesty-first system hands a judge the question that ends the
pitch. A test scans every metric description for feeling-words and fails on one.
`mouth_width_ratio` is a distance. "Smiling" would be an inference.

---

## What is real, and what is not

This section is the point of the project. We would rather lose marks than
overstate a number.

**Real, measured:**
- HR **3.22 bpm MAE** vs chest ECG at **88.1% coverage** (WESAD S2–S4, 3,794 windows)
- Learned scorer beats rules leave-one-subject-out (F1 **0.67** vs **0.41**)
- `gsr_sigma` is the top feature; 90/100 stress windows attributed AROUSAL
- 60 s sustain: 305 → 2.3 alarms/day at unchanged sensitivity
- Phone camera: 95.7% face detection over 553 frames, clocks aligned to 144 ms

**Not real yet, and we will say so on stage:**
- **No recording from our own hardware.** The I2C bus is being rewired; every
  threshold is provisional until we measure a real body.
- **The EXERTION branch is unvalidated.** WESAD is a seated study with no
  exercise condition. It works on synthetic data we wrote ourselves.
- **`EARCLIP_MAX30102` is `None`** on purpose. There is no honest way to guess a
  shipping threshold, and a placeholder would get quoted.
- **rPPG (heart rate from the face) is parked.** It ran, returned 90 bpm, and the
  spectral peak was 3.7% of the band — barely above noise. We are not shipping a
  number we don't trust; that would contradict the entire thesis.
- Episode-level detection rate is not yet measured.

---

## Hardware

ESP32 DOIT 30-pin · MAX30102 (PPG) · AD8232 (ECG) · GSR finger clip ·
MPU6050 (motion) · SSD1306 OLED · buzzer. Single 3.3 V rail, no 5 V anywhere.

Authoritative pin map and wiring order: **`docs/FIRMWARE_CONTRACT.md`**.

The device shows a verdict it did not compute — rows go up the serial line at
230400, the laptop runs the same gate the tests cover, one line comes back and
the OLED paints it. Reimplementing the gate in C would create a second source of
truth for the one decision the product rests on. Stated cost: **untethered, the
device records but cannot score.**

---

## Team

**Team Munix** — Recursion Edition II
Abhinav Pallath · Sujan · Adesh
