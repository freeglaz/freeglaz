"""Z9 Pro on a roll: the PDF page is the measured roll and the image is drawn
under the PJL window the Pro centres on the roll; the PJL header is unchanged.

Reference case (print of 2026-11-23): image 1230 x 98 mm, roll measured at
1268.8 mm, economical roll (margins 5 mm), GE off. Before the fix the MediaBox
was PAPERWIDTH (1240 mm) with the image at x = 5 mm, and the Pro cut ~14 mm on
one side. Expected now: MediaBox 1268.8 mm, image at ~19.4 mm, ~19.4 mm of paper
on each side. Nothing here talks to a printer.
"""
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import numpy as np
import pikepdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageCms
from tifffile import imwrite

from lib.z9_client.exceptions import Z9GeometryError
from lib.z9_client.printing import (
    PAGE_LAYOUT_PAPERWIDTH, PAGE_LAYOUT_Z9PRO_ROLL, PJL_PER_MM, PrintJob, PrintOps,
    mm_to_pjl, mm_to_pt, paperwidth_pjl, z9pro_roll_image_x_mm,
)
from webapp.backend.main import app
from webapp.backend.models import LoadedPaper, PrintParams
from webapp.backend.routes.status import get_z9
from webapp.backend.services import print_worker
from webapp.backend.services.print_geometry import (
    PaperIccInfo, UnsupportedPlacement, compute_geometry, detect_geometry_issues,
)

ROLL = 1268.8
IMG_W, IMG_H = 1230.0, 98.0
VARIABLE_PJL = ("JobAcct4=", "JobAcct5=", "TIMESTAMP")


# ─── helpers ────────────────────────────────────────────────────────────────
def _icc() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _tiff(tmp_path: Path, w_mm: float, h_mm: float, px_per_mm: int = 1) -> Path:
    """An RGB TIFF of w x h mm (resolution in inches; 1 px/mm by default, enough
    for the PDF/PRN geometry; the preview route wants a printable resolution)."""
    p = tmp_path / f"img_{w_mm:g}x{h_mm:g}_{px_per_mm}.tif"
    arr = np.full((round(h_mm * px_per_mm), round(w_mm * px_per_mm), 3), 180, np.uint8)
    dpi = 25.4 * px_per_mm
    imwrite(p, arr, photometric="rgb", resolution=(dpi, dpi), resolutionunit="INCH",
            extratags=[(34675, 7, None, _icc(), False)])
    return p


def _roll(width: Optional[float] = ROLL) -> LoadedPaper:
    return LoadedPaper(id="MID_ROLL", short_id=0, name="CanvasMatteWhite", category="canvas",
                       is_factory=False, media_source="ROLL", sheet_width_mm=None,
                       sheet_height_mm=None, roll_width_mm=width)


def _sheet(w: float = 210.0, h: float = 297.0) -> LoadedPaper:
    return LoadedPaper(id="MID_A4", short_id=0, name="HPCotton", category="paper", is_factory=True,
                       media_source="MANUAL_FEED", sheet_width_mm=w, sheet_height_mm=h,
                       roll_width_mm=None)


def _params(**kw) -> PrintParams:
    return PrintParams(**{"gloss_enhancer": "OFF", **kw})


def _job(tmp_path, loaded, family, params=None, w=IMG_W, h=IMG_H, gloss="OFF"):
    params = params or _params(gloss_enhancer=gloss)
    geom = compute_geometry(loaded, params, w, h, family=family)
    job = print_worker._build_lib_job(_tiff(tmp_path, w, h), params, loaded, geom)
    return geom, job


def _build(tmp_path, job, name):
    ops = PrintOps.__new__(PrintOps)            # no client: nothing is sent
    pdf = tmp_path / f"{name}.pdf"
    prn = tmp_path / f"{name}.prn"
    job.validate()
    ops.build_pdfx4(job, pdf)
    ops.build_prn(job, pdf, prn)
    return pdf, prn


def _pjl_lines(prn: Path) -> list[str]:
    data = prn.read_bytes()
    head = data[:data.index(b"@PJL ENTER LANGUAGE=")].decode()
    return [ln for ln in head.splitlines() if not any(v in ln for v in VARIABLE_PJL)]


def _page(pdf: Path):
    """(MediaBox width mm, image x mm, image y mm, image w mm, image h mm)."""
    with pikepdf.open(pdf) as doc:
        page = doc.pages[0]
        box = [float(v) for v in page.MediaBox]
        a, _, _, d, e, f = [float(v) for v in pikepdf.parse_content_stream(page)[1].operands]
    pt = mm_to_pt(1.0)
    return box[2] / pt, e / pt, f / pt, a / pt, d / pt


