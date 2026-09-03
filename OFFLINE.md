# Offline readiness — cached 2026-09-03, **updated 2026-09-04** (camera + game)

Everything below is on this laptop **now**. Verified, not assumed: the firmware
was compiled inside a network namespace with no route to the internet
(`unshare -r -n pio run` → SUCCESS, 1.78 s). If the venue wifi dies, nothing
here needs it.

| What | Where | Size | Verified |
|---|---|---|---|
| ESP32 toolchain (xtensa gcc, esptool, framework-arduino) | `~/.platformio` | 1.5 G | ✅ built offline |
| Arduino libs — MAX3010x, MPU6050, SSD1306, GFX, BusIO, Unified Sensor | `firmware/.pio/libdeps` | — | ✅ pinned in `platformio.ini` |
| Python wheels — numpy scipy sklearn matplotlib pandas pyserial flask streamlit plotly | `wheelhouse/` (56 wheels) | 180 M | ✅ `pip install --no-index --find-links=wheelhouse` |
| Already installed into `venv/` | pyserial, matplotlib, pandas, flask + the originals | — | ✅ |
| WESAD, full | `data/wesad/WESAD.zip` | 2.1 G | ✅ |
| WESAD S2–S4 extracted | `data/wesad/WESAD/` | 2.9 G | ✅ coverage ran on it |
| USB-serial kernel drivers | in-kernel | — | ✅ cp210x, ch341, ftdi_sio, cdc_acm all present |
| **YuNet face detector** | `models/face_detection_yunet_2023mar.onnx` | 227 K | ✅ sha256 verified, loads with no network |
| **Haar cascades (fallback)** | `models/haarcascade_*.xml` | 1.5 M | ✅ downloaded 09-04 |
| **opencv-python-headless 5.0** | `wheelhouse/` + installed into `venv/` | 61 M | ✅ ran against the live phone stream |
| **The game** | `game/index.html` + `questions.json` | 44 K | ✅ zero dependencies — no CDN, no webfonts, no images. Opens from `file://` with the network unplugged |
| **Ambient audio** | `game/assets/ambient.ogg` | 581 K | ✅ on disk, gitignored (licence unconfirmed). The game runs silent without it |

PlatformIO's update checks are throttled to 999999 days, so it will not stall
on a dead network looking for a newer core.

## ⚠ Two camera traps found on 2026-09-04

**`opencv-python-headless` 5.0 ships NO Haar cascades.** `cv2/data/` contains two
files and neither is a detector, so `cv2.data.haarcascades` resolves to an empty
directory. Everything the camera needs is vendored in `models/` for that reason —
do not rely on OpenCV bringing its own.

**This laptop has no camera at all** — no `/dev/video*`, `uvcvideo` not loaded.
The phone is the only camera, over MJPEG on the LAN (IP Webcam). That needs the
laptop and phone on the same network, which at a venue means a **phone hotspot,
not the venue wifi**. Check it before trusting it:

```bash
./venv/bin/python camcheck.py http://<phone-ip>:8080/video
```

It reports whether the stream opens, the real measured fps, and whether a face is
actually being found — and names what to touch when the answer is no. A phone in
portrait streams sideways, which finds a face in ~2% of frames and looks exactly
like a dead camera; the reader auto-corrects for it and prints the rotation.

## ⚠ The one thing that will waste 20 minutes at the venue

`groups` in your shell prints `users ollama docker video input wheel` — **no
`uucp`**. But `/etc/group` says you *are* in `uucp`. The membership was added
after this login session started, so the session never picked it up, and
`/dev/ttyUSB0` will come back **permission denied** the first time you flash.

No sudo needed. In the terminal you flash from:

```bash
newgrp uucp        # then flash in THAT shell
```

A reboot fixes it permanently. Check with `ls -l /dev/ttyUSB0` once the ESP32
is plugged in.

## Flash and run

```bash
cd ~/vitalguard/firmware && pio run -t upload && pio device monitor
```

If the board is a CH340 clone it enumerates as `/dev/ttyUSB0`; a CP2102 also
`/dev/ttyUSB0`; a native-USB S3 would be `/dev/ttyACM0`. All three drivers are
present.

## If you need a package that is not cached

You will not be able to get it. Check this list before the venue, not after.
