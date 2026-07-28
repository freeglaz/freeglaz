"""Smoke tests /api/print/preview (Z9Client mocked, file_storage isolated)."""
import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest
from PIL import Image, ImageCms
from fastapi.testclient import TestClient

from webapp.backend.main import app
from webapp.backend.routes.status import get_z9
from webapp.backend.services.print_geometry import PaperIccInfo


def _srgb_icc_bytes() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _make_tiff(tmp_path: Path, w_mm: float, h_mm: float, dpi: int = 300) -> Path:
    """Build a TIFF of the desired size in mm."""
    px_w = round(w_mm / 25.4 * dpi)
    px_h = round(h_mm / 25.4 * dpi)
    p = tmp_path / "in.tif"
    Image.new("RGB", (px_w, px_h), color=(200, 100, 50)).save(
        p, format="TIFF", dpi=(dpi, dpi), icc_profile=_srgb_icc_bytes(),
    )
    return p


def _make_dashboard(loaded: Optional[dict]) -> dict:
    base = {
        "identification": {"ModelName": "Z9", "SerialNumber": "S", "FwReleaseName": "F"},
        "ink_levels": {}, "ink_warnings": [], "global_status": "Ready",
    }
    if loaded:
        base.update(loaded)
    else:
        base.update({"loaded_paper_id": None, "loaded_paper_name": None,
                     "loaded_paper_source": None, "loaded_paper_source_label": None,
                     "loaded_paper_width_mm": None, "loaded_paper_length_mm": None})
    return base


def _fake_z9(loaded: Optional[dict]):
    dashboard = _make_dashboard(loaded)

    class _Device:
        def status(self_inner): return dashboard
        def device_status(self_inner):
            return {"ActivitiesOverview": {"MostRelevantActivity": {"Name": "NoActivity"}}}

    class _Paper:
        def get(self_inner, _):
            return {"id": "X", "name": "FakePaper",
                    "category_id": "PHOTO", "is_factory": False}
        def capabilities(self_inner, _): return None

    class _Soap:
        """Stub: returns no ICC bytes. Tests that want a specific ICC name
        pre-fill app.state.paper_icc_cache before the call."""
        def get_profile(self_inner, medium_id, gloss_enhancer, color_space):
            return {"outcome": "OK", "icc_bytes": None}

    return SimpleNamespace(device=_Device(), paper=_Paper(), soap=_Soap())


@pytest.fixture
def client():
    app.state.capabilities_cache = {}
    app.state.paper_icc_cache = {}
    yield TestClient(app)
    app.dependency_overrides.clear()


def _upload(client: TestClient, tiff_path: Path) -> str:
    with open(tiff_path, "rb") as f:
        r = client.post("/api/files", files={"file": ("in.tif", f, "image/tiff")})
    assert r.status_code == 200, r.text
    return r.json()["file_id"]


# ─── tests ──────────────────────────────────────────────────────────────


def test_preview_nominal_centered_manualfeed(client, tmp_path):
    """Image 100×150 on Canson 210×297 MANUAL_FEED → centered, can_print=true.

    The paper cache is pre-filled with a PaperIccInfo named without md5 →
    matching falls on the normalized-name branch. file_icc_name is "" (empty
    Pillow sRGB) → unknown with reason "no embedded ICC profile in file".
    Mainly checks that the route does not crash and exposes paper_icc_name.
    """
    app.state.paper_icc_cache[("P1", "FULLPAGE", "PRINTER_RGB")] = PaperIccInfo(
        name="PaperProfile_GEON", md5=None,
    )
    app.dependency_overrides[get_z9] = lambda: _fake_z9({
        "loaded_paper_id": "P1", "loaded_paper_name": "FakePaper",
        "loaded_paper_source": "MANUAL_FEED",
        "loaded_paper_width_mm": 210.0, "loaded_paper_length_mm": 297.0,
    })
    fid = _upload(client, _make_tiff(tmp_path, 100.0, 150.0))

    r = client.post("/api/print/preview", json={"file_id": fid, "params": {}})
    assert r.status_code == 200
    data = r.json()
    assert data["can_print"] is True
    assert data["blocking_issues"] == []
    g = data["geometry"]
    assert g["media_source"] == "MANUAL_FEED"
    assert g["centered_x"] and g["centered_y"]
    # 100x150 centered on 210x297 → x=55, y=73.5
    assert abs(g["image_x_mm"] - 55.0) < 0.5
    assert abs(g["image_y_mm"] - 73.5) < 0.5
    assert g["margin_left_mm"] == pytest.approx(g["margin_right_mm"], abs=0.1)
    assert data["paper_icc_name"] == "PaperProfile_GEON"


