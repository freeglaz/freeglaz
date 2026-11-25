"""printtarg layout options of external-instrument charts: -a (patch size factor) and -h
(ColorMunki high density, staggered strips), the 580 mm ColorMunki roll strips and the paper
estimate made before any TIFF.

- the options are a per-instrument whitelist checked on the server: ColorMunki -a 1.0 / 0.9 /
  0.8 and -h, never both; i1Pro 3 Plus -a 1.0 only, no -h;
- the layout probes and the run that counts get the same printtarg options;
- a staggered strip keeps its own position in chart.json (each strip from its own patches);
- real charts (printtarg installed): every patch with a unique colour lies inside the strip the
  .ti2 names, in patch order, for the standard and the high-density layouts, on the roll and on
  an A4 sheet; the estimate announces what the generator then lays out.
These geometries are printtarg's: none of them is a proof that the instrument reads them.
Offline: no printer, no instrument."""
import subprocess
from pathlib import Path

import numpy as np
import pytest
import tifffile

from lib.z9_client import printtarg_chart as P
from lib.z9_client.argyll import find_argyll_binary
from webapp.backend.tests.test_printtarg_chart import (  # noqa: F401  (fixtures)
    RESIDENT, NOW, _check_common, _check_transverse, _desc, _drop_tiffs_after_test, _orch,
    _page_fixture, argyll, ti1_800)


# ─── whitelist ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("instrument, scale, hd, expected", [
    ("colormunki", None, None, (1.0, False)),
    ("colormunki", 0.9, False, (0.9, False)),
    ("colormunki", 0.8, None, (0.8, False)),
    ("colormunki", 1.0, True, (1.0, True)),
    ("colormunki", None, True, (1.0, True)),
    ("i1pro3plus", None, None, (1.0, False)),
])
def test_offered_options(instrument, scale, hd, expected):
    assert P.check_layout_options(instrument, scale, hd) == expected


@pytest.mark.parametrize("instrument, scale, hd, code", [
    ("colormunki", 0.85, False, "unsupported_patch_scale"),
    ("colormunki", 1.2, False, "unsupported_patch_scale"),
    ("colormunki", 0.9, True, "unsupported_option_combination"),
    ("colormunki", 0.8, True, "unsupported_option_combination"),
    ("i1pro3plus", 0.9, False, "unsupported_patch_scale"),
    ("i1pro3plus", 0.8, False, "unsupported_patch_scale"),
    ("i1pro3plus", None, True, "unsupported_high_density"),
    ("i1pro2", None, None, "unsupported_instrument"),
])
def test_refused_options(instrument, scale, hd, code):
    with pytest.raises(P.PrinttargChartError) as e:
        P.check_layout_options(instrument, scale, hd)
    assert e.value.code == code


def test_nominal_geometry_is_printtargs():
    """ArgyllCMS 3.5.0 printtarg.c: -a scales length, width and spacer, -h narrows the strip
    to 13.7 mm and shifts every other one by 0.5 x (length + 0.5 x spacer)."""
    g = P.patch_geometry
    assert g("colormunki") == {"patch_length_mm": 14.0, "spacer_mm": 1.0, "patch_width_mm": 28.0,
                               "strip_pitch_mm": 28.0, "step_mm": 15.0, "stagger_mm": 0.0}
    assert (g("colormunki", 0.9)["patch_length_mm"], g("colormunki", 0.9)["patch_width_mm"],
            g("colormunki", 0.9)["spacer_mm"]) == (12.6, 25.2, 0.9)
    assert (g("colormunki", 0.8)["patch_length_mm"], g("colormunki", 0.8)["patch_width_mm"],
            g("colormunki", 0.8)["step_mm"]) == (11.2, 22.4, 12.0)
    hd = g("colormunki", 1.0, True)
    assert (hd["patch_length_mm"], hd["patch_width_mm"], hd["stagger_mm"]) == (14.0, 13.7, 7.25)
    assert g("i1pro3plus") == {"patch_length_mm": 20.0, "spacer_mm": 2.0, "patch_width_mm": 16.0,
                               "strip_pitch_mm": 16.0, "step_mm": 22.0, "stagger_mm": 0.0}


