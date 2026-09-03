// VitalGuard recorder firmware -- ESP32 DevKit v1
//
//   The firmware is a dumb recorder. It reads sensors and emits rows.
//   No heart rate, no filtering, no smoothing, no decisions.  (D2)
//
// Two rules from docs/FIRMWARE_CONTRACT.md, enforced structurally here:
//
//   1. NEVER write a row you had to guess at. If a sensor read fails we write
//      the raw failure value. Nothing is carried forward. A held-over value is
//      indistinguishable from a real one downstream, and "never present a stale
//      number as current" is the entire product thesis.
//
//   2. NEVER hide a dropped sample. The sampler runs at a hard 100 Hz on its
//      own core; if the writer cannot keep up, the drop is COUNTED and written
//      into the sidecar .meta file. A recording that silently lost 4% of its
//      samples has a corrupted sample rate, and every frequency-domain number
//      computed from it downstream is wrong with no way to tell.
//
// Architecture: two cores, one job each.
//
//   core 1  sampler  -- fixed 100 Hz cadence, I2C + ADC reads only. Never
//                       touches SD, serial or the display. Nothing here is
//                       allowed to block, because a blocked sampler is a
//                       drifting sample rate.
//   core 0  writer   -- drains the ring buffer, formats CSV, writes SD/serial,
//                       repaints the OLED. Slow and interruptible by design.
//
// A single-loop version was tried on paper first and rejected: a full 128x64
// I2C OLED repaint is ~5 ms at 400 kHz, which is half the 10 ms sample period.
// The display would have silently eaten the sample rate.

#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include <SD.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "MAX30105.h"

// --- pin map (docs/FIRMWARE_CONTRACT.md) ----------------------------------
static const int PIN_SDA      = 21;
static const int PIN_SCL      = 22;
static const int PIN_GSR      = 35;   // ADC1. MUST be ADC1 -- see note below.
static const int PIN_ECG      = 34;   // ADC1.
// 2026-09-04: GSR and ECG SWAPPED to match the bench.
// These were 34/35 the other way round. Sujan's rewire sheet has AD8232 OUT on
// D34, and that is the wiring he bench-proved a clean ~80 bpm QRS through. Both
// pins are ADC1 so the swap is electrically free, and a tested wire beats an
// untested constant. Nothing else changes: gsr_raw still means GSR.
//
// ⚠ These two are the most dangerous constants in the file. Both are analogRead
// on adjacent ADC1 pins, so crossing them raises no error and produces numbers
// that look entirely plausible -- gsr_raw quietly containing ECG. There is no
// runtime check that can catch it. The boot self-test is the only defence:
// touch the ECG leads and watch which channel moves.
static const int PIN_LO_PLUS  = 32;
static const int PIN_LO_MINUS = 33;
static const int PIN_BUZZER   = 25;   // ADC2 pin, used digitally -- unaffected.
static const int PIN_BTN      = 27;   // ADC2 pin, used digitally -- unaffected.
static const int PIN_SD_CS    =  5;

// ⚠ ADC2 stops working the instant WiFi is enabled: analogRead returns garbage
// with no error and it looks perfect on the bench with WiFi off. GSR on 35 and
// ECG on 34 are ADC1 and are therefore safe. WiFi is not enabled in this
// firmware at all, which is the belt to that braces.

// 230400, not 115200. At 100 Hz a row is ~60 bytes = 6 kB/s, which is 52% of
// a 115200 line -- and the verdict channel below shares it. Any hiccup at 52%
// utilisation drops rows, and a dropped row is a corrupted sample rate.
static const uint32_t SERIAL_BAUD = 230400;
static const uint32_t SAMPLE_HZ  = 100;
static const uint32_t PERIOD_US  = 1000000UL / SAMPLE_HZ;

// --- the record ------------------------------------------------------------
// Binary in the ring buffer, formatted only by the writer. Formatting 14 fields
// with printf costs ~200 us; doing that inside the 10 ms sampler would burn 2%
// of the budget for no reason.
struct Row {
  uint32_t t_ms;
  uint32_t ppg_ir, ppg_red;
  float    ax, ay, az;
  float    gx, gy, gz;
  uint16_t gsr_raw, ecg_raw;
  uint8_t  lead_off, btn, label;
};