# ─── the reference case ─────────────────────────────────────────────────────
def test_reference_case_geometry():
    g = compute_geometry(_roll(), _params(), IMG_W, IMG_H, family="z9pro")
    assert g.page_layout == PAGE_LAYOUT_Z9PRO_ROLL
    assert (g.pjl_left_margin_mm, g.pjl_top_margin_mm) == (5.0, 5.0)     # what PJL gets
    assert g.image_x_mm == pytest.approx(19.4, abs=0.02)                 # where it lands
    assert g.auto_x_mm == g.image_x_mm and g.centered_x
    assert g.margin_left_mm == pytest.approx(g.margin_right_mm, abs=0.05)
    assert g.sheet_width_mm == ROLL and g.sheet_height_mm == pytest.approx(108.0)
    assert detect_geometry_issues(g, g.media_source) == ([], [])


def test_reference_case_pjl_unchanged_and_pdf_centred(tmp_path):
    g_pro, pro = _job(tmp_path, _roll(), "z9pro")
    g_old, old = _job(tmp_path, _roll(), None)                 # the historical layout
    assert (pro.page_layout, old.page_layout) == (PAGE_LAYOUT_Z9PRO_ROLL, PAGE_LAYOUT_PAPERWIDTH)
    pdf_pro, prn_pro = _build(tmp_path, pro, "pro")
    pdf_old, prn_old = _build(tmp_path, old, "old")
    # PJL: identical, line for line (UUID and timestamps aside)
    assert _pjl_lines(prn_pro) == _pjl_lines(prn_old)
    params = PrintOps.inspect_prn(prn_pro)["params"]
    assert (params["PAPERWIDTH"], params["LEFTMARGIN"], params["RIGHTMARGIN"]) == ("35150", "142", "142")
    assert (params["GLOSSENHANCER"], params["MEDIASOURCE"]) == ("OFF", "ROLL")
    # PDF: before = PAPERWIDTH page, image at 5 mm; after = roll page, image centred
    w, x, y, iw, ih = _page(pdf_old)
    assert (w, x, y) == (pytest.approx(1240.0, abs=0.01), pytest.approx(5.0), pytest.approx(5.0))
    w, x, y, iw, ih = _page(pdf_pro)
    assert w == pytest.approx(ROLL, abs=1e-6)
    assert x == pytest.approx(19.4, abs=0.02) and y == pytest.approx(5.0)
    assert (iw, ih) == (pytest.approx(IMG_W), pytest.approx(IMG_H))
    assert (w - x - iw) == pytest.approx(x, abs=0.05)                     # ~19.4 mm each side
    # the image sits exactly under the centred PJL window (reduced by the margins)
    page_mm = int(params["PAPERWIDTH"]) / PJL_PER_MM
    window_left = (ROLL - page_mm) / 2 + int(params["LEFTMARGIN"]) / PJL_PER_MM
    assert x == pytest.approx(window_left, abs=1e-6)


def test_gloss_enhancer_on_keeps_the_same_pjl(tmp_path):
    _, pro = _job(tmp_path, _roll(), "z9pro", gloss="FULLPAGE")
    _, old = _job(tmp_path, _roll(), None, gloss="FULLPAGE")
    _, prn_pro = _build(tmp_path, pro, "pro_ge")
    _, prn_old = _build(tmp_path, old, "old_ge")
    assert _pjl_lines(prn_pro) == _pjl_lines(prn_old)
    assert "@PJL SET GLOSSENHANCER = FULLPAGE" in _pjl_lines(prn_pro)


def test_vertical_offset_is_kept(tmp_path):
    g, job = _job(tmp_path, _roll(), "z9pro", params=_params(offset_y_mm=20.0))
    assert (g.pjl_top_margin_mm, g.image_y_mm, job.offset_y_mm) == (25.0, 25.0, 25.0)
    assert g.sheet_height_mm == pytest.approx(25.0 + IMG_H + 5.0)
    assert g.image_x_mm == pytest.approx(19.4, abs=0.02)


# ─── the Z9 and every other family keep the historical layout ───────────────
@pytest.mark.parametrize("family", [None, "z9", "designjet"])
def test_other_families_on_a_roll_are_unchanged(tmp_path, family):
    g, job = _job(tmp_path, _roll(609.6), family, w=200.0, h=50.0)
    assert g.page_layout == PAGE_LAYOUT_PAPERWIDTH
    assert (g.image_x_mm, g.pjl_left_margin_mm, job.offset_x_mm) == (5.0, 5.0, 5.0)
    pdf, _ = _build(tmp_path, job, f"roll_{family}")
    w, x, *_ = _page(pdf)
    assert w == pytest.approx(5.0 + 200.0 + 5.0) and x == pytest.approx(5.0)