def test_presets_and_offered_options():
    assert P.PRESETS[("colormunki", "roll24")] == P.LayoutPreset(610.0, 580.0, False)
    assert P.PRESETS[("colormunki", "a4")].page_h_mm == 190.0           # A4 preset unchanged
    assert P.PRESETS[("i1pro3plus", "roll24")] == P.LayoutPreset(610.0, 500.0, True)
    assert P.PRESETS[("colormunki", "roll24")].page_h_mm <= P.printable_width_mm("roll24")
    opts = {i["key"]: i["layout_options"] for i in P.supported_options()["instruments"]}
    cm, i1 = opts["colormunki"], opts["i1pro3plus"]
    assert (cm["patch_scales"], cm["default_patch_scale"], cm["high_density"]) == ([1.0, 0.9, 0.8], 1.0, True)
    assert cm["combine_high_density_and_scale"] is False and cm["physically_validated"] is False
    assert cm["geometry"]["high_density"]["patch_width_mm"] == 13.7
    assert set(cm["geometry"]["standard"]) == {"1", "0.9", "0.8"}
    assert (i1["patch_scales"], i1["high_density"]) == ([1.0], False) and "high_density" not in i1["geometry"]


# ─── printtarg arguments ────────────────────────────────────────────────────
def _argv(**kw):
    args = dict(instrument="colormunki", page_w_mm=745, page_h_mm=580, unlimited_strip=False,
                dpi=300, random_start=7, base="b", margin_in_tiff=True)
    args.update(kw)
    return P.build_printtarg_argv("printtarg", **args)


def test_argv_carries_the_options():
    assert _argv() == ["printtarg", "-v", "-iCM", "-L", "-M", "6", "-C", "-R", "7", "-T300",
                       "-p745x580", "b"]
    assert _argv(patch_scale=0.9) == ["printtarg", "-v", "-iCM", "-L", "-a", "0.9", "-M", "6",
                                      "-C", "-R", "7", "-T300", "-p745x580", "b"]
    assert _argv(high_density=True) == ["printtarg", "-v", "-iCM", "-L", "-h", "-M", "6", "-C",
                                        "-R", "7", "-T300", "-p745x580", "b"]
    for kw, code in (({"high_density": True, "patch_scale": 0.8}, "unsupported_option_combination"),
                     ({"instrument": "i1pro3plus", "unlimited_strip": True, "high_density": True},
                      "unsupported_high_density")):
        with pytest.raises(P.PrinttargChartError) as e:
            _argv(**kw)
        assert e.value.code == code


@pytest.mark.parametrize("kw", [{}, {"patch_scale": 0.8}, {"high_density": True}])
def test_probes_and_the_run_that_counts_get_the_same_options(tmp_path, kw):
    """A probe differs from the final run only by what renders the TIFF (-C, -T)."""
    prober = P._LayoutProber("printtarg", tmp_path, tmp_path / "x.ti1", instrument="colormunki",
                             page_h_mm=580, unlimited_strip=False, random_start=7,
                             margin_in_tiff=True, timeout=10, **kw)
    probe = P.build_printtarg_argv("printtarg", page_w_mm=745, dpi=None, base="b", **prober.kw)
    final = _argv(**kw)
    assert [a for a in final if a not in ("-C", "-T300")] == probe


# ─── staggered strips keep their own position (synthetic page) ──────────────
def _staggered_page(patches, labels, steps, *, pw=40, ph=48, gap=8, x0=20, y0=60, shift=28):
    """White page, strips as columns, patch 1 on top, every other strip moved down by
    ``shift`` px (printtarg -h)."""
    h = y0 + steps * (ph + gap) + shift + 40
    w = x0 + len(labels) * pw + 20
    page = np.full((h, w, 3), P.WHITE16, np.uint16)
    col = {lab: i for i, lab in enumerate(labels)}
    for p in patches:
        i = col[p["strip"]]
        x = x0 + i * pw
        y = y0 + (p["patch"] - 1) * (ph + gap) + (shift if i % 2 else 0)
        page[y:y + ph, x:x + pw] = np.array(P._q16(p["rgb"]), np.uint16)
    return page


def test_a_staggered_strip_keeps_its_own_position():
    patches, labels, steps, mult = _page_fixture(n=24, steps=6, n_strips=4)
    page = _staggered_page(patches, labels, steps)
    rep = P.verify_page_patches(page, patches, labels, steps=steps, dpi=100, multiplicity=mult, step=2)
    assert rep["staggered"] and abs(rep["strip_stagger_mm"] - 28 * 25.4 / 100) < 0.6
    areas = rep["_strip_areas_px"]
    assert abs((areas[1][0] - areas[0][0]) - 28) <= 2 and abs(areas[2][0] - areas[0][0]) <= 2
    blocks = [{"page": 1, "rotation": "cw", "y_px": 0, "h_px": page.shape[1]}]
    strips = P.strips_in_chart(blocks, [rep], [labels], page_h_px=[page.shape[0]], dpi=100)
    chart = np.rot90(page, k=-1)
    for p in patches:                         # every patch lies inside its own strip's box
        ys, xs = np.nonzero((chart == np.array(P._q16(p["rgb"]), np.uint16)).all(axis=-1))
        s = next(s for s in strips if s["label"] == p["strip"])
        x, y = xs.mean() * 25.4 / 100, ys.mean() * 25.4 / 100
        assert s["x_mm"][0] <= x <= s["x_mm"][1] and s["y_mm"][0] <= y <= s["y_mm"][1], p["sample_loc"]


