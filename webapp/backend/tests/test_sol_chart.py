"""Free chart orchestration (sol_chart).

Generates chart + descriptor + TIFF in dry-run (NO Z9, no printing,
no scan). Checks the chain, the placement coherence renderer↔scanLayout,
feasibility (gap), the descriptor, and the library.
"""
from datetime import datetime
from pathlib import Path

import pytest

from lib.z9_client import sol_chart as SC
from lib.z9_client import chart_geometry_refonte as G
from lib.z9_client.sol_scanlayout import build_scan_fields_refonte

# Stand-in for the RESIDENT profile in tests (synthetic non-HP printer profile, mluc
# desc; in prod it's the slot profile read live). We check that the TIFF embeds
# THIS profile.
_ASSETS = Path(__file__).resolve().parents[3] / "lib" / "z9_client" / "assets"
_FIXTURES = Path(__file__).resolve().parent / "fixtures"
REF_ICC = _ASSETS / "sRGB_IEC61966-2.1.icc"
RESIDENT_STANDIN = _FIXTURES / "synthetic_test_resident_A.icc"   # ≠ sRGB → embed verifiable


def _orch(*a, **k):
    """orchestrate_free_chart with an injected resident stand-in (the
    geometry/descriptor tests do not evaluate colorimetric accuracy)."""
    k.setdefault("reference_icc_path", REF_ICC)
    k.setdefault("gloss_enhancer", "OFF")
    return SC.orchestrate_free_chart(*a, **k)


def _write_ti1(path: Path, n: int):
    """Small valid Argyll .ti1 (RGB %, SAMPLE_ID) — arbitrary composition."""
    lines = ['CGATS.17', 'KEYWORD "SAMPLE_ID"', 'COLOR_REP "RGB"',
             'NUMBER_OF_FIELDS 4', 'BEGIN_DATA_FORMAT',
             'SAMPLE_ID RGB_R RGB_G RGB_B', 'END_DATA_FORMAT',
             f'NUMBER_OF_SETS {n}', 'BEGIN_DATA']
    for i in range(n):
        v = round(100.0 * i / max(1, n - 1), 4)
        lines.append(f'{i+1} {v} {v} {v}')
    lines += ['END_DATA', '']
    path.write_text('\n'.join(lines), encoding='utf-8')


def test_orchestrate_full_chain(tmp_path):
    ti1 = tmp_path / "c.ti1"
    _write_ti1(ti1, 60)
    res = _orch(
        ti1, media_key="a3", columns=10, source="test",
        paper={"name": "Papier Test"}, charts_root=tmp_path / "charts",
        now=datetime(2026, 6, 6, 14, 32, 7, 123456),
    )
    # DERIVED cols (fixed-pitch model) = 18 native a3; `columns=10` ignored; 60 patches → rows
    assert res.n_patches == 60 and res.cols == 18
    assert res.feasibility.ok
    # produced files
    cdir = Path(res.chart_dir)
    assert (cdir / "chart.json").is_file()
    assert (cdir / "chart.tif").is_file()
    assert Path(res.preview_path).is_file()
    # scanLayout reflects N (PatchesPerRow = derived cols)
    sf = dict(res.scan_fields)
    assert sf["NumPatches"] == "60" and sf["PatchesPerRow"] == "18"
    assert sf["ZeroReference"] == "MediaEdges"


def test_placement_single_source_coherence(tmp_path):
    """The descriptor's scanLayout == build_scan_fields_refonte(SAME offset):
    a single placement source (renderer/scanLayout/print)."""
    import json
    ti1 = tmp_path / "c.ti1"
    _write_ti1(ti1, 60)
    res = _orch(ti1, "a3", 10, charts_root=tmp_path / "ch")
    desc = json.loads(Path(res.descriptor_path).read_text())
    pl = desc["scanLayout"]["placement"]
    layout = G.compute_layout(G.MEDIA["a3"], patch_count=60, columns=10)
    # X centered; Y TOP-ANCHORED (fixed head, independent of height)
    assert pl["offset_x_mm"] == pytest.approx((297.0 - layout.chart_w_mm) / 2.0)
    assert pl["offset_y_mm"] == pytest.approx(G.HEAD_MARGIN_MM)
    assert pl["mode"] == "top_anchored"
    # recomputation with the SAME offset → same fields as stored
    recomputed = dict(build_scan_fields_refonte(
        layout, offset_x_mm=pl["offset_x_mm"], offset_y_mm=pl["offset_y_mm"]))
    assert desc["scanLayout"]["fields"] == recomputed


