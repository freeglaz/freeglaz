# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression: the cctiff device output must be readable by BOTH downstream paths
of the Convert → Print hand-off — the Print pipeline (build_pdfx4 / tifffile) and
the preview (libvips). cctiff's LZW default is a single huge strip that neither can
decode in this environment; the uncompressed (-N) output fixes it, pixels identical.

(A/B) apply_cctiff(uncompressed=True) → -N ; default False keeps LZW (non-regression).
(D.1) -N output is PIXEL-IDENTICAL to the LZW output (proven, not asserted).
(D.3) a real -N device output is readable by Print AND preview.
(C)   preview retries with [unlimited] on the libvips memory error (unit, hermetic).
"""
import subprocess
from pathlib import Path

import numpy as np
import pyvips
import pytest
import tifffile

from lib.z9_client import devicelink
from lib.z9_client.argyll import find_argyll_binary
from webapp.backend.routes import files as files_route

_ASSETS = Path(__file__).resolve().parents[3] / "lib" / "z9_client" / "assets"


def _argyll() -> bool:
    return bool(find_argyll_binary("collink") and find_argyll_binary("cctiff"))


def _link_and_input(tmp: Path):
    """A real -G -ir link (sRGB → ClayRGB) + a small 16-bit RGB input for cctiff."""
    link = tmp / "link.icc"
    devicelink.run_collink(_ASSETS / "sRGB_IEC61966-2.1.icc",
                           _ASSETS / "ClayRGB-elle-V2-g22.icc",
                           link, intent="r", quality="l")
    h, w = 140, 160                                   # ≥10mm at 300dpi (build_pdfx4 min)
    yy, xx = np.mgrid[0:h, 0:w]
    arr = np.stack([(xx * 811) & 0xFFFF, (yy * 1201) & 0xFFFF,
                    ((xx * yy * 29) + 7) & 0xFFFF], axis=-1).astype(np.uint16)
    inp = tmp / "in.tif"
    tifffile.imwrite(str(inp), arr, photometric="rgb", resolution=(300, 300))
    return link, inp


def _decoded_pixels(tiff: Path) -> np.ndarray:
    """Read a TIFF's pixels regardless of compression (flatten LZW via libtiff)."""
    with tifffile.TiffFile(tiff) as t:
        comp = int(t.pages[0].tags["Compression"].value)
    if comp == 1:
        return tifffile.imread(str(tiff))
    flat = tiff.with_suffix(".flat.tif")
    subprocess.run(["tiffcp", "-c", "none", str(tiff), str(flat)], check=True, capture_output=True)
    return tifffile.imread(str(flat))


@pytest.mark.skipif(not _argyll(), reason="collink/cctiff not installed")
def test_cctiff_default_is_lzw_uncompressed_flag_is_N(tmp_path):
    """Non-regression: default (no flag) stays LZW; uncompressed=True → no compression."""
    link, inp = _link_and_input(tmp_path)
    lzw = tmp_path / "lzw.tif"; devicelink.apply_cctiff(link, inp, lzw)
    unc = tmp_path / "unc.tif"; devicelink.apply_cctiff(link, inp, unc, uncompressed=True)
    with tifffile.TiffFile(lzw) as t:
        assert int(t.pages[0].tags["Compression"].value) == 5    # LZW (existing callers unchanged)
    with tifffile.TiffFile(unc) as t:
        assert int(t.pages[0].tags["Compression"].value) == 1    # none


@pytest.mark.skipif(not _argyll(), reason="collink/cctiff not installed")
def test_cctiff_uncompressed_is_pixel_identical_to_lzw(tmp_path):
    """D.1: the -N output is bit-for-bit identical to the LZW output — proven, not
    asserted. Guards the Convert→Print integrity (only the compression changes)."""
    link, inp = _link_and_input(tmp_path)
    lzw = tmp_path / "lzw.tif"; devicelink.apply_cctiff(link, inp, lzw)
    unc = tmp_path / "unc.tif"; devicelink.apply_cctiff(link, inp, unc, uncompressed=True)
    assert np.array_equal(_decoded_pixels(lzw), _decoded_pixels(unc))


@pytest.mark.skipif(not _argyll(), reason="collink/cctiff not installed")
def test_uncompressed_device_output_readable_by_print_and_preview(tmp_path):
    """D.3: a real Convert-style -N device output (embed dest) is readable by the
    Print pipeline (build_pdfx4/tifffile) AND the preview (libvips)."""
    from lib.z9_client.printing import PrintJob, PrintOps
    link, inp = _link_and_input(tmp_path)
    dev = tmp_path / "device.tif"
    devicelink.apply_cctiff(link, inp, dev,
                            embed_icc=_ASSETS / "ClayRGB-elle-V2-g22.icc", uncompressed=True)
    # preview (libvips) — must not raise
    assert len(files_route._render_tiff_preview(dev)) > 0
    # print pipeline (build_pdfx4 / tifffile) — must not raise
    job = PrintJob.for_tiff(dev, paper_id="A" * 32, media_source="ROLL",
                            sheet_w_mm=610, sheet_h_mm=200,
                            offset_x_mm=10, offset_y_mm=10, orientation=0)
    job.validate()
    out_pdf = tmp_path / "o.pdf"
    PrintOps(None).build_pdfx4(job, out_pdf)
    assert out_pdf.exists() and out_pdf.stat().st_size > 0


def test_preview_retries_with_unlimited_on_libvips_memory_error(monkeypatch, tmp_path):
    """C: on the libvips memory error (single huge strip), _render_tiff_preview
    retries once with the [unlimited] loadpath. Hermetic — no libvips decode."""
    calls = []

    def fake_thumb(loadpath: str) -> bytes:
        calls.append(loadpath)
        if "[unlimited]" not in loadpath:
            raise pyvips.Error("unable to copy to memory")   # first attempt trips the limit
        return b"\x89PNG-ok"

    monkeypatch.setattr(files_route, "_tiff_thumbnail_png", fake_thumb)
    out = files_route._render_tiff_preview(tmp_path / "device.tif")
    assert out == b"\x89PNG-ok"
    assert len(calls) == 2 and calls[0].endswith("device.tif") and calls[1].endswith("device.tif[unlimited]")
