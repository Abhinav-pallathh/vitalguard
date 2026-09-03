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
                 "hr_bpm": 88.0, "trust": "trusted", "calibrated": True, "n_events": 0}


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
