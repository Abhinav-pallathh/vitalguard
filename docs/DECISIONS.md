# Decision ledger

Every non-obvious choice, with the alternative and the reason. Written down so
we do not re-litigate them at 2am, and so we can answer "why did you do it that
way?" on stage without improvising.

| # | Decision | Alternative | Why |
|---|---|---|---|
| D1 | **One sample rate, 100 Hz, all channels** | Per-sensor native rates (ECG 250, GSR 4) | Multi-rate means resampling and alignment, and alignment bugs are *silent*. 100 Hz is enough for R-peak **timing** (not morphology) and harmlessly oversamples GSR. Costs us ECG waveform detail we do not claim. |
| D2 | **Firmware computes nothing** | Compute HR on-device (the MAX30102 library offers it) | Every derived number must be reproducible from the raw recording. If HR is computed on-device we can never re-run an improved algorithm over old data, and we can never test it. |
| D3 | **`lead_off` is logged as a first-class field** | Infer electrode contact from the signal | The AD8232 exposes LO+/LO− in hardware. It is the only quality input we do not have to infer, it is free, and it is *ground truth*. Inferring what the hardware already tells you is how you get it wrong. |
| D4 | **Window = 10 s, hop = 1 s** | Shorter (faster reaction) or longer (stabler) | ~11 beats at rest — enough for a stable HR estimate and for the skewness SQI to be meaningful. Shorter makes quality noisy; longer makes us slow to notice a strap slipping, which is the failure we exist to catch. |
| D5 | **`any_lead_off`, not average** | Fraction of samples detached | A hardware signal saying the electrode came off is not something to average away. One detached sample poisons the window. |
| D6 | **Skewness SQI (SSQI) as the primary quality index** | Hand-tuned amplitude threshold | Published as the *optimal* PPG SQI of eight tested, and explicitly characterised as low-computation and real-time suitable. Turns "we picked a threshold" into a citation. |
| D7 | **WESAD for algorithm shape, never for deployed weights** | Train on WESAD, ship the model | WESAD's wrist BVP is an Empatica E4 at 64 Hz. Ours is a MAX30102 at 100 Hz (the ear clip was dropped 2026-09-02; placement is still open). That is textbook sensor domain shift and it **fails silently**. Same mistake as training on UCI HAR and deploying to an MPU6050. |
| D8 | **Rule-based severity scorer ships, even though the model beat it** | Ship the classifier | ⚠ **Updated 2026-09-04 — the original wording said "ML only if it beats it", and it did:** HistGradientBoosting beats the rules leave-one-subject-out, F1 **0.67 vs 0.41**. We still ship the rules, and the reason changed from "the model isn't better" to an explicit trade: the rules are explainable on stage, debuggable at 2am, and cannot fail in a way we can't narrate. The model's win is quoted as *evidence that we chose the weaker model knowingly*, which is a stronger claim than pretending it lost. See D15. |
| D9 | **AD8232 is a reference instrument, not a shown feature** | Display ECG as a fourth vital | It is not in the submitted pitch. Used as reference, it validates our PPG heart rate with a real number and a denominator. Shown as a feature, it diverges from what was approved. |
| D10 | **`unknown` is not a training class** | Treat unlabelled as `rest` | Unlabelled means nobody pressed the button. It does not mean the subject was resting. Conflating the two poisons the baseline model with whatever was actually happening. |

