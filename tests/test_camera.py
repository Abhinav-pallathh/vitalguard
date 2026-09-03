"""The camera reports geometry. It must never report a state, or a default."""
import re

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
