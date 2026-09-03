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
static const int PIN_GSR      = 34;   // ADC1. MUST be ADC1 -- see note below.
static const int PIN_ECG      = 35;   // ADC1.
static const int PIN_LO_PLUS  = 32;
static const int PIN_LO_MINUS = 33;
static const int PIN_BUZZER   = 25;   // ADC2 pin, used digitally -- unaffected.
static const int PIN_BTN      = 27;   // ADC2 pin, used digitally -- unaffected.
static const int PIN_SD_CS    =  5;

// ⚠ ADC2 stops working the instant WiFi is enabled: analogRead returns garbage
// with no error and it looks perfect on the bench with WiFi off. GSR on 34 and
// ECG on 35 are ADC1 and are therefore safe. WiFi is not enabled in this
// firmware at all, which is the belt to that braces.

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
    if (has_ppg && ppg.available()) {
      r.ppg_ir  = ppg.getFIFOIR();
      r.ppg_red = ppg.getFIFORed();
      ppg.nextSample();
    } else {
      r.ppg_ir = r.ppg_red = 0;
    }
    if (has_ppg) ppg.check();

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

static void paint(bool recording) {
  if (!has_oled) return;
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);

  oled.setTextSize(2);
  oled.setCursor(0, 0);
  oled.println(recording ? "REC" : "IDLE");

  oled.setTextSize(1);
  oled.setCursor(0, 20);
  oled.printf("rows  %lu\n", (unsigned long)g_written);
  // Drops are on the FIRST screen, not in a debug menu. A number the wearer
  // can see is a number somebody checks.
  oled.printf("drop  %lu\n", (unsigned long)g_dropped);
  oled.printf("label %s\n", LABELS[g_label]);
  oled.printf("%s%s%s%s\n", has_ppg ? "PPG " : "-- ", has_imu ? "IMU " : "-- ",
                            has_sd  ? "SD "  : "-- ", "");
  oled.display();
}

void setup() {
  Serial.begin(115200);
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

  size_t drained = 0;
  while (g_tail != g_head && drained < 256) {
    emit(g_ring[g_tail]);
    g_tail = (g_tail + 1) % RING_N;
    g_written++; drained++;
  }
  flush_out();

  if (millis() - last_paint > 500) { last_paint = millis(); paint(true); }
  if (has_sd && millis() - last_meta > 5000) {
    last_meta = millis();
    logfile.flush();          // survive a yanked battery mid-recording
    write_meta();
  }
  delay(2);
}