def test_aligned_strips_keep_one_common_span():
    """Every chart made before -h: one span for all strips, as before."""
    from webapp.backend.tests.test_printtarg_chart import _synthetic_page
    patches, labels, steps, mult = _page_fixture(n=24, steps=6, n_strips=4)
    rep = P.verify_page_patches(_synthetic_page(patches, labels, steps), patches, labels,
                                steps=steps, dpi=100, multiplicity=mult, step=2)
    assert not rep["staggered"] and rep["strip_stagger_mm"] == 0.0
    assert all(list(a) == list(rep["_patch_area_px"]) for a in rep["_strip_areas_px"])


# ─── real printtarg ─────────────────────────────────────────────────────────
def _targen(tmp_path, n):
    subprocess.run([find_argyll_binary("targen"), "-d2", "-G", "-f", str(n), str(tmp_path / f"t{n}")],
                   check=True, capture_output=True, timeout=120)
    return tmp_path / f"t{n}.ti1"


def _patches_follow_the_ti2(desc, chart_dir):
    """Independently of the generator's own checks: each patch with a unique, non-white colour
    is inside the box chart.json gives its strip, and along each strip the patches follow their
    .ti2 numbers (patch 1 on the right)."""
    chart = np.asarray(tifffile.imread(Path(chart_dir) / desc["files"]["tiff"]))
    reg = P._color_regions(chart, 2)
    dpi = desc["media"]["dpi"]
    keys = [P._key16(p["rgb"]) for p in desc["patches_by_location"]]
    white = P._key16((100, 100, 100))
    box = {s["label"]: s for s in desc["strips"]}
    cands = []
    for p, k in zip(desc["patches_by_location"], keys):
        if k == white or keys.count(k) != 1:
            continue
        i = int(np.searchsorted(reg["keys"], k))
        assert int(reg["keys"][i]) == k, p["sample_loc"]
        cands.append((p, i))
    # a colour printtarg also paints elsewhere (its coloured spacers: cyan, magenta…) is not a
    # patch position: the same rule as the generator (verify_page_patches), on the count
    med = np.median([reg["counts"][i] for _, i in cands])
    along, checked = {}, 0
    for p, i in cands:
        if not 0.5 * med <= reg["counts"][i] <= 1.5 * med:
            continue
        x, y = reg["cx"][i] * 25.4 / dpi, reg["cy"][i] * 25.4 / dpi
        s = box[p["strip"]]
        assert s["x_mm"][0] <= x <= s["x_mm"][1] and s["y_mm"][0] <= y <= s["y_mm"][1], p["sample_loc"]
        along.setdefault(p["strip"], []).append((p["patch"], x))
        checked += 1
    for lab, pts in along.items():
        xs = [x for _, x in sorted(pts)]
        assert all(a > b for a, b in zip(xs, xs[1:])), lab
    return checked


@argyll
def test_real_colormunki_high_density_on_the_roll(tmp_path, ti1_800):
    res = _orch(ti1_800, tmp_path / "charts", instrument="colormunki", high_density=True)
    desc = _check_common(res, ti1_800)
    _check_transverse(desc)
    g, pt, geo = desc["geometry"], desc["printtarg"], desc["patch_geometry"]
    assert "-h" in pt["argv"] and "-a" not in pt["argv"] and (pt["patch_scale"], pt["high_density"]) == (1.0, True)
    assert (g["steps_in_pass"], g["total_strips"], g["n_pages"]) == (34, 24, 1)
    assert abs(g["tiff_w_mm"] - 580) < 0.3
    assert geo["nominal"]["patch_width_mm"] == 13.7 and geo["physically_validated"] is False
    assert geo["measured"]["staggered"] and abs(geo["measured"]["strip_stagger_mm"] - 7.25) < 0.4
    assert abs(geo["measured"]["strip_pitch_mm"] - 13.7) < 0.4
    # neighbouring strips are shifted along their length by the stagger, every other one aligned
    x0 = [s["x_mm"][0] for s in desc["strips"]]
    assert all(abs(abs(a - b) - 7.25) < 0.4 for a, b in zip(x0, x0[1:]))
    assert all(abs(a - b) < 0.4 for a, b in zip(x0, x0[2:]))
    assert _patches_follow_the_ti2(desc, res.chart_dir) > 700


