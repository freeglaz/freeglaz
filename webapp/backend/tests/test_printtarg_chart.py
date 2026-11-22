"""External-instrument charts (printtarg layout): argv, .ti1/.ti2 checks, patch
positions from pixels, roll composition (pages turned so that strips cross the roll,
or the previous unturned stack), and the real printtarg when it is installed.

Offline only: no printer, no instrument. The real-printtarg tests use synthetic .ti1
files (targen or generated here) and run at 100 dpi (the layout does not depend on the
resolution); the 300 dpi reference run is opt-in (FREEGLAZ_TEST_PRINTTARG_300DPI=1,
about 0.6 GB written to the temporary folder).
"""
import json
import math
import os
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
import tifffile

from lib.z9_client import printtarg_chart as P
from lib.z9_client.argyll import find_argyll_binary

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
RESIDENT = _FIXTURES / "synthetic_test_resident_A.icc"
NOW = datetime(2026, 11, 22, 15, 0, 0)


@pytest.fixture(autouse=True)
def _drop_tiffs_after_test(tmp_path):
    """Real printtarg runs write ~0.1 GB of TIFF each and pytest keeps the last three
    runs' temporary folders (often on a RAM-backed /tmp): delete the TIFFs once the
    test is over, keep the small files (.ti2, chart.json) for diagnosis."""
    yield
    for tif in tmp_path.rglob("*.tif"):
        tif.unlink(missing_ok=True)


# ─── argv ────────────────────────────────────────────────────────────────────
def test_argv_i1pro3plus_preset():
    pre = P.PRESETS[("i1pro3plus", "roll24")]
    argv = P.build_printtarg_argv("/x/printtarg", instrument="i1pro3plus",
                                  page_w_mm=pre.page_w_mm, page_h_mm=pre.page_h_mm,
                                  unlimited_strip=pre.unlimited_strip, dpi=300,
                                  random_start=121, base="CHT-20261122-1500-00")
    assert argv == ["/x/printtarg", "-v", "-i3p", "-L", "-P", "-C", "-R", "121", "-T300",
                    "-p610x500", "CHT-20261122-1500-00"]


def test_argv_transverse_includes_the_margin_and_probes_render_nothing():
    kw = dict(instrument="i1pro3plus", page_w_mm=657, page_h_mm=500, unlimited_strip=True,
              random_start=49, base="b", margin_in_tiff=True)
    assert P.build_printtarg_argv("printtarg", dpi=300, **kw) == [
        "printtarg", "-v", "-i3p", "-L", "-P", "-M", "6", "-C", "-R", "49", "-T300",
        "-p657x500", "b"]
    # a layout probe: same layout options, no TIFF (PostScript only)
    assert P.build_printtarg_argv("printtarg", dpi=None, **kw) == [
        "printtarg", "-v", "-i3p", "-L", "-P", "-M", "6", "-R", "49", "-p657x500", "b"]


def test_argv_colormunki_preset_has_no_unlimited_strip():
    pre = P.PRESETS[("colormunki", "roll24")]
    argv = P.build_printtarg_argv("printtarg", instrument="colormunki",
                                  page_w_mm=pre.page_w_mm, page_h_mm=pre.page_h_mm,
                                  unlimited_strip=pre.unlimited_strip, dpi=300,
                                  random_start=0, base="b")
    assert "-iCM" in argv and "-P" not in argv and argv[-2] == "-p610x450"


@pytest.mark.parametrize("kw, code", [
    ({"instrument": "i1pro2"}, "unsupported_instrument"),
    ({"base": "-v"}, "invalid_base_name"),
    ({"base": "a b"}, "invalid_base_name"),
    ({"dpi": 299.5}, "invalid_dpi"),
    ({"page_h_mm": 0}, "invalid_page_size"),
    ({"random_start": -1}, "invalid_random_start"),
])
def test_argv_refusals(kw, code):
    args = dict(instrument="i1pro3plus", page_w_mm=610, page_h_mm=500,
                unlimited_strip=True, dpi=300, random_start=1, base="b")
    args.update(kw)
    with pytest.raises(P.PrinttargChartError) as e:
        P.build_printtarg_argv("printtarg", **args)
    assert e.value.code == code


def test_only_studied_instruments_are_offered():
    assert set(P.INSTRUMENTS) == {"i1pro3plus", "colormunki"}
    assert {k[0] for k in P.PRESETS} == set(P.INSTRUMENTS)
    assert P.SUPPORTED_MEDIA == ("roll24",)


def test_strip_labels_default_pattern():
    labels = P.strip_labels(89)
    assert labels[:3] == ["A", "B", "C"] and labels[25] == "Z"
    assert labels[26] == "AA" and labels[51] == "AZ" and labels[52] == "BA"
    assert labels[-1] == "CK"


def test_default_random_start_is_stable_and_in_range():
    a = P.default_random_start("CHT-20261122-1500-00", 800)
    assert a == P.default_random_start("CHT-20261122-1500-00", 800)
    assert 0 <= a < 800


