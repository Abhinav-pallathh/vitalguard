// VitalGuard recorder firmware -- matches docs/FIRMWARE_CONTRACT.md's 14-field
// row exactly, verified against real hardware 2026-09-04:
//   t_ms,ppg_ir,ppg_red,ax,ay,az,gx,gy,gz,gsr_raw,ecg_raw,lead_off,btn,label
// Confirmed 100Hz on real hardware (measured t_ms spacing, mean 10.0ms).
//
// Built with Arduino IDE / arduino-cli, not PlatformIO -- see
// firmware/src/main.cpp for the PlatformIO version this is meant to be
// reconciled with, not silently replace. Two independent implementations of
// the same contract currently exist; that's a team decision, not a code one.
//
// NO FILTERING OR AVERAGING HERE, ON PURPOSE (contract D2: "the firmware is a
// dumb recorder... does no filtering, does no smoothing, makes no decisions").
// An earlier draft 10x-oversampled the ECG channel to cancel 50Hz mains hum;
// that needs a ~20ms window and doesn't fit inside a 10ms/100Hz tick anyway,
// and D2 forbids this class of on-device processing regardless. Raw ADC value
// only -- as a result, raw ecg_raw visibly carries 50Hz mains ripple when read
// this way. That's expected, not a bug; hum rejection is a Python-side problem
// now, same as every other derived signal in this project.
//
// KNOWN DEVIATION, disclosed not hidden: contract asks for 230400 baud. On
// this exact board/cable/adapter, 230400 produced corrupted rows (dropped
// bytes mid-line, even inside the header) while 115200 came through clean at
// every rate tested. Root-caused by isolating the variable (identical code,
// baud swapped) rather than assumed. At today's ~2.4kB/s actual throughput,
// 115200 has ample headroom; only matters once a return channel is added.
// Worth retrying 230400 with a different USB cable before trusting this as
// permanent -- cheap cables are the usual cause of this exact symptom.
//
// Chip note: the "MPU6050" module on this board is actually an MPU6500
// (WHO_AM_I=0x70, confirmed by direct register read). A common GY-521
// substitution. Adafruit_MPU6050 hard-checks WHO_AM_I==0x68 and reports
// "not found" on this exact part -- talking to the register map directly
// instead (both chips share the same basic accel/gyro registers), so this
// firmware has no MPU6050-library dependency at all.
//
// Two real bugs found and fixed getting to 100Hz (was landing at ~25Hz):
//   1. particleSensor.getIR() then getRed() each independently block until a
//      NEW FIFO sample exists, so calling both waited for two samples, not
//      one. Fixed: check()+getFIFOIR()+getFIFORed()+nextSample() reads both
//      LED channels off the SAME buffered sample. Halved the wait (~38ms -> ~18ms).
//   2. particleSensor.setup()'s default ledMode=3 is multi-LED mode for the
//      3-LED MAX30105 variant. This is a 2-LED MAX30102 -- explicitly passing
//      ledMode=2 (red+IR only) dropped the PPG read to ~1.5ms and got the
//      real rate the rest of the way to 100Hz.
//
// Not yet wired: the label button. label ships as "unknown", btn as 0.
// Not yet tuned: the red PPG LED current below -- first light, not calibrated.

#include <Wire.h>
#include "MAX30105.h"

MAX30105 particleSensor;

// --- pins, per docs/FIRMWARE_CONTRACT.md, matching the real bench ---
const int GSR_PIN  = 35; // ADC1, analogRead, GSR finger-clip
const int ECG_PIN  = 34; // ADC1, analogRead, AD8232 OUTPUT
const int LO_PLUS  = 32; // AD8232 LO+ (leads-off detect)
const int LO_MINUS = 33; // AD8232 LO- (leads-off detect)
// AD8232 SDN is wired directly to the 3V3 rail (tied high), not a GPIO.
// Shared I2C bus, SDA=21 SCL=22: MAX30102 (0x57), OLED (0x3C, unused here),
// MPU6500-as-MPU6050 (0x68).

const uint8_t MPU_ADDR = 0x68;
const uint32_t PERIOD_US = 10000; // 100Hz, fixed cadence per contract

uint32_t next;

void mpuWriteReg(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.write(val);
  Wire.endTransmission();
}

