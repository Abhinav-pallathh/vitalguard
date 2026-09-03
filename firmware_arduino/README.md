# Arduino-IDE recorder firmware (candidate, not yet the team's choice)

This is a second, independent implementation of `docs/FIRMWARE_CONTRACT.md`'s
14-field row, built and verified against the real bench tonight (2026-09-04)
using Arduino IDE / arduino-cli. It does **not** replace `firmware/src/main.cpp`
(the PlatformIO build) -- that decision belongs to the team, not to whoever
gets hardware working first.

## Why this exists

DECISIONS.md notes "we have still never recorded our own body" as an open
gap. Sujan's actual bench (AD8232 + MAX30102 + GSR + a GY-521 board) has been
wired and individually proven all session; this firmware is what finally
emits the real 14-field contract row off that hardware, so a real recording
can happen instead of staying provisional.

## Verified tonight, on real hardware

- I2C scan: `0x3C 0x57 0x68` (OLED, MAX30102, MPU6050-slot), all present.
- 590 consecutive rows captured; `t_ms` spacing is exactly 10.0ms
  mean/min/max -- real 100Hz, measured, not assumed.
- Accel shows ~0.92g on one axis at rest (gravity, right axis, slight tilt or
  factory trim explains the <1.0), gyro has a normal small bias on one axis
  (uncalibrated MEMS zero-rate offset) -- neither blocks recording, both are
  candidates for a real calibration pass later.

## Two real bugs found and fixed (firmware was landing at ~25Hz, not 100Hz)

1. **`particleSensor.getIR()` then `getRed()` each independently block** until
   a *new* FIFO sample exists, so calling both waited for two separate
   samples rather than reading two channels off one. Fixed by pulling one
   buffered sample (`check()` + `getFIFOIR()` + `getFIFORed()` +
   `nextSample()`) instead. Cut the wait from ~38ms to ~18ms.
2. **`particleSensor.setup()`'s default `ledMode=3`** is multi-LED mode for
   the 3-LED MAX30105 variant. This board's MAX30102 only has 2 LEDs
   (red+IR), so the default was servicing a channel that doesn't physically
   exist. Passing `ledMode=2` explicitly dropped the PPG read to ~1.5ms and
   got the real rate to 100Hz.

## Also found: the "MPU6050" on this board is an MPU6500

Direct register read: `WHO_AM_I` (reg `0x75`) returns `0x70`, not the genuine
MPU6050's `0x68`. Common GY-521 substitution. `Adafruit_MPU6050` (what
`firmware/src/main.cpp` depends on via `platformio.ini`) hard-checks that
register and will report "not found" on this exact part. If the team's other
MPU6050 module is from the same batch, `firmware/src/main.cpp` would hit this
same wall the first time it's run against real hardware -- worth checking
before assuming that firmware works as written. This sketch talks to the
register map directly instead, with no MPU6050-library dependency.

## Known deviation, disclosed rather than hidden

Contract asks for 230400 baud. On this exact board/cable/USB adapter, 230400
produced corrupted rows -- dropped bytes mid-line, even inside the header --
while 115200 came through completely clean, repeatedly, at every test. This
was isolated as the *only* variable (identical firmware, baud swapped) before
concluding it wasn't a logic bug. At tonight's actual throughput (~2.4kB/s)
115200 has plenty of headroom; this only matters once a verdict-return
channel is added. Worth retrying 230400 with a different USB cable -- that's
the usual cause of exactly this symptom -- before treating it as permanent.

## Not yet done

- Label button isn't wired; every row ships `btn=0,label=unknown`. Fine for
  proving the pipeline end-to-end, not usable as training data yet.
- Red PPG LED current (`0x1F`) is a first-light default, not calibrated.
- Does not touch SD logging, the verdict-return channel, the OLED, or the
  buzzer -- purely the recorder side of the contract.

## Open question for the team

Two real, tested, hardware-verified firmwares now exist for the same 14-field
contract: this one (Arduino IDE, tested tonight) and `firmware/src/main.cpp`
(PlatformIO, per `firmware/platformio.ini`, apparently never run against real
hardware yet per DECISIONS.md). Which one the team actually flashes and
demos needs to be a real decision, not a default.
