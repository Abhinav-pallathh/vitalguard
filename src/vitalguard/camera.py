"""The camera channel -- what the person's face DID, never what it meant.

Facial emotion recognition was proposed and rejected (2026-09-03). The mapping
from facial movement to emotion is not consistent across people or contexts,
and shipping a contested black box inside a project whose thesis is *refuse to
show a number you don't trust* hands a judge the one question that ends the
pitch: "why does your honesty layer apply to the finger but not the face?"

So the camera does one honest job here: it reports observable geometry. Every
metric below is phrased as the literal thing measured, and a test fails if any
description contains a feeling-word.

⚠ WHAT THIS CANNOT DO: BLINK.
The plan said blink detection. YuNet returns five landmarks -- two eye CENTRES,
a nose tip, two mouth corners. Eye centres cannot give an eye-aspect-ratio,
because that needs eyelid contours, which no model we have on disk produces.
The Haar eye cascade can say "an eye was found or not", but on a phone stream
that flickers with lighting and head angle, and reporting that as blink rate
would be exactly the plausible-looking lie this project exists to refuse.
Blink is therefore NOT reported. Do not add it without a landmark model that
actually traces eyelids.

⚠ AND, AS WITH behaviour.py: NONE OF THIS FEEDS THE SCORER.
The test gets harder over time by construction, so every camera signal drifts by
construction. A model given both learns "person held still late in the test =
stress" and scores the clock while appearing to score the body.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median

import numpy as np

# Metric name -> (unit, the literal thing measured). The phrasing is the guard:
# if you cannot write the sentence without naming a feeling, it does not belong.
METRICS: dict[str, tuple[str, str]] = {
    "head_motion_px_s":     ("px/s", "median landmark travel per second, scaled by face width"),
    "head_motion_peak":     ("px/s", "largest single-frame landmark travel in the block"),
    "face_absent_fraction": ("0-1",  "fraction of frames in which no face was found"),
    "turn_fraction":        ("0-1",  "fraction of frames with the nose off-centre between the eyes"),
    "distance_change":      ("0-1",  "spread of face width, as a fraction of its median"),
    "head_tilt_deg":        ("deg",  "median angle of the line between the eyes, off horizontal"),
    "head_tilt_range_deg":  ("deg",  "largest minus smallest eye-line angle in the block"),
    "mouth_width_ratio":    ("ratio","distance between mouth corners, over inter-ocular distance"),
}

# ⚠ THE LINE, for anyone adding a metric here.
# mouth_width_ratio is a DISTANCE. "Smiling" would be an inference about a state,
# and inferring states from facial geometry is the thing this project rejected --
# it is not consistent across people or contexts. The same number rises for a
# grin, a grimace and a person stretching their jaw, and nothing in this file is
# allowed to guess which. Report the ratio, name it after the distance, and let
# the report say only that it moved.

# Nose sits this far off the eye midpoint before we call the head turned. Read
# off geometry, not tuned on people: at ~30 deg yaw the nose crosses roughly a
# quarter of the inter-ocular distance. Deliberately generous -- it is a coarse
# "looked away" flag, not a gaze tracker, and it is named turn_fraction for that
# reason rather than anything that sounds like attention.
TURN_RATIO = 0.25


@dataclass(frozen=True, slots=True)
class FaceObservation:
    """One frame's geometry, on the DEVICE clock (stamped by the bridge)."""

    t_ms: int
    present: bool
    eye_r: tuple[float, float] | None = None
    eye_l: tuple[float, float] | None = None
    nose: tuple[float, float] | None = None
    mouth_r: tuple[float, float] | None = None
    mouth_l: tuple[float, float] | None = None
    width: float | None = None

    @property
    def eye_span(self) -> float | None:
        if self.eye_r is None or self.eye_l is None:
            return None
        span = float(np.hypot(self.eye_l[0] - self.eye_r[0], self.eye_l[1] - self.eye_r[1]))
        return span if span > 0 else None

    @property
    def tilt_deg(self) -> float | None:
        """Angle of the eye line off horizontal. A head dropping or leaning."""
        if self.eye_r is None or self.eye_l is None:
            return None
        return float(np.degrees(np.arctan2(self.eye_l[1] - self.eye_r[1],
                                           self.eye_l[0] - self.eye_r[0])))

    @property
    def mouth_ratio(self) -> float | None:
        """Mouth-corner distance over inter-ocular distance.

        Scale-free on purpose: the eye span is the only rigid ruler a face
        carries, so this survives the person moving nearer or further away.
        """
        span = self.eye_span
        if span is None or self.mouth_r is None or self.mouth_l is None:
            return None
        return float(np.hypot(self.mouth_l[0] - self.mouth_r[0],
                              self.mouth_l[1] - self.mouth_r[1]) / span)

    @property
    def turned(self) -> bool | None:
        """Nose off the midpoint between the eyes -- the head is turned."""
        if not self.present or self.nose is None or self.eye_r is None or self.eye_l is None:
            return None
        mid_x = (self.eye_r[0] + self.eye_l[0]) / 2.0
        span = abs(self.eye_l[0] - self.eye_r[0])
        if span <= 0:
            return None
        return abs(self.nose[0] - mid_x) / span > TURN_RATIO