// Returns true and fills ax..gz (g / deg/s) on success, false on a short read
// -- caller writes NaN in that case rather than carrying a stale value
// forward (same "never guess" rule the contract states for every channel).
bool readMPU(float &ax, float &ay, float &az, float &gx, float &gy, float &gz) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B); // ACCEL_XOUT_H, 14 bytes: accel(6) temp(2) gyro(6)
  if (Wire.endTransmission(false) != 0) return false;
  Wire.requestFrom((int)MPU_ADDR, 14);
  if (Wire.available() < 14) return false;

  int16_t rax = (Wire.read() << 8) | Wire.read();
  int16_t ray = (Wire.read() << 8) | Wire.read();
  int16_t raz = (Wire.read() << 8) | Wire.read();
  Wire.read(); Wire.read(); // temp, unused
  int16_t rgx = (Wire.read() << 8) | Wire.read();
  int16_t rgy = (Wire.read() << 8) | Wire.read();
  int16_t rgz = (Wire.read() << 8) | Wire.read();

  ax = rax / 16384.0f; ay = ray / 16384.0f; az = raz / 16384.0f; // +/-2g range
  gx = rgx / 131.0f;   gy = rgy / 131.0f;   gz = rgz / 131.0f;   // +/-250dps range
  return true;
}

void setup() {
  Serial.begin(115200); // see "KNOWN DEVIATION" note above
  analogReadResolution(12); // contract requires 0-4095 on gsr_raw/ecg_raw
  pinMode(LO_PLUS, INPUT);
  pinMode(LO_MINUS, INPUT);

  Wire.begin(21, 22);
  Wire.setClock(400000); // I2C_SPEED_FAST, matches the proven MAX30102 init

  if (!particleSensor.begin(Wire, I2C_SPEED_FAST)) {
    Serial.println("# MAX30102 not found -- check wiring (SDA=21, SCL=22, addr 0x57)");
    while (true) { delay(1000); }
  }
  particleSensor.setup(0x1F, 4, 2, 400, 411, 4096); // ledMode=2, see note above
  particleSensor.setPulseAmplitudeIR(0x1F);
  particleSensor.setPulseAmplitudeRed(0x1F); // contract wants ppg_red too; not yet tuned

  mpuWriteReg(0x6B, 0x00); // PWR_MGMT_1: clear sleep
  delay(50);
  mpuWriteReg(0x1C, 0x00); // ACCEL_CONFIG: +/-2g
  mpuWriteReg(0x1B, 0x00); // GYRO_CONFIG:  +/-250dps

  Serial.println("t_ms,ppg_ir,ppg_red,ax,ay,az,gx,gy,gz,gsr_raw,ecg_raw,lead_off,btn,label");
  next = micros();
}

void loop() {
  while ((int32_t)(micros() - next) < 0) {} // fixed cadence, no drift
  next += PERIOD_US;

  unsigned long tMs = millis();

  while (particleSensor.available() == false) particleSensor.check();
  long irRaw = particleSensor.getFIFOIR();     // same buffered sample --
  long redRaw = particleSensor.getFIFORed();   // one wait, not two
  particleSensor.nextSample();

  float ax, ay, az, gx, gy, gz;
  bool mpuOk = readMPU(ax, ay, az, gx, gy, gz);

  int gsrRaw = analogRead(GSR_PIN);
  int ecgRaw = analogRead(ECG_PIN);
  int leadOff = (digitalRead(LO_PLUS) == HIGH || digitalRead(LO_MINUS) == HIGH) ? 1 : 0;

  Serial.print(tMs); Serial.print(",");
  Serial.print(irRaw); Serial.print(",");
  Serial.print(redRaw); Serial.print(",");
  if (mpuOk) {
    Serial.print(ax, 4); Serial.print(",");
    Serial.print(ay, 4); Serial.print(",");
    Serial.print(az, 4); Serial.print(",");
    Serial.print(gx, 2); Serial.print(",");
    Serial.print(gy, 2); Serial.print(",");
    Serial.print(gz, 2); Serial.print(",");
  } else {
    Serial.print("nan,nan,nan,nan,nan,nan,");
  }
  Serial.print(gsrRaw); Serial.print(",");
  Serial.print(ecgRaw); Serial.print(",");
  Serial.print(leadOff); Serial.print(",");
  Serial.print(0); Serial.print(","); // btn -- not wired yet
  Serial.println("unknown"); // label -- not wired yet
}
