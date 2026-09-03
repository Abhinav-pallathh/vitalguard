"""The camera reports geometry. It must never report a state, or a default."""
import re
import time

import pytest

from vitalguard.camera import METRICS, FaceObservation, summarise

FEELINGS = ("stress", "anxious", "anxiety", "nervous", "calm", "relax", "fear",
            "afraid", "happy", "sad", "angry", "anger", "emotion", "mood",
            "confident", "confidence", "attention", "attentive", "focus",
            "engaged", "distract", "bored", "tired", "fatigue")


def obs(i, **kw):
    d = dict(t_ms=i * 100, present=True, eye_r=(90., 100.), eye_l=(130., 100.),
             nose=(110., 105.), width=60.)
    d.update(kw)
    return FaceObservation(**d)


def test_no_metric_describes_a_feeling():
    """The rejection of face-emotion recognition, enforced in code."""
    for name, (unit, phrasing) in METRICS.items():
        blob = f"{name} {unit} {phrasing}".lower()
        bad = [w for w in FEELINGS if re.search(rf"\b{w}", blob)]
        assert not bad, f"{name} describes a feeling: {bad}"


def test_blink_is_not_reported():
    """YuNet gives eye CENTRES, not eyelid contours. A blink rate from that
    would be a plausible-looking lie, which is the one thing this project
    refuses. If someone adds it, they must justify it here first."""
    assert not any("blink" in k for k in METRICS)


def test_empty_block_returns_none_not_zero():
    """Zero means 'did not move'. None means 'could not see'. Different claims."""
    assert summarise([]) == dict.fromkeys(METRICS, None)


def test_a_face_never_found_reports_absence_not_stillness():
    out = summarise([FaceObservation(t_ms=i * 100, present=False) for i in range(10)])
    assert out["face_absent_fraction"] == 1.0
    assert out["head_motion_px_s"] is None     # not 0.0
    assert out["turn_fraction"] is None


def test_still_face_reads_as_no_motion():
    out = summarise([obs(i) for i in range(10)])
    assert out["head_motion_px_s"] == 0.0
    assert out["face_absent_fraction"] == 0.0


def test_motion_is_scaled_by_face_width():
    """Leaning toward the phone must not read as agitation. The same head
    travel at twice the apparent size is half the reported motion."""
    near = summarise([obs(i, nose=(110. + i * 6, 105.), width=120.) for i in range(6)])
    far = summarise([obs(i, nose=(110. + i * 6, 105.), width=60.) for i in range(6)])
    assert far["head_motion_px_s"] == pytest.approx(2 * near["head_motion_px_s"])


def test_turned_head_is_detected_from_nose_offset():
    straight = obs(0, nose=(110., 105.))          # dead centre between the eyes
    turned = obs(1, nose=(126., 105.))            # 16px off a 40px span = 0.4
    assert straight.turned is False
    assert turned.turned is True
    assert summarise([straight, turned])["turn_fraction"] == 0.5


def test_turn_is_undefined_when_the_face_is_not_there():
    assert FaceObservation(t_ms=0, present=False).turned is None


def test_distance_change_tracks_face_width_spread():
    out = summarise([obs(0, width=50.), obs(1, width=100.), obs(2, width=75.)])
    assert out["distance_change"] == pytest.approx(50 / 75)


def test_frames_stamped_too_close_together_are_dropped_not_divided_by():
    """The device clock is a stamp, not a frame rate. A real run produced
    42,058 px/s from a seated person because two frames 70 ms apart landed 1 ms
    apart in device time. Such pairs are unmeasurable, not fast."""
    from vitalguard.camera import MIN_DT_S
    close = [obs(0, t_ms=0, nose=(110., 105.)), obs(1, t_ms=1, nose=(160., 105.))]
    assert summarise(close)["head_motion_px_s"] is None
    ok = [obs(0, t_ms=0, nose=(110., 105.)),
          obs(1, t_ms=int(MIN_DT_S * 1000) + 30, nose=(110., 105.))]
    assert summarise(ok)["head_motion_px_s"] == 0.0