def summarise(obs: list[FaceObservation]) -> dict[str, float | None]:
    """Reduce a block of frames to per-block metrics.

    Returns None for anything the block cannot support, rather than a default.
    A zero here would read as "the person did not move", which is a different
    claim from "we could not see them" -- and the second one is the truth when
    the face was never found.
    """
    out: dict[str, float | None] = dict.fromkeys(METRICS, None)
    if not obs:
        return out
    ev = sorted(obs, key=lambda o: o.t_ms)

    out["face_absent_fraction"] = sum(1 for o in ev if not o.present) / len(ev)

    seen = [o for o in ev if o.present and o.nose is not None and o.width]
    if len(seen) < 2:
        return out

    turns = [o.turned for o in seen if o.turned is not None]
    if turns:
        out["turn_fraction"] = sum(turns) / len(turns)

    widths = [o.width for o in seen if o.width]
    if len(widths) >= 2:
        m = median(widths)
        if m > 0:
            out["distance_change"] = float((max(widths) - min(widths)) / m)

    # Travel between consecutive SEEN frames, scaled by face width so leaning
    # closer to the phone does not read as agitation.
    tilts = [o.tilt_deg for o in seen if o.tilt_deg is not None]
    if tilts:
        out["head_tilt_deg"] = float(median(tilts))
        out["head_tilt_range_deg"] = float(max(tilts) - min(tilts))

    ratios = [o.mouth_ratio for o in seen if o.mouth_ratio is not None]
    if ratios:
        out["mouth_width_ratio"] = float(median(ratios))

    speeds: list[float] = []
    for a, b in zip(seen, seen[1:]):
        dt = (b.t_ms - a.t_ms) / 1000.0
        if dt <= 0 or not a.width:
            continue
        d = float(np.hypot(b.nose[0] - a.nose[0], b.nose[1] - a.nose[1]))
        speeds.append((d / a.width) * 100.0 / dt)
    if speeds:
        out["head_motion_px_s"] = float(median(speeds))
        out["head_motion_peak"] = float(max(speeds))
    return out


class FaceReader:
    """Wraps YuNet. Import-safe with no camera and no model present.

    ⚠ ORIENTATION IS NOT OPTIONAL. A phone streaming its sensor's native
    landscape while held in portrait delivers frames rotated 90 degrees, and
    YuNet only detects upright faces -- on the first real stream this produced a
    face in 2% of frames, which looks exactly like bad lighting or a bad camera
    and is neither. `calibrate()` settles it by trying all four rotations once
    and keeping whichever actually finds a face, so the failure cannot reach the
    venue as a mystery.
    """

    def __init__(self, model_path: str, size: tuple[int, int] = (320, 320),
                 score_threshold: float = 0.7) -> None:
        import cv2
        self._cv2 = cv2
        self._det = cv2.FaceDetectorYN.create(model_path, "", size,
                                              score_threshold=score_threshold)
        self._size = size
        self.rotation: int | None = None      # None until calibrated: 0/90/180/270

    _ROT = {90: 0, 180: 1, 270: 2}            # cv2.ROTATE_* codes, by degrees

    def _rotate(self, frame, deg: int):
        if not deg:
            return frame
        return self._cv2.rotate(frame, self._ROT[deg])

    def calibrate(self, frames: list) -> int | None:
        """Pick the rotation that finds the most faces. Returns None if none do."""
        best, best_n = None, 0
        for deg in (0, 90, 180, 270):
            n = sum(1 for f in frames if self._detect_raw(self._rotate(f, deg)) is not None)
            if n > best_n:
                best, best_n = deg, n
        self.rotation = best
        return best

    def _detect_raw(self, frame):
        h, w = frame.shape[:2]
        self._det.setInputSize((w, h))
        _, faces = self._det.detect(frame)
        if faces is None or len(faces) == 0:
            return None
        return max(faces, key=lambda r: r[2] * r[3])

    def observe(self, frame, t_ms: int) -> FaceObservation:
        frame = self._rotate(frame, self.rotation or 0)
        h, w = frame.shape[:2]
        self._det.setInputSize((w, h))
        _, faces = self._det.detect(frame)
        if faces is None or len(faces) == 0:
            return FaceObservation(t_ms=t_ms, present=False)
        # Largest face only. A second face in shot is a bystander, not the subject.
        f = max(faces, key=lambda r: r[2] * r[3])
        return FaceObservation(
            t_ms=t_ms, present=True,
            eye_r=(float(f[4]), float(f[5])),
            eye_l=(float(f[6]), float(f[7])),
            nose=(float(f[8]), float(f[9])),
            mouth_r=(float(f[10]), float(f[11])),
            mouth_l=(float(f[12]), float(f[13])),
            width=float(f[2]),
        )
