"""Intrinsic measurement QC (lib.z9_client.measure_qc) — synthetic data only.

Checks both what must be flagged AND where the detector must abstain
(not_assessable) or cannot see (documented limits)."""
import json
import time

import numpy as np
import pytest

from lib.z9_client import measure_qc as mq
from lib.z9_client.colorimetry import xyz_to_lab
from webapp.backend.tests.synthetic_ti3 import (grid, irregular, lab_to_xyz, make_ti3, measure, nearest,
                                                paper_lab)

FLAGGED = ("to_check", "strong")


def _run(rgb, xyz, **kw):
    return mq.analyze_ti3(make_ti3(rgb, xyz, **{k: v for k, v in kw.items() if k in ("ids", "lab_columns", "originator")}),
                          **{k: v for k, v in kw.items() if k in ("params", "allow_merged")})


def _status(rep, i):
    return rep["patches"][i]["status"]


def _codes(rep, i):
    return [r["code"] for r in rep["patches"][i]["reasons"]]


def _set_lab(xyz, i, lab):
    out = xyz.copy()
    out[i] = lab_to_xyz(lab)[0]
    return out


# ─── clean data: no alarm ──────────────────────────────────────────────────
@pytest.mark.parametrize("rgb", [grid(7), irregular(600), grid(5)], ids=["grid343", "irregular600", "sparse125"])
def test_clean_charts_raise_no_alarm(rgb):
    rep = _run(rgb, measure(rgb))
    assert rep["counts"]["strong"] == 0 and rep["counts"]["to_check"] == 0
    # abstention allowed only where the model must extrapolate (cube corners)
    assert all(p["position"] == "corner" for p in rep["patches"] if p["status"] == "not_assessable")
    assert not [f for f in rep["findings"] if f["code"] in ("SET_INCOHERENT", "ZONE_INCOHERENT")]


def test_report_schema_is_stable_and_deterministic():
    rgb = grid(6)
    text = make_ti3(rgb, measure(rgb))
    a, b = mq.analyze_ti3(text), mq.analyze_ti3(text)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["algo_version"] == mq.QC_ALGO_VERSION and a["params_digest"] == mq.QCParams().digest()
    assert set(a) >= {"n_patches", "counts", "median_residual", "thresholds", "integrity", "findings", "limits", "patches"}
    p = a["patches"][0]
    assert set(p) >= {"sample_id", "rgb", "rgb8", "lab", "lab_pred", "de00", "score", "position", "n_neighbors",
                      "reliability", "status", "reasons", "duplicates"}
    assert set(a["counts"]) == set(mq.STATUSES) and sum(a["counts"].values()) == a["n_patches"]
    for p in a["patches"]:
        assert p["status"] in mq.STATUSES and p["position"] in mq.POSITIONS
        for r in p["reasons"]:
            assert r["code"].isupper()                    # stable codes, no UI text
    json.dumps(a)                                         # JSON-serializable


def test_params_digest_changes_with_parameters():
    assert mq.QCParams().digest() != mq.QCParams(z_low=3.0).digest()


# ─── isolated errors by position ───────────────────────────────────────────
@pytest.mark.parametrize("target,allowed", [
    ((2 / 6, 3 / 6, 4 / 6), ("strong",)),                       # interior
    ((0.0, 3 / 6, 4 / 6), ("strong",)),                         # face
    ((0.0, 0.0, 3 / 6), ("strong", "to_check")),                # edge
    ((0.0, 0.0, 1.0), ("strong", "not_assessable")),            # corner: never "coherent"
])
def test_isolated_missed_patch_is_flagged_by_position(target, allowed):
    rgb = grid(7)
    xyz = measure(rgb)
    i = nearest(rgb, target)
    rep = _run(rgb, _set_lab(xyz, i, paper_lab(xyz) + [0.0, 0.3, -0.4]))
    assert _status(rep, i) in allowed
    others = [p for k, p in enumerate(rep["patches"]) if k != i and p["status"] == "strong"]
    assert not others                                           # no collateral "strong"


