"""Single colorimetric reference (lib.z9_client.colorimetry) + scan_delta wiring.

Lab must be recomputed from XYZ with the Argyll D50 white (what colprof reads);
stored LAB_* columns are only checked; files without XYZ fall back to LAB_*."""
import numpy as np
import pytest

from lib.z9_client import colorimetry as cm
from lib.z9_client.profiling import _xyz_to_lab

# Sharma, Wu, Dalal (2005) CIEDE2000 test data — the 34 published pairs.
SHARMA = [
    ((50, 2.6772, -79.7751), (50, 0, -82.7485), 2.0425), ((50, 3.1571, -77.2803), (50, 0, -82.7485), 2.8615),
    ((50, 2.8361, -74.0200), (50, 0, -82.7485), 3.4412), ((50, -1.3802, -84.2814), (50, 0, -82.7485), 1.0000),
    ((50, -1.1848, -84.8006), (50, 0, -82.7485), 1.0000), ((50, -0.9009, -85.5211), (50, 0, -82.7485), 1.0000),
    ((50, 0, 0), (50, -1, 2), 2.3669), ((50, -1, 2), (50, 0, 0), 2.3669),
    ((50, 2.49, -0.001), (50, -2.49, 0.0009), 7.1792), ((50, 2.49, -0.001), (50, -2.49, 0.0010), 7.1792),
    ((50, 2.49, -0.001), (50, -2.49, 0.0011), 7.2195), ((50, 2.49, -0.001), (50, -2.49, 0.0012), 7.2195),
    ((50, -0.001, 2.49), (50, 0.0009, -2.49), 4.8045), ((50, -0.001, 2.49), (50, 0.0010, -2.49), 4.8045),
    ((50, -0.001, 2.49), (50, 0.0011, -2.49), 4.7461), ((50, 2.5, 0), (50, 0, -2.5), 4.3065),
    ((50, 2.5, 0), (73, 25, -18), 27.1492), ((50, 2.5, 0), (61, -5, 29), 22.8977),
    ((50, 2.5, 0), (56, -27, -3), 31.9030), ((50, 2.5, 0), (58, 24, 15), 19.4535),
    ((50, 2.5, 0), (50, 3.1736, 0.5854), 1.0000), ((50, 2.5, 0), (50, 3.2972, 0), 1.0000),
    ((50, 2.5, 0), (50, 1.8634, 0.5757), 1.0000), ((50, 2.5, 0), (50, 3.2592, 0.3350), 1.0000),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ((63.0109, -31.0961, -5.8663), (62.8187, -29.7946, -4.0864), 1.2630),
    ((61.2901, 3.7196, -5.3901), (61.4292, 2.2480, -4.9620), 1.8731),
    ((35.0831, -44.1164, 3.7933), (35.0232, -40.0716, 1.5901), 1.8645),
    ((22.7233, 20.0904, -46.6940), (23.0331, 14.9730, -42.5619), 2.0373),
    ((36.4612, 47.8580, 18.3852), (36.2715, 50.5065, 21.2231), 1.4146),
    ((90.8027, -2.0831, 1.4410), (91.1528, -1.6435, 0.0447), 1.4441),
    ((90.9257, -0.5406, -0.9208), (88.6381, -0.8985, -0.7239), 1.5381),
    ((6.7747, -0.2908, -2.4247), (5.8714, -0.0985, -2.2286), 0.6377),
    ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082),
]


def test_ciede2000_sharma_full_table_vectorized_and_scalar():
    a = np.array([p[0] for p in SHARMA], float)
    b = np.array([p[1] for p in SHARMA], float)
    expected = np.array([p[2] for p in SHARMA])
    assert np.abs(cm.ciede2000(a, b) - expected).max() < 1e-4
    for x, y, e in SHARMA:                       # scalar path returns a float
        v = cm.ciede2000(x, y)
        assert isinstance(v, float) and abs(v - e) < 1e-4


def test_xyz_to_lab_is_the_existing_formula_with_argyll_white():
    """Same formula as profiling._xyz_to_lab (no new transformation), Argyll D50 white."""
    rng = np.random.default_rng(1)
    xyz = rng.uniform(0, 100, (500, 3))
    xyz[:20] = rng.uniform(0, 0.8, (20, 3))       # linear branch (very dark)
    ref = np.array([_xyz_to_lab(*x, cm.ARGYLL_D50_WHITE) for x in xyz])
    assert np.abs(cm.xyz_to_lab(xyz) - ref).max() < 1e-9
    assert np.allclose(cm.xyz_to_lab(cm.ARGYLL_D50_WHITE), [100.0, 0.0, 0.0], atol=1e-9)


def _rows(xyz, lab=None):
    rows = []
    for i, x in enumerate(xyz):
        r = [str(i + 1), "50", "50", "50"] + [f"{v:.5f}" for v in x]
        if lab is not None:
            r += [f"{v:.4f}" for v in lab[i]]
        rows.append(r)
    return rows


FMT_XYZ = ["SAMPLE_ID", "RGB_R", "RGB_G", "RGB_B", "XYZ_X", "XYZ_Y", "XYZ_Z"]
FMT_XYZ_LAB = FMT_XYZ + ["LAB_L", "LAB_A", "LAB_B"]


def test_resolve_prefers_xyz_and_checks_consistent_lab_columns():
    xyz = np.array([[20.0, 21.0, 18.0], [80.0, 83.0, 70.0]])
    lab = cm.xyz_to_lab(xyz)
    out, info = cm.resolve_ti3_lab([], FMT_XYZ_LAB, _rows(xyz, lab))
    assert info["lab_source"] == "xyz" and info["xyz_scale"] == "0-100"
    assert info["lab_columns_consistent"] is True and info["lab_columns_n_mismatch"] == 0
    assert np.allclose(out, lab, atol=1e-9)