def test_model_guarantees_no_overlap():
    """FIXED-PITCH model: cols is DERIVED from the native pitch (columns is no
    longer an input) → the mark↔patch margin is ALWAYS ≥ native gap (2.2 mm). The old
    risk "too many columns → overlap" is eliminated BY CONSTRUCTION."""
    for fmt in ("a4", "a3", "a2", "roll24", "roll17"):
        feas = SC.check_feasibility(G.compute_layout(G.MEDIA[fmt], patch_count=2))
        assert feas.gap_mm >= 2.2 - 0.01, f"{fmt}: gap {feas.gap_mm:.3f} < 2,2"


def test_feasibility_helper_gap():
    """Fixed-pitch model, CENTERED block: gap = residual/2 symmetric (output, not imposed).
    a3 centered = 3.06 mm; no format goes below the safeguard (> 0.5 mm)."""
    a3 = SC.check_feasibility(G.compute_layout(G.MEDIA["a3"], 200))
    assert a3.ok and abs(a3.gap_mm - 3.06) < 0.05
    for fmt in ("a4", "a2", "roll24", "roll17"):
        assert SC.check_feasibility(G.compute_layout(G.MEDIA[fmt], 2)).gap_mm > 0.5


def test_format_capacity_table():
    """Computed capacity (DERIVED cols × rows) — frozen values (anti-regression).
    Fixed-pitch model, cols packed to max (+0.5): a4=12, a2=27, roll24=40, roll17=28."""
    assert SC.format_capacity("a4")["max_patches"] == 204    # 12 × 17
    assert SC.format_capacity("a3")["max_patches"] == 468    # 18 × 26  (≥ 464 validated)
    assert SC.format_capacity("a2")["max_patches"] == 1080   # 27 × 40
    for roll in ("roll24", "roll17"):
        cap = SC.format_capacity(roll)
        assert cap["is_roll"] and cap["max_patches"] is None
        assert cap["rows_per_m"] > 0


def test_capacity_matches_empirical():
    """The table must match the empirical: A3/464 fits; A4/220 overflows, A4/128 yes.
    (a4 cols=12, max 17 rows = 204 patches.)"""
    assert SC.check_feasibility(G.compute_layout(G.MEDIA["a3"], 464)).ok          # validated
    bad = SC.check_feasibility(G.compute_layout(G.MEDIA["a4"], 220))
    assert not bad.ok and not bad.height_ok                                       # 19 rows > 17
    assert SC.check_feasibility(G.compute_layout(G.MEDIA["a4"], 128)).ok          # 11 rows


def test_smallest_format_for():
    assert SC.smallest_format_for(150) == "a4"     # ≤ 204
    assert SC.smallest_format_for(300) == "a3"     # 204 < 300 ≤ 468
    assert SC.smallest_format_for(600) == "a2"     # 468 < 600 ≤ 1080
    assert SC.smallest_format_for(5000) == "roll24"  # > all sheets → roll


def test_a4_supported(tmp_path):
    """A4 = smallest supported sheet (210 mm) — conforming geometry + generation."""
    assert "a4" in G.MEDIA
    layout = G.compute_layout(G.MEDIA["a4"], patch_count=120)
    assert layout.media.width_mm == 210.0 and layout.media.height_mm == 297.0
    assert layout.chart_w_mm == pytest.approx(190.0)         # 210 − 2·10
    assert layout.cols == 12                                  # derived pitch 13.8, packed to max (+0.5)
    feas = SC.check_feasibility(layout)
    # fixed-pitch a4 model: cols=12 at pitch 13.8, CENTERED block → gap = residual/2 symmetric
    # ≈ 2.97 mm (columns packed to max, tight gaps ~HP).
    assert feas.ok and feas.gap_mm == pytest.approx(2.97, abs=0.05)
    # end-to-end generation
    ti1 = tmp_path / "c.ti1"
    _write_ti1(ti1, 120)
    res = _orch(ti1, "a4", 11, charts_root=tmp_path / "ch")   # 11 ignored (derived cols)
    assert res.n_patches == 120 and res.cols == 12
    assert res.feasibility.ok


