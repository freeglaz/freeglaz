"""Smoke tests — Convert (DeviceLink socle, JALON 1).

Covers the deterministic, hardware-independent surface of the route:
  - /api/convert/source-info : embedded space/TRC detection, has_profile flags;
  - /api/convert : the guard chain UP TO the DEST resolution (no source profile
    → 400, no Z9 configured → 409). The actual collink/cctiff run needs a Z9 +
    Argyll and is exercised live, not here.
"""
import hashlib
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from webapp.backend.main import app
from webapp.backend.services import file_storage

_ASSETS = Path(__file__).resolve().parents[3] / "lib" / "z9_client" / "assets"


def _argyll_convert_available() -> bool:
    from lib.z9_client.argyll import find_argyll_binary
    return bool(find_argyll_binary("collink") and find_argyll_binary("cctiff"))


def _pixel_hash(tiff_path) -> str:
    Image.MAX_IMAGE_PIXELS = None
    arr = np.asarray(Image.open(tiff_path))
    return hashlib.sha256(np.ascontiguousarray(arr).tobytes()).hexdigest()


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


def _tiffgamut_available() -> bool:
    from lib.z9_client.argyll import find_argyll_binary
    return bool(find_argyll_binary("tiffgamut") and find_argyll_binary("collink"))


def test_collink_argv_image_gam_axis():
    """JALON 3: image-aware = a ``.gam`` passed to ``-G`` (``-G <image.gam>``);
    off = bare ``-G``. Orthogonal to the intent (argv-level, no binary)."""
    from lib.z9_client import devicelink

    # image-aware ON: the gam is the token right after -G
    on = devicelink.build_collink_argv("collink", "s.icc", "d.icc", "o.icc",
                                       intent="p", image_gam="img.gam")
    assert "-G" in on
    assert on[on.index("-G") + 1] == "img.gam"
    assert "-ip" in on                      # intent independent of the gam

    # image-aware OFF (JALON 1 behaviour): bare -G, no gam token
    off = devicelink.build_collink_argv("collink", "s.icc", "d.icc", "o.icc",
                                        intent="p")
    assert "-G" in off
    assert not any(str(a).endswith(".gam") for a in off)


def test_collink_argv_dest_viewcond_axis():
    """JALON 4: a print preset adds ``-d <preset>``; default/None adds nothing.
    Orthogonal to intent and image-aware. Strict allow-list."""
    from lib.z9_client import devicelink

    # preset → -d pp, placed as its own token after the intent
    on = devicelink.build_collink_argv("collink", "s.icc", "d.icc", "o.icc",
                                       intent="p", dest_viewcond="pp")
    assert "-d" in on
    assert on[on.index("-d") + 1] == "pp"

    # default / None / "" → no -d at all (unchanged JALON 1-3 behaviour)
    for default in ("default", None, ""):
        off = devicelink.build_collink_argv("collink", "s.icc", "d.icc", "o.icc",
                                            intent="p", dest_viewcond=default)
        assert "-d" not in off

    # combines with image-aware, still one -d
    combo = devicelink.build_collink_argv("collink", "s.icc", "d.icc", "o.icc",
                                          intent="p", image_gam="img.gam",
                                          dest_viewcond="pm")
    assert combo[combo.index("-G") + 1] == "img.gam"
    assert combo[combo.index("-d") + 1] == "pm"

    # strict: non-print / unknown presets are refused
    for bad in ("mt", "jd", "xx"):
        with pytest.raises(ValueError):
            devicelink.build_collink_argv("collink", "s.icc", "d.icc", "o.icc",
                                          dest_viewcond=bad)


@pytest.mark.skipif(not _tiffgamut_available(),
                    reason="tiffgamut/collink not installed")
def test_image_aware_extracts_gam_and_feeds_collink(tmp_path):
    """JALON 3 end-to-end: tiffgamut extracts the image gamut, collink -G
    consumes it and builds a DeviceLink. Bundled v2 assets, no Z9."""
    from lib.z9_client import devicelink, tiffgamut

    src_icc = _ASSETS / "sRGB_IEC61966-2.1.icc"
    dst_icc = _ASSETS / "ClayRGB-elle-V2-g22.icc"
    g = np.zeros((16, 16, 3), np.uint8)
    g[..., 0] = np.arange(16, dtype=np.uint8)[None, :] * 16
    g[..., 1] = np.arange(16, dtype=np.uint8)[:, None] * 16
    g[..., 2] = 200
    tif = tmp_path / "in.tif"
    Image.fromarray(g).save(tif, format="TIFF")

    gam = tmp_path / "image.gam"
    tiffgamut.run_tiffgamut(src_icc, tif, gam)
    assert gam.exists() and gam.stat().st_size > 0

    link = tmp_path / "link.icc"
    devicelink.run_collink(src_icc, dst_icc, link,
                           intent="p", quality="l", image_gam=gam)
    assert link.exists() and link.stat().st_size > 0


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


@pytest.mark.skipif(not _argyll_convert_available(),
                    reason="collink/cctiff not installed")
