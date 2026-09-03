"""The bridge must never let an unalignable event look aligned."""
import json
import urllib.error
import urllib.request

import pytest

from vitalguard.behaviour import Channel, Event
from vitalguard.bridge import Bridge


@pytest.fixture
def bridge(tmp_path):
    (tmp_path / "index.html").write_text("<h1>the gate</h1>")
    b = Bridge(tmp_path, port=0).start()
    yield b
    b.stop()


def get(b, path):
    with urllib.request.urlopen(b.url.rstrip("/") + path, timeout=3) as r:
        return json.loads(r.read())


def post(b, payload):
    req = urllib.request.Request(b.url.rstrip("/") + "/event",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read())


def test_serves_the_game(bridge):
    with urllib.request.urlopen(bridge.url + "index.html", timeout=3) as r:
        assert b"the gate" in r.read()


def test_state_is_honest_before_any_device_data(bridge):
    s = get(bridge, "/state")
    assert s["device_t_ms"] is None
    assert s["personal_sigma"] is None
    assert s["trust"] == "unscored"
    assert s["calibrated"] is False


def test_publish_then_state(bridge):
    bridge.state.publish(device_t_ms=12_000, trust="trusted", calibrated=True,
                         personal_sigma=1.7, gsr_sigma=0.9, hr_bpm=88.0)
    s = get(bridge, "/state")
    assert s == {"device_t_ms": 12_000, "personal_sigma": 1.7, "gsr_sigma": 0.9,
                 "hr_bpm": 88.0, "trust": "trusted", "calibrated": True,
                 "n_events": 0, "camera": None}


def test_camera_reports_none_when_none_is_attached(bridge):
    """An absent camera must read as absent, not as a camera seeing nothing."""
    assert get(bridge, "/state")["camera"] is None


def test_camera_state_is_visible_next_to_the_vitals(bridge):
    """The operator has to see a dead camera during the run, not discover an
    empty channel in the report afterwards."""
    class FakeCam:
        frames, faces, rotation, reconnects, error = 100, 96, 90, 1, None
        face_fraction = 0.96
    bridge.state.camera = FakeCam()
    from vitalguard.camera import FaceObservation
    bridge.state.record_face(FaceObservation(t_ms=1, present=True))
    c = get(bridge, "/state")["camera"]
    assert c["face_fraction"] == 0.96
    assert c["rotation"] == 90 and c["reconnects"] == 1
    assert c["face_now"] is True


def test_camera_observations_reach_the_summary(bridge):
    from vitalguard.camera import FaceObservation
    for i in range(6):
        bridge.state.record_face(FaceObservation(
            t_ms=i * 100, present=True, eye_r=(0., 0.), eye_l=(40., 0.),
            nose=(20., 10.), mouth_r=(10., 30.), mouth_l=(30., 30.), width=60.))
    s = bridge.state.face_summary()
    assert s["face_absent_fraction"] == 0.0
    assert s["mouth_width_ratio"] == pytest.approx(0.5)


def test_event_is_stamped_with_the_device_clock(bridge):
    bridge.state.publish(device_t_ms=30_000, trust="trusted", calibrated=True)
    assert post(bridge, {"t_ms": 5_000, "event": "keydown"})["device_t_ms"] == 30_000


def test_event_before_the_device_arrives_is_kept_but_never_placed(bridge):
    """It happened, so we keep it. It has no device time, so it is not on the
    timeline -- and must not be smuggled onto one."""
    post(bridge, {"t_ms": 10, "event": "question_shown"})
    assert bridge.state.events[0].device_t_ms is None
    assert bridge.state.events[0].offset_ms is None
    assert bridge.state.behaviour_events() == []
    assert bridge.state.audit().unstamped == 1


def test_behaviour_events_come_back_on_the_device_clock(bridge):
    bridge.state.publish(device_t_ms=60_000, trust="trusted", calibrated=True)
    post(bridge, {"t_ms": 1_000, "event": "keydown"})
    post(bridge, {"t_ms": 1_050, "event": "backspace"})
    evs = bridge.state.behaviour_events()
    assert [e.t_ms for e in evs] == [60_000, 60_000]
    assert [e.event for e in evs] == [Event.KEYDOWN, Event.BACKSPACE]
    assert evs[0].channel is Channel.INPUT


def test_a_word_not_in_the_vocabulary_is_dropped_not_guessed(bridge):
    bridge.state.publish(device_t_ms=1_000, trust="trusted", calibrated=True)
    post(bridge, {"t_ms": 5, "event": "felt_nervous"})
    assert len(bridge.state.events) == 1          # recorded raw
    assert bridge.state.behaviour_events() == []  # never reaches the metrics


def test_clock_audit_measures_disagreement_rather_than_assuming_none(bridge):
    for i in range(10):
        bridge.state.publish(device_t_ms=100_000 + i * 100, trust="trusted", calibrated=True)
        post(bridge, {"t_ms": i * 100, "event": "keydown"})
    a = bridge.state.audit()
    assert a.n == 10
    assert a.offset_median_ms == 100_000
    assert a.offset_spread_ms == 0.0
    assert a.alignable is True