def test_p95_absorbs_a_bad_frame_at_a_REALISTIC_block_size():
    """A max is the number a reader would quote, and one mis-detected landmark
    ruins it. p95 fixes that only once the block is big enough -- at 14 fps a
    20 s block is ~280 frames, where one spike is 0.4% and cannot reach p95.
    The median is robust regardless, which is why it is the headline metric."""
    steady = [obs(i, t_ms=i * 70, nose=(110. + i, 105.)) for i in range(280)]
    spike = steady + [obs(280, t_ms=280 * 70 + 70, nose=(900., 105.))]
    clean, dirty = summarise(steady), summarise(spike)
    assert dirty["head_motion_p95"] == pytest.approx(clean["head_motion_p95"], rel=.05)
    assert dirty["head_motion_px_s"] == pytest.approx(clean["head_motion_px_s"])
    assert "head_motion_peak" not in METRICS


def test_p95_is_honest_about_being_near_the_max_on_a_short_block():
    """Documented limit, not a bug: with only 20 frames the 95th percentile IS
    the top sample, so a short block cannot absorb an outlier. Report the
    median from short blocks."""
    steady = [obs(i, t_ms=i * 100, nose=(110. + i, 105.)) for i in range(20)]
    spike = steady + [obs(20, t_ms=2000, nose=(900., 105.))]
    assert summarise(spike)["head_motion_p95"] > 500          # not absorbed
    assert summarise(spike)["head_motion_px_s"] == pytest.approx(
        summarise(steady)["head_motion_px_s"])                # median holds


def test_a_gap_in_frames_does_not_manufacture_speed():
    """Frames the detector dropped must not be interpolated into travel."""
    seen = [obs(0, nose=(110., 105.)), obs(50, nose=(110., 105.))]
    assert summarise(seen)["head_motion_px_s"] == 0.0


def test_camera_exports_nothing_the_scorer_can_consume():
    """Same wall as behaviour.py. The test gets harder over time by
    construction, so any camera signal drifts by construction -- a model given
    both would score the clock while appearing to score the body."""
    import vitalguard.camera as cam
    assert not hasattr(cam, "FEATURE_NAMES")
    assert not hasattr(cam, "features")
    assert not any("feature" in n.lower() for n in dir(cam))


# --- the geometry added after Abhi asked what else the face can give us -----

def test_tilt_is_the_eye_line_angle():
    level = obs(0, eye_r=(90., 100.), eye_l=(130., 100.))
    tipped = obs(1, eye_r=(90., 100.), eye_l=(130., 140.))     # 40 across, 40 down
    assert level.tilt_deg == pytest.approx(0.0)
    assert tipped.tilt_deg == pytest.approx(45.0)
    out = summarise([level, tipped])
    assert out["head_tilt_range_deg"] == pytest.approx(45.0)


def test_mouth_ratio_is_scale_free():
    """The eye span is the only rigid ruler a face carries. Moving nearer the
    phone must not change the ratio, or every metric becomes a distance metric."""
    near = obs(0, eye_r=(0., 0.), eye_l=(80., 0.), mouth_r=(20., 60.), mouth_l=(60., 60.))
    far = obs(1, eye_r=(0., 0.), eye_l=(40., 0.), mouth_r=(10., 30.), mouth_l=(30., 30.))
    assert near.mouth_ratio == pytest.approx(0.5)
    assert far.mouth_ratio == pytest.approx(0.5)


def test_mouth_ratio_is_named_after_the_distance_not_a_state():
    """It rises for a grin, a grimace and a jaw stretch alike. Nothing here is
    allowed to guess which -- that guess is the rejected face-emotion claim."""
    unit, phrasing = METRICS["mouth_width_ratio"]
    assert "smile" not in phrasing.lower() and "smil" not in "mouth_width_ratio"
    assert "distance" in phrasing.lower()


def test_landmarks_missing_means_none_not_a_default():
    o = obs(0, mouth_r=None, mouth_l=None)
    assert o.mouth_ratio is None
    assert summarise([o, obs(1, mouth_r=None, mouth_l=None)])["mouth_width_ratio"] is None