def test_cctiff_embed_tags_device_without_changing_pixels(tmp_path):
    """JALON 2 (volet 2): embedding the paper profile (cctiff -e) is a pure
    ASSIGNMENT — the device pixels are byte-identical with and without -e, and
    only the -e output carries the ICC. Uses two bundled v2 assets as
    source/dest; no Z9 needed."""
    from lib.z9_client import devicelink

    src_icc = _ASSETS / "sRGB_IEC61966-2.1.icc"
    dst_icc = _ASSETS / "ClayRGB-elle-V2-g22.icc"
    # deterministic little RGB gradient (no ICC needed: the link defines source)
    g = np.zeros((16, 16, 3), np.uint8)
    g[..., 0] = np.arange(16, dtype=np.uint8)[None, :] * 16
    g[..., 1] = np.arange(16, dtype=np.uint8)[:, None] * 16
    g[..., 2] = 128
    in_tif = tmp_path / "in.tif"
    Image.fromarray(g).save(in_tif, format="TIFF")

    link = tmp_path / "link.icc"
    devicelink.run_collink(src_icc, dst_icc, link, intent="r", quality="l")

    plain = tmp_path / "plain.tif"
    tagged = tmp_path / "tagged.tif"
    devicelink.apply_cctiff(link, in_tif, plain)
    devicelink.apply_cctiff(link, in_tif, tagged, embed_icc=dst_icc)

    # Same device pixels, tag added only to the -e output
    assert _pixel_hash(plain) == _pixel_hash(tagged)
    assert Image.open(tagged).info.get("icc_profile")
    assert not Image.open(plain).info.get("icc_profile")


def test_convert_lut_support_classifier():
    """LUT-type classifier (the real Argyll discriminant, NOT the ICC version)."""
    from lib.z9_client import devicelink
    mft2_v4 = (_ASSETS.parent.parent.parent / "webapp" / "backend" / "tests"
               / "fixtures" / "synthetic_test_resident_A.icc").read_bytes()
    mab_v4 = (_ASSETS / "sRGB_v4_ICC_preference.icc").read_bytes()
    matrix_v2 = (_ASSETS / "sRGB_IEC61966-2.1.icc").read_bytes()
    assert mft2_v4[8] == 4 and devicelink.convert_lut_support(mft2_v4) == "SUPPORTED_MFT"
    assert mab_v4[8] == 4 and devicelink.convert_lut_support(mab_v4) == "UNSUPPORTED_MAB_MBA"
    assert devicelink.convert_lut_support(matrix_v2) == "SUPPORTED_MATRIX"


def _stage_with_icc(icc_bytes: bytes) -> str:
    """Stage a source.tif carrying ``icc_bytes`` as its embedded profile."""
    file_id, dir_path = file_storage.new_storage()
    Image.new("RGB", (32, 32), color=(120, 60, 20)).save(
        dir_path / "source.tif", format="TIFF", dpi=(300, 300), icc_profile=icc_bytes)
    return file_id


def test_convert_refuses_mab_mba_source_cleanly_before_argyll():
    """A source using mAB/mBA v4 LUTs → clean 422 unsupported_lut, BEFORE any Z9
    or Argyll call (no cryptic Argyll message leaks)."""
    c = _client()
    mab = (_ASSETS / "sRGB_v4_ICC_preference.icc").read_bytes()
    fid = _stage_with_icc(mab)
    r = c.post("/api/convert", json={"file_id": fid, "gloss_enhancer": "OFF"})
    assert r.status_code == 422, r.text
    d = r.json()["detail"]
    assert d["code"] == "unsupported_lut" and d["which"] == "source"
    # factual message, no Argyll internals
    assert "mAB/mBA" in d["message"] and "Unable to locate usable conversion" not in d["message"]


def test_convert_mft_source_passes_lut_gate():
    """An mft2 source passes the LUT gate (fails later on Z9=None → 409, not 422)."""
    c = _client()
    mft = (_ASSETS.parent.parent.parent / "webapp" / "backend" / "tests"
           / "fixtures" / "synthetic_test_resident_A.icc").read_bytes()
    fid = _stage_with_icc(mft)
    r = c.post("/api/convert", json={"file_id": fid, "gloss_enhancer": "OFF"})
    assert r.status_code == 409, r.text        # Z9 not configured — gate passed


def test_print_gate_unaffected_by_convert_lut_guard():
    """Print non-regression: an mAB-embedded TIFF stays printable (the print gate
    keys on has_icc, not the LUT type) — the Convert guard never touches Print."""
    from webapp.backend.services.file_inspector import to_file_info
    from lib.z9_client import devicelink
    import inspect as _inspect
    from webapp.backend.routes import printing
    mab = (_ASSETS / "sRGB_v4_ICC_preference.icc").read_bytes()
    fid, dir_path = file_storage.new_storage()
    p = dir_path / "source.tif"
    Image.new("RGB", (64, 64), color=(180, 90, 30)).save(p, format="TIFF", dpi=(300, 300), icc_profile=mab)
    info = to_file_info(fid, "source.tif", p)
    assert info.has_icc is True and info.is_printable is True   # Print unaffected
    assert devicelink.convert_lut_support(mab) == "UNSUPPORTED_MAB_MBA"  # Convert refuses
    # structural: the Print route never calls the Convert-scoped LUT guard
    assert "convert_lut_support" not in _inspect.getsource(printing)


def test_convert_without_z9_configured_409(sample_tiff_path):
    """Valid source profile but no Z9 wired (no lifespan in TestClient) → the
    DEST cannot be resolved → 409, BEFORE any Argyll call."""
    c = _client()
    fid = _upload(c, sample_tiff_path, "sample.tif")
    r = c.post("/api/convert", json={"file_id": fid, "gloss_enhancer": "OFF"})
    assert r.status_code == 409, r.text