@argyll
@pytest.mark.parametrize("scale, steps, strips, width", [(1.0, 34, 24, 28.0), (0.8, 43, 19, 22.4)])
def test_real_colormunki_standard_sizes_on_the_roll(tmp_path, ti1_800, scale, steps, strips, width):
    res = _orch(ti1_800, tmp_path / "charts", instrument="colormunki", patch_scale=scale)
    desc = _check_common(res, ti1_800)
    _check_transverse(desc)
    g, geo = desc["geometry"], desc["patch_geometry"]
    assert (g["steps_in_pass"], g["total_strips"]) == (steps, strips)
    assert ("-a" in desc["printtarg"]["argv"]) == (scale != 1.0)
    assert not geo["measured"]["staggered"] and abs(geo["measured"]["strip_pitch_mm"] - width) < 0.4
    assert abs(geo["measured"]["patch_pitch_mm"] - 15.0 * scale) < 0.3
    assert len({tuple(s["x_mm"]) for s in desc["strips"]}) == 1         # aligned strips
    assert _patches_follow_the_ti2(desc, res.chart_dir) > 700


@argyll
@pytest.mark.parametrize("hd, n, strips", [(False, 64, 8), (True, 128, 16)])
def test_real_colormunki_on_an_a4_sheet(tmp_path, hd, n, strips):
    ti1 = _targen(tmp_path, n)
    res = P.orchestrate_printtarg_chart(ti1, instrument="colormunki", media_key="a4",
                                        reference_icc_path=RESIDENT, gloss_enhancer="OFF",
                                        charts_root=tmp_path / "charts", dpi=100, now=NOW,
                                        high_density=hd)
    desc = _desc(res)
    g = desc["geometry"]
    assert (g["steps_in_pass"], g["total_strips"], g["n_pages"]) == (8, strips, 1)
    assert desc["printtarg"]["page_h_mm"] == 190.0 and ("-h" in desc["printtarg"]["argv"]) == hd
    assert g["tiff_h_mm"] <= P.sheet_chart_limits("a4")["max_h_mm"]
    assert desc["patch_geometry"]["physically_validated"] is (not hd)    # A4 standard: read in P2
    assert _patches_follow_the_ti2(desc, res.chart_dir) >= n - 8


@argyll
@pytest.mark.parametrize("kw", [{}, {"patch_scale": 0.9}, {"high_density": True}])
def test_the_estimate_announces_what_the_generator_lays_out(tmp_path, ti1_800, kw):
    est = P.estimate_printtarg_layout(instrument="colormunki", media_key="roll24", n_patches=800, **kw)
    res = _orch(ti1_800, tmp_path / "charts", instrument="colormunki", **kw)
    g = _desc(res)["geometry"]
    assert (est["patches_per_strip"], est["total_strips"], est["n_pages"]) == \
        (g["steps_in_pass"], g["total_strips"], g["n_pages"])
    assert abs(est["chart_h_mm"] - g["tiff_h_mm"]) < 1.0 and abs(est["chart_w_mm"] - g["tiff_w_mm"]) < 0.3
    assert abs(est["roll_length_mm"] - g["roll_length_mm"]) < 1.0
    # from the .ti1 itself: the same answer
    assert P.estimate_printtarg_layout(instrument="colormunki", media_key="roll24",
                                       ti1_path=ti1_800, **kw)["total_strips"] == g["total_strips"]


@argyll
def test_estimate_refusals():
    with pytest.raises(P.PrinttargChartError) as e:
        P.estimate_printtarg_layout(instrument="colormunki", media_key="a4", n_patches=300, high_density=True)
    assert e.value.code == "too_many_strips_for_sheet"
    for kw, code in (({"n_patches": 0}, "unsupported_patch_count"),
                     ({"n_patches": 100, "instrument": "i1pro3plus", "high_density": True}, "unsupported_high_density"),
                     ({"n_patches": 100, "instrument": "i1pro3plus", "media_key": "a4"}, "unsupported_media")):
        args = {"instrument": "colormunki", "media_key": "roll24", **kw}
        with pytest.raises(P.PrinttargChartError) as e:
            P.estimate_printtarg_layout(**args)
        assert e.value.code == code


def test_refused_options_leave_nothing(tmp_path):
    ti1 = tmp_path / "a.ti1"
    ti1.write_text("CTI1\n")
    for kw in ({"instrument": "i1pro3plus", "high_density": True},
               {"instrument": "colormunki", "patch_scale": 0.9, "high_density": True}):
        with pytest.raises(P.PrinttargChartError):
            _orch(ti1, tmp_path / "charts", **kw)
    assert not (tmp_path / "charts").exists()