# ─── .ti1 / .ti2 checks (synthetic CGATS) ────────────────────────────────────
def _write_ti1(path, rows, rep="RGB"):
    lines = ["CTI1", f'COLOR_REP "{rep}"', "NUMBER_OF_FIELDS 4", "BEGIN_DATA_FORMAT",
             "SAMPLE_ID RGB_R RGB_G RGB_B", "END_DATA_FORMAT",
             f"NUMBER_OF_SETS {len(rows)}", "BEGIN_DATA"]
    lines += [f"{sid} {r:.4f} {g:.4f} {b:.4f}" for sid, (r, g, b) in rows]
    lines += ["END_DATA", ""]
    Path(path).write_text("\n".join(lines))


def _ti1_rows(n):
    return [(str(i + 1), (100.0 * i / n, 50.0 + 40.0 * ((i * 7) % n) / n, 100.0 - 90.0 * i / n))
            for i in range(n)]


def _write_ti2(path, entries, *, steps, passes, instrument="GretagMacbeth i1 Pro",
               strip_pattern=P.STRIP_INDEX_PATTERN):
    """entries: [(sample_id, loc, (r, g, b))] in any order."""
    lines = ["CTI2", f'TARGET_INSTRUMENT "{instrument}"', 'COLOR_REP "iRGB"',
             'RANDOM_START "3"', f'STEPS_IN_PASS "{steps}"',
             f'PASSES_IN_STRIPS2 "{",".join(map(str, passes))}"',
             f'STRIP_INDEX_PATTERN "{strip_pattern}"',
             f'PATCH_INDEX_PATTERN "{P.PATCH_INDEX_PATTERN}"',
             f'INDEX_ORDER "{P.INDEX_ORDER}"', "NUMBER_OF_FIELDS 5", "BEGIN_DATA_FORMAT",
             "SAMPLE_ID SAMPLE_LOC RGB_R RGB_G RGB_B", "END_DATA_FORMAT",
             f"NUMBER_OF_SETS {len(entries)}", "BEGIN_DATA"]
    lines += [f'{sid} "{loc}" {r:.5f} {g:.5f} {b:.5f}' for sid, loc, (r, g, b) in entries]
    lines += ["END_DATA", ""]
    Path(path).write_text("\n".join(lines))


def _layout_entries(ti1_rows, steps, passes, quantize=True):
    """printtarg-like .ti2: patches fill strips A, B, … then white padding (id 0)."""
    labels = P.strip_labels(sum(passes))
    slots = [f"{lab}{k}" for lab in labels for k in range(1, steps + 1)]
    out = []
    for i, loc in enumerate(slots):
        if i < len(ti1_rows):
            sid, rgb = ti1_rows[i]
            if quantize:   # printtarg -T writes the 16-bit rendered values
                rgb = tuple(q / P.WHITE16 * 100.0 for q in P._q16(rgb))
            out.append((sid, loc, rgb))
        else:
            out.append(("0", loc, (100.0, 100.0, 100.0)))
    return out


def test_ti1_reader_refusals(tmp_path):
    p = tmp_path / "a.ti1"
    _write_ti1(p, [("0", (1, 2, 3))])
    with pytest.raises(P.PrinttargChartError) as e:
        P.read_ti1_patches(p)
    assert e.value.code == "ti1_sample_id_reserved"
    _write_ti1(p, [("1", (1, 2, 3)), ("1", (4, 5, 6))])
    with pytest.raises(P.PrinttargChartError) as e:
        P.read_ti1_patches(p)
    assert e.value.code == "ti1_duplicate_sample_id"
    _write_ti1(p, [("1", (1, 2, 300))])
    with pytest.raises(P.PrinttargChartError) as e:
        P.read_ti1_patches(p)
    assert e.value.code == "ti1_rgb_out_of_range"
    _write_ti1(p, [("1", (1, 2, 3))], rep="CMYK")
    with pytest.raises(P.PrinttargChartError) as e:
        P.read_ti1_patches(p)
    assert e.value.code == "ti1_not_rgb"


def test_ti2_check_accepts_printtarg_conventions(tmp_path):
    rows = _ti1_rows(7)
    _write_ti1(tmp_path / "a.ti1", rows)
    _write_ti2(tmp_path / "a.ti2", _layout_entries(rows, 3, [2, 1]), steps=3, passes=[2, 1])
    chk = P.check_ti2(P.read_ti1_patches(tmp_path / "a.ti1"), tmp_path / "a.ti2",
                      instrument="i1pro3plus")
    assert chk.steps_in_pass == 3 and chk.passes_in_strips == [2, 1] and chk.n_pages == 2
    assert chk.padding_locations == ["C2", "C3"]
    assert [p["sample_loc"] for p in chk.patches][:4] == ["A1", "A2", "A3", "B1"]
    assert {p["page"] for p in chk.patches if p["strip"] in ("A", "B")} == {1}
    assert {p["page"] for p in chk.patches if p["strip"] == "C"} == {2}


def _mutated(rows, kind):
    e = _layout_entries(rows, 3, [2, 1])
    if kind == "missing":
        e[0] = ("0", e[0][1], (100.0, 100.0, 100.0))
    elif kind == "duplicate_id":
        e[1] = (e[0][0], e[1][1], e[0][2])
    elif kind == "rgb":
        e[0] = (e[0][0], e[0][1], (e[0][2][0] + 0.5, e[0][2][1], e[0][2][2]))
    elif kind == "padding_colour":
        e[-1] = ("0", e[-1][1], (99.0, 100.0, 100.0))
    elif kind == "duplicate_loc":
        e[1] = (e[1][0], e[0][1], e[1][2])
    elif kind == "unknown_id":
        e[0] = ("999", e[0][1], e[0][2])
    return e


