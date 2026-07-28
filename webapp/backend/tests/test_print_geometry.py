"""Unit tests for print geometry — invariants per mode.

Guard against CLI ↔ Webapp divergence reported by user: the frontend
preview showed an "infinite sheet" of 9999mm in ROLL because
`paper.height_mm` is null on the status side for a roll, whereas the
backend correctly computes `sheet_height_mm = image_h + 10mm` via
``compute_geometry`` (ROLL economy mode).
"""
from webapp.backend.models import LoadedPaper, PrintParams
from webapp.backend.services.print_geometry import (
    ROLL_ECON_BOTTOM_PAD_MM,
    ROLL_ECON_MARGIN_MM,
    compute_geometry,
)


def _default_params(**kw) -> PrintParams:
    return PrintParams(
        gloss_enhancer="OFF", quality="HIGH", copies=1,
        offset_x_mm=0.0, offset_y_mm=0.0,
        max_detail="OFF", drytime="NORMAL", rendermode="COLOR",
        **kw,
    )


def _roll_paper(roll_width_mm: float = 609.6) -> LoadedPaper:
    return LoadedPaper(
        id="MID_TEST_ROLL", short_id=0,
        name="CansonPhotolustreRC0526", category="canvas",
        is_factory=False, media_source="ROLL",
        sheet_width_mm=None, sheet_height_mm=None,
        roll_width_mm=roll_width_mm,
    )


def _sheet_paper(w: float = 210.0, h: float = 297.0) -> LoadedPaper:
    return LoadedPaper(
        id="MID_TEST_A4", short_id=0,
        name="HPCotton290gsm", category="paper",
        is_factory=True, media_source="MANUAL_FEED",
        sheet_width_mm=w, sheet_height_mm=h,
        roll_width_mm=None,
    )


# ─── ROLL economy ─────────────────────────────────────────────────────


def test_roll_economic_sheet_height_equals_image_plus_pad():
    """Concrete case reported by user: 100×50mm on a 609.6mm roll must
    produce a virtual sheet 609.6×60mm (50 + 10mm bottom pad).
    """
    geom = compute_geometry(
        _roll_paper(609.6), _default_params(),
        image_w_mm=100.0, image_h_mm=50.0,
    )
    assert geom.sheet_width_mm == 609.6
    assert geom.sheet_height_mm == 50.0 + ROLL_ECON_BOTTOM_PAD_MM == 60.0
    assert geom.media_source == "ROLL"


def test_roll_economic_image_at_top_left_with_margin():
    """In ROLL economy, the image is positioned at (5mm, 5mm) — not
    centered (historical economy mode to minimize paper).
    """
    geom = compute_geometry(
        _roll_paper(609.6), _default_params(),
        image_w_mm=100.0, image_h_mm=50.0,
    )
    assert geom.image_x_mm == ROLL_ECON_MARGIN_MM == 5.0
    assert geom.image_y_mm == ROLL_ECON_MARGIN_MM == 5.0
    assert geom.centered_x is False
    assert geom.centered_y is False


def test_roll_virtual_document_uses_override_height():
    """With ``sheet_height_mm_override`` (virtual document mode), the
    sheet adopts the given height and the image is centered like a
    classic sheet.
    """
    geom = compute_geometry(
        _roll_paper(609.6),
        _default_params(sheet_height_mm_override=420.0),
        image_w_mm=100.0, image_h_mm=50.0,
    )
    assert geom.sheet_height_mm == 420.0
    # Centered: (sheet_w - img_w) / 2 and (sheet_h - img_h) / 2
    assert geom.image_x_mm == (609.6 - 100.0) / 2
    assert geom.image_y_mm == (420.0 - 50.0) / 2
    assert geom.centered_x is True
    assert geom.centered_y is True


# ─── MANUAL_FEED ──────────────────────────────────────────────────────


def test_manual_feed_centers_image_on_sheet():
    geom = compute_geometry(
        _sheet_paper(210.0, 297.0), _default_params(),
        image_w_mm=100.0, image_h_mm=50.0,
    )
    assert geom.sheet_width_mm == 210.0
    assert geom.sheet_height_mm == 297.0
    assert geom.image_x_mm == (210.0 - 100.0) / 2 == 55.0
    assert geom.image_y_mm == (297.0 - 50.0) / 2 == 123.5
    assert geom.centered_x is True
    assert geom.centered_y is True
