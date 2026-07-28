# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""build_pdfx4 — single ICC source via job.icc_override (commits #1/#2).

Proves the unification is safe:
  - override == the TIFF's own ICC  →  BYTE-IDENTICAL PDF (chart migration #2
    is a provable no-op: the chart just feeds build_pdfx4 the bytes it already
    had in the TIFF tag);
  - override embeds those bytes in BOTH PDF/X-4 roles (image /ICCBased AND
    OutputIntent /DestOutputProfile), preserving the image==OutputIntent
    invariant the preflight guards;
  - a DIFFERENT override changes the embedded ICC (both roles) while leaving
    the pixels byte-for-byte intact (the gesture never touches the raster).
"""
from pathlib import Path

import numpy as np
import pikepdf
import pytest
import tifffile

from lib.z9_client.printing import PrintJob, PrintOps

_ASSETS = Path(__file__).resolve().parents[3] / "lib/z9_client/assets"


def _make_tiff(path: Path, icc: bytes) -> None:
    arr = (np.arange(32 * 20 * 3, dtype=np.uint16).reshape(20, 32, 3) * 3)
    tifffile.imwrite(
        path, arr, photometric="rgb",
        extratags=[(34675, "B", len(icc), icc, False)],
        resolution=(300, 300),
    )


def _job(tiff: Path, **kw) -> PrintJob:
    return PrintJob(
        tiff_path=tiff, paper_id="DEADBEEF", paper_name="t",
        media_source="MANUALFEED", sheet_w_mm=210.0, sheet_h_mm=297.0,
        image_w_mm=50.0, image_h_mm=31.25, offset_x_mm=10.0, offset_y_mm=10.0,
        **kw,
    )


def _roles(pdf_path: Path):
    with pikepdf.open(pdf_path) as pdf:
        im = next(iter(pdf.pages[0].Resources.XObject.values()))
        icc_img = bytes(im.ColorSpace[1].read_bytes())
        icc_oi = bytes(pdf.Root.OutputIntents[0].DestOutputProfile.read_bytes())
        pixels = bytes(im.read_bytes())
    return icc_img, icc_oi, pixels


@pytest.fixture
def icc_resident() -> bytes:
    return (_ASSETS / "sRGB2014.icc").read_bytes()


@pytest.fixture
def icc_other() -> bytes:
    return (_ASSETS / "eciRGB_v2.icc").read_bytes()


def test_override_equal_to_tag_is_byte_identical(tmp_path, icc_resident):
    # Chart migration (#2): feeding the same bytes via override must produce a
    # byte-identical PDF vs reading them from the TIFF tag.
    tiff = tmp_path / "src.tif"
    _make_tiff(tiff, icc_resident)
    ops = PrintOps(client=None)  # build_pdfx4 does not use the client

    pdf_tag = tmp_path / "a.pdf"
    pdf_over = tmp_path / "b.pdf"
    ops.build_pdfx4(_job(tiff), pdf_tag)
    ops.build_pdfx4(_job(tiff, icc_override=icc_resident), pdf_over)

    assert pdf_tag.read_bytes() == pdf_over.read_bytes()


def test_override_embeds_in_both_roles(tmp_path, icc_resident, icc_other):
    tiff = tmp_path / "src.tif"
    _make_tiff(tiff, icc_resident)          # tag = resident
    ops = PrintOps(client=None)

    pdf = tmp_path / "c.pdf"
    ops.build_pdfx4(_job(tiff, icc_override=icc_other), pdf)   # override = other

    icc_img, icc_oi, _ = _roles(pdf)
    assert icc_img == icc_other             # image role overridden
    assert icc_oi == icc_other              # OutputIntent role overridden
    assert icc_img == icc_oi                # image==OutputIntent invariant kept


def test_chart_refetch_via_helper_equals_tag(tmp_path, icc_resident):
    """Chart non-regression (no reprint needed): at unchanged profile, the fresh
    refetch at the go (fetch_resident_icc → getProfile) returns the SAME bytes as
    the generation-time TIFF tag → the PDF built with that override is
    BYTE-IDENTICAL to the PDF built from the tag. The printed chart is unchanged."""
    from types import SimpleNamespace
    from lib.z9_client.printing import fetch_resident_icc

    tiff = tmp_path / "chart.tif"
    _make_tiff(tiff, icc_resident)                # tag = resident (generation-time)
    ops = PrintOps(client=None)

    # fresh refetch at the go returns the same resident bytes (profile unchanged)
    z9 = SimpleNamespace(
        soap=SimpleNamespace(get_profile=lambda **kw: {"icc_bytes": icc_resident}))
    fetched = fetch_resident_icc(z9, "MID", "OFF", "COLOR")
    assert fetched == icc_resident

    pdf_tag = tmp_path / "a.pdf"
    pdf_refetch = tmp_path / "b.pdf"
    ops.build_pdfx4(_job(tiff), pdf_tag)                          # tag path
    ops.build_pdfx4(_job(tiff, icc_override=fetched), pdf_refetch)  # refetch path

    assert pdf_tag.read_bytes() == pdf_refetch.read_bytes()


def test_override_never_touches_pixels(tmp_path, icc_resident, icc_other):
    tiff = tmp_path / "src.tif"
    _make_tiff(tiff, icc_resident)
    ops = PrintOps(client=None)

    pdf_tag = tmp_path / "a.pdf"
    pdf_over = tmp_path / "c.pdf"
    ops.build_pdfx4(_job(tiff), pdf_tag)
    ops.build_pdfx4(_job(tiff, icc_override=icc_other), pdf_over)

    _, _, px_tag = _roles(pdf_tag)
    _, _, px_over = _roles(pdf_over)
    assert px_tag == px_over                # raster identical despite ICC swap