def test_isolated_error_in_irregular_chart():
    rgb = irregular(600, seed=3)
    xyz = measure(rgb)
    i = nearest(rgb, (0.45, 0.55, 0.35))
    lab = xyz_to_lab(xyz)
    rep = _run(rgb, _set_lab(xyz, i, lab[i] + [12.0, -10.0, 15.0]))
    assert _status(rep, i) == "strong" and "MODEL_RESIDUAL" in _codes(rep, i)


def test_two_neighbouring_errors_are_both_flagged():
    rgb = grid(7)
    xyz = measure(rgb)
    i = nearest(rgb, (3 / 6, 3 / 6, 3 / 6))
    j = nearest(rgb, (3 / 6, 4 / 6, 3 / 6))
    lab = xyz_to_lab(xyz)
    bad = _set_lab(_set_lab(xyz, i, paper_lab(xyz)), j, lab[nearest(rgb, (1, 1, 0))])
    rep = _run(rgb, bad)
    assert _status(rep, i) in FLAGGED and _status(rep, j) in FLAGGED


def test_dispersed_errors_are_all_flagged():
    rgb = grid(7)
    xyz = measure(rgb)
    lab = xyz_to_lab(xyz)
    targets = [(1 / 6, 1 / 6, 5 / 6), (5 / 6, 1 / 6, 1 / 6), (1 / 6, 5 / 6, 1 / 6), (4 / 6, 4 / 6, 4 / 6), (2 / 6, 2 / 6, 2 / 6)]
    idx = [nearest(rgb, t) for t in targets]
    bad = xyz.copy()
    for k, i in enumerate(idx):
        bad = _set_lab(bad, i, lab[idx[(k + 2) % len(idx)]])       # read another patch's colour
    rep = _run(rgb, bad)
    assert all(_status(rep, i) == "strong" for i in idx)


# ─── zones, coherent drift, limits ─────────────────────────────────────────
def test_corrupted_zone_is_not_reported_as_coherent():
    rgb = irregular(800, seed=5)
    xyz = measure(rgb)
    lab = xyz_to_lab(xyz)
    centre = np.array([0.5, 0.5, 0.5])
    zone = np.where(np.linalg.norm(rgb - centre, axis=1) < 0.17)[0]
    rng = np.random.default_rng(11)
    bad = xyz.copy()
    for i in zone:
        v = rng.normal(size=3)
        bad = _set_lab(bad, i, lab[i] + 12.0 * v / np.linalg.norm(v))
    rep = _run(rgb, bad)
    statuses = [_status(rep, i) for i in zone]
    assert len(zone) >= 8
    assert sum(s != "coherent" for s in statuses) >= 0.6 * len(zone)
    assert any(f["code"] in ("ZONE_INCOHERENT", "SET_INCOHERENT") for f in rep["findings"])


def test_coherent_regional_drift_is_a_documented_limit():
    """The same shift on a whole region is indistinguishable from the printer response."""
    rgb = irregular(800, seed=6)
    xyz = measure(rgb)
    lab = xyz_to_lab(xyz)
    zone = np.where(np.linalg.norm(rgb - np.array([0.6, 0.4, 0.5]), axis=1) < 0.17)[0]
    bad = xyz.copy()
    for i in zone:
        bad = _set_lab(bad, i, lab[i] + [5.0, 4.0, -4.0])
    rep = _run(rgb, bad)
    assert sum(_status(rep, i) == "coherent" for i in zone) >= 0.8 * len(zone)
    assert "COHERENT_REGIONAL_DRIFT_UNDETECTABLE" in rep["limits"]


def test_sparse_chart_reports_low_density():
    rgb = grid(4)                                                 # 64 patches
    rep = _run(rgb, measure(rgb))
    assert any(f["code"] == "LOW_DENSITY" for f in rep["findings"])
    assert rep["counts"]["strong"] == 0


def test_too_few_patches_abstain_instead_of_concluding():
    rgb = np.array([[0, 0, 0], [1, 1, 1], [0.5, 0.5, 0.5], [1, 0, 0]], float)
    rep = _run(rgb, measure(rgb))
    assert rep["counts"]["not_assessable"] == 4 and rep["counts"]["coherent"] == 0