| D11 | **Behaviour and camera never feed the scorer** | Fuse all channels into one model | The test gets harder over time *by construction*, so any behaviour signal drifts by construction. A model given both learns "slow answers = stress" and scores **the clock** while appearing to score the body — invisibly, and it would look like a better model. Enforced by a test that fails if either module exports a feature. |
| D12 | **Facial emotion recognition rejected; the camera reports geometry only** | Read expressions with a FER model (Sujan's proposal, 2026-09-03) | The facial-movement→emotion mapping is not consistent across people or contexts (Barrett et al. 2019). Shipping a contested black box inside a system whose thesis is *refuse to show a number you don't trust* hands a judge one question that ends the pitch: "why does your honesty layer apply to the finger but not the face?" The camera keeps two honest jobs: observable geometry, and rPPG as an independent estimator. A test scans every metric phrasing for feeling-words. |
| D13 | **Both clocks travel with every event; alignment is measured** | Assume the two clocks agree, or align by wall time | The game runs on `performance.now()`, the device on `millis()`. Assuming agreement makes the entire report a guess with no way to notice. Carrying both means a session can *prove* it is alignable, or say it is not. It caught its own first bug: an 882 ms spread that was our own 1 s stamping quantisation, not clock drift. |
| D14 | **The device clock is a stamp, never a frame rate** | Derive camera fps from the stamps we already have | Discovered by running it: frames genuinely 70 ms apart can land 1 ms apart in device time, and distance/dt then produced **42,058 px/s** off a seated person. Pairs closer than `MIN_DT_S` are dropped and counted, not divided by. |
| D15 | **No pretrained emotion or stress weights, anywhere** | Use an off-the-shelf stress classifier | Every model in this project is trained by us on WESAD, and per D7 only for algorithm *shape*. The only borrowed weights are YuNet, which finds face landmarks and makes no claim about a person's state. |
| D17 | **The model ships as a PROBABILITY and a disagreement flag** | Ship it as a second opinion that can escalate | This makes *"the model navigates, determinism concludes"* literally true instead of aspirational. `model.py` returns `p_arousal` and one of agree / disagree / no opinion; it exposes no severity, and a test asserts `scorer.py`, `gate.py`, `baseline.py` and `hr.py` do not import it. Its false positives therefore never reach the wearer as an alarm — which is what makes it safe to tune the threshold for sensitivity (0.83) rather than for alarm rate. Same pattern as `hr.py`'s two estimators: report the disagreement, never average it away. |
| D16 | **The practice round is the reference; a floored spread says so** | Population thresholds for behaviour metrics | Act 1 is untimed and unscored precisely so it can be the baseline — the same role `baseline.py` plays for heart rate. When the practice round is too uniform to measure spread, the floor kicks in and the sigma becomes a *lower bound*, not a z-score; the report prints "beyond practice" rather than a precision it did not earn. |

## Open questions

- ~~Is the GSR module on the bench?~~ **In hand, 2026-09-04.** One gate left: confirm the module is 3.3 V-native before its VCC touches the rail — this board has no 5 V anywhere.
- ~~Sujan's existing ML/UI code~~ — **discarded by decision, 2026-09-02. Clean slate.**
- **Does the strong "no consumer device does this" claim survive checking?**
  The Apple Watch ECG app returns *inconclusive* on poor contact. The safe and
  still-true version: no consumer device exposes **per-reading, continuous**
  quality to the user *and* personalises its threshold. Still needs verifying.
- **D6 thresholds are provisional.** Fit on synthetic data. MUST be re-fit on
  real recordings before any number is quoted — see `calibrate.py`.
- **We have still never recorded our own body.** The I2C bus is being rewired
  (see `FIRMWARE_CONTRACT.md`). Until then every threshold here is provisional
  and the EXERTION branch has zero real evidence.
- ~~**D8 leaves the model unshipped.**~~ **Closed 2026-09-04 — see D17.**
  `models/arousal_v1.joblib` ships with a card of leave-one-subject-out numbers,
  `live.py` runs it per window, and `/report` counts its agreements and
  disagreements per act. It still concludes nothing. What remains open is that
  its training data is a seated lab study, so **the EXERTION branch is outside
  what this model has ever seen** — one real recording of our own bodies would be
  the first data that is not WESAD.
- **rPPG is parked, not rejected.** 90 bpm at a 3.7% spectral peak is not a
  number we will show. Its value was always *disagreement* with the finger clip,
  which cannot be measured until the finger clip works.
