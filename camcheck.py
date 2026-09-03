"""Point this at the phone before trusting anything downstream.

    ./venv/bin/python camcheck.py http://192.168.29.xxx:8080/video

It answers the only questions that matter at a venue: does the stream open, how
fast does it really arrive, and is a face actually being found. It prints what
to go and touch when the answer is no -- same shape as the firmware's boot
self-test, and for the same reason: a dead camera and a wrong URL look identical
from the pipeline's side.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import cv2

from vitalguard.camera import FaceReader, summarise

MODEL = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"
BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, YELLOW, RED = "\033[32m", "\033[33m", "\033[31m"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        print(f"{YELLOW}Start IP Webcam on the phone first, then pass the /video URL "
              f"it shows on screen.{RESET}")
        return 2
    src = sys.argv[1]
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0

    if not MODEL.exists():
        print(f"{RED}model missing:{RESET} {MODEL}")
        return 1

    t0 = time.time()
    print(f"{BOLD}opening{RESET} {src}  {DIM}(up to 10s){RESET}")
    cap = cv2.VideoCapture(src)
    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 10_000)
    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5_000)
    if not cap.isOpened():
        print(f"{RED}  STREAM   will not open{RESET}")
        print("    -> is IP Webcam actually started (green 'Start server' pressed)?")
        print("    -> is the URL the one the phone shows, ending in /video ?")
        print("    -> is the phone on the same WiFi as this laptop, not mobile data?")
        return 1

    reader = FaceReader(str(MODEL))

    # Settle orientation FIRST. A rotated stream looks identical to bad lighting.
    probe = []
    while len(probe) < 6 and time.time() - t0 < 6:
        ok, f = cap.read()
        if ok:
            probe.append(f)
    rot = reader.calibrate(probe) if probe else None
    if rot is None:
        print(f"  ROTATION {YELLOW}no face at any rotation{RESET} -- continuing at 0 deg")
        reader.rotation = 0
    elif rot:
        print(f"  ROTATION {GREEN}{rot} deg{RESET}  {DIM}stream is sideways; corrected in software{RESET}")
    else:
        print(f"  ROTATION {GREEN}upright{RESET}")

    obs, dropped = [], 0
    t0 = time.time()
    while time.time() - t0 < seconds:
        ok, frame = cap.read()
        if not ok:
            dropped += 1
            if dropped > 30:
                break
            continue
        obs.append(reader.observe(frame, int((time.time() - t0) * 1000)))
    cap.release()

    if not obs:
        print(f"{RED}  FRAMES   none arrived{RESET}  (dropped {dropped} reads)")
        print("    -> the port answered but sent no video. Wrong path? Try /video and /videofeed.")
        return 1

    elapsed = (obs[-1].t_ms - obs[0].t_ms) / 1000.0 or 1e-9
    fps = (len(obs) - 1) / elapsed
    seen = sum(1 for o in obs if o.present)
    frac = seen / len(obs)
    h, w = frame.shape[:2]

    print(f"\n{BOLD}what the camera actually gave us{RESET}")
    print(f"  STREAM   {GREEN}open{RESET}  {w}x{h}")
    print(f"  RATE     {fps:5.1f} fps measured over {elapsed:.1f}s "
          f"({len(obs)} frames, {dropped} dropped reads)")
    if fps < 8:
        print(f"           {YELLOW}below 8 fps -- head motion will be coarse. "
              f"Lower the resolution in IP Webcam.{RESET}")

    if frac == 0:
        print(f"  FACE     {RED}never found in {len(obs)} frames{RESET}")
        print("    -> is the phone pointed at your face, and is the room lit from the front?")
        print("    -> a backlit face (window behind you) is the usual cause.")
    elif frac < 0.8:
        print(f"  FACE     {YELLOW}found in {frac*100:.0f}% of frames{RESET} -- "
              f"reposition the phone until this is above 90%")
    else:
        print(f"  FACE     {GREEN}found in {frac*100:.0f}% of frames{RESET}")

    print(f"\n{BOLD}metrics over this sample{RESET}")
    for k, v in summarise(obs).items():
        print(f"  {k:<22} {'--' if v is None else f'{v:8.3f}'}")
    print(f"\n{DIM}Sit still for a clean baseline, then run it again while moving "
          f"to confirm head_motion actually responds.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