def test_dark_tones_do_not_raise_false_alarms():
    ramp = np.repeat(np.linspace(0.0, 0.25, 26)[:, None], 3, axis=1)
    rgb = np.vstack([grid(6), ramp[1:]])
    rep = _run(rgb, measure(rgb))
    assert rep["counts"]["strong"] == 0 and rep["counts"]["to_check"] == 0


# ─── physical checks, white / black ────────────────────────────────────────
def test_patch_lighter_than_paper_is_strong():
    rgb = grid(6)
    xyz = measure(rgb)
    i = nearest(rgb, (0.4, 0.6, 0.4))
    rep = _run(rgb, _set_lab(xyz, i, paper_lab(xyz) + [4.0, 0.0, 0.0]))
    assert _status(rep, i) == "strong" and "PHYS_LIGHTER_THAN_PAPER" in _codes(rep, i)


def test_corrupted_white_implicates_the_white_not_every_patch():
    rgb = grid(6)
    xyz = measure(rgb)
    w = nearest(rgb, (1, 1, 1))
    rep = _run(rgb, _set_lab(xyz, w, [70.0, 0.0, 0.0]))         # white read far too dark
    assert "PHYS_WHITE_SUSPECT" in _codes(rep, w)
    assert not any("PHYS_LIGHTER_THAN_PAPER" in _codes(rep, k) for k in range(len(rgb)))


def test_impossible_chroma_and_negative_xyz():
    rgb = grid(6)
    xyz = measure(rgb)
    i = nearest(rgb, (0.2, 0.8, 0.2))
    j = nearest(rgb, (0.8, 0.2, 0.8))
    bad = _set_lab(xyz, i, [50.0, -110.0, 90.0])                # chroma ≈ 142
    bad[j] = [-2.0, 5.0, 3.0]
    rep = _run(rgb, bad)
    assert "PHYS_CHROMA" in _codes(rep, i) and "PHYS_NEGATIVE_XYZ" in _codes(rep, j)


def test_luminance_inversion_on_interior_ramp():
    ramp = np.repeat(np.linspace(0.3, 0.7, 9)[:, None], 3, axis=1)
    rgb = np.vstack([grid(5), ramp])
    xyz = measure(rgb)
    lab = xyz_to_lab(xyz)
    i = len(rgb) - 3                                            # light end of the ramp read much darker
    rep = _run(rgb, _set_lab(xyz, i, lab[i] - [25.0, 0.0, 0.0]))
    assert _status(rep, i) in FLAGGED
    assert any("MONOTONIC_INVERSION" in _codes(rep, k) for k in range(len(rgb)))


# ─── repeated readings within the scan ─────────────────────────────────────
def _with_repeats(n_rep=4):
    base = grid(6)
    extra = np.vstack([np.zeros((n_rep - 1, 3)), np.ones((n_rep - 1, 3))])
    return np.vstack([base, extra])


def test_concordant_repeats_confirm_discordant_repeat_is_strong():
    rgb = _with_repeats(4)                                      # 4 blacks, 4 whites
    xyz = measure(rgb)
    rep = _run(rgb, xyz)
    blacks = [k for k in range(len(rgb)) if np.allclose(rgb[k], 0)]
    assert all(_status(rep, k) == "coherent" for k in blacks)
    assert all(rep["patches"][k]["duplicates"]["n"] == 4 for k in blacks)
    bad = _set_lab(xyz, blacks[2], xyz_to_lab(xyz)[blacks[2]] + [6.0, 2.0, 2.0])
    rep = _run(rgb, bad)
    assert _status(rep, blacks[2]) == "strong" and "DUP_DISCORDANT" in _codes(rep, blacks[2])
    assert all(_status(rep, k) == "coherent" for k in blacks if k != blacks[2])


def test_divergent_pair_twin_is_cleared_by_the_model():
    rgb = np.vstack([grid(6), [[0.4, 0.6, 0.4]]])                # one interior duplicate pair
    xyz = measure(rgb)
    a, b = nearest(rgb[:-1], (0.4, 0.6, 0.4)), len(rgb) - 1
    bad = _set_lab(xyz, b, xyz_to_lab(xyz)[b] + [10.0, 8.0, -8.0])
    rep = _run(rgb, bad)
    assert _status(rep, b) in FLAGGED and "DUP_PAIR_DIVERGENT" in _codes(rep, b)
    assert _status(rep, a) == "coherent" and "DUP_TWIN_DIVERGES" in _codes(rep, a)


