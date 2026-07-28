"""Smoke tests upload + info + preview."""
import io
from pathlib import Path

import pikepdf
from PIL import Image
from fastapi.testclient import TestClient

from webapp.backend.main import app
from webapp.backend.services import file_storage
from webapp.backend.services.file_inspector import to_file_info


def _client() -> TestClient:
    return TestClient(app)


def test_tiff_upload_info_preview_roundtrip(sample_tiff_path):
    c = _client()
    with open(sample_tiff_path, "rb") as f:
        r = c.post("/api/files", files={"file": ("sample.tif", f, "image/tiff")})
    assert r.status_code == 200, r.text
    fid = r.json()["file_id"]
    assert file_storage.is_valid_file_id(fid)
    assert r.json()["filename"] == "sample.tif"

    r = c.get(f"/api/files/{fid}/info")
    assert r.status_code == 200
    info = r.json()
    assert info["kind"] == "tiff"
    assert info["dpi"] == 300
    assert info["has_icc"] is True
    assert info["bits_per_sample"] == 8
    # 64 px @ 300 dpi = 64/300*25.4 ≈ 5.42 mm
    assert abs(info["width_mm"] - 5.4186) < 0.01
    assert info["is_printable"] is True
    assert info["blocking_issues"] == []
    # 8-bit + embedded sRGB -> 1 warning "TIFF 8-bit"
    assert any("8-bit" in w for w in info["warnings"])
    # icc_name may be "" (Pillow generates an sRGB with an empty desc tag) — the
    # invariant that matters is that extraction was attempted without crashing.
    assert info["icc_name"] is not None

    r = c.get(f"/api/files/{fid}/preview")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    Image.open(io.BytesIO(r.content)).verify()


def test_pdf_upload_is_rejected(sample_pdf_path):
    """The webapp is TIFF-only: a PDF upload is refused at the gate (by content,
    not extension) with a structured code. The PDF *inspection* helper still
    exists and is unit-tested via ``to_file_info`` below — only the upload route
    no longer accepts PDF."""
    c = _client()
    with open(sample_pdf_path, "rb") as f:
        r = c.post("/api/files", files={"file": ("sample.pdf", f, "application/pdf")})
    assert r.status_code == 415, r.text
    assert r.json()["detail"]["code"] == "not_tiff"


def test_missing_file_id_returns_404():
    c = _client()
    fake = "00000000-0000-4000-8000-000000000000"
    assert c.get(f"/api/files/{fake}/info").status_code == 404
    assert c.get(f"/api/files/{fake}/preview").status_code == 404


def test_unsupported_extension_returns_415():
    c = _client()
    r = c.post("/api/files", files={"file": ("evil.txt", b"hello", "text/plain")})
    assert r.status_code == 415


def test_invalid_file_id_format_returns_404():
    c = _client()
    # Not a UUID4 -> is_valid_file_id() rejects before any filesystem access
    assert c.get("/api/files/..%2Fetc/info").status_code == 404
    assert c.get("/api/files/not-a-uuid/info").status_code == 404


def _make_pdf_with_output_intent(path, info_str, oci_str=None):
    """Generate an A4 PDF with OutputIntent /Info (+ optional OCI)."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(595, 842))
    oi_dict = {
        "/Type": pikepdf.Name("/OutputIntent"),
        "/S":    pikepdf.Name("/GTS_PDFX"),
        "/Info": pikepdf.String(info_str),
    }
    if oci_str is not None:
        oi_dict["/OutputConditionIdentifier"] = pikepdf.String(oci_str)
    output_intent = pdf.make_indirect(pikepdf.Dictionary(oi_dict))
    pdf.Root["/OutputIntents"] = pikepdf.Array([output_intent])
    pdf.save(str(path))
    pdf.close()


def test_inspect_pdf_extracts_icc_name_from_output_intent_info(tmp_path):
    """PDF with OutputIntent /Info -> icc_name extracted, has_icc=True."""
    p = tmp_path / "with_oi_info.pdf"
    _make_pdf_with_output_intent(
        p,
        info_str="HP DesignJet Z9 24in 2025, Canson Photolustre RC 2021, GE OFF",
    )
    info = to_file_info("dummy-id", "with_oi_info.pdf", p)
    assert info.kind == "pdf"
    assert info.has_icc is True
    assert info.icc_name == "HP DesignJet Z9 24in 2025, Canson Photolustre RC 2021, GE OFF"


def test_inspect_pdf_falls_back_to_output_condition_identifier(tmp_path):
    """PDF with OutputIntent without /Info but with /OutputConditionIdentifier:
    the fallback takes over."""
    p = tmp_path / "with_oci_only.pdf"
    # Create an OutputIntent with OCI but without /Info (empty Info).
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(595, 842))
    oi = pdf.make_indirect(pikepdf.Dictionary({
        "/Type": pikepdf.Name("/OutputIntent"),
        "/S":    pikepdf.Name("/GTS_PDFX"),
        "/OutputConditionIdentifier": pikepdf.String("FOGRA39"),
    }))
    pdf.Root["/OutputIntents"] = pikepdf.Array([oi])
    pdf.save(str(p))
    pdf.close()

    info = to_file_info("dummy-id", "with_oci_only.pdf", p)
    assert info.has_icc is True
    assert info.icc_name == "FOGRA39"


def test_icc_name_extracted_from_v4_mluc_profile():
    """ICC v4 profiles whose desc tag is in mluc format (multi-localized
    unicode) — common on all modern ICC v4 profiles — used to return
    ``""`` with the historic custom parser in
    ``lib.z9_client.printing._get_icc_profile_description``.

    We verify that the ``_extract_icc_description`` helper (based on Pillow
    ImageCms, which covers mluc) reads the name correctly. Fixture: a
    non-HP v4 mluc profile already bundled (``sRGB_v4_ICC_preference.icc``, mluc desc tag).
    """
    from webapp.backend.services.file_inspector import _extract_icc_description

    icc_path = (
        Path(__file__).resolve().parents[3]
        / "lib" / "z9_client" / "assets" / "sRGB_v4_ICC_preference.icc"
    )
    assert icc_path.exists(), f"fixture introuvable : {icc_path}"
    icc_bytes = icc_path.read_bytes()
    desc = _extract_icc_description(icc_bytes)
    assert desc == "sRGB v4 ICC preference perceptual intent beta"


def test_icc_name_extracted_from_v2_ascii_profile():
    """Also passes ICC v2 profiles (ASCII desc tag). Regression test to
    ensure that the switch to ImageCms does not break v2 profiles that
    already worked with the custom parser."""
    from webapp.backend.services.file_inspector import _extract_icc_description

    icc_path = (
        Path(__file__).resolve().parents[3]
        / "lib" / "z9_client" / "assets" / "sRGB_IEC61966-2.1.icc"
    )
    assert icc_path.exists(), f"fixture introuvable : {icc_path}"
    icc_bytes = icc_path.read_bytes()
    desc = _extract_icc_description(icc_bytes)
    assert desc == "sRGB IEC61966-2.1"