def test_resident_icc_required(tmp_path):
    """Profiling: no sRGB by default → reference_icc_path mandatory."""
    ti1 = tmp_path / "c.ti1"
    _write_ti1(ti1, 40)
    with pytest.raises(ValueError, match="[Rr]ésident requis|reference_icc_path"):
        SC.orchestrate_free_chart(ti1, "a3", 8, reference_icc_path=None,
                                  charts_root=tmp_path / "ch")


def test_icc_name_mluc_parsed():
    """Profile name extracted from the mluc desc tag (UTF-16BE, ICC v4):
    non-empty (fixes the offset bug that produced '')."""
    from lib.z9_client.printing import _get_icc_profile_description
    resident = (RESIDENT_STANDIN).read_bytes()
    name = _get_icc_profile_description(resident)
    assert name and "synthetic" in name.lower() and "srgb" not in name.lower()
    # ICC ASCII/other profiles remain readable (no regression)
    srgb = _get_icc_profile_description(REF_ICC.read_bytes())
    assert "srgb" in srgb.lower()


def test_resident_tag_embedded_and_traced(tmp_path):
    """The TIFF embeds the provided RESIDENT profile (not sRGB); the descriptor records
    tag_source=resident-live + GE."""
    import json
    from lib.z9_client.printing import _get_icc_profile_description
    from lib.z9_client.chart_render_refonte import save_tiff_refonte  # noqa (coupling)
    import tifffile
    ti1 = tmp_path / "c.ti1"
    _write_ti1(ti1, 48)
    res = SC.orchestrate_free_chart(
        ti1, "a3", 8, reference_icc_path=RESIDENT_STANDIN, gloss_enhancer="FULLPAGE",
        charts_root=tmp_path / "ch")
    # ICC embedded in the TIFF == the provided resident (≠ sRGB)
    with tifffile.TiffFile(res.tiff_path) as tf:
        embedded = bytes(tf.pages[0].tags[34675].value)
    assert embedded == RESIDENT_STANDIN.read_bytes()
    name = _get_icc_profile_description(embedded)
    assert "srgb" not in name.lower()
    # descriptor recorded
    cm = json.loads(Path(res.descriptor_path).read_text())["color_management"]
    assert cm["tag_source"] == "resident-live"
    assert cm["gloss_enhancer"] == "FULLPAGE"
    assert cm["icc_name"] == name


def test_chart_id_format():
    cid = SC.generate_chart_id(datetime(2026, 6, 6, 9, 5, 3, 0))
    assert cid.startswith("CHT-20260606-0905-")
    assert len(cid) == len("CHT-20260606-0905-XX")


def test_descriptor_contents(tmp_path):
    import json
    ti1 = tmp_path / "c.ti1"
    _write_ti1(ti1, 48)
    res = _orch(
        ti1, "a3", 8, source="targen -d2 -f48", paper={"name": "Baryta"},
        charts_root=tmp_path / "ch")
    d = json.loads(Path(res.descriptor_path).read_text())
    assert d["schema_version"] == SC.SCHEMA_VERSION
    assert d["source"] == "targen -d2 -f48"
    assert d["geometry"]["patch_count"] == 48
    assert len(d["patches_in_layout_order"]) == 48
    assert d["chart_id"] in d["printed_id"]["text"]
    assert "Baryta" in d["printed_id"]["text"]
    assert d["printed_id"]["rendered_on_tiff"] is True   # footer rendered
    assert "Spacing" in d["scanLayout"]["emu"]


def test_library_list_and_load(tmp_path):
    root = tmp_path / "charts"
    _write_ti1(tmp_path / "a.ti1", 40)
    _write_ti1(tmp_path / "b.ti1", 50)
    r1 = _orch(tmp_path / "a.ti1", "a3", 8, charts_root=root,
                                   now=datetime(2026, 6, 5, 10, 0, 0, 1))
    r2 = _orch(tmp_path / "b.ti1", "a3", 8, charts_root=root,
                                   now=datetime(2026, 6, 6, 10, 0, 0, 2))
    listed = SC.list_charts(charts_root=root)
    assert [c["chart_id"] for c in listed] == [r2.chart_id, r1.chart_id]  # date desc
    assert all(c["scanned"] is False for c in listed)
    # load by ID
    desc = SC.load_chart(r1.chart_id, charts_root=root)
    assert desc["chart_id"] == r1.chart_id
    with pytest.raises(FileNotFoundError):
        SC.load_chart("CHT-inconnu", charts_root=root)


