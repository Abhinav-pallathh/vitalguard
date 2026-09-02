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
| D7 | **WESAD for algorithm shape, never for deployed weights** | Train on WESAD, ship the model | WESAD's wrist BVP is an Empatica E4 at 64 Hz. Ours is a MAX30102 ear clip at 100 Hz. That is textbook sensor domain shift and it **fails silently**. Same mistake as training on UCI HAR and deploying to an MPU6050. |
| D8 | **Rule-based severity scorer first, ML only if it beats it** | Go straight to a classifier | A rule-based scorer is explainable on stage, debuggable, and needs no training data. If a model cannot beat explicit rules on our own recordings, the model is decoration. |
| D9 | **AD8232 is a reference instrument, not a shown feature** | Display ECG as a fourth vital | It is not in the submitted pitch. Used as reference, it validates our PPG heart rate with a real number and a denominator. Shown as a feature, it diverges from what was approved. |
| D10 | **`unknown` is not a training class** | Treat unlabelled as `rest` | Unlabelled means nobody pressed the button. It does not mean the subject was resting. Conflating the two poisons the baseline model with whatever was actually happening. |

## Open questions

- ~~Is the GSR module on the bench?~~ **CONFIRMED on the bench, 2026-09-02.**
- ~~Sujan's existing ML/UI code~~ — **discarded by decision, 2026-09-02. Clean slate.**
- **Does the strong "no consumer device does this" claim survive checking?**
  The Apple Watch ECG app returns *inconclusive* on poor contact. The safe and
  still-true version: no consumer device exposes **per-reading, continuous**
  quality to the user *and* personalises its threshold. Still needs verifying.
- **D6 thresholds are provisional.** Fit on synthetic data. MUST be re-fit on
  real recordings before any number is quoted — see `calibrate.py`.
