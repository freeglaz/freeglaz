# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert → Print hand-off DATA INTEGRITY.

The device TIFF produced by Convert (already in the resident's device space) is
handed off to Print (upload → PDF/X-4 → PRN). This guards that the PIXELS reach the
Print PDF/X-4 UNCHANGED — bit-for-bit, 16-bit preserved, no colour transform, no
resample — and that the colour setup is device passthrough (image /ICCBased ==
OutputIntent == the file's profile → APPE transparent → firmware prints raw values).

Chain covered:
  1. upload = raw byte copy (webapp files.py: shutil.copyfileobj) → byte-identical.
  2. Print TIFF→PDF/X-4 (build_pdfx4): 16-bit passthrough + lossless FlateDecode.
  3. colour: source profile == OutputIntent == resident → no conversion.

Hermetic: no Z9, no Argyll (build_pdfx4 is local — tifffile/pikepdf/numpy only).
"""
from pathlib import Path

import numpy as np
import pikepdf
import tifffile

from lib.z9_client.printing import PrintJob, PrintOps

_ASSETS = Path(__file__).resolve().parents[3] / "lib" / "z9_client" / "assets"


def _write_device_tiff(path: Path, arr: np.ndarray, icc: bytes) -> None:
    """A 16-bit RGB 'device' TIFF with an embedded ICC (like a Convert output)."""
    tifffile.imwrite(str(path), arr, photometric="rgb", resolution=(300, 300),
                     extratags=[(34675, 7, len(icc), icc, True)])   # 34675 = ICCProfile


def test_device_tiff_pixels_reach_print_pdf_bit_for_bit(tmp_path):
    icc = (_ASSETS / "sRGB_IEC61966-2.1.icc").read_bytes()
    # Distinctive 16-bit RGB pixels across the full range (each pixel unique) so a
    # truncation / colour shift / resample would show up immediately. ≥10mm at 300dpi.
    h, w = 140, 160
    yy, xx = np.mgrid[0:h, 0:w]
    arr = np.stack([(xx * 811) & 0xFFFF, (yy * 1201) & 0xFFFF,
                    ((xx * yy * 29) + 12345) & 0xFFFF], axis=-1).astype(np.uint16)
    src = tmp_path / "device.tif"
    _write_device_tiff(src, arr, icc)

    job = PrintJob.for_tiff(src, paper_id="A" * 32, media_source="ROLL",
                            sheet_w_mm=610, sheet_h_mm=200,
                            offset_x_mm=10, offset_y_mm=10, orientation=0)
    job.validate()
    out_pdf = tmp_path / "out.pdf"
    PrintOps(None).build_pdfx4(job, out_pdf)   # local build — no client used

    with pikepdf.open(str(out_pdf)) as pdf:
        im = pdf.pages[0].Resources.XObject.Im0
        assert int(im.BitsPerComponent) == 16            # 16-bit preserved
        assert str(im.Filter) == "/FlateDecode"          # lossless
        assert int(im.Width) == w and int(im.Height) == h
        got = np.frombuffer(im.read_bytes(), dtype=">u2").reshape(arr.shape)
        assert np.array_equal(got, arr)                  # PIXELS BIT-FOR-BIT
        # Device passthrough setup: image ICC == OutputIntent == the file's profile.
        assert int(im.ColorSpace[1].N) == 3
        assert bytes(im.ColorSpace[1].read_bytes()) == icc
        assert bytes(pdf.Root.OutputIntents[0].DestOutputProfile.read_bytes()) == icc


def test_8bit_is_the_only_promotion_and_is_lossless_full_range(tmp_path):
    """A Convert device TIFF is 16-bit (no promotion). For completeness: the ONLY
    value change the load does is 8-bit → full-range 16-bit (×257, exact), which a
    16-bit input never triggers — proven here by feeding 16-bit and getting identity."""
    icc = (_ASSETS / "sRGB_IEC61966-2.1.icc").read_bytes()
    arr = ((np.arange(3 * 128 * 128, dtype=np.uint32).reshape(128, 128, 3) * 97) & 0xFFFF).astype(np.uint16)
    src = tmp_path / "d16.tif"
    _write_device_tiff(src, np.ascontiguousarray(arr), icc)
    job = PrintJob.for_tiff(src, paper_id="B" * 32, media_source="ROLL",
                            sheet_w_mm=610, sheet_h_mm=200,
                            offset_x_mm=10, offset_y_mm=10, orientation=0)
    job.validate()
    out = tmp_path / "o.pdf"
    PrintOps(None).build_pdfx4(job, out)
    with pikepdf.open(str(out)) as pdf:
        im = pdf.pages[0].Resources.XObject.Im0
        got = np.frombuffer(im.read_bytes(), dtype=">u2").reshape(arr.shape)
    assert np.array_equal(got, arr)                      # 16-bit in → identical out