def test_footer_rendered_and_patches_invariant(tmp_path):
    """Footer rendered + patches/scanLayout zone UNCHANGED (anti-regression)."""
    import json
    import numpy as np
    from lib.z9_client import chart_render_refonte as R
    from lib.z9_client.chart import read_ti1
    ti1 = tmp_path / "c.ti1"
    _write_ti1(ti1, 60)
    res = _orch(ti1, "a3", 10, paper={"name": "Papier é à ü"},
                                    charts_root=tmp_path / "ch")
    d = json.loads(Path(res.descriptor_path).read_text())
    assert d["printed_id"]["rendered_on_tiff"] is True

    # patches zone pixel-identical with/without footer + taller raster
    patches = read_ti1(ti1)
    layout = G.compute_layout(G.MEDIA["a3"], patch_count=60, columns=10)
    chart_only = R.render_refonte(patches, layout, dpi=300.0)
    with_footer = R.render_footer_band(chart_only, text=d["printed_id"]["text"],
                                       dpi=300.0, footer_h_mm=10.0)
    h0 = chart_only.shape[0]
    assert np.array_equal(with_footer[:h0], chart_only)        # patches/marks intact
    assert with_footer.shape[0] > chart_only.shape[0]          # footer added at the bottom
    assert with_footer.shape[1] == chart_only.shape[1]         # width unchanged
    assert (with_footer[h0:].sum(axis=2) < 200).any()          # text (black) in footer

    # scanLayout unchanged (derived from Layout, not from raster)
    pl = d["scanLayout"]["placement"]
    recomputed = dict(build_scan_fields_refonte(
        layout, offset_x_mm=pl["offset_x_mm"], offset_y_mm=pl["offset_y_mm"]))
    assert d["scanLayout"]["fields"] == recomputed


def test_height_overflow_rejected(tmp_path):
    """Chart too tall (bottom margin < scan margin) → height REFUSAL BEFORE rendering.
    Fixed-pitch model: a3 derived cols=18, max 26 rows (468 patches). 600 patches →
    ceil(600/18)=34 rows > 26 → overflows (columns ignored)."""
    ti1 = tmp_path / "c.ti1"
    _write_ti1(ti1, 600)
    with pytest.raises(ValueError, match="height|overflow"):
        _orch(ti1, "a3", 18, charts_root=tmp_path / "ch")   # 18 ignored (derived cols)


def test_footer_band_leaves_patch_zone_untouched():
    """render_footer_band: pure post-processing, zone [0:h] strictly intact."""
    import numpy as np
    from lib.z9_client.chart_render_refonte import render_footer_band
    chart = np.random.default_rng(0).integers(0, 256, (200, 300, 3), dtype=np.uint8)
    res = render_footer_band(chart, text="freeglaz TEST 42", dpi=300.0, footer_h_mm=10.0)
    assert np.array_equal(res[:200], chart)
    assert res.shape[0] > 200


def test_library_scanned_status(tmp_path):
    root = tmp_path / "charts"
    _write_ti1(tmp_path / "a.ti1", 40)
    r = _orch(tmp_path / "a.ti1", "a3", 8, charts_root=root)
    meas = Path(r.chart_dir) / "measurements"
    meas.mkdir()
    (meas / "20260606.ti3").write_text("CTI3\n")
    assert SC.list_charts(charts_root=root)[0]["scanned"] is True