@pytest.mark.parametrize("kind, code", [
    ("missing", "ti2_missing_sample_id"),
    ("duplicate_id", "ti2_duplicate_sample_id"),
    ("rgb", "ti2_rgb_mismatch"),
    ("padding_colour", "ti2_padding_not_white"),
    ("duplicate_loc", "ti2_location_duplicate"),
    ("unknown_id", "ti2_unknown_sample_id"),
])
def test_ti2_check_refusals(tmp_path, kind, code):
    rows = _ti1_rows(7)
    _write_ti1(tmp_path / "a.ti1", rows)
    _write_ti2(tmp_path / "a.ti2", _mutated(rows, kind), steps=3, passes=[2, 1])
    with pytest.raises(P.PrinttargChartError) as e:
        P.check_ti2(P.read_ti1_patches(tmp_path / "a.ti1"), tmp_path / "a.ti2",
                    instrument="i1pro3plus")
    assert e.value.code == code


@pytest.mark.parametrize("kw, code", [
    ({"instrument": "X-Rite ColorMunki"}, "ti2_keyword_unexpected"),
    ({"strip_pattern": "A-Z, 1-9"}, "ti2_keyword_unexpected"),
    ({"passes": [2, 2]}, "ti2_set_count"),
])
def test_ti2_check_header_refusals(tmp_path, kw, code):
    rows = _ti1_rows(7)
    _write_ti1(tmp_path / "a.ti1", rows)
    args = {"steps": 3, "passes": [2, 1]}
    args.update(kw)
    _write_ti2(tmp_path / "a.ti2", _layout_entries(rows, 3, [2, 1]), **args)
    with pytest.raises(P.PrinttargChartError) as e:
        P.check_ti2(P.read_ti1_patches(tmp_path / "a.ti1"), tmp_path / "a.ti2",
                    instrument="i1pro3plus")
    assert e.value.code == code


# ─── patch positions from pixels (synthetic page) ────────────────────────────
def _synthetic_page(patches, labels, steps, *, pw=40, ph=48, gap=8, x0=20, y0=60):
    """White page, strips as columns (left to right in label order), patch 1 on top."""
    h = y0 + steps * (ph + gap) + 40
    w = x0 + len(labels) * pw + 20
    page = np.full((h, w, 3), P.WHITE16, np.uint16)
    col = {lab: i for i, lab in enumerate(labels)}
    for p in patches:
        x = x0 + col[p["strip"]] * pw
        y = y0 + (p["patch"] - 1) * (ph + gap)
        page[y:y + ph, x:x + pw] = np.array(P._q16(p["rgb"]), np.uint16)
    page[y0 - 6:y0 - 4, :] = 0                       # a black rule, like spacers/text
    return page