static const char *LABELS[] = {"unknown", "rest", "exercise", "stress"};
static volatile uint8_t g_label = 0;

// --- single-producer / single-consumer ring ------------------------------
// One writer index touched only by core 1, one reader index touched only by
// core 0. No mutex: a mutex in the sampler is a place the sampler can block.
static const size_t RING_N = 512;          // 5.12 s of slack at 100 Hz
static Row      g_ring[RING_N];
static volatile size_t g_head = 0, g_tail = 0;
static volatile uint32_t g_dropped = 0;    // rule 2: counted, never hidden
static volatile uint32_t g_written = 0;

// --- device presence -------------------------------------------------------
static bool has_ppg = false, has_imu = false, has_oled = false, has_sd = false;

MAX30105        ppg;
Adafruit_MPU6050 imu;
Adafruit_SSD1306 oled(128, 64, &Wire, -1);
File            logfile;
static char     logname[24] = "";

static bool i2c_present(uint8_t addr) {
  Wire.beginTransmission(addr);
  return Wire.endTransmission() == 0;
}

// ---------------------------------------------------------------------------
// core 1 -- the sampler. Hard cadence, no I/O.
// ---------------------------------------------------------------------------
static void sampler_task(void *) {
  // micros() with a fixed period, NOT delay(10). delay accumulates however
  // long the loop body took, and a drifting sample rate silently corrupts
  // every frequency-domain measurement made downstream.
  uint32_t next = micros();
  sensors_event_t a, g, temp;

  for (;;) {
    while ((int32_t)(micros() - next) < 0) { /* spin: this core has one job */ }
    next += PERIOD_US;

    Row r;
    r.t_ms = millis();

    // PPG. If the FIFO has nothing new we write 0 rather than repeating the
    // last sample. 0 reads as a flatline downstream and the quality gate marks
    // the window UNSCORED -- which is the correct and honest outcome. A
    // repeated sample would instead look like a real, very clean signal.
    if (has_ppg) ppg.check();                 // poll FIFO first, then read it
    if (has_ppg && ppg.available()) {
      r.ppg_ir  = ppg.getFIFOIR();
      r.ppg_red = ppg.getFIFORed();
      ppg.nextSample();
    } else {
      r.ppg_ir = r.ppg_red = 0;
    }

    if (has_imu && imu.getEvent(&a, &g, &temp)) {
      r.ax = a.acceleration.x / 9.80665f;   // m/s^2 -> g, schema says g
      r.ay = a.acceleration.y / 9.80665f;
      r.az = a.acceleration.z / 9.80665f;
      r.gx = g.gyro.x * 57.2957795f;        // rad/s -> deg/s, schema says deg/s
      r.gy = g.gyro.y * 57.2957795f;
      r.gz = g.gyro.z * 57.2957795f;
    } else {
      // NaN, not 0. Zero acceleration is a CLAIM -- "the wearer is perfectly
      // still" -- and the severity scorer reads exactly that claim to decide
      // an elevated heart rate is UNEXPLAINED rather than exercise. A missing
      // IMU must never be able to manufacture an alarm.
      r.ax = r.ay = r.az = r.gx = r.gy = r.gz = NAN;
    }

    r.gsr_raw  = analogRead(PIN_GSR);
    r.ecg_raw  = analogRead(PIN_ECG);
    r.lead_off = (digitalRead(PIN_LO_PLUS) || digitalRead(PIN_LO_MINUS)) ? 1 : 0;
    r.btn      = (digitalRead(PIN_BTN) == LOW) ? 1 : 0;
    r.label    = g_label;

    size_t head = g_head, next_head = (head + 1) % RING_N;
    if (next_head == g_tail) {
      g_dropped++;            // writer fell behind. Counted, reported, never hidden.
    } else {
      g_ring[head] = r;
      g_head = next_head;
    }
  }
}

// ---------------------------------------------------------------------------
// core 0 -- the writer. Slow, blocking, interruptible.
// ---------------------------------------------------------------------------
static char g_out[1024];
static size_t g_out_len = 0;

static void flush_out() {
  if (!g_out_len) return;
  if (has_sd) { logfile.write((const uint8_t *)g_out, g_out_len); }
  else        { Serial.write((const uint8_t *)g_out, g_out_len); }
  g_out_len = 0;
}