def test_preview_overflow_blocks(client, tmp_path):
    """TIFF 235×313 on Canson 210×293 → image too wide AND too tall.

    With the per-axis diagnostic follow-up, we expect distinct messages
    for width and height (not a generic
    "exceeds printable area" message). When the image is too large,
    the position sub-errors are not reported (the root cause
    is the size).
    """
    app.dependency_overrides[get_z9] = lambda: _fake_z9({
        "loaded_paper_id": "P1", "loaded_paper_name": "FakePaper",
        "loaded_paper_source": "MANUAL_FEED",
        "loaded_paper_width_mm": 210.0, "loaded_paper_length_mm": 293.0,
    })
    fid = _upload(client, _make_tiff(tmp_path, 235.0, 313.0))
    r = client.post("/api/print/preview", json={"file_id": fid, "params": {}})
    assert r.status_code == 200
    data = r.json()
    assert data["can_print"] is False
    msgs = data["blocking_issues"]
    # Per-axis diagnostic — 2 distinct messages, one per oversized axis
    assert any("too wide" in m and "235" in m and "200" in m for m in msgs), msgs
    assert any("too tall" in m and "313" in m for m in msgs), msgs
    # Directional details (computed despite blocking) — overflows on 4 sides
    g = data["geometry"]
    assert g["overflow_left_mm"]   > 0
    assert g["overflow_top_mm"]    > 0
    assert g["overflow_right_mm"]  > 0
    assert g["overflow_bottom_mm"] > 0


def test_preview_oversize_by_centering(client, tmp_path):
    """Image 200×280 on Canson 210×293 MANUAL_FEED: width OK
    (200 < 200 usable), but height 280 > 270.6 usable → root cause
    = image too tall (follow-up: we state it explicitly
    rather than reporting a position overflow)."""
    app.dependency_overrides[get_z9] = lambda: _fake_z9({
        "loaded_paper_id": "P1", "loaded_paper_name": "FakePaper",
        "loaded_paper_source": "MANUAL_FEED",
        "loaded_paper_width_mm": 210.0, "loaded_paper_length_mm": 293.0,
    })
    fid = _upload(client, _make_tiff(tmp_path, 200.0, 280.0))
    r = client.post("/api/print/preview", json={"file_id": fid, "params": {}})
    assert r.status_code == 200
    data = r.json()
    assert data["can_print"] is False
    g = data["geometry"]
    # Image centered on sheet: image_y = (293-280)/2 = 6.5
    # printable_bottom = 293 - 17.4 = 275.6 ; image_bottom = 6.5 + 280 = 286.5
    # → overflow_bottom ≈ 10.9
    assert g["overflow_left_mm"]   == 0.0
    assert g["overflow_top_mm"]    == 0.0
    assert g["overflow_right_mm"]  == 0.0
    assert g["overflow_bottom_mm"] > 10.0
    assert g["overflow_bottom_mm"] < 12.0
    msgs = data["blocking_issues"]
    assert any("too tall" in m and "280" in m for m in msgs), msgs


# ─── follow-up: 3 distinct per-axis cases ──────────────────────────────


def test_validation_position_below_margin_shows_clear_error(client, tmp_path):
    """Position X below the left margin → dedicated message, NOT
    "image too large" (the image fits easily)."""
    app.dependency_overrides[get_z9] = lambda: _fake_z9({
        "loaded_paper_id": "P1", "loaded_paper_name": "FakePaper",
        "loaded_paper_source": "MANUAL_FEED",
        "loaded_paper_width_mm": 210.0, "loaded_paper_length_mm": 293.0,
    })
    fid = _upload(client, _make_tiff(tmp_path, 100.0, 100.0))
    # Image 100×100 on 210×293 → centered X=55. offset_x_mm=-55 pushes X=0
    # while printable_x=5 → overflow_left=5.
    r = client.post("/api/print/preview", json={
        "file_id": fid,
        "params": {"offset_x_mm": -55.0},
    })
    data = r.json()
    assert data["can_print"] is False
    msgs = data["blocking_issues"]
    assert any("Position X" in m and "below" in m and "left margin" in m for m in msgs), msgs
    # NO "too wide/tall" message (image fits)
    assert not any("too wide" in m or "too tall" in m for m in msgs), msgs


def test_validation_image_too_far_right_shows_position_error(client, tmp_path):
    """Image OK but positioned too far right → "Position X
    max = ..." message (NOT "too wide")."""
    app.dependency_overrides[get_z9] = lambda: _fake_z9({
        "loaded_paper_id": "P1", "loaded_paper_name": "FakePaper",
        "loaded_paper_source": "MANUAL_FEED",
        "loaded_paper_width_mm": 210.0, "loaded_paper_length_mm": 293.0,
    })
    fid = _upload(client, _make_tiff(tmp_path, 100.0, 100.0))
    # Image 100×100: printable_w = 200, max position X = 5 + 200 - 100 = 105
    # offset_x_mm=+60 → image_x = 55+60 = 115 → overflows by 10mm.
    r = client.post("/api/print/preview", json={
        "file_id": fid,
        "params": {"offset_x_mm": 60.0},
    })
    data = r.json()
    assert data["can_print"] is False
    msgs = data["blocking_issues"]
    assert any("Position X max" in m and "105" in m for m in msgs), msgs
    assert not any("too wide" in m or "too tall" in m for m in msgs), msgs


