"""scanLayout generator (sol_scanlayout).

Two paths:
  - LEGACY (chart.py, 75 px mark center) = FROZEN EMU-PORT ORACLE: reproduces
    byte-exact the 464 POST validated live. Unit test of the conversion
    MECHANICS (EMU->inches, %g, order) — NOT the print path.
  - NATIVE-REWRITE (decision B) = print path: derived from the SAME geometry
    as render_refonte (coherent by construction). Golden value + grid
    proximity; the Spacing (-0.54 mm = conformity correction) will be validated
    empirically on the first real scan.
"""

import pytest

from lib.z9_client import chart_geometry_refonte as G
from lib.z9_client import sol_native as N
from lib.z9_client import sol_scanlayout as S


def test_oracle_464_byte_identical():
    """464/18/A3 generated == reference POST (byte-identical, 313 bytes)."""
    ref_body = N.build_scan_body(N.REFERENCE_SCAN_FIELDS)
    gen_body = N.build_scan_body(S.build_scan_fields(num_cols=18, patch_count=464,
                                                     paper="a3"))
    assert gen_body == ref_body
    assert len(gen_body) == 313


def test_oracle_fields_match_one_by_one():
    """Each field (key+value) of the generated set == reference, in the same order."""
    gen = S.build_scan_fields(num_cols=18, patch_count=464, paper="a3")
    assert gen == N.REFERENCE_SCAN_FIELDS


def test_parametric_counts_reflected():
    """Any N patches / N columns -> exact NumPatches/PatchesPerRow,
    no hardcoded number."""
    for cols, n in [(11, 200), (25, 1000), (7, 49), (40, 5000)]:
        fields = dict(S.build_scan_fields(num_cols=cols, patch_count=n, paper="a3"))
        assert fields["NumPatches"] == str(n)
        assert fields["PatchesPerRow"] == str(cols)
        # 14 fields, text format, ZeroReference expected
        assert fields["ZeroReference"] == "MediaEdges"
        assert fields["GridType"] == "HexagonalShiftFirst"


def test_parametric_geometry_changes_with_cols():
    """The scanLayout depends on geometry (cols/paper): 2 distinct cols ->
    distinct FirstPatch_X/ToNextPatch_X (not a constant)."""
    a = dict(S.build_scan_fields(num_cols=11, patch_count=200, paper="a3"))
    b = dict(S.build_scan_fields(num_cols=18, patch_count=200, paper="a3"))
    assert a["ToNextPatch_X"] != b["ToNextPatch_X"]
    assert a["FirstPatch_X"] != b["FirstPatch_X"]


def test_explicit_dims_equivalent_to_preset():
    """Explicit dimensions == preset (parametric, no dependency on the preset)."""
    via_preset = S.build_scan_fields(num_cols=18, patch_count=464, paper="a3")
    via_dims = S.build_scan_fields(num_cols=18, patch_count=464,
                                   target_width_mm=277.0, sheet_w_mm=297.0,
                                   sheet_h_mm=420.0, media_source="MANUALFEED",
                                   placement="centered")
    assert via_dims == via_preset


# ── NATIVE-REWRITE (decision B) — print path ────────────────────────────────
def test_refonte_golden_464_a3():
    """Golden value: native-rewrite scanLayout 464 patches A3 (coherent with
    render_refonte). Freezes these values -> any geometry change is flagged.
    NATIVE-ANCHORED NON-FIXED-STEP MODEL: cols=18 (counted native), pitch_X =
    MEASURED HP CONSTANT (a3 = 14.03 mm = 252.5/18, user -> ToNextPatch_X
    0.552362), pitch_Y = 13.0 mm (ToNextPatch_Y 0.511811), patch block CENTERED
    between the marks. cf Docs/Methode_Geometrie_Chartes_Ancrage_Natif.md
    sections 2.4/2.5"""
    layout = G.compute_layout(G.MEDIA["a3"], patch_count=464)
    f = dict(S.build_scan_fields_refonte(layout))
    assert f["NumPatches"] == "464"
    assert f["PatchesPerRow"] == "18"              # counted native
    assert f["SkewMarksToPatch_X"] == "0.646177"   # block CENTERED between the marks
    assert f["FirstPatch_X"] == "1.28947"
    assert f["FirstPatch_Y"] == "1.81934"          # TOP-ANCHORED (head 27.5) — Y unchanged
    assert f["ToNextPatch_X"] == "0.552362"        # pitch_X 14.03 mm (MEASURED HP: 252.5/18)
    assert f["ToNextPatch_Y"] == "0.511811"        # pitch_Y 13.0 mm (measured)