def test_identical_duplicate_rows_are_not_treated_as_independent_readings():
    rgb = np.vstack([grid(6), grid(6)[:10]])
    xyz = measure(rgb)
    xyz[-10:] = xyz[:10]                                        # copied rows
    rep = _run(rgb, xyz)
    assert "IDENTICAL_DUPLICATE_ROWS" in rep["integrity"]["warnings"]
    assert not any("DUP_CONFIRMED" in _codes(rep, k) for k in range(len(rgb)))
    assert rep["patches"][0]["duplicates"]["identical_values"] is True


# ─── inputs ────────────────────────────────────────────────────────────────
def test_non_numeric_sample_ids_are_preserved():
    rgb = grid(5)
    ids = [f"P{k:03d}-x" for k in range(len(rgb))]
    rep = _run(rgb, measure(rgb), ids=ids)
    assert [p["sample_id"] for p in rep["patches"]] == ids


def test_wrong_lab_columns_are_reported_and_ignored():
    rgb = grid(6)
    xyz = measure(rgb)
    wrong = xyz_to_lab(xyz / 100.0)                             # historical spec2cie bug
    a = _run(rgb, xyz)
    b = _run(rgb, xyz, lab_columns=wrong)
    assert "LAB_COLUMNS_INCONSISTENT_WITH_XYZ" in b["integrity"]["warnings"]
    assert [p["status"] for p in a["patches"]] == [p["status"] for p in b["patches"]]


def test_lab_only_input_is_supported_and_reported():
    rgb = grid(5)
    lab = xyz_to_lab(measure(rgb))
    rows = "\n".join(f"{k} {r[0]*100:.3f} {r[1]*100:.3f} {r[2]*100:.3f} {l[0]:.4f} {l[1]:.4f} {l[2]:.4f}"
                     for k, (r, l) in enumerate(zip(rgb, lab)))
    text = ("CTI3\nBEGIN_DATA_FORMAT\nSAMPLE_ID RGB_R RGB_G RGB_B LAB_L LAB_A LAB_B\nEND_DATA_FORMAT\n"
            f"NUMBER_OF_SETS {len(rgb)}\nBEGIN_DATA\n{rows}\nEND_DATA\n")
    rep = mq.analyze_ti3(text)
    assert "NO_XYZ_LAB_COLUMNS_USED" in rep["integrity"]["warnings"] and rep["counts"]["strong"] == 0


def test_merged_file_is_refused_unless_explicitly_allowed():
    rgb = grid(5)
    text = make_ti3(rgb, measure(rgb), originator="freeglaz multipass (lib/z9_client/multipass.py)")
    with pytest.raises(mq.QCInputError) as e:
        mq.analyze_ti3(text)
    assert e.value.code == "MERGED_FILE"
    assert "MERGED_FILE_ANALYSED" in mq.analyze_ti3(text, allow_merged=True)["integrity"]["warnings"]


_HDR = "CTI3\nBEGIN_DATA_FORMAT\n{fmt}\nEND_DATA_FORMAT\nNUMBER_OF_SETS {n}\nBEGIN_DATA\n{rows}\nEND_DATA\n"