def test_rotation_defaults_to_uncalibrated_not_to_zero():
    """A phone in portrait streams sideways and YuNet only sees upright faces.
    On the first real stream that gave a face in 2% of frames -- indistinguishable
    from bad lighting. The reader must not silently assume upright."""
    import inspect

    from vitalguard.camera import FaceReader
    src = inspect.getsource(FaceReader)
    assert "self.rotation: int | None = None" in src
    assert "calibrate" in src


# --- the runner -------------------------------------------------------------

class _FakeCap:
    """Stands in for cv2.VideoCapture: N good frames, then failures."""

    def __init__(self, good=5, then_fail=True):
        self.good, self.then_fail, self.n, self.released = good, then_fail, 0, False

    def read(self):
        self.n += 1
        if self.n <= self.good:
            return True, object()
        return (False, None) if self.then_fail else (True, object())

    def set(self, *a):
        pass

    def isOpened(self):
        return True

    def release(self):
        self.released = True


def _runner(monkeypatch, cap, stamp, sink, **kw):
    from vitalguard.camera import CameraRunner, FaceObservation

    class FakeReader:
        rotation = None

        def __init__(self, *a, **k):
            pass

        def calibrate(self, frames):
            self.rotation = 90
            return 90

        def observe(self, frame, t_ms):
            return FaceObservation(t_ms=t_ms, present=True, eye_r=(0., 0.),
                                   eye_l=(40., 0.), nose=(20., 10.),
                                   mouth_r=(10., 30.), mouth_l=(30., 30.), width=60.)

    monkeypatch.setattr("vitalguard.camera.FaceReader", FakeReader)
    r = CameraRunner("fake://", "model", stamp, sink, **kw)
    monkeypatch.setattr(r, "_open", lambda: cap)
    return r


def test_runner_refuses_to_emit_before_the_device_clock_exists(monkeypatch):
    """An observation we cannot place on the timeline is a row that looks like
    evidence and is not. Same rule the bridge applies to browser events."""
    got = []
    r = _runner(monkeypatch, _FakeCap(good=40, then_fail=False),
                stamp=lambda: None, sink=got.append, calibrate_n=2)
    r.start(); time.sleep(0.3); r.stop()
    assert got == []
    assert r.frames == 0


def test_runner_stamps_with_the_device_clock(monkeypatch):
    got = []
    r = _runner(monkeypatch, _FakeCap(good=40, then_fail=False),
                stamp=lambda: 7_777, sink=got.append, calibrate_n=2)
    r.start(); time.sleep(0.3); r.stop()
    assert got, "runner produced nothing"
    assert all(o.t_ms == 7_777 for o in got)
    assert r.faces == r.frames > 0


def test_runner_calibrates_rotation_before_reading(monkeypatch):
    r = _runner(monkeypatch, _FakeCap(good=40, then_fail=False),
                stamp=lambda: 1, sink=lambda o: None, calibrate_n=3)
    r.start(); time.sleep(0.25); r.stop()
    assert r.rotation == 90


def test_a_dropped_stream_reconnects_and_the_count_is_reported(monkeypatch):
    """Phones sleep and WiFi stutters. The recording it is attached to may be
    the only one anyone gets that day, so a drop must not end the run -- but a
    run that limped must not look like a clean one either."""
    r = _runner(monkeypatch, _FakeCap(good=3, then_fail=True),
                stamp=lambda: 1, sink=lambda o: None, calibrate_n=1)
    r.RECONNECT_WAIT_S = 0.01
    r.MAX_CONSECUTIVE_FAILS = 5
    r.start(); time.sleep(0.4); r.stop()
    assert r.reconnects >= 1


def test_a_missing_model_is_an_error_not_a_crash(monkeypatch):
    from vitalguard.camera import CameraRunner
    r = CameraRunner("fake://", "/nope/missing.onnx", lambda: 1, lambda o: None)
    r.start(); time.sleep(0.2); r.stop()
    assert r.error is not None
    assert r.frames == 0