def test_a_drifting_offset_refuses_to_call_itself_alignable(bridge):
    """This is the batching alarm: if the browser buffers events, arrival time
    stops meaning event time and the spread blows out. It must not pass."""
    for i in range(10):
        bridge.state.publish(device_t_ms=100_000 + i * 1_000, trust="trusted", calibrated=True)
        post(bridge, {"t_ms": i * 100, "event": "keydown"})
    a = bridge.state.audit()
    assert a.offset_spread_ms == 8_100.0
    assert a.alignable is False


def test_too_few_events_is_not_alignable_however_tight(bridge):
    bridge.state.publish(device_t_ms=500, trust="trusted", calibrated=True)
    post(bridge, {"t_ms": 0, "event": "keydown"})
    assert bridge.state.audit().alignable is False


def test_malformed_event_is_rejected_not_silently_dropped(bridge):
    with pytest.raises(urllib.error.HTTPError) as e:
        post(bridge, {"event": "keydown"})          # no t_ms
    assert e.value.code == 400


def test_saved_session_carries_the_audit_so_alignment_is_never_asserted(bridge, tmp_path):
    for i in range(8):
        bridge.state.publish(device_t_ms=2_000 + i, trust="trusted", calibrated=True)
        post(bridge, {"t_ms": i, "event": "keydown"})
    out = tmp_path / "session.json"
    bridge.save(out)
    d = json.loads(out.read_text())
    assert d["clock_audit"]["alignable"] is True
    assert d["clock_audit"]["n"] == 8
    assert len(d["events"]) == 8
    assert d["events"][0]["offset_ms"] == 2_000


def test_tick_moves_the_clock_between_hops(bridge):
    """Physiology arrives once a second; the clock must not."""
    bridge.state.publish(device_t_ms=10_000, trust="trusted", calibrated=True)
    bridge.state.tick(10_010)
    bridge.state.tick(10_020)
    assert get(bridge, "/state")["device_t_ms"] == 10_020
    assert get(bridge, "/state")["hr_bpm"] is None   # untouched by tick


def test_sample_rate_stamping_keeps_the_spread_inside_the_alignable_bar(bridge):
    """The regression this was written for: stamping once per 1 s hop gave a
    ~900 ms spread that was pure quantisation and failed a healthy session."""
    for i in range(12):
        bridge.state.tick(50_000 + i * 10)          # 100 Hz, as the device runs
        post(bridge, {"t_ms": i * 10, "event": "keydown"})
    a = bridge.state.audit()
    assert a.offset_spread_ms == 0.0
    assert a.alignable is True


# --- the report -------------------------------------------------------------

def _q(bridge, t0, qid, first="A", final=None, latency=900, commit=800, switch=None):
    from vitalguard.behaviour import Event
    final = final or first
    bridge.state.tick(t0);          post(bridge, {"t_ms": t0, "event": "question_shown", "detail": qid})
    t = t0 + latency
    bridge.state.tick(t);           post(bridge, {"t_ms": t, "event": "keydown", "detail": first})
    if switch:
        t += switch
        bridge.state.tick(t);       post(bridge, {"t_ms": t, "event": "keydown", "detail": final})
        bridge.state.tick(t);       post(bridge, {"t_ms": t, "event": "answer_changed", "detail": f"{first}->{final}"})
    t += commit
    bridge.state.tick(t);           post(bridge, {"t_ms": t, "event": "answer_committed", "detail": f"{qid}:{final}"})
    return t


def test_report_without_a_practice_act_refuses_to_baseline(bridge):
    """Falling back to fixed thresholds would produce numbers indistinguishable
    from real ones -- the exact cross-person scoring this project refuses."""
    t = 0
    for i in range(3):
        t = _q(bridge, t + 1000, f"a2q{i}")
    r = get(bridge, "/report")
    assert r["baselined"] is False
    assert "practice" in r["why"]
    assert r["acts"]["2"]["n_answers"] == 3


def test_report_baselines_later_acts_against_the_practice_round(bridge):
    t = 0
    for i in range(4):
        t = _q(bridge, t + 1000, f"a1q{i}", latency=900 + i * 30)
    for i in range(3):
        t = _q(bridge, t + 1000, f"a4q{i}", latency=3_500, switch=600)
    r = get(bridge, "/report")
    assert r["baselined"] is True
    devs = {d["metric"]: d for d in r["acts"]["4"]["deviations"]}
    assert devs["decision_latency_ms"]["personal_sigma"] > 0
    assert "practice" in devs["decision_latency_ms"]["says"]


def test_acts_are_split_by_the_question_id_not_by_timing(bridge):
    """The game already encodes the act. Deriving it from gaps would invent a
    boundary the test did not have."""
    t = _q(bridge, 0, "a1q0")
    t = _q(bridge, t + 500, "a3q0")      # no long gap between them
    r = get(bridge, "/report")
    assert set(r["acts"]) == {"1", "3"}


def test_report_carries_the_clock_verdict_so_it_is_never_implied(bridge):
    _q(bridge, 0, "a1q0")
    r = get(bridge, "/report")
    assert "alignable" in r and "clock_spread_ms" in r