# ─── VISUAL order of patches (boustrophedon + anti-run) ─────────────────────
def test_serpentine_clean_grid_boustrophedon():
    """Clean grid R,G,B ∈ {0,128,255} (27 unique): the serpentine alternates the
    direction of B at each (R,G) → no seam. First patches locked."""
    pat = [(f"{i}", r, g, b)
           for i, (r, g, b) in enumerate((r, g, b)
               for r in (0, 128, 255) for g in (0, 128, 255) for b in (0, 128, 255))]
    out = SC.order_patches_serpentine(pat)
    rgb = [(p[1], p[2], p[3]) for p in out]
    # plane R=0: g asc; b asc then desc then asc (boustrophedon)
    assert rgb[:9] == [(0, 0, 0), (0, 0, 128), (0, 0, 255),
                       (0, 128, 255), (0, 128, 128), (0, 128, 0),
                       (0, 255, 0), (0, 255, 128), (0, 255, 255)]
    # plane R switch (0→128) without G/B jump: (0,255,255) → (128,255,255)
    assert rgb[8] == (0, 255, 255) and rgb[9] == (128, 255, 255)


def test_serpentine_is_permutation_and_keeps_sample_rgb():
    """Reorders WITHOUT altering the set: same multiset, and each sample_id keeps
    its RGB (pairing coherence / Hypothesis C: placement only)."""
    pat = [(f"{i}", (i * 7) % 256, (i * 13) % 256, (i * 29) % 256) for i in range(60)]
    out = SC.order_patches_serpentine(pat)
    assert sorted(out) == sorted(pat)                  # exact permutation
    before = {p[0]: (p[1], p[2], p[3]) for p in pat}
    after = {p[0]: (p[1], p[2], p[3]) for p in out}
    assert before == after                             # sample_id ↔ rgb intact


def test_antirun_disperses_white_black_dups():
    """Real case: 4 whites + 4 blacks (targen alignment duplicates) + a gray ramp.
    After ordering: NO strictly identical consecutive RGB."""
    pat = ([(f"W{i}", 255, 255, 255) for i in range(4)]
           + [(f"K{i}", 0, 0, 0) for i in range(4)]
           + [(f"g{v}", v, v, v) for v in range(8, 250, 8)])
    out = SC.order_patches_serpentine(pat)
    rgbs = [(p[1], p[2], p[3]) for p in out]
    assert all(rgbs[i] != rgbs[i - 1] for i in range(1, len(rgbs))), "residual flat"
    assert sorted(out) == sorted(pat)                  # nothing lost
    assert rgbs.count((255, 255, 255)) == 4 and rgbs.count((0, 0, 0)) == 4


def test_orchestrate_applies_order_to_descriptor(tmp_path):
    """Integration: the descriptor (patches_in_layout_order) reflects the final order
    (serpentine + anti-run) -> stage 7 coherent by construction."""
    import json
    from lib.z9_client.chart import read_ti1
    # ti1 with white/black duplicates at the head (like targen) + ramp
    lines = ['CGATS.17', 'KEYWORD "SAMPLE_ID"', 'COLOR_REP "RGB"',
             'NUMBER_OF_FIELDS 4', 'BEGIN_DATA_FORMAT',
             'SAMPLE_ID RGB_R RGB_G RGB_B', 'END_DATA_FORMAT', 'BEGIN_DATA']
    rows = [(100, 100, 100)] * 4 + [(0, 0, 0)] * 4 + \
           [(round(100.0 * v / 255, 4),) * 3 for v in range(8, 200, 6)]
    for i, t in enumerate(rows):
        lines.append(f'{i+1} {t[0]} {t[1]} {t[2]}')
    lines += ['END_DATA', '']
    ti1 = tmp_path / "d.ti1"
    ti1.write_text("\n".join(lines))
    root = tmp_path / "ch"
    r = _orch(ti1, "a3", 8, charts_root=root)
    desc = json.loads((Path(r.chart_dir) / "chart.json").read_text())
    layout_rgb = [tuple(x["rgb"]) for x in desc["patches_in_layout_order"]]
    expected = [(p[1], p[2], p[3])
                for p in SC.order_patches_serpentine(read_ti1(ti1))]
    assert layout_rgb == expected
    assert all(layout_rgb[i] != layout_rgb[i - 1] for i in range(1, len(layout_rgb)))


def test_legacy_464_chart_path_does_not_reorder():
    """Path separation: chart.py (legacy 464, validated cube+121) does NOT call
    the serpentine order — reserved for FREE charts (orchestrate_free_chart)."""
    src = (Path(__file__).resolve().parents[3] / "lib" / "z9_client" / "chart.py").read_text()
    assert "order_patches_serpentine" not in src
