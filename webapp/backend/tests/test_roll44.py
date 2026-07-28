"""44" unlock: roll44 preset (1118 mm), same path as roll24.

LACKING a 44" machine, the ORACLE = the official HP 44" chart (~73 full-width
patches). The code must derive ~73 columns (±2-3) at the MEASURED 24" pitch (14.04),
universal patch geometry. Internal scaling, never exposed.
"""
from fastapi.testclient import TestClient

from lib.z9_client import chart_geometry_refonte as G
from lib.z9_client import sol_chart as SC
from webapp.backend.main import app


def test_roll44_in_media():
    assert "roll44" in G.MEDIA
    m = G.MEDIA["roll44"]
    assert m.source == "roll" and m.width_mm == 1118.0


def test_roll44_pitch_is_measured_24in_not_nominal():
    # Inherits the MEASURED 24" pitch (universal), NOT the 13.8 nominal of non-natives.
    L = G.compute_layout(G.MEDIA["roll44"], patch_count=464)
    assert L.pitch_x_mm == G.PITCH_X_MEASURED_MM["roll24"] == 14.04
    assert L.pitch_x_mm != G.PITCH_X_NOMINAL_MM


def test_roll44_oracle_column_count():
    # ORACLE: exactly 73 columns (official HP 44" chart: 10×7 + 3 = 73, cube).
    # The content width is tuned to derive 73 at the measured pitch 14.04.
    cols = G.compute_layout(G.MEDIA["roll44"], patch_count=464).cols
    assert cols == 73, f"roll44 cols={cols}, attendu 73 (charte HP 44 officielle)"


def test_roll44_cols_derived_not_fixed():
    # cols DERIVED from the width (not hardcoded in _NATIVE_COLS like roll24).
    assert "roll44" not in G._NATIVE_COLS
    assert G.compute_layout(G.MEDIA["roll44"], patch_count=464).cols \
        > G.compute_layout(G.MEDIA["roll24"], patch_count=464).cols   # 44" > 24"


def test_no_regression_existing_formats():
    assert G.compute_layout(G.MEDIA["roll24"], patch_count=464).cols == 40
    assert G.compute_layout(G.MEDIA["a3"], patch_count=464).cols == 18
    assert G.compute_layout(G.MEDIA["a4"], patch_count=464).pitch_x_mm == G.PITCH_X_NOMINAL_MM


def test_format_capacity_roll44():
    cap = SC.format_capacity("roll44")
    assert cap["is_roll"] is True and cap["max_patches"] is None
    assert cap["max_cols"] >= 70


def test_webapp_formats_exposes_roll44_backend_driven():
    # The webapp is backend-driven (list from MEDIA) → roll44 appears without
    # touching the front.
    with TestClient(app) as c:
        fmts = {f["key"]: f for f in c.get("/api/charts/formats").json()["formats"]}
    assert "roll44" in fmts
    assert fmts["roll44"]["is_roll"] is True
    assert fmts["roll44"]["width_mm"] == 1118.0