static void emit(const Row &r) {
  // Exactly the 14 columns of schema.py FIELDS, in exactly that order.
  // If this format string and schema.py ever disagree, read_csv() fails loudly
  // on the header rather than shifting every channel by one.
  g_out_len += snprintf(g_out + g_out_len, sizeof(g_out) - g_out_len,
      "%lu,%lu,%lu,%.4f,%.4f,%.4f,%.2f,%.2f,%.2f,%u,%u,%u,%u,%s\n",
      (unsigned long)r.t_ms, (unsigned long)r.ppg_ir, (unsigned long)r.ppg_red,
      r.ax, r.ay, r.az, r.gx, r.gy, r.gz,
      r.gsr_raw, r.ecg_raw, r.lead_off, r.btn, LABELS[r.label]);
  if (g_out_len > sizeof(g_out) - 160) flush_out();
}

static void write_meta() {
  // The sidecar exists so a recording can never be quoted without its own
  // provenance. Anything read off a recording with drops > 0 has a sample rate
  // that is not 100 Hz, and the analysis has to know that.
  if (!has_sd) return;
  char metaname[28];
  snprintf(metaname, sizeof(metaname), "%.*s.meta", (int)(strlen(logname) - 4), logname);
  File m = SD.open(metaname, FILE_WRITE);
  if (!m) return;
  m.printf("file=%s\n", logname);
  m.printf("sample_rate_hz=%lu\n", (unsigned long)SAMPLE_HZ);
  m.printf("rows_written=%lu\n", (unsigned long)g_written);
  m.printf("samples_dropped=%lu\n", (unsigned long)g_dropped);
  m.printf("ppg=%d imu=%d oled=%d sd=%d\n", has_ppg, has_imu, has_oled, has_sd);
  m.printf("uptime_ms=%lu\n", (unsigned long)millis());
  m.close();
}

// --- the verdict channel ---------------------------------------------------
//
// The device displays a conclusion it did not reach. Rows go up the serial
// line; the laptop runs the SAME gate / hr / baseline / scorer the test suite
// covers; one line comes back:
//
//     V,<trust>,<bpm|-->,<context>,<reason>
//
// This is deliberate, and it is not a shortcut around D2. Reimplementing the
// gate in C would create a second source of truth for the one decision the
// whole product rests on, and the two would drift -- silently, because the
// firmware copy has no test suite. The device stays a dumb recorder and a dumb
// display. What it shows is exactly what the verified pipeline concluded.
//
// The honest cost: an untethered device cannot score. That is Phase 4, and
// pretending otherwise on stage would be the one lie this project cannot tell.

static char     g_trust[10]  = "";
static char     g_bpm[8]     = "";
static char     g_ctx[14]    = "";
static char     g_why[42]    = "";
static uint32_t g_verdict_ms = 0;

static void read_verdict() {
  static char line[128];
  static size_t n = 0;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || n >= sizeof(line) - 1) {
      line[n] = 0; n = 0;
      if (line[0] == 'V' && line[1] == ',') {
        char *p = line + 2, *f[4] = {nullptr, nullptr, nullptr, nullptr};
        for (int i = 0; i < 4 && p; i++) {
          f[i] = p;
          char *comma = strchr(p, ',');
          if (comma) { *comma = 0; p = comma + 1; } else p = nullptr;
        }
        if (f[0]) strncpy(g_trust, f[0], sizeof(g_trust) - 1);
        if (f[1]) strncpy(g_bpm,   f[1], sizeof(g_bpm)   - 1);
        if (f[2]) strncpy(g_ctx,   f[2], sizeof(g_ctx)   - 1);
        if (f[3]) strncpy(g_why,   f[3], sizeof(g_why)   - 1);
        g_verdict_ms = millis();
      }
    } else if (c != '\r') {
      line[n++] = c;
    }
  }
}

