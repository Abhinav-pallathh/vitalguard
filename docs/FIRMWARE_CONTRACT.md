# Firmware contract

**Scope change 2026-09-02: Sujan builds the perfboard only. All software,
firmware included, is ours.** This was written as a handoff document; it is now
our own build spec. The content did not need to change — a contract you write
for someone else is exactly the contract you should hold yourself to.

Serial runs at **230400**, not 115200. A 100 Hz row is ~60 bytes = 6 kB/s,
which is 52% of a 115200 line, and the verdict return channel shares it. At
52% utilisation any hiccup drops rows, and a dropped row means the file is not
100 Hz any more.

Toolchain: **PlatformIO** (`platformio.ini` lives in the repo, so the library
versions are pinned and the build is reproducible for both of us).

**The firmware is a dumb recorder.** It reads sensors and emits rows. It computes
no heart rate, does no filtering, does no smoothing, and makes no decisions.
Everything derived happens in Python where it is testable and reproducible.

If you only read one line: **emit these 14 fields, in this order, at 100 Hz.**

---

## Pin map

| Signal | GPIO | Bus / mode | Note |
|---|---|---|---|
| MAX30102 SDA | 21 | I2C | addr `0x57` |
| MAX30102 SCL | 22 | I2C | shared bus |
| MPU6050 | 21 / 22 | I2C | addr `0x68` — no clash |
| OLED SSD1306 | 21 / 22 | I2C | addr `0x3C` — no clash |
| GSR out | **35** | `analogRead` | ADC1, input-only pin. Swapped 2026-09-04 to match the bench. |
| AD8232 OUTPUT | **34** | `analogRead` | ADC1, input-only pin. Swapped 2026-09-04 to match the bench. |
| AD8232 LO+ | 32 | digital in | electrode detached = HIGH |
| AD8232 LO− | 33 | digital in | electrode detached = HIGH |
| Buzzer | 25 | digital / PWM | |
| Label button | 27 | `INPUT_PULLUP` | pressed = LOW |
| SD MOSI / MISO / SCK / CS | 23 / 19 / 18 / 5 | SPI | |

**⚠ The one that will bite you:** both analog sensors MUST be on **ADC1**
(GPIO 32–39). **ADC2 stops working the instant WiFi is enabled** — `analogRead`
returns garbage with no error. On the bench with WiFi off it looks perfect.
GSR on 35 and ECG on 34 are correct (swapped 2026-09-04). Buzzer on 25 and button on 27 are ADC2
pins but used *digitally*, which is unaffected.

Three I2C devices on GPIO 21/22 with the existing 2× 10 kΩ pull-ups is fine.
Run an I2C scanner once and confirm you see `0x57`, `0x68`, `0x3C`.

---

## The row format

Exactly these columns, exactly this order, one row per sample:

```
t_ms,ppg_ir,ppg_red,ax,ay,az,gx,gy,gz,gsr_raw,ecg_raw,lead_off,btn,label
```

| Field | Type | Source |
|---|---|---|
| `t_ms` | int | `millis()` — monotonic. **This is the clock.** Not wall time. |
| `ppg_ir` | int | MAX30102 IR, **raw counts** |
| `ppg_red` | int | MAX30102 red, **raw counts** |
| `ax ay az` | float | accel in **g** |
| `gx gy gz` | float | gyro in **deg/s** |
| `gsr_raw` | int | `analogRead(35)`, 0–4095 |
| `ecg_raw` | int | `analogRead(34)`, 0–4095 |
| `lead_off` | int | `digitalRead(32) \|\| digitalRead(33)` → 0 or 1 |
| `btn` | int | button pressed → 1 |
| `label` | str | `unknown` \| `rest` \| `exercise` \| `stress` |

Write the header line once at the top of each file.

### Sketch

```cpp
const uint32_t PERIOD_US = 10000;   // 100 Hz
uint32_t next = micros();

void loop() {
  while ((int32_t)(micros() - next) < 0) {}   // fixed cadence, no drift
  next += PERIOD_US;

  int lo = digitalRead(LO_PLUS) || digitalRead(LO_MINUS);
  int btn = (digitalRead(BTN) == LOW);

  logfile.printf("%lu,%lu,%lu,%.4f,%.4f,%.4f,%.2f,%.2f,%.2f,%d,%d,%d,%d,%s\n",
    millis(), irValue, redValue, ax, ay, az, gx, gy, gz,
    analogRead(35), analogRead(34), lo, btn, currentLabel);
}
```

**Use `micros()` with a fixed period, not `delay(10)`.** `delay` accumulates
drift from however long the loop body took, and a drifting sample rate silently
corrupts every frequency-domain measurement we make downstream.

---

## Two rules

1. **Never write a row you had to guess at.** If a sensor read fails, write the
   raw failure value — do not carry the previous reading forward. Held-over
   values are indistinguishable from real ones downstream, and "never present a
   stale number as current" is the entire product thesis.
2. **`label` is set by the button, at record time.** During a labelled run, hold
   the button and set `currentLabel`. Post-hoc labelling from memory is guessing.

---

## First deliverable

60 seconds of `rest`, sitting still, ear clip on, electrodes attached.

**Do this on the breadboard. Do not wait for the perfboard.** The breadboard rig
already reads all three sensors; the perfboard is a form-factor upgrade, not a
prerequisite. Everything downstream — the baseline model, the scorer, and every
threshold in `gate.py` — is provisional until a real recording exists, so the
recording is the single highest-value thing on the board right now.

Serial-first is fine for the first capture (`pio device monitor` piped to a
file). SD logging can come after; it is needed for the untethered demo, not for
the first dataset.