@pytest.mark.parametrize("family", [None, "z9", "z9pro"])
def test_sheets_are_unchanged(tmp_path, family):
    """Manual feed: centred on the sheet, MediaBox = PAPERWIDTH, whatever the family
    (the Pro 64" has no sheet feed; its MANUAL_FEED path is left as it was)."""
    g, job = _job(tmp_path, _sheet(), family, w=127.0, h=100.0)
    assert g.page_layout == PAGE_LAYOUT_PAPERWIDTH
    assert g.image_x_mm == pytest.approx((210.0 - 127.0) / 2) == g.pjl_left_margin_mm
    pdf, prn = _build(tmp_path, job, f"sheet_{family}")
    w, x, *_ = _page(pdf)
    assert w == pytest.approx(41.5 + 127.0 + 5.0) and x == pytest.approx(41.5)
    assert PrintOps.inspect_prn(prn)["params"]["MEDIASOURCE"] == "MANUALFEED"


def test_z9_user_offset_still_moves_the_image():
    g = compute_geometry(_roll(609.6), _params(offset_x_mm=50.0), 100.0, 20.0, family="z9")
    assert (g.image_x_mm, g.pjl_left_margin_mm) == (55.0, 55.0)


# ─── refusals on the Z9 Pro roll (no PJL margin is changed instead) ─────────
@pytest.mark.parametrize("params, word", [
    (_params(offset_x_mm=10.0), "horizontal"),
    (_params(sheet_width_mm_override=1100.0), "width override"),
    (_params(sheet_height_mm_override=500.0), "virtual document"),
])
def test_z9pro_roll_refuses_what_needs_other_margins(params, word):
    with pytest.raises(UnsupportedPlacement, match=word):
        compute_geometry(_roll(), params, IMG_W, IMG_H, family="z9pro")


@pytest.mark.parametrize("width", [None, 0.0, -5.0])
def test_z9pro_roll_without_a_roll_width_is_refused(width):
    with pytest.raises(UnsupportedPlacement, match="roll width is unknown"):
        compute_geometry(_roll(width), _params(), IMG_W, IMG_H, family="z9pro")


# ─── limits ─────────────────────────────────────────────────────────────────
def test_image_almost_as_wide_as_the_roll():
    w = ROLL - 10.2                                   # 0.2 mm of play once the 2 x 5 mm are taken
    g = compute_geometry(_roll(), _params(), w, IMG_H, family="z9pro")
    assert g.image_x_mm == pytest.approx(5.1, abs=0.04)
    assert detect_geometry_issues(g, g.media_source)[0] == []


def test_image_wider_than_the_printable_roll_is_blocked():
    g = compute_geometry(_roll(), _params(), ROLL - 8.0, IMG_H, family="z9pro")
    blocking, _ = detect_geometry_issues(g, g.media_source)
    assert blocking and "too wide" in blocking[0]


def test_job_validation_of_the_z9pro_roll_layout(tmp_path):
    tif = _tiff(tmp_path, 100.0, 20.0)
    base = dict(tiff_path=tif, paper_id="X", image_w_mm=100.0, image_h_mm=20.0, offset_x_mm=5.0,
                offset_y_mm=5.0, sheet_h_mm=30.0, page_layout=PAGE_LAYOUT_Z9PRO_ROLL)
    PrintJob(media_source="ROLL", sheet_w_mm=610.0, **base).validate()
    with pytest.raises(Z9GeometryError) as e:
        PrintJob(media_source="MANUALFEED", sheet_w_mm=610.0, **base).validate()
    assert "is for a roll" in str(e.value.details)
    with pytest.raises(Z9GeometryError) as e:                           # roll narrower than the page
        PrintJob(media_source="ROLL", sheet_w_mm=105.0, **{**base, "image_w_mm": 100.0}).validate()
    assert any("narrower than the page" in d or "overflows" in d for d in e.value.details)
    with pytest.raises(Z9GeometryError):
        PrintJob(media_source="ROLL", sheet_w_mm=610.0, **{**base, "page_layout": "centred"}).validate()


def test_paperwidth_helper_matches_what_build_prn_emits(tmp_path):
    for w, off in ((1230.0, 5.0), (99.99, 5.0), (127.3, 41.35), (612.7, 17.25)):
        _, job = _job(tmp_path, _roll(1400.0), None, w=w, h=30.0)
        job.offset_x_mm = off
        pdf, prn = _build(tmp_path, job, f"pw_{w:g}")
        assert int(PrintOps.inspect_prn(prn)["params"]["PAPERWIDTH"]) == paperwidth_pjl(off, job.image_w_mm)
    assert mm_to_pjl(5.0) == 142
    assert z9pro_roll_image_x_mm(1240.012, 5.0, IMG_W) == pytest.approx(5.0, abs=0.02)


