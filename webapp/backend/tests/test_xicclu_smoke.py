"""Smoke tests — xicclu wrapper (diagnostic bench building block)."""
from pathlib import Path

import pytest

from lib.z9_client import xicclu

_ASSETS = Path(__file__).resolve().parents[3] / "lib" / "z9_client" / "assets"


def test_parse_line_output_tail():
    """Parses the value tuple after the LAST '->', dropping the [space] tag."""
    line = "0.500000 0.200000 0.100000 [RGB] -> Lut -> 35.618873 23.813281 24.889062 [Lab]"
    assert xicclu._parse_line(line) == pytest.approx((35.618873, 23.813281, 24.889062))
    # inverted-form line with an extra '->' still takes the final output
    assert xicclu._parse_line("30 40 -20 [Lab] -> MatrixBwd -> 0.30 0.16 0.31 [RGB]") \
        == pytest.approx((0.30, 0.16, 0.31))
    # non-lookup lines → None
    assert xicclu._parse_line("Diagnostic: something") is None
    assert xicclu._parse_line("") is None


def test_run_xicclu_rejects_bad_args():
    with pytest.raises(ValueError):
        xicclu.run_xicclu("p.icc", [(0, 0, 0)], direction="zzz")
    with pytest.raises(ValueError):
        xicclu.run_xicclu("p.icc", [(0, 0, 0)], pcs="nope")


def _xicclu_available() -> bool:
    from lib.z9_client.argyll import find_argyll_binary
    return bool(find_argyll_binary("xicclu"))


@pytest.mark.skipif(not _xicclu_available(), reason="xicclu not installed")
def test_run_xicclu_forward_device_to_lab_roundtrip():
    """Real xicclu: sRGB device (1,1,1) forward → Lab ≈ white (L*≈100)."""
    srgb = _ASSETS / "sRGB_IEC61966-2.1.icc"
    out = xicclu.run_xicclu(srgb, [(1.0, 1.0, 1.0)], direction="f", pcs="lab")
    assert len(out) == 1
    L, a, b = out[0]
    assert L == pytest.approx(100.0, abs=1.0)   # white → L*100
    assert abs(a) < 2 and abs(b) < 2            # neutral