def test_resolve_reports_historical_wrong_lab_columns_and_ignores_them():
    """Historical bug: LAB_* computed on 0-1 XYZ (L* of white ≈ 8) while XYZ are right."""
    xyz = np.array([[20.0, 21.0, 18.0], [80.0, 83.0, 70.0], [5.0, 5.2, 4.0]])
    wrong = cm.xyz_to_lab(xyz / 100.0)
    out, info = cm.resolve_ti3_lab([], FMT_XYZ_LAB, _rows(xyz, wrong))
    assert info["lab_source"] == "xyz"
    assert info["lab_columns_consistent"] is False and info["lab_columns_n_mismatch"] == 3
    assert np.allclose(out, cm.xyz_to_lab(xyz), atol=1e-9)        # stored columns NOT used


def test_resolve_lab_only_fallback_and_0_1_scale_detection():
    lab = np.array([[50.0, 1.0, -2.0], [60.0, 3.0, 4.0]])
    rows = [[str(i), "50", "50", "50"] + [f"{v}" for v in l] for i, l in enumerate(lab)]
    out, info = cm.resolve_ti3_lab([], ["SAMPLE_ID", "RGB_R", "RGB_G", "RGB_B", "LAB_L", "LAB_A", "LAB_B"], rows)
    assert info["lab_source"] == "lab_columns" and info["xyz_scale"] is None
    assert np.allclose(out, lab)
    xyz01 = np.array([[0.20, 0.21, 0.18], [0.80, 0.83, 0.70]])
    out, info = cm.resolve_ti3_lab([], FMT_XYZ, _rows(xyz01))
    assert info["xyz_scale"] == "0-1"
    assert np.allclose(out, cm.xyz_to_lab(xyz01 * 100.0))


@pytest.mark.parametrize("fmt,rows", [
    (["SAMPLE_ID", "RGB_R", "RGB_G", "RGB_B"], [["1", "0", "0", "0"]]),                       # no XYZ, no Lab
    (FMT_XYZ, [["1", "0", "0", "0", "1.0", "abc", "1.0"]]),                                   # non numeric
    (FMT_XYZ, [["1", "0", "0", "0", "1.0", "nan", "1.0"]]),                                   # non finite
    (FMT_XYZ, [["1", "0", "0", "0", "1.0"]]),                                                 # short row
])
def test_resolve_rejects_incomplete_or_invalid(fmt, rows):
    with pytest.raises(ValueError):
        cm.resolve_ti3_lab([], fmt, rows)


def _ti3_text(rows_xyz, lab_cols=None):
    fmt = "SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z" + (" LAB_L LAB_A LAB_B" if lab_cols is not None else "")
    lines = []
    for i, x in enumerate(rows_xyz):
        line = f"{i} 50 50 50 {x[0]:.5f} {x[1]:.5f} {x[2]:.5f}"
        if lab_cols is not None:
            line += " " + " ".join(f"{v:.4f}" for v in lab_cols[i])
        lines.append(line)
    return (f"CTI3\nNORMALIZED_TO_Y_100 \"YES\"\nNUMBER_OF_FIELDS {len(fmt.split())}\nBEGIN_DATA_FORMAT\n{fmt}\n"
            f"END_DATA_FORMAT\nNUMBER_OF_SETS {len(rows_xyz)}\nBEGIN_DATA\n" + "\n".join(lines) + "\nEND_DATA\n")


def test_scan_delta_uses_xyz_not_stale_lab_columns(tmp_path):
    """Two scans with IDENTICAL XYZ but one with corrupted LAB_* columns: the delta must be ~0
    (XYZ is what colprof reads) and the discrepancy must be reported, the file left untouched."""
    from webapp.backend.services import scan_delta
    xyz = np.array([[20.0 + i, 21.0 + i, 18.0 + i] for i in range(20)])
    good = cm.xyz_to_lab(xyz)
    bad = good.copy(); bad[3] = [90.0, 40.0, -40.0]
    t1 = tmp_path / "a.ti3"; t1.write_text(_ti3_text(xyz, good))
    t2 = tmp_path / "b.ti3"; t2.write_text(_ti3_text(xyz, bad))
    before = t2.read_bytes()
    r = scan_delta.compute_scan_delta([t1, t2])
    assert r["summary"]["max"] < 0.01
    assert r["lab_integrity"]["a.ti3"]["lab_columns_consistent"] is True
    assert r["lab_integrity"]["b.ti3"]["lab_columns_consistent"] is False
    assert r["lab_integrity"]["b.ti3"]["lab_columns_n_mismatch"] == 1
    assert t2.read_bytes() == before                                   # read-only
    q = scan_delta.compute_scan_qc([t1, t2])
    assert q["summary"]["max"] < 0.01 and q["lab_integrity"]["b.ti3"]["lab_source"] == "xyz"


def test_scan_delta_truncated_ti3_is_a_clean_error(tmp_path):
    from webapp.backend.services import scan_delta
    t = tmp_path / "t.ti3"
    t.write_text("CTI3\nBEGIN_DATA_FORMAT\nSAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z\nEND_DATA_FORMAT\n"
                 "BEGIN_DATA\n1 0 0 0 1 1 1\n")                          # no END_DATA
    with pytest.raises(ValueError):
        scan_delta.read_scan(t)