# ─── the two print paths and the user PDF ───────────────────────────────────
def test_lib_job_carries_the_pjl_margins_and_the_layout(tmp_path):
    g, job = _job(tmp_path, _roll(), "z9pro")
    assert (job.offset_x_mm, job.offset_y_mm) == (g.pjl_left_margin_mm, g.pjl_top_margin_mm) == (5.0, 5.0)
    assert job.page_layout == g.page_layout == PAGE_LAYOUT_Z9PRO_ROLL
    assert job.sheet_w_mm == ROLL


def _fake_printer(model: str, source: str, width_mm: float):
    dashboard = {"identification": {"ModelName": model, "ModelNumber": model, "SerialNumber": "S",
                                    "FwReleaseName": "F"},
                 "ink_levels": {}, "ink_warnings": [], "global_status": "Ready",
                 "loaded_paper_id": "P", "loaded_paper_name": "Roll", "loaded_paper_source": source,
                 "loaded_paper_source_label": source, "loaded_paper_width_mm": width_mm,
                 "loaded_paper_length_mm": None}
    device = SimpleNamespace(status=lambda: dashboard, device_status=lambda: {})
    paper = SimpleNamespace(get=lambda _: {"id": "P", "name": "Roll", "category_id": "PHOTO",
                                           "is_factory": False},
                            capabilities=lambda _: None)
    soap = SimpleNamespace(get_profile=lambda *a, **k: {"outcome": "OK", "icc_bytes": None})
    return SimpleNamespace(device=device, paper=paper, soap=soap)


@pytest.fixture
def client():
    app.state.capabilities_cache = {}
    app.state.paper_icc_cache = {}
    yield TestClient(app)
    app.dependency_overrides.clear()


def _upload(client, path: Path, mime: str) -> str:
    with open(path, "rb") as f:
        r = client.post("/api/files", files={"file": (path.name, f, mime)})
    assert r.status_code == 200, r.text
    return r.json()["file_id"]


def test_preview_on_a_z9pro_roll(client, tmp_path):
    app.state.paper_icc_cache[("P", "FULLPAGE", "PRINTER_RGB")] = PaperIccInfo(name="Roll", md5=None)
    app.dependency_overrides[get_z9] = lambda: _fake_printer("HP DesignJet Z9 Pro 64in PS", "ROLL", ROLL)
    fid = _upload(client, _tiff(tmp_path, IMG_W, IMG_H, px_per_mm=8), "image/tiff")   # 203 dpi
    data = client.post("/api/print/preview", json={"file_id": fid, "params": {}}).json()
    g = data["geometry"]
    assert g["page_layout"] == "z9pro_roll" and g["pjl_left_margin_mm"] == 5.0
    assert g["image_x_mm"] == pytest.approx(19.4, abs=0.02) and g["auto_x_mm"] == g["image_x_mm"]
    assert data["can_print"] is True and not data["blocking_issues"]
    moved = client.post("/api/print/preview", json={"file_id": fid, "params": {"offset_x_mm": 30}}).json()
    assert moved["can_print"] is False and any("horizontal" in b for b in moved["blocking_issues"])


def test_a_user_pdf_is_refused_by_the_webapp_and_the_layout(client, tmp_path):
    """User PDFs: the webapp only takes TIFFs (415 at upload), and the library refuses
    the z9pro_roll layout for a ready-made PDF (sent with its own MediaBox, the
    layout would not apply). A PDF sent through the Python API with the default
    layout is placed by the Pro as before the fix."""
    pdf = tmp_path / "user.pdf"
    Image.new("RGB", (500, 40), (90, 90, 90)).save(pdf, "PDF", resolution=25.4)
    with open(pdf, "rb") as f:
        r = client.post("/api/files", files={"file": ("user.pdf", f, "application/pdf")})
    assert r.status_code == 415 and r.json()["detail"]["code"] == "not_tiff"
    job = PrintJob(pdf_path=pdf, paper_id="X", media_source="ROLL", sheet_w_mm=ROLL, sheet_h_mm=60.0,
                   image_w_mm=500.0, image_h_mm=40.0, offset_x_mm=5.0, offset_y_mm=5.0,
                   page_layout=PAGE_LAYOUT_Z9PRO_ROLL)
    with pytest.raises(Z9GeometryError) as e:
        job.validate()
    assert any("ready-made PDF" in d for d in e.value.details)


def test_preview_on_a_z9_roll_is_unchanged(client, tmp_path):
    app.dependency_overrides[get_z9] = lambda: _fake_printer("HP DesignJet Z9+ 24in", "ROLL", 609.6)
    fid = _upload(client, _tiff(tmp_path, 200.0, 50.0, px_per_mm=8), "image/tiff")
    g = client.post("/api/print/preview", json={"file_id": fid, "params": {}}).json()["geometry"]
    assert g["page_layout"] == "paperwidth"
    assert g["image_x_mm"] == g["pjl_left_margin_mm"] == pytest.approx(5.0, abs=0.01)