// Wrap a reason across the 21-char line the 128 px display gives at size 1,
// breaking on spaces. A truncated reason is a refusal the wearer cannot act
// on, which is the same as no reason at all.
static void print_wrapped(const char *text, int y, int max_lines) {
  int line = 0, i = 0;
  while (text[i] && line < max_lines) {
    int take = 0, last_space = -1;
    while (text[i + take] && take < 21) {
      if (text[i + take] == ' ') last_space = take;
      take++;
    }
    if (text[i + take] && last_space > 0) take = last_space;
    oled.setCursor(0, y + line * 8);
    for (int k = 0; k < take; k++) oled.write(text[i + k]);
    i += take;
    while (text[i] == ' ') i++;
    line++;
  }
}

static void paint(bool recording) {
  if (!has_oled) return;
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);

  // A verdict older than 3 s is a stale verdict, and a stale verdict shown as
  // current is the exact failure this product exists to refuse. It expires.
  bool fresh = g_verdict_ms && (millis() - g_verdict_ms) < 3000;

  if (fresh) {
    if (!strcmp(g_trust, "unscored")) {
      // THE screen. No number. Not the last one, not an estimate, not a dash
      // that could be mistaken for a low reading.
      oled.setTextSize(2); oled.setCursor(0, 0);  oled.println("NO");
      oled.setCursor(0, 16); oled.println("READING");
      oled.setTextSize(1);
      print_wrapped(g_why, 36, 3);
    } else {
      oled.setTextSize(3); oled.setCursor(0, 0);
      oled.print(g_bpm);
      oled.setTextSize(1); oled.print(" bpm");
      if (!strcmp(g_trust, "degraded")) { oled.setCursor(100, 16); oled.print("~low"); }
      oled.setTextSize(1); oled.setCursor(0, 28); oled.println(g_ctx);
      print_wrapped(g_why, 40, 3);
    }
  } else {
    oled.setTextSize(2); oled.setCursor(0, 0);
    oled.println(recording ? "REC" : "IDLE");
    oled.setTextSize(1); oled.setCursor(0, 20);
    oled.printf("rows  %lu\n", (unsigned long)g_written);
    // Drops are on the FIRST screen, not in a debug menu. A number the wearer
    // can see is a number somebody checks.
    oled.printf("drop  %lu\n", (unsigned long)g_dropped);
    oled.printf("label %s\n", LABELS[g_label]);
    oled.printf("%s%s%s\n", has_ppg ? "PPG " : "--  ", has_imu ? "IMU " : "--  ",
                             has_sd  ? "SD "  : "--  ");
  }
  oled.display();
}

// The buzzer is the thesis, audible. It sounds for an UNEXPLAINED elevation
// and stays SILENT through an identical heart rate that motion or skin
// conductance explains. A device that beeps at every workout is a device
// somebody switches off, and then it is not there on the day it matters.
static void alarm_if_needed() {
  static uint32_t last = 0;
  bool fresh = g_verdict_ms && (millis() - g_verdict_ms) < 3000;
  bool alarming = fresh && !strcmp(g_ctx, "UNEXPLAINED")
                  && strcmp(g_trust, "unscored") != 0;
  if (alarming && millis() - last > 4000) {
    last = millis();
    for (int i = 0; i < 3; i++) {
      digitalWrite(PIN_BUZZER, HIGH); delay(70);
      digitalWrite(PIN_BUZZER, LOW);  delay(90);
    }
  }
}