def test_validation_image_too_large_shows_size_error_not_position(client, tmp_path):
    """Image wider than the printable area → "too wide" message
    ONLY for that axis (NO "Position X" message). The root cause
    is the size, not the position."""
    app.dependency_overrides[get_z9] = lambda: _fake_z9({
        "loaded_paper_id": "P1", "loaded_paper_name": "FakePaper",
        "loaded_paper_source": "MANUAL_FEED",
        "loaded_paper_width_mm": 210.0, "loaded_paper_length_mm": 293.0,
    })
    # Image 250×100 → too wide (printable_w=200), but height OK.
    fid = _upload(client, _make_tiff(tmp_path, 250.0, 100.0))
    r = client.post("/api/print/preview", json={"file_id": fid, "params": {}})
    data = r.json()
    assert data["can_print"] is False
    msgs = data["blocking_issues"]
    assert any("too wide" in m and "250" in m for m in msgs), msgs
    # No Position X message — size is the root cause, not the position
    assert not any("Position X" in m for m in msgs), msgs
    # No Y message either (height OK)
    assert not any("Position Y" in m or "too tall" in m for m in msgs), msgs


def test_preview_no_paper_blocks(client, tmp_path):
    """No paper loaded → can_print=false, clear message."""
    app.dependency_overrides[get_z9] = lambda: _fake_z9(loaded=None)
    fid = _upload(client, _make_tiff(tmp_path, 100.0, 100.0))
    r = client.post("/api/print/preview", json={"file_id": fid, "params": {}})
    assert r.status_code == 200
    data = r.json()
    assert data["can_print"] is False
    assert any("paper" in m.lower() for m in data["blocking_issues"])


def test_preview_roll_economy_mode(client, tmp_path):
    """ROLL 609mm without override → economy mode: sheet_h = image_h + 10mm, image top-left + 5mm.

    TODO live: validate when a real roll is loaded on the Z9.
    """
    app.state.paper_icc_cache[("P2", "FULLPAGE", "PRINTER_RGB")] = PaperIccInfo(
        name="RollProfile", md5=None,
    )
    app.dependency_overrides[get_z9] = lambda: _fake_z9({
        "loaded_paper_id": "P2", "loaded_paper_name": "FakeRoll",
        "loaded_paper_source": "ROLL",
        "loaded_paper_width_mm": 609.0, "loaded_paper_length_mm": None,
    })
    fid = _upload(client, _make_tiff(tmp_path, 200.0, 300.0))
    r = client.post("/api/print/preview", json={"file_id": fid, "params": {}})
    assert r.status_code == 200
    data = r.json()
    g = data["geometry"]
    assert g["media_source"] == "ROLL"
    assert g["sheet_width_mm"] == pytest.approx(609.0, abs=0.5)
    assert g["sheet_height_mm"] == pytest.approx(310.0, abs=1.0)  # 300 + 10
    assert g["image_x_mm"] == pytest.approx(5.0, abs=0.1)
    assert g["image_y_mm"] == pytest.approx(5.0, abs=0.1)
    assert g["centered_x"] is False and g["centered_y"] is False
    assert data["can_print"] is True


def test_preview_unknown_file_id_returns_404(client):
    app.dependency_overrides[get_z9] = lambda: _fake_z9(loaded=None)
    fake = "00000000-0000-4000-8000-000000000000"
    r = client.post("/api/print/preview", json={"file_id": fake, "params": {}})
    assert r.status_code == 404


# ─── ICC matching MD5 + name fallback + unknown ────────────────────────
#
# Matching prioritizes bytes (md5). The test TIFFs below
# embed a controlled ICC via _make_tiff_with_icc() which lets us
# choose the exact bytes (same as cache → match, different →
# mismatch). We do not touch the existing _make_tiff() — it always
# generates a standard Pillow sRGB, used by the other tests.


def _make_tiff_with_icc(
    tmp_path: Path, w_mm: float, h_mm: float, icc_bytes: bytes, dpi: int = 300,
) -> Path:
    px_w = round(w_mm / 25.4 * dpi)
    px_h = round(h_mm / 25.4 * dpi)
    p = tmp_path / f"in_{hashlib.md5(icc_bytes).hexdigest()[:8]}.tif"
    Image.new("RGB", (px_w, px_h), color=(180, 200, 220)).save(
        p, format="TIFF", dpi=(dpi, dpi), icc_profile=icc_bytes,
    )
    return p


