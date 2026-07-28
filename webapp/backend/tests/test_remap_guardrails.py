"""Anti-misalignment guardrails for the remap (guardrail-remap branch).

Turns a silent corruption (missing patch → shifted remap → wrong profile
with no error) into a clean error:
  1. remap matches by SAMPLE_ID (not by rank) → a missing patch no longer
     shifts the others;
  2. count-consistency assert (len + set of SAMPLE_ID) → clear abort
     if incomplete/unordered scan;
  3. parse_cgats_data rejects a declared NUMBER_OF_SETS ≠ actual.

Cases covered: (a) nominal unchanged; (b) incomplete ti3 → abort; (c) SAMPLE_ID
out of order → correct re-association; (d) inconsistent NUMBER_OF_SETS.
"""

import json

import pytest

from lib.z9_client.profiling import ProfilingOps, parse_cgats_data


# 4 reference patches: sample_id 1..4, distinct 8-bit RGB.
SIDECAR_PATCHES = [
    {"index": 0, "sample_id": 1, "rgb": [0, 0, 0]},
    {"index": 1, "sample_id": 2, "rgb": [255, 0, 0]},
    {"index": 2, "sample_id": 3, "rgb": [0, 255, 0]},
    {"index": 3, "sample_id": 4, "rgb": [0, 0, 255]},
]


def _write_sidecar(path, patches=SIDECAR_PATCHES):
    path.write_text(json.dumps({
        "output": "chart.tif",
        "layout": {"num_cols": 2, "nrows": 2},
        "patches_in_layout_order": patches,
    }))


def _write_ti3(path, rows):
    """rows = list of (sample_id, r_pct, g_pct, b_pct). RGB deliberately
    'nominal' (wrong) — the remap must replace them with those from the sidecar."""
    lines = [
        'CTI3',
        'DESCRIPTOR "firmware"',
        'COLOR_REP "RGB_XYZ"',
        'BEGIN_DATA_FORMAT',
        'SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X',
        'END_DATA_FORMAT',
        f'NUMBER_OF_SETS {len(rows)}',
        'BEGIN_DATA',
    ]
    for sid, r, g, b in rows:
        # XYZ_X = sid*1.0: marker to track which measurement is on which row
        lines.append(f'{sid} {r:.4f} {g:.4f} {b:.4f} {float(sid):.4f}')
    lines.append('END_DATA')
    path.write_text('\n'.join(lines) + '\n')


def _read_ti3_rows(path):
    """Returns {sample_id: (R, G, B, XYZ_X)} from a written ti3."""
    out = {}
    in_data = False
    fmt = None
    for ln in path.read_text().splitlines():
        s = ln.strip()
        if s == 'BEGIN_DATA_FORMAT':
            fmt = None
            continue
        if fmt is None and s and s.split()[0] == 'SAMPLE_ID':
            fmt = s.split()
            continue
        if s == 'BEGIN_DATA':
            in_data = True
            continue
        if s == 'END_DATA':
            break
        if in_data and s:
            toks = s.split()
            sid = int(toks[0])
            out[sid] = (float(toks[1]), float(toks[2]), float(toks[3]), float(toks[4]))
    return out


def _pct(v8):
    return v8 * 100.0 / 255.0


# ─── (a) Nominal case 4/4 ordered: remap succeeds, RGB substituted ───────────

def test_nominal_ordered_remap_succeeds(tmp_path):
    sc = tmp_path / "sidecar.json"; _write_sidecar(sc)
    ti3 = tmp_path / "in.ti3"
    _write_ti3(ti3, [(1, 9, 9, 9), (2, 9, 9, 9), (3, 9, 9, 9), (4, 9, 9, 9)])
    out = tmp_path / "out.ti3"

    res = ProfilingOps().remap_ti3_from_sidecar(ti3, sc, out)

    assert res.n_patches_ti3 == 4
    assert res.n_patches_sidecar == 4
    rows = _read_ti3_rows(out)
    # each sample_id received its RGB from the sidecar (converted to pct)
    for p in SIDECAR_PATCHES:
        r, g, b, xyz = rows[p["sample_id"]]
        assert abs(r - _pct(p["rgb"][0])) < 1e-3
        assert abs(g - _pct(p["rgb"][1])) < 1e-3
        assert abs(b - _pct(p["rgb"][2])) < 1e-3
        # the measurement (XYZ_X = sid) stayed on the correct row
        assert abs(xyz - p["sample_id"]) < 1e-6


# ─── (b) incomplete ti3 (patch 3 missing) → clear abort, no profile ─────

def test_missing_patch_aborts(tmp_path):
    sc = tmp_path / "sidecar.json"; _write_sidecar(sc)
    ti3 = tmp_path / "in.ti3"
    _write_ti3(ti3, [(1, 9, 9, 9), (2, 9, 9, 9), (4, 9, 9, 9)])  # 3 missing
    out = tmp_path / "out.ti3"

    with pytest.raises(ValueError) as exc:
        ProfilingOps().remap_ti3_from_sidecar(ti3, sc, out)
    msg = str(exc.value)
    assert "3 measurements read" in msg and "4 expected" in msg
    assert "[3]" in msg  # missing SAMPLE_ID reported
    assert not out.exists()  # no ti3 produced


# ─── (c) SAMPLE_ID out of order → correct re-association (not by rank) ─

def test_out_of_order_sample_ids_remap_by_id(tmp_path):
    sc = tmp_path / "sidecar.json"; _write_sidecar(sc)
    ti3 = tmp_path / "in.ti3"
    # physical order: 2, 4, 1, 3 (firmware would have scanned in this order)
    _write_ti3(ti3, [(2, 9, 9, 9), (4, 9, 9, 9), (1, 9, 9, 9), (3, 9, 9, 9)])
    out = tmp_path / "out.ti3"

    ProfilingOps().remap_ti3_from_sidecar(ti3, sc, out)
    rows = _read_ti3_rows(out)
    # the sample_id=3 patch must have the RGB from sidecar[sid=3] = (0,255,0),
    # NOT the one at position 3 (which in positional remap would give (0,0,255)).
    r, g, b, xyz = rows[3]
    assert (round(r), round(g), round(b)) == (0, 100, 0)
    assert abs(xyz - 3) < 1e-6


# ─── (d) parse_cgats_data: declared NUMBER_OF_SETS ≠ actual → error ─────────

def test_parse_cgats_declared_count_mismatch_raises():
    bad = (
        "CGATS.17\n"
        'NUMBER_OF_SETS 3\n'           # declares 3
        "BEGIN_DATA_FORMAT\n"
        "SAMPLE_ID RGB_R RGB_G RGB_B\n"
        "END_DATA_FORMAT\n"
        "BEGIN_DATA\n"
        "1 0 0 0\n"
        "2 100 100 100\n"             # ... but 2 rows
        "END_DATA\n"
    )
    with pytest.raises(ValueError) as exc:
        parse_cgats_data(bad)
    assert "NUMBER_OF_SETS" in str(exc.value)


def test_parse_cgats_declared_count_match_ok():
    good = (
        "CGATS.17\n"
        'NUMBER_OF_SETS 2\n'
        "BEGIN_DATA_FORMAT\n"
        "SAMPLE_ID RGB_R RGB_G RGB_B\n"
        "END_DATA_FORMAT\n"
        "BEGIN_DATA\n"
        "1 0 0 0\n"
        "2 100 100 100\n"
        "END_DATA\n"
    )
    parsed = parse_cgats_data(good)
    assert len(parsed["data"]) == 2