// One second of every channel, printed in plain English BEFORE the CSV header.
// This exists to answer one question fast: is this sensor dead, or is this wire
// wrong? A channel that never moves is not reading anything, whatever the I2C
// scan said -- a device can acknowledge its address and still have an unplugged
// electrode hanging off it.
//
// Printed as human text ahead of the header, so live.py shows it as boot
// chatter and read_csv never sees it.
static void self_test() {
  Serial.println("self-test: 1 s of raw values, watch the SPREAD not the value");
  uint32_t ir_lo = UINT32_MAX, ir_hi = 0;
  uint16_t gsr_lo = 4095, gsr_hi = 0, ecg_lo = 4095, ecg_hi = 0;
  float a_lo = 99, a_hi = -99;
  int lo_off = 0, n = 0;
  sensors_event_t a, g, t;

  uint32_t end = millis() + 1000;
  while (millis() < end) {
    if (has_ppg) { ppg.check();
      if (ppg.available()) { uint32_t v = ppg.getFIFOIR();
        ir_lo = min(ir_lo, v); ir_hi = max(ir_hi, v); ppg.nextSample(); } }
    if (has_imu && imu.getEvent(&a, &g, &t)) {
      float m = sqrtf(a.acceleration.x*a.acceleration.x +
                      a.acceleration.y*a.acceleration.y +
                      a.acceleration.z*a.acceleration.z) / 9.80665f;
      a_lo = min(a_lo, m); a_hi = max(a_hi, m);
    }
    uint16_t gs = analogRead(PIN_GSR), ec = analogRead(PIN_ECG);
    gsr_lo = min(gsr_lo, gs); gsr_hi = max(gsr_hi, gs);
    ecg_lo = min(ecg_lo, ec); ecg_hi = max(ecg_hi, ec);
    lo_off += (digitalRead(PIN_LO_PLUS) || digitalRead(PIN_LO_MINUS));
    n++;
    delay(5);
  }

  // A flat channel is reported as a PROBLEM, in the words that tell you what to
  // go and touch. "ppg_ir 0..0" is a fact; "sensor not reading" is an action.
  if (!has_ppg) Serial.println("  PPG    MISSING       -> check SDA/SCL + 3V3, addr 0x57");
  else if (ir_hi <= ir_lo + 100)
    Serial.printf("  PPG    FLAT %lu        -> finger/clip not on the sensor\n",
                  (unsigned long)ir_hi);
  else Serial.printf("  PPG    %lu..%lu  ok (a pulse should swing thousands)\n",
                     (unsigned long)ir_lo, (unsigned long)ir_hi);

  if (!has_imu) Serial.println("  IMU    MISSING       -> check SDA/SCL + 3V3, addr 0x68");
  else if (a_hi < 0.5f)
    Serial.println("  IMU    reads ~0 g     -> wrong, gravity alone is 1.0 g. Check wiring.");
  else Serial.printf("  IMU    %.2f..%.2f g  ok (still = ~1.00)\n", a_lo, a_hi);

  if (gsr_hi <= gsr_lo && (gsr_hi == 0 || gsr_hi >= 4095))
    Serial.printf("  GSR    STUCK AT %u   -> 0 = not powered/not wired, 4095 = shorted\n", gsr_hi);
  else Serial.printf("  GSR    %u..%u      ok (fingers on = big change)\n", gsr_lo, gsr_hi);

  if (lo_off == n)
    Serial.println("  ECG    leads OFF      -> electrodes not on skin (fine if unused)");
  else if (ecg_hi <= ecg_lo + 5)
    Serial.printf("  ECG    FLAT %u       -> AD8232 OUTPUT not on GPIO 35?\n", ecg_hi);
  else Serial.printf("  ECG    %u..%u      ok, leads attached\n", ecg_lo, ecg_hi);

  Serial.printf("  BTN    %s\n", digitalRead(PIN_BTN) == LOW
                                 ? "reads PRESSED at boot -> wired to 3V3 instead of GND?"
                                 : "ok (not pressed)");
  Serial.printf("  SD     %s\n", has_sd ? "card mounted" : "no card -> serial only");
  digitalWrite(PIN_BUZZER, HIGH); delay(120); digitalWrite(PIN_BUZZER, LOW);
  Serial.println("  BUZZER just beeped -- if you heard nothing, check GPIO 25 + polarity");
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(300);
  Serial.println("\nVitalGuard recorder");

  pinMode(PIN_LO_PLUS,  INPUT);
  pinMode(PIN_LO_MINUS, INPUT);
  pinMode(PIN_BTN,      INPUT_PULLUP);
  pinMode(PIN_BUZZER,   OUTPUT);
  digitalWrite(PIN_BUZZER, LOW);
  analogReadResolution(12);                 // schema says 0-4095
  analogSetPinAttenuation(PIN_GSR, ADC_11db);
  analogSetPinAttenuation(PIN_ECG, ADC_11db);

  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setClock(400000);

  // Report what is actually on the bus rather than what the pin map says
  // should be. An address that does not answer is a fact; the pin map is a
  // plan.
  Serial.print("i2c:");
  for (uint8_t a = 1; a < 127; a++) if (i2c_present(a)) Serial.printf(" 0x%02X", a);
  Serial.println();

  has_ppg = ppg.begin(Wire, I2C_SPEED_FAST);
  if (has_ppg) {
    // 400 Hz internal with 4x averaging = 100 Hz out, so the FIFO always has a
    // sample ready when the 100 Hz sampler asks. Matching the rates exactly
    // would race, and losing that race means writing a zero.
    ppg.setup(0x1F /*LED*/, 4 /*avg*/, 2 /*red+IR*/, 400 /*Hz*/, 411 /*us*/, 4096);
  }
  has_imu = imu.begin(0x68, &Wire);
  if (has_imu) {
    imu.setAccelerometerRange(MPU6050_RANGE_4_G);
    imu.setGyroRange(MPU6050_RANGE_500_DEG);
    imu.setFilterBandwidth(MPU6050_BAND_44_HZ);   // < 50 Hz Nyquist at 100 Hz
  }
  has_oled = i2c_present(0x3C) && oled.begin(SSD1306_SWITCHCAPVCC, 0x3C);

  has_sd = SD.begin(PIN_SD_CS);
  if (has_sd) {
    for (int i = 1; i < 1000; i++) {
      snprintf(logname, sizeof(logname), "/REC%03d.CSV", i);
      if (!SD.exists(logname)) break;
    }
    logfile = SD.open(logname, FILE_WRITE);
    has_sd = (bool)logfile;
  }
  if (!has_sd) Serial.println("no SD -- streaming to serial, pipe it to a file");

  // Self-test BEFORE the header. Everything the device says after the header
  // must be a data row -- live.py locks on at the header and treats every
  // later line as one, so prose printed afterwards reads as corrupt data.
  self_test();

  // The header, once, at the top of the file. schema.read_csv checks it.
  const char *hdr = "t_ms,ppg_ir,ppg_red,ax,ay,az,gx,gy,gz,"
                    "gsr_raw,ecg_raw,lead_off,btn,label\n";
  if (has_sd) logfile.print(hdr); else Serial.print(hdr);

  Serial.printf("ppg=%d imu=%d oled=%d sd=%d file=%s\n",
                has_ppg, has_imu, has_oled, has_sd, has_sd ? logname : "-");
  paint(false);

  // Refuse to record without the two sensors every downstream layer requires.
  // Recording anyway would produce a file that LOOKS valid -- correct header,
  // correct row count, correct sample rate -- and is evidence about nothing.
  if (!has_ppg || !has_imu) {
    Serial.println("REFUSING to record: PPG and IMU are both required.");
    if (has_oled) {
      oled.clearDisplay(); oled.setTextSize(1); oled.setCursor(0, 0);
      oled.println("NOT RECORDING");
      oled.printf("PPG %s\n", has_ppg ? "ok" : "MISSING");
      oled.printf("IMU %s\n", has_imu ? "ok" : "MISSING");
      oled.println("check I2C wiring");
      oled.display();
    }
    for (;;) { digitalWrite(PIN_BUZZER, HIGH); delay(60);
               digitalWrite(PIN_BUZZER, LOW);  delay(2000); }
  }

  xTaskCreatePinnedToCore(sampler_task, "sampler", 4096, nullptr,
                          configMAX_PRIORITIES - 1, nullptr, 1);
}

void loop() {
  static uint32_t last_paint = 0, last_meta = 0, last_btn = 0;

  // Button cycles the label AT RECORD TIME. Post-hoc labelling from memory is
  // guessing, and `unknown` deliberately does not mean `rest`.
  if (digitalRead(PIN_BTN) == LOW && millis() - last_btn > 400) {
    last_btn = millis();
    g_label = (g_label + 1) % 4;
    digitalWrite(PIN_BUZZER, HIGH); delay(25); digitalWrite(PIN_BUZZER, LOW);
  }

  read_verdict();

  size_t drained = 0;
  while (g_tail != g_head && drained < 256) {
    emit(g_ring[g_tail]);
    g_tail = (g_tail + 1) % RING_N;
    g_written++; drained++;
  }
  flush_out();

  if (millis() - last_paint > 500) { last_paint = millis(); paint(true); alarm_if_needed(); }
  if (has_sd && millis() - last_meta > 5000) {
    last_meta = millis();
    logfile.flush();          // survive a yanked battery mid-recording
    write_meta();
  }
  delay(2);
}