def test_refonte_pitch_measured_and_block_centered():
    """pitch_X = measured CONSTANT (PITCH_X_MEASURED_MM), NEVER derived from a closure
    (roll24 = 14.04 = 561.5/40; a3 = 14.03 = 252.5/18, measured HP). And the patch block
    is CENTERED between the marks: LEFT gap (even row <-> left mark) == RIGHT gap
    (odd row <-> right mark). cf doc sections 2.4/2.5."""
    from lib.z9_client.chart_render_refonte import mark_delta_mm, mark_width_mm
    mark_w = mark_width_mm(delta_mm=mark_delta_mm(0, 0.0))
    Lr = G.compute_layout(G.MEDIA["roll24"], patch_count=464)
    assert Lr.pitch_x_mm == G.PITCH_X_MEASURED_MM["roll24"] == 14.04
    assert abs(40 * Lr.pitch_x_mm - 561.6) < 0.1                    # HP row 561.5 mm
    assert Lr.pitch_y_mm == 13.0                                    # pitch_Y measured
    # CENTERING: left gap == right gap, for roll24 AND a3
    for fmt in ("roll24", "a3"):
        L = G.compute_layout(G.MEDIA[fmt], patch_count=464)
        fp_x = L.first_patch_mm[0]
        gap_left = (fp_x - L.patch_w_mm / 2.0) - mark_w
        odd_last_right = fp_x + (L.cols - 1) * L.pitch_x_mm + L._odd_off_mm + L.patch_w_mm / 2.0
        gap_right = (L.chart_w_mm - mark_w) - odd_last_right
        assert abs(gap_left - gap_right) < 0.02, f"{fmt}: gap G {gap_left:.3f} ≠ D {gap_right:.3f}"
        assert gap_left > 0.5, f"{fmt}: gap {gap_left:.3f} trop petit"


def test_refonte_coherent_with_render_mark_geometry():
    """Coherence BY CONSTRUCTION: Spacing = first_patch_x - mark_w/2, with
    mark_w from the SAME functions as the renderer."""
    from lib.z9_client.chart_render_refonte import mark_delta_mm, mark_width_mm
    layout = G.compute_layout(G.MEDIA["a3"], patch_count=464, columns=18)
    mark_w = mark_width_mm(delta_mm=mark_delta_mm(layout.cols, layout.patch_w_mm))
    expected_spacing_mm = layout.first_patch_mm[0] - mark_w / 2.0
    f = dict(S.build_scan_fields_refonte(layout))
    assert abs(float(f["SkewMarksToPatch_X"]) * 25.4 - expected_spacing_mm) < 1e-3


def test_refonte_parametric_any_chart():
    """Any N patches/formats — no hardcoded 464/75 px. cols is DERIVED
    (non-fixed-step model): PatchesPerRow == layout.cols, not an input."""
    for media, n in [("a3", 200), ("a2", 1000), ("roll24", 5000), ("a3", 49)]:
        layout = G.compute_layout(G.MEDIA[media], patch_count=n)
        f = dict(S.build_scan_fields_refonte(layout))
        assert f["NumPatches"] == str(n)
        assert f["PatchesPerRow"] == str(layout.cols)   # DERIVED, not an input
        assert f["ZeroReference"] == "MediaEdges"
        assert float(f["SkewMarksToPatch_X"]) > 0   # mark to the left of the 1st patch