def test_icc_match_by_color_hash_even_when_names_differ(client, tmp_path):
    """Colour-hash takes priority: identical colour tables → match, even if the
    desc names diverge (real case of custom HP profiles with empty desc on the
    file side but firmware name on the paper side)."""
    from webapp.backend.services.icc_identity import icc_color_hash
    icc = _srgb_icc_bytes()
    ch = icc_color_hash(icc)
    # Paper cache: same colour tables, firmware name different from the file's desc
    app.state.paper_icc_cache[("P1", "FULLPAGE", "PRINTER_RGB")] = PaperIccInfo(
        name="HPDesignjetZ9CansonPhotolustreGEON", md5="whatever", color_hash=ch,
    )
    app.dependency_overrides[get_z9] = lambda: _fake_z9({
        "loaded_paper_id": "P1", "loaded_paper_name": "FakePaper",
        "loaded_paper_source": "MANUAL_FEED",
        "loaded_paper_width_mm": 210.0, "loaded_paper_length_mm": 297.0,
    })
    fid = _upload(client, _make_tiff_with_icc(tmp_path, 100.0, 150.0, icc))

    r = client.post("/api/print/preview", json={"file_id": fid, "params": {}})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["icc_match"] == "match"
    assert "colour tables" in (data["icc_match_reason"] or "").lower()


def test_icc_mismatch_by_color_hash(client, tmp_path):
    """Different colour tables → mismatch, regardless of names."""
    file_icc = _srgb_icc_bytes()
    # Cache: same (artificial) name, different colour hash → decision follows the hash
    app.state.paper_icc_cache[("P1", "FULLPAGE", "PRINTER_RGB")] = PaperIccInfo(
        name="sRGB built-in", md5="x", color_hash="a-different-colour-hash",
    )
    app.dependency_overrides[get_z9] = lambda: _fake_z9({
        "loaded_paper_id": "P1", "loaded_paper_name": "FakePaper",
        "loaded_paper_source": "MANUAL_FEED",
        "loaded_paper_width_mm": 210.0, "loaded_paper_length_mm": 297.0,
    })
    fid = _upload(client, _make_tiff_with_icc(tmp_path, 100.0, 100.0, file_icc))

    r = client.post("/api/print/preview", json={"file_id": fid, "params": {}})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["icc_match"] == "mismatch"
    assert "colour tables" in (data["icc_match_reason"] or "").lower()
    # User warning present
    assert any("File ICC profile" in w for w in data["warnings"])


def test_icc_match_by_name_when_md5_unavailable(client, tmp_path):
    """Normalized-name fallback when one side has no MD5 (e.g. paper.details()
    fallback metadata without accessible bytes). Case-insensitive to multiple
    spaces and to lowercasing."""
    icc = _srgb_icc_bytes()
    # We would force the file name by pre-filling via the Pillow desc tag
    # — but Pillow generates an sRGB with an empty desc. So we check the branch
    # from the reverse side: paper without md5, file without md5 but with a name.
    # In practice we build a cache without md5 but with a name identical
    # (normalized) to the name extracted from the Pillow sRGB ("sRGB built-in" or similar).
    # Since we do not control the Pillow desc, we test the function directly.
    from webapp.backend.services.print_geometry import icc_match_status

    status, reason = icc_match_status(
        file_icc_name="Adobe RGB (1998)", file_icc_color_hash=None,
        paper_icc_name="adobe  rgb (1998)", paper_icc_color_hash=None,
    )
    assert status == "match"
    assert "Identical ICC names" in reason

    status, reason = icc_match_status(
        file_icc_name="Adobe RGB (1998)", file_icc_color_hash=None,
        paper_icc_name="sRGB IEC61966-2.1", paper_icc_color_hash=None,
    )
    assert status == "mismatch"
    assert "Different ICC names" in reason


def test_icc_status_distinguishes_none_from_unknown(client):
    """File without ICC = 'none' (deterministic, neutral). Paper unresolved /
    partial data = 'unknown' (failure, amber alert). Not merely a UX nuance:
    the frontend badge treats these 2 states differently."""
    from webapp.backend.services.print_geometry import icc_match_status

    # File with no ICC at all → none
    s, r = icc_match_status(None, None, "HPSomePaper", "abc123")
    assert s == "none" and "File without embedded ICC" in r

    # Paper unresolved (SOAP get_profile failed) → unknown
    s, r = icc_match_status("Adobe RGB", "fff111", None, None)
    assert s == "unknown" and "Paper ICC profile not resolved" in r

    # Partial data (file has a name, paper has an md5) → unknown
    s, r = icc_match_status("Adobe RGB", None, None, "abc123")
    assert s == "unknown" and "Insufficient data" in r