def _page_fixture(n=20, steps=5, n_strips=4):
    rows = _ti1_rows(n)
    labels = P.strip_labels(n_strips)
    patches = []
    for i, (sid, rgb) in enumerate(rows):
        patches.append({"sample_id": sid, "sample_loc": f"{labels[i // steps]}{i % steps + 1}",
                        "strip": labels[i // steps], "patch": i % steps + 1, "page": 1,
                        "rgb": list(rgb)})
    mult = {}
    for p in patches:
        mult[P._key16(p["rgb"])] = mult.get(P._key16(p["rgb"]), 0) + 1
    return patches, labels, steps, mult


def test_patch_positions_verified_from_pixels():
    patches, labels, steps, mult = _page_fixture()
    page = _synthetic_page(patches, labels, steps)
    rep = P.verify_page_patches(page, patches, labels, steps=steps, dpi=100,
                                multiplicity=mult, step=2)
    assert rep["located"] == len(patches) and rep["strips"] == 4
    assert rep["patch_1_at"] == "top"


def test_patch_positions_detect_swapped_patches():
    patches, labels, steps, mult = _page_fixture()
    drawn = [dict(p) for p in patches]
    drawn[1]["patch"], drawn[3]["patch"] = drawn[3]["patch"], drawn[1]["patch"]
    page = _synthetic_page(drawn, labels, steps)
    with pytest.raises(P.PrinttargChartError) as e:
        P.verify_page_patches(page, patches, labels, steps=steps, dpi=100,
                              multiplicity=mult, step=2)
    # the swap breaks the order on the strip's pitch (or the pitch itself)
    assert e.value.code in ("patch_map_order", "patch_map_pitch_inconsistent")


def test_patch_positions_detect_wrong_strip():
    patches, labels, steps, mult = _page_fixture()
    drawn = [dict(p) for p in patches]
    drawn[0]["strip"], drawn[5]["strip"] = drawn[5]["strip"], drawn[0]["strip"]
    page = _synthetic_page(drawn, labels, steps)
    with pytest.raises(P.PrinttargChartError) as e:
        P.verify_page_patches(page, patches, labels, steps=steps, dpi=100,
                              multiplicity=mult, step=2)
    assert e.value.code == "patch_map_wrong_strip"


def test_patch_positions_detect_missing_patch():
    patches, labels, steps, mult = _page_fixture()
    page = _synthetic_page(patches[:-1], labels, steps)
    with pytest.raises(P.PrinttargChartError) as e:
        P.verify_page_patches(page, patches, labels, steps=steps, dpi=100,
                              multiplicity=mult, step=2)
    assert e.value.code == "patch_missing_on_page"


# ─── stacking ────────────────────────────────────────────────────────────────
def _write_page(path, arr, dpi=100):
    # printtarg writes its resolution in pixels per centimetre
    tifffile.imwrite(path, arr, photometric="rgb", compression=None,
                     resolution=(dpi / 2.54, dpi / 2.54), resolutionunit="CENTIMETER")


def test_stack_pages_copies_pixels_and_embeds_icc(tmp_path):
    rng = np.random.default_rng(1)
    pages = []
    for i, h in enumerate((30, 30, 30)):
        arr = rng.integers(0, 65536, size=(h, 17, 3), dtype=np.uint16)
        pages.append(tmp_path / f"p_{i + 1:02d}.tif")
        _write_page(pages[-1], arr)
    icc = RESIDENT.read_bytes()
    out = tmp_path / "chart.tif"
    res = P.stack_pages(pages, out, dpi=100, gap_px=5, icc_bytes=icc, chunk_bytes=7 * 17 * 6)
    assert res["height_px"] == 3 * 30 + 2 * 5 and res["offsets_px"] == [0, 35, 70]
    stacked = tifffile.imread(out)
    for p, y0 in zip(pages, res["offsets_px"]):
        assert np.array_equal(stacked[y0:y0 + 30], tifffile.imread(p))
    assert (stacked[30:35] == P.WHITE16).all() and (stacked[65:70] == P.WHITE16).all()
    with tifffile.TiffFile(out) as t:
        pg = t.pages[0]
        assert bytes(pg.tags[34675].value) == icc
        assert pg.tags[282].value == (100, 1) and int(pg.tags[296].value) == 2   # inch
        assert pg.dtype == np.uint16 and pg.compression == 1
    # the printing path reads it with the right physical size
    from lib.z9_client.printing import TiffInfo
    info = TiffInfo.from_path(out)
    assert abs(info.width_mm - 17 / 100 * 25.4) < 1e-6
    with pytest.raises(P.PrinttargChartError) as e:
        P.stack_pages(pages, out, dpi=100, gap_px=5, icc_bytes=icc)
    assert e.value.code == "output_exists"


@pytest.mark.parametrize("rotation, k", [("cw", -1), ("ccw", 1)])
def test_stack_pages_turns_pages_exactly(tmp_path, rotation, k):
    """Turned pages: every pixel kept, no mirror. Checked against numpy's rot90 and
    against explicit corners (clockwise: the page's top-left lands top-right)."""
    rng = np.random.default_rng(2)
    arrs = [rng.integers(0, 65536, size=(11, 23, 3), dtype=np.uint16) for _ in range(2)]
    pages = []
    for i, a in enumerate(arrs):
        pages.append(tmp_path / f"p_{i + 1:02d}.tif")
        _write_page(pages[-1], a)
    out = tmp_path / "chart.tif"
    res = P.stack_pages(pages, out, dpi=100, gap_px=0, icc_bytes=b"icc", rotation=rotation,
                        chunk_bytes=5 * 11 * 6)
    chart = tifffile.imread(out)
    assert chart.shape == (2 * 23, 11, 3) and res["offsets_px"] == [0, 23]
    for a, y0 in zip(arrs, res["offsets_px"]):
        blk = chart[y0:y0 + 23]
        assert np.array_equal(blk, np.rot90(a, k=k))
        if rotation == "cw":     # top-left → top-right, top-right → bottom-right
            assert (blk[0, -1] == a[0, 0]).all() and (blk[-1, -1] == a[0, -1]).all()
            assert (blk[0, 0] == a[-1, 0]).all()
        else:                    # top-left → bottom-left, top-right → top-left
            assert (blk[-1, 0] == a[0, 0]).all() and (blk[0, 0] == a[0, -1]).all()
    assert sorted(map(tuple, chart.reshape(-1, 3))) == sorted(
        map(tuple, np.concatenate([a.reshape(-1, 3) for a in arrs])))   # same pixels


def test_stack_pages_refuses_different_widths(tmp_path):
    a, b = tmp_path / "a.tif", tmp_path / "b.tif"
    _write_page(a, np.zeros((10, 12, 3), np.uint16))
    _write_page(b, np.zeros((10, 13, 3), np.uint16))
    with pytest.raises(P.PrinttargChartError) as e:
        P.stack_pages([a, b], tmp_path / "o.tif", dpi=100, gap_px=2, icc_bytes=b"x")
    assert e.value.code == "page_format_unexpected"
    assert not (tmp_path / "o.tif").exists()


def test_orchestrate_refusals_before_any_file(tmp_path):
    ti1 = tmp_path / "a.ti1"
    _write_ti1(ti1, _ti1_rows(10))
    root = tmp_path / "charts"
    for kw, code in (({"instrument": "spyder"}, "unsupported_instrument"),
                     ({"media_key": "a3"}, "unsupported_media"),
                     ({"reference_icc_path": tmp_path / "none.icc"}, "resident_profile_missing")):
        args = dict(instrument="i1pro3plus", media_key="roll24", reference_icc_path=RESIDENT,
                    charts_root=root, now=NOW)
        args.update(kw)
        with pytest.raises(P.PrinttargChartError) as e:
            P.orchestrate_printtarg_chart(ti1, **args)
        assert e.value.code == code
    assert not root.exists()


# ─── composed chart: orientation is checked on the final pixels ──────────────
def _cw_chart(page):
    return np.ascontiguousarray(np.rot90(page, k=-1))


@pytest.mark.parametrize("wrong, code", [
    (lambda pg: np.ascontiguousarray(pg.transpose(1, 0, 2)), None),   # transposition
    (lambda pg: np.ascontiguousarray(np.fliplr(_cw_chart(pg))), None),  # mirror left/right
    (lambda pg: np.ascontiguousarray(np.flipud(_cw_chart(pg))), None),  # mirror top/bottom
    (lambda pg: np.ascontiguousarray(np.rot90(pg, k=1)), None),        # counter-clockwise
    (lambda pg: np.ascontiguousarray(np.rot90(pg, k=2)), None),        # 180° (not 90°)
])
def test_composed_chart_rejects_mirrors_and_wrong_turns(wrong, code):
    patches, labels, steps, mult = _page_fixture(n=24, steps=6, n_strips=4)
    page = _synthetic_page(patches, labels, steps)
    good = _cw_chart(page)
    blocks = [{"page": 1, "rotation": "cw", "y_px": 0, "h_px": good.shape[0]}]
    rep = P.verify_composed_chart(good, blocks, [patches], [labels], steps=steps, dpi=100,
                                  multiplicity=mult)
    assert rep[0]["located"] == len(patches)
    bad = wrong(page)
    blocks = [{"page": 1, "rotation": "cw", "y_px": 0, "h_px": bad.shape[0]}]
    with pytest.raises(P.PrinttargChartError) as e:
        P.verify_composed_chart(bad, blocks, [patches], [labels], steps=steps, dpi=100,
                                multiplicity=mult)
    assert e.value.code.startswith("patch_map_")


def test_strip_geometry_of_a_clockwise_chart():
    """Clockwise: strip A on top, strips downwards in label order, patch 1 on the right."""
    patches, labels, steps, mult = _page_fixture(n=24, steps=6, n_strips=4)
    page = _synthetic_page(patches, labels, steps)
    rep = P.verify_page_patches(page, patches, labels, steps=steps, dpi=100,
                                multiplicity=mult, step=2)
    blocks = [{"page": 1, "rotation": "cw", "y_px": 0, "h_px": page.shape[1]}]
    strips = P.strips_in_chart(blocks, [rep], [labels], page_h_px=[page.shape[0]], dpi=100)
    assert [s["label"] for s in strips] == labels
    tops = [s["y_mm"][0] for s in strips]
    assert tops == sorted(tops) and all(s["patch_1_side"] == "right" for s in strips)
    chart = _cw_chart(page)
    # the first patch of strip A really sits at the right end of the top strip row
    a1 = next(p for p in patches if p["sample_loc"] == "A1")
    ys, xs = np.nonzero((chart == np.array(P._q16(a1["rgb"]), np.uint16)).all(axis=-1))
    s0 = strips[0]
    assert s0["y_mm"][0] <= ys.mean() * 25.4 / 100 <= s0["y_mm"][1]
    assert xs.mean() > chart.shape[1] / 2


class _FakeProber(P._LayoutProber):
    """printtarg's pagination stand-in: rows = floor((W − 20) / 16)."""
    def __init__(self, total):
        self.cache, self.log, self.total_strips, self.last_error = {}, [], total, ""

    def rows(self, w):
        self.log.append(w)
        return max(0, (w - 20) // 16)


@pytest.mark.parametrize("total, pages, per_page", [
    (40, 1, 40),        # reference: all strips on one page longer than the nominal one
    (5, 1, 5),          # small chart: a page shorter than the nominal one
    (37, 1, 37),        # exactly the nominal capacity
    (89, 2, 45),        # over the 1 m guard: two even pages (45 + 44), not 61 + 28
    (200, 4, 50),
])
def test_transverse_page_plan(total, pages, per_page):
    pr = _FakeProber(total)
    plan = P.plan_transverse_pages(pr, 610, max_page_mm=1000)
    assert (plan["pages"], plan["strips_per_page"]) == (pages, per_page)
    w = plan["page_w_mm"]
    assert pr.rows(w) >= per_page > pr.rows(w - 1)          # the smallest page that fits
    assert w <= 1000


def test_printable_width_comes_from_media_and_margins():
    # roll24: 610 mm nominal minus the 5 + 5 mm mechanical ROLL margins
    assert P.printable_width_mm("roll24") == 600.0
    pre = P.PRESETS[("i1pro3plus", "roll24")]
    assert pre.page_w_mm - 2 * P.PRINTTARG_MARGIN_MM <= P.printable_width_mm("roll24")


# ─── real printtarg (skipped when Argyll is not installed) ───────────────────
_HAVE_ARGYLL = bool(find_argyll_binary("printtarg") and find_argyll_binary("targen"))
argyll = pytest.mark.skipif(not _HAVE_ARGYLL, reason="printtarg/targen (Argyll) not installed")


@pytest.fixture(scope="module")
def ti1_800(tmp_path_factory):
    d = tmp_path_factory.mktemp("targen")
    subprocess.run([find_argyll_binary("targen"), "-d2", "-G", "-f", "800", str(d / "t")],
                   check=True, capture_output=True, timeout=120)
    return d / "t.ti1"


def _orch(ti1, root, **kw):
    kw.setdefault("dpi", 100)
    kw.setdefault("now", NOW)
    return P.orchestrate_printtarg_chart(ti1, media_key="roll24", reference_icc_path=RESIDENT,
                                         gloss_enhancer="OFF", charts_root=root, **kw)


def _desc(res):
    return json.loads(Path(res.descriptor_path).read_text())


def _check_common(res, ti1, dpi=100):
    desc = _desc(res)
    d = Path(res.chart_dir)
    assert desc["layout"] == "printtarg" and "scanLayout" not in desc
    assert desc["color_management"]["tag_source"] == "resident-live"
    # every .ti1 SAMPLE_ID exactly once, with its RGB (quantized to 16 bits)
    ti1_rows = P.read_ti1_patches(ti1)
    by_id = {}
    for p in desc["patches_by_location"]:
        by_id.setdefault(p["sample_id"], []).append(p)
    assert all(len(by_id[sid]) == 1 for sid, _ in ti1_rows)
    assert all(P._q16(by_id[sid][0]["rgb"]) == P._q16(rgb) for sid, rgb in ti1_rows)
    assert len(by_id.get("0", [])) == desc["geometry"]["padding_count"]
    # composed TIFF = the pages, turned exactly as declared (numpy reference), in order
    g, comp = desc["geometry"], desc["composition"]
    chart = tifffile.memmap(d / desc["files"]["tiff"], mode="r")
    assert chart.shape == (g["tiff_h_px"], g["tiff_w_px"], 3)
    k = {"cw": -1, "ccw": 1}.get(comp["rotation"], 0)
    for blk in comp["blocks"]:
        page = tifffile.memmap(d / blk["file"], mode="r")
        assert np.array_equal(chart[blk["y_px"]:blk["y_px"] + blk["h_px"]], np.rot90(page, k=k))
    assert sum(b["h_px"] for b in comp["blocks"]) + comp["gap_px"] * (len(comp["blocks"]) - 1) \
        == g["tiff_h_px"]
    assert g["tiff_w_mm"] <= desc["media"]["printable_width_mm"]
    with tifffile.TiffFile(d / desc["files"]["tiff"]) as t:
        tg = t.pages[0].tags
        assert tg[282].value == (dpi, 1) and tg[283].value == (dpi, 1) and int(tg[296].value) == 2
        assert bytes(tg[34675].value) == RESIDENT.read_bytes()
    # originals kept, hashes recorded
    for f, h in zip(desc["files"]["pages"], desc["provenance"]["pages_sha256"]):
        assert P._sha256(d / f) == h
    assert P._sha256(d / desc["files"]["ti2"]) == desc["provenance"]["ti2_sha256"]
    assert not (d / "printtarg" / "probes").exists()
    # the printtarg command is recorded as run
    argv = desc["printtarg"]["argv"]
    assert argv[1:4] == ["-v", f"-i{desc['instrument']['printtarg_selector']}", "-L"]
    assert f"-T{dpi}" in argv and "-C" in argv
    # final pixels: every locatable patch found again, block by block
    assert [f["located"] for f in desc["patch_map"]["final_chart"]] == \
        [p["located"] for p in desc["patch_map"]["per_page"]]
    return desc


def _check_transverse(desc):
    g, comp, strips = desc["geometry"], desc["composition"], desc["strips"]
    assert desc["roll_layout"] == "transverse" and comp["rotation"] == "cw"
    assert "-M" in desc["printtarg"]["argv"] and comp["gap_px"] == 0
    # strips cross the roll: in label order from the top, patch 1 on the right
    assert [s["label"] for s in strips] == P.strip_labels(g["total_strips"])
    tops = [s["y_mm"][0] for s in strips]
    assert tops == sorted(tops) and tops[0] > 0
    assert all(s["patch_1_side"] == "right" for s in strips)
    assert all(0 < s["x_mm"][0] < s["x_mm"][1] < g["tiff_w_mm"] for s in strips)
    assert strips[-1]["y_mm"][1] < g["tiff_h_mm"]
    # the composed width is the printtarg page height (margins included)
    assert abs(g["tiff_w_mm"] - desc["printtarg"]["page_h_mm"]) < 0.3
    assert abs(g["tiff_h_mm"] - sum(b["h_mm"] for b in comp["blocks"])) < 0.01
    # pagination chosen by probes, confirmed by the real run
    ls = desc["layout_search"]
    assert ls["pages"] == g["n_pages"] and ls["strips_total"] == g["total_strips"]
    assert max(g["passes_in_strips"]) - min(g["passes_in_strips"]) < g["n_pages"]
    assert desc["printtarg"]["page_w_mm"] == ls["page_w_mm"] <= ls["max_page_length_mm"]


def _ti2_rows(desc, chart_dir):
    _, _, rows = P._read_cgats(Path(chart_dir) / desc["files"]["ti2"])
    return rows


@argyll
def test_real_i1pro3plus_reference_transverse(tmp_path, ti1_800):
    """Reference: 800 patches → 20 per strip, 40 strips, now ONE page whose
    strips cross a 24" roll, instead of two 488 mm pages stacked (≈ 998 mm of roll)."""
    res = _orch(ti1_800, tmp_path / "charts", instrument="i1pro3plus")
    desc = _check_common(res, ti1_800)
    _check_transverse(desc)
    g = desc["geometry"]
    assert (g["steps_in_pass"], g["total_strips"], g["n_pages"]) == (20, 40, 1)
    assert g["passes_in_strips"] == [40] and g["padding_count"] == 0
    assert desc["patch_map"]["located"] >= 790        # 4 whites + 4 blacks cannot be located
    assert abs(g["tiff_w_mm"] - 500) < 0.3            # strip length across the roll
    assert g["tiff_h_mm"] < 700 and g["roll_length_mm"] < 710   # was ≈ 998 mm
    page = desc["patch_map"]["per_page"][0]
    assert page["strip_patch_span_mm"] < 515          # printed span, NOT the scan length


@argyll
def test_real_transverse_keeps_the_ti2_of_the_previous_assembly(tmp_path, ti1_800):
    """Same chart id → same random start: the .ti2 data rows (SAMPLE_ID, SAMPLE_LOC,
    RGB) are identical whether printtarg paginates on 610 mm or on one long page."""
    new = _orch(ti1_800, tmp_path / "a", instrument="i1pro3plus")
    old = _orch(ti1_800, tmp_path / "b", instrument="i1pro3plus", roll_layout="pages")
    dn, do = _desc(new), _desc(old)
    assert _ti2_rows(dn, new.chart_dir) == _ti2_rows(do, old.chart_dir)
    strip_of = lambda d: [{k: p[k] for k in p if k != "page"} for p in d["patches_by_location"]]
    assert strip_of(dn) == strip_of(do)
    assert do["roll_layout"] == "pages" and do["composition"]["rotation"] is None
    assert do["geometry"]["passes_in_strips"] == [37, 3] and "-M" not in do["printtarg"]["argv"]
    assert dn["geometry"]["roll_length_mm"] < do["geometry"]["roll_length_mm"] - 300


@argyll
def test_real_previous_assembly_still_available(tmp_path, ti1_800):
    """roll_layout="pages": the first increment's composition, unchanged."""
    res = _orch(ti1_800, tmp_path / "charts", instrument="i1pro3plus", roll_layout="pages")
    desc = _check_common(res, ti1_800)
    g = desc["geometry"]
    assert (g["steps_in_pass"], g["total_strips"], g["n_pages"]) == (20, 40, 2)
    assert desc["printtarg"]["argv"][4] == "-P" and desc["layout_search"] is None
    assert abs(g["tiff_w_mm"] - 598) < 0.3
    assert abs(g["tiff_h_mm"] - (2 * 488 + 12)) < 0.6
    assert all(s["patch_1_side"] == "top" for s in desc["strips"])


@argyll
def test_real_i1pro3plus_standard_short_strips(tmp_path, ti1_800):
    """Without -P the strips hold 9 patches: 89 strips exceed the 1 m page guard, so
    they are spread evenly over two turned pages (and roll use is worse than stacked
    pages here: short strips leave the roll width unused)."""
    res = _orch(ti1_800, tmp_path / "charts", instrument="i1pro3plus",
                page_h_mm=300, unlimited_strip=False)
    desc = _check_common(res, ti1_800)
    _check_transverse(desc)
    g = desc["geometry"]
    assert (g["steps_in_pass"], g["total_strips"], g["n_pages"]) == (9, 89, 2)
    assert sorted(g["passes_in_strips"]) == [44, 45] and g["padding_count"] == 1
    assert "-P" not in desc["printtarg"]["argv"]


@argyll
def test_real_colormunki_450mm(tmp_path, ti1_800):
    res = _orch(ti1_800, tmp_path / "charts", instrument="colormunki")
    desc = _check_common(res, ti1_800)
    _check_transverse(desc)
    g = desc["geometry"]
    assert (g["steps_in_pass"], g["total_strips"], g["n_pages"]) == (26, 31, 1)
    assert g["padding_count"] == 6 and abs(g["tiff_w_mm"] - 450) < 0.3
    assert desc["instrument"]["ti2_target_instrument"] == "X-Rite ColorMunki"
    assert "-P" not in desc["printtarg"]["argv"]


@argyll
def test_real_several_even_pages(tmp_path, ti1_800):
    """A lower page guard forces several pages: even shares, each turned, stacked."""
    res = _orch(ti1_800, tmp_path / "charts", instrument="i1pro3plus", max_page_length_mm=300)
    desc = _check_common(res, ti1_800)
    _check_transverse(desc)
    g = desc["geometry"]
    assert g["n_pages"] >= 3 and sum(g["passes_in_strips"]) == 40
    blocks = desc["composition"]["blocks"]
    assert [b["y_px"] for b in blocks] == [sum(x["h_px"] for x in blocks[:i])
                                           for i in range(len(blocks))]
    assert {s["block"] for s in desc["strips"]} == set(range(1, g["n_pages"] + 1))


def _targen(tmp_path, n):
    subprocess.run([find_argyll_binary("targen"), "-d2", "-G", "-f", str(n),
                    str(tmp_path / f"t{n}")], check=True, capture_output=True, timeout=120)
    return tmp_path / f"t{n}.ti1"


@argyll
@pytest.mark.parametrize("n, strips, padding", [
    (95, 5, 5),         # small chart: a page shorter than the nominal one, partial strip
    (740, 37, 0),       # exactly the nominal page capacity, every strip full
    (790, 40, 10),      # not a multiple of 20: white SAMPLE_ID 0 padding
])
def test_real_transverse_patch_counts(tmp_path, n, strips, padding):
    ti1 = _targen(tmp_path, n)
    res = _orch(ti1, tmp_path / "charts", instrument="i1pro3plus")
    desc = _check_common(res, ti1)
    _check_transverse(desc)
    g = desc["geometry"]
    assert (g["total_strips"], g["n_pages"], g["padding_count"]) == (strips, 1, padding)
    assert desc["files"]["pages"] == [f"printtarg/{res.chart_id}.tif"]   # no _01 suffix
    # every patch with a unique, non-white RGB is located (targen -G: 4 whites, 4 blacks)
    keys = [P._key16(rgb) for _, rgb in P.read_ti1_patches(ti1)]
    white = P._key16((100, 100, 100))
    assert desc["patch_map"]["located"] == sum(
        1 for k in keys if keys.count(k) == 1 and k != white)
    pad = [p for p in desc["patches_by_location"] if p["sample_id"] == "0"]
    assert len(pad) == padding and all(P._q16(p["rgb"]) == (65535,) * 3 for p in pad)
    if n == 95:
        assert desc["printtarg"]["page_w_mm"] < 610 and g["tiff_h_mm"] < 150


@argyll
def test_real_reproducible_from_the_recorded_command(tmp_path, ti1_800):
    a = _desc(_orch(ti1_800, tmp_path / "a", instrument="i1pro3plus"))
    b = _desc(_orch(ti1_800, tmp_path / "b", instrument="i1pro3plus"))
    assert a["printtarg"]["argv"][1:] == b["printtarg"]["argv"][1:]
    assert a["patches_by_location"] == b["patches_by_location"]
    assert a["strips"] == b["strips"]


@argyll
@pytest.mark.parametrize("layout", ["transverse", "pages"])
def test_real_printtarg_failure_leaves_nothing(tmp_path, ti1_800, layout):
    root = tmp_path / "charts"
    with pytest.raises(P.PrinttargChartError) as e:
        _orch(ti1_800, root, instrument="i1pro3plus", page_h_mm=50, roll_layout=layout)
    assert e.value.code == "printtarg_failed"
    assert "Paper size" in e.value.detail
    assert list(root.iterdir()) == []


@argyll
def test_real_existing_chart_is_never_overwritten(tmp_path, ti1_800):
    root = tmp_path / "charts"
    res = _orch(ti1_800, root, instrument="colormunki")
    before = {p: p.stat().st_mtime_ns for p in Path(res.chart_dir).rglob("*")}
    with pytest.raises(P.PrinttargChartError) as e:
        _orch(ti1_800, root, instrument="colormunki")        # same `now` → same chart id
    assert e.value.code == "chart_dir_exists"
    assert {p: p.stat().st_mtime_ns for p in Path(res.chart_dir).rglob("*")} == before


def test_single_table_ti1_is_refused_before_printtarg(tmp_path):
    ti1 = tmp_path / "one.ti1"
    _write_ti1(ti1, _ti1_rows(10))
    root = tmp_path / "charts"
    with pytest.raises(P.PrinttargChartError) as e:
        P.orchestrate_printtarg_chart(ti1, instrument="i1pro3plus", media_key="roll24",
                                      reference_icc_path=RESIDENT, charts_root=root, now=NOW)
    assert e.value.code == "ti1_missing_targen_tables"
    assert not root.exists()


@argyll
@pytest.mark.skipif(os.environ.get("FREEGLAZ_TEST_PRINTTARG_300DPI") != "1",
                    reason="opt-in: writes about 0.6 GB (FREEGLAZ_TEST_PRINTTARG_300DPI=1)")
def test_real_i1pro3plus_reference_at_300_dpi(tmp_path, ti1_800):
    res = _orch(ti1_800, tmp_path / "charts", instrument="i1pro3plus", dpi=300)
    desc = _check_common(res, ti1_800, dpi=300)
    _check_transverse(desc)
    g = desc["geometry"]
    w, h = g["page_tiff_px"][0]
    assert (g["tiff_w_px"], g["tiff_h_px"]) == (h, w)              # one page, turned
    assert g["passes_in_strips"] == [40] and abs(g["tiff_w_mm"] - 500) < 0.1
    assert math.isclose(g["tiff_h_mm"], w / 300 * 25.4, abs_tol=1e-3)