@pytest.mark.parametrize("text,code", [
    (_HDR.format(fmt="SAMPLE_ID RGB_R RGB_G XYZ_X XYZ_Y XYZ_Z", n=1, rows="1 0 0 1 1 1"), "MISSING_COLUMNS"),
    (_HDR.format(fmt="SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z", n=2, rows="1 0 0 0 1 1 1\n1 9 9 9 2 2 2"),
     "DUPLICATE_SAMPLE_ID"),
    (_HDR.format(fmt="SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z", n=1, rows="1 0 0 0 1 x 1"), "INVALID_COLORIMETRY"),
    (_HDR.format(fmt="SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z", n=1, rows="1 0 0 255 1 1 1"), "RGB_OUT_OF_RANGE"),
    (_HDR.format(fmt="SAMPLE_ID RGB_R RGB_G RGB_B", n=1, rows="1 0 0 0"), "INVALID_COLORIMETRY"),
    (_HDR.format(fmt="SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z", n=1, rows="1 0 0 0 1 1"), "SHORT_ROW"),
    ("CTI3\nBEGIN_DATA_FORMAT\nSAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z\nEND_DATA_FORMAT\nBEGIN_DATA\n1 0 0 0 1 1 1\n",
     "TRUNCATED"),
    ("CTI3\nnothing here\n", "NO_DATA"),
])
def test_malformed_ti3_raise_coded_errors(text, code):
    with pytest.raises(mq.QCInputError) as e:
        mq.analyze_ti3(text)
    assert e.value.code == code and code in mq.ERROR_CODES


def test_evaluated_patch_never_participates_in_its_own_prediction():
    rgb = np.vstack([grid(6), [[0.4, 0.6, 0.4]]])
    xyz = measure(rgb)
    i, twin = nearest(rgb[:-1], (0.4, 0.6, 0.4)), len(rgb) - 1
    a = _run(rgb, xyz)
    bad = _set_lab(_set_lab(xyz, i, [90.0, 50.0, -60.0]), twin, [20.0, -40.0, 30.0])
    b = _run(rgb, bad)
    assert a["patches"][i]["lab_pred"] == b["patches"][i]["lab_pred"]


def test_number_of_sets_mismatch_is_reported():
    rgb = grid(5)
    text = make_ti3(rgb, measure(rgb)).replace(f"NUMBER_OF_SETS {len(rgb)}", "NUMBER_OF_SETS 999")
    assert "NUMBER_OF_SETS_MISMATCH" in mq.analyze_ti3(text)["integrity"]["warnings"]


def test_2000_patches_in_reasonable_time():
    rgb = irregular(2000, seed=9)
    t0 = time.perf_counter()
    rep = _run(rgb, measure(rgb))
    assert rep["n_patches"] == 2000 and time.perf_counter() - t0 < 30   # ≈ 2.3 s on the dev machine


def test_every_emitted_code_belongs_to_the_documented_api():
    """Reason / finding / warning codes are a stable API (frontend translations)."""
    rgb = np.vstack([grid(6), np.zeros((3, 3)), [[0.4, 0.6, 0.4]], grid(6)[:4]])
    xyz = measure(rgb)
    lab = xyz_to_lab(xyz)
    xyz[-4:] = xyz[:4]                                                    # identical copies
    i = nearest(rgb[:216], (0.5, 0.5, 0.5))
    bad = _set_lab(xyz, i, paper_lab(xyz) + [3.0, 0, 0])
    bad = _set_lab(bad, len(rgb) - 5, lab[len(rgb) - 5] + [9.0, 6.0, -6.0])
    bad[nearest(rgb[:216], (0.8, 0.2, 0.8))] = [-2.0, 5.0, 3.0]
    for rep in (_run(rgb, bad), _run(grid(4), measure(grid(4))), _run(rgb, xyz, lab_columns=xyz_to_lab(xyz / 100))):
        for p in rep["patches"]:
            assert all(r["code"] in mq.REASON_CODES for r in p["reasons"])
        assert all(f["code"] in mq.FINDING_CODES for f in rep["findings"])
        assert all(w in mq.WARNING_CODES for w in rep["integrity"]["warnings"])


def test_reliability_verdict_is_provided_by_the_backend():
    """The frontend must not re-derive reliability: the report carries it."""
    rgb = irregular(600)
    rep = _run(rgb, measure(rgb))
    for p in rep["patches"]:
        rel = p["reliability"]
        assert isinstance(rel["assessable"], bool) and isinstance(rel["issues"], list)
        assert rel["assessable"] == (not rel["issues"])
        if p["status"] == "not_assessable":
            assert rel["assessable"] is False and set(rel["issues"]) <= set(mq.REASON_CODES)
    assert rep["report_format"] == mq.REPORT_FORMAT
