import pytest

from vitalguard.schema import CSV_HEADER, FIELDS, Sample, read_csv, to_arrays, write_csv
from vitalguard import synth


def _one(**over):
    base = dict(
        t_ms=0, ppg_ir=80000, ppg_red=57600, ax=0.0, ay=0.0, az=1.0,
        gx=0.0, gy=0.0, gz=0.0, gsr_raw=1800, ecg_raw=2048,
        lead_off=0, btn=0, label="rest",
    )
    base.update(over)
    return Sample(**base)


def test_roundtrip_preserves_every_field(tmp_path):
    original = synth.generate("rest", duration_s=3.0, seed=1)
    p = write_csv(tmp_path / "r.csv", original)
    back = read_csv(p)
    assert len(back) == len(original)
    assert back[0] == original[0]
    assert back[-1] == original[-1]


def test_bad_label_is_rejected_at_construction():
    with pytest.raises(ValueError, match="not in"):
        _one(label="panic")


def test_header_mismatch_fails_loudly(tmp_path):
    """A silent positional read would shift every channel by one column.

    This is the failure that would make every downstream number wrong while
    everything still 'worked', so it must raise, not warn.
    """
    p = tmp_path / "bad.csv"
    p.write_text(CSV_HEADER.replace("gsr_raw", "gsr") + "\n")
    with pytest.raises(ValueError, match="does not match schema"):
        read_csv(p)


def test_field_order_is_stable():
    """Firmware emits positionally. If this order changes, firmware breaks."""
    assert FIELDS[0] == "t_ms"
    assert FIELDS[-1] == "label"
    assert "lead_off" in FIELDS


def test_to_arrays_gives_one_column_per_field():
    cols = to_arrays(synth.generate("rest", duration_s=2.0, seed=1))
    assert set(cols) == set(FIELDS)
    assert all(v.size == 200 for v in cols.values())
