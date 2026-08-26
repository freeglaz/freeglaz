"""Smoke tests — Convert (DeviceLink socle, JALON 1).

Covers the deterministic, hardware-independent surface of the route:
  - /api/convert/source-info : embedded space/TRC detection, has_profile flags;
  - /api/convert : the guard chain UP TO the DEST resolution (no source profile
    → 400, no Z9 configured → 409). The actual collink/cctiff run needs a Z9 +
    Argyll and is exercised live, not here.
"""
from pathlib import Path

from PIL import Image
from fastapi.testclient import TestClient

from webapp.backend.main import app
from webapp.backend.services import file_storage


def _client() -> TestClient:
    return TestClient(app)


def _upload(c: TestClient, path: Path, name: str) -> str:
    with open(path, "rb") as f:
        r = c.post("/api/files", files={"file": (name, f, "image/tiff")})
    assert r.status_code == 200, r.text
    return r.json()["file_id"]


def _stage_no_icc(color=(120, 60, 20)) -> str:
    """Stage a TIFF WITHOUT an embedded ICC directly in storage.

    The public upload gate rejects a no-ICC TIFF (415 ``no_icc``), so this
    defensive branch is unreachable through /api/files — we stage the source
    file directly to exercise it. (Écart noted: gate-vs-route redundancy.)
    """
    file_id, dir_path = file_storage.new_storage()
    Image.new("RGB", (32, 32), color=color).save(
        dir_path / "source.tif", format="TIFF", dpi=(300, 300))
    return file_id


def test_source_info_reads_embedded_space(sample_tiff_path):
    """A TIFF with an embedded sRGB profile → has_profile True + RGB space."""
    c = _client()
    fid = _upload(c, sample_tiff_path, "sample.tif")
    r = c.get(f"/api/convert/source-info?file_id={fid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_profile"] is True
    assert body["color_space"].strip() == "RGB"
    assert "trc" in body


def test_source_info_no_embedded_profile():
    """A TIFF WITHOUT an embedded profile → has_profile False (detection, not
    a frozen default space)."""
    c = _client()
    fid = _stage_no_icc()
    r = c.get(f"/api/convert/source-info?file_id={fid}")
    assert r.status_code == 200, r.text
    assert r.json() == {"has_profile": False}


def test_source_info_unknown_file_404():
    c = _client()
    fake = "00000000-0000-4000-8000-000000000000"
    assert c.get(f"/api/convert/source-info?file_id={fake}").status_code == 404


def test_convert_refuses_image_without_source_profile():
    """No embedded profile → the conversion is refused (400), never assume a
    default source space."""
    c = _client()
    fid = _stage_no_icc()
    r = c.post("/api/convert", json={"file_id": fid, "gloss_enhancer": "OFF"})
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "no_source_profile"


def test_collink_argv_intent_token_is_single_i():
    """Regression: the collink intent flag is ``-i`` + the bare choice (``-ir``),
    NOT ``-iir``. collink rejects 'ir' as an intent — the values must be the raw
    choices r|p|lp, not ir|ip|ilp."""
    from lib.z9_client import devicelink

    argv = devicelink.build_collink_argv(
        "collink", "src.icc", "dst.icc", "out.icc", intent="r", quality="h")
    assert "-ir" in argv
    assert "-iir" not in argv
    assert "-qh" in argv
    assert "-G" in argv
    assert devicelink.ALLOWED_INTENTS == ("r", "p", "lp")

    import pytest
    with pytest.raises(ValueError):
        devicelink.build_collink_argv("collink", "s", "d", "o", intent="ir")


def _icc_tags(icc: bytes) -> dict:
    """Minimal tag table parse: {sig: type} for assertions."""
    n = int.from_bytes(icc[128:132], "big")
    out = {}
    for i in range(n):
        o = 132 + i * 12
        sig = icc[o:o + 4]
        off = int.from_bytes(icc[o + 4:o + 8], "big")
        out[sig] = icc[off:off + 4]
    return out


def test_normalize_v4_profile_for_argyll():
    """A real v4 profile (mluc desc) → v2.4, mluc tags dropped, colorimetry kept.

    Fixture: sRGB_v4_ICC_preference.icc (v4, mluc text tags) already bundled.
    """
    from lib.z9_client import devicelink

    v4 = (Path(__file__).resolve().parents[3]
          / "lib" / "z9_client" / "assets" / "sRGB_v4_ICC_preference.icc").read_bytes()
    assert v4[8] == 4, "fixture must be ICC v4"
    assert any(t == b"mluc" for t in _icc_tags(v4).values())

    norm = devicelink.normalize_icc_for_argyll(v4)
    assert norm[8] == 2                                 # downgraded to v2
    tags = _icc_tags(norm)
    assert not any(t == b"mluc" for t in tags.values())  # no mluc left
    # Colorimetric tags survive (this is a cLUT v4 profile → A2B0 present)
    assert b"wtpt" in tags
    assert b"A2B0" in tags or b"rXYZ" in tags
    assert int.from_bytes(norm[0:4], "big") == len(norm)  # header size fixed


def test_normalize_v2_profile_unchanged():
    """A v2 profile with no mluc tags is returned byte-identical (no needless
    repackaging)."""
    from lib.z9_client import devicelink

    v2 = (Path(__file__).resolve().parents[3]
          / "lib" / "z9_client" / "assets" / "sRGB_IEC61966-2.1.icc").read_bytes()
    assert v2[8] == 2
    assert devicelink.normalize_icc_for_argyll(v2) == v2


def test_convert_without_z9_configured_409(sample_tiff_path):
    """Valid source profile but no Z9 wired (no lifespan in TestClient) → the
    DEST cannot be resolved → 409, BEFORE any Argyll call."""
    c = _client()
    fid = _upload(c, sample_tiff_path, "sample.tif")
    r = c.post("/api/convert", json={"file_id": fid, "gloss_enhancer": "OFF"})
    assert r.status_code == 409, r.text