def test_roll_uses_head_margin():
    """POINT A — roll is also TOP-ANCHORED head 27.5 (Path A: recut -> reload as
    manual sheet -> offline scan; the edge detector requires the head margin).
    FirstPatch_Y = HEAD + fp_y, NOT the "online" head 5."""
    layout = G.compute_layout(G.MEDIA["roll24"], patch_count=500, columns=36)
    f = dict(S.build_scan_fields_refonte(layout))
    expected_mm = G.HEAD_MARGIN_MM + layout.first_patch_mm[1]
    assert abs(float(f["FirstPatch_Y"]) * 25.4 - expected_mm) < 0.05
    assert float(f["FirstPatch_Y"]) * 25.4 > 40   # not the head 5 (~24mm)


def test_refonte_explicit_offset_overrides():
    """The orchestration can pass the REAL print placement."""
    layout = G.compute_layout(G.MEDIA["a3"], patch_count=464, columns=18)
    auto = dict(S.build_scan_fields_refonte(layout))
    forced = dict(S.build_scan_fields_refonte(layout, offset_x_mm=5.0, offset_y_mm=5.0))
    # FirstPatch changes with the offset; Delta/Spacing (relative) unchanged
    assert forced["FirstPatch_X"] != auto["FirstPatch_X"]
    assert forced["ToNextPatch_X"] == auto["ToNextPatch_X"]
    assert forced["SkewMarksToPatch_X"] == auto["SkewMarksToPatch_X"]


# ── INVARIANT render<->scanLayout (multi-format anti-regression) ⭐ ───────────
@pytest.mark.parametrize("fmt, cols, n", [
    ("a4", 11, 187),
    ("a3", 18, 464),
    ("a2", 25, 975),
    ("roll24", 40, 464),
    ("roll17", 24, 200),
])
def test_render_scanlayout_invariant(fmt, cols, n):
    """DECISIVE INVARIANT: the physical X,Y positions (mm) of the SKEWS and
    PATCHES computed on the RENDER side (placement from chart_render_refonte) must
    EQUAL those encoded in the scanLayout (build_scan_fields_refonte), for
    EACH format. Scan registration depends entirely on this: any
    render<->scanLayout misalignment on a size = that size will fail the
    scan. Guard: a future geometry change that misaligns the two
    paths (computed by DIFFERENT code) immediately breaks this test.
    Tolerance 0.05 mm (observed delta < 0.01 mm; float noise margin)."""
    from lib.z9_client.chart_render_refonte import mark_delta_mm, mark_width_mm

    media = G.MEDIA[fmt]
    layout = G.compute_layout(media, patch_count=n, columns=cols)

    # --- RENDER side: positions as chart_render_refonte DRAWS them ---
    fp_x, fp_y = layout.first_patch_mm
    mark_w = mark_width_mm(delta_mm=mark_delta_mm(layout.cols, layout.patch_w_mm))
    # the renderer places the LEFT mark at block_x0 = 0 -> mark center = mark_w/2 ;
    # 1st patch at center fp_x -> skew->patch distance = fp_x - mark_w/2
    r_skew_to_patch = fp_x - mark_w / 2.0
    # default placement: X centered, Y top-anchored fixed head (cf orchestration)
    off_x = (media.width_mm - layout.chart_w_mm) / 2.0
    off_y = G.HEAD_MARGIN_MM
    r_first_x = off_x + fp_x            # absolute (media edges)
    r_first_y = off_y + fp_y
    r_delta_x = layout.pitch_x_mm
    r_delta_y = layout.pitch_y_mm

    # --- scanLayout side: what we declare to the firmware ---
    sl = dict(S.build_scan_fields_refonte(layout))
    mm = lambda key: float(sl[key]) * 25.4   # scanLayout in inches -> mm

    TOL = 0.05  # mm
    assert abs(mm("SkewMarksToPatch_X") - r_skew_to_patch) < TOL, f"{fmt}: skew→patch"
    assert abs(mm("FirstPatch_X") - r_first_x) < TOL, f"{fmt}: FirstPatch_X"
    assert abs(mm("FirstPatch_Y") - r_first_y) < TOL, f"{fmt}: FirstPatch_Y"
    assert abs(mm("ToNextPatch_X") - r_delta_x) < TOL, f"{fmt}: ToNextPatch_X"
    assert abs(mm("ToNextPatch_Y") - r_delta_y) < TOL, f"{fmt}: ToNextPatch_Y"
