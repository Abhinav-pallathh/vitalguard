import numpy as np

from vitalguard import synth
from vitalguard.replay import from_csv, windows
from vitalguard.schema import SAMPLE_RATE_HZ, write_csv


def test_window_count_and_size():
    s = synth.generate("rest", duration_s=30.0)
    w = list(windows(s, window_s=10.0, hop_s=1.0))
    assert len(w) == 21          # (30 - 10) / 1 + 1
    assert all(x.n == 1000 for x in w)
    assert all(abs(x.duration_s - 10.0) < 1e-9 for x in w)


def test_short_recording_yields_nothing():
    assert list(windows(synth.generate("rest", duration_s=5.0), window_s=10.0)) == []


def test_empty_input_yields_nothing():
    assert list(windows([])) == []


def test_windows_advance_by_exactly_one_hop():
    w = list(windows(synth.generate("rest", duration_s=20.0), window_s=10.0, hop_s=1.0))
    assert w[1].t_start_ms - w[0].t_start_ms == 1000


def test_lead_off_uses_any_not_average():
    """One detached sample in a 1000-sample window must still flag the window."""
    s = synth.generate("rest", duration_s=12.0)
    s[500].lead_off = 1
    w = list(windows(s, window_s=10.0, hop_s=1.0))
    assert w[0].any_lead_off is True


def test_label_is_majority_vote():
    w = list(windows(synth.generate("exercise", duration_s=30.0), window_s=10.0))
    assert w[len(w) // 2].label == "exercise"


def test_csv_replay_matches_in_memory(tmp_path):
    s = synth.generate("stress", duration_s=15.0, seed=3)
    p = write_csv(tmp_path / "s.csv", s)
    a = list(windows(s, window_s=10.0, hop_s=1.0))
    b = list(from_csv(p, window_s=10.0, hop_s=1.0))
    assert len(a) == len(b)
    assert np.allclose(a[0].cols["ppg_ir"].astype(float), b[0].cols["ppg_ir"].astype(float))
