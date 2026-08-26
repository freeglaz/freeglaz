# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Strict input gate for the webapp file upload — single-page RGB TIFF only.

The webapp GUI accepts ONLY what the print pipeline has been characterised and
validated for, end to end: a single-page, RGB, 8- or 16-bit TIFF. Everything
else — PDF, CMYK/grayscale/other colour model, alpha/extra channels, multi-page,
or float/32-bit — is rejected cleanly and early, with a message that tells the
user what to export instead.

A MISSING embedded ICC is NOT rejected here (JALON 2): the print pipeline tags
the loaded paper's resident + OutputIntent resident = identity, so the input
profile has no incidence — a chart (raw device values) must be printable without
one. The absence is surfaced downstream as a non-blocking warning
(``file_inspector`` "No embedded ICC profile" + the ICC 'none' badge), not a
gate refusal. The Convert tab keeps its OWN source-profile requirement (it reads
the source space) — that guard lives in the convert route, not here.

No fallback conversion: freeglaz never silently reinterprets colour (CMYK->RGB,
alpha flatten, default ICC…). We reject, we do not transform.

Scope: this gate is a WEBAPP-layer decision only. The CLI ``freeglaz print``
path and the internal TIFF->PDF/X-4 generation (``_load_tiff_for_pdf``) are
untouched — in particular the 8-bit -> 16-bit promotion still happens downstream
there. Type detection is by CONTENT (magic bytes + TIFF tags), never by the
filename extension (which is spoofable).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from tifffile import TiffFile

# TIFF magic bytes: "II*\0" (little-endian) / "MM\0*" (big-endian). imghdr is
# deprecated in 3.13, so we sniff the header ourselves.
_TIFF_MAGIC = (b"II\x2a\x00", b"MM\x00\x2a")
# BigTIFF (version 43 / 0x2B): a genuine TIFF, but a >4 GB variant we do not
# support — named explicitly so we never mislabel it as "not a TIFF".
_BIGTIFF_MAGIC = (b"II\x2b\x00", b"MM\x00\x2b")

_PHOTOMETRIC_RGB = 2  # TIFF PhotometricInterpretation value for RGB


class WebappInputRejected(Exception):
    """A user-uploaded file failed the strict webapp TIFF-RGB contract.

    ``code`` is a stable machine token the frontend maps to a localized
    message; ``message`` is a self-contained English fallback.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_tiff_upload(path: Path) -> None:
    """Return None if ``path`` satisfies the webapp contract, else raise.

    Contract (all required): TIFF magic bytes, exactly one page (IFD),
    PhotometricInterpretation == RGB, SamplesPerPixel == 3 with no extra
    channel, dtype uint8 or uint16. An embedded ICC is NOT required (a missing
    one is a downstream warning, cf. module docstring). The checks run in this
    order so the message points at the most relevant single reason.
    """
    with open(path, "rb") as fh:
        head = fh.read(4)
    if head in _BIGTIFF_MAGIC:
        raise WebappInputRejected(
            "bigtiff",
            "BigTIFF (over 4 GB) is not supported. Export a standard TIFF.",
        )
    if head not in _TIFF_MAGIC:
        raise WebappInputRejected(
            "not_tiff",
            "Only TIFF files are accepted. Export your image as TIFF from your "
            "editor.",
        )

    try:
        with TiffFile(path) as tif:
            n_pages = len(tif.pages)
            page = tif.pages[0]
            photometric = int(page.photometric)
            samples = int(page.samplesperpixel)
            extrasamples = tuple(page.extrasamples or ())
            dtype = page.dtype
    except WebappInputRejected:
        raise
    except Exception as e:  # noqa: BLE001 — any parse failure = not a usable TIFF
        raise WebappInputRejected(
            "not_tiff",
            "This file could not be read as a TIFF. Export your image as TIFF "
            "from your editor.",
        ) from e

    if n_pages != 1:
        raise WebappInputRejected(
            "multipage",
            "Multi-page TIFF is not supported. Export a single image.",
        )
    if photometric != _PHOTOMETRIC_RGB:
        raise WebappInputRejected(
            "not_rgb",
            "Non-RGB TIFF is not supported. Export as RGB, converted into the "
            "paper's ICC profile.",
        )
    # A 4th channel is tolerated only as a single alpha channel (RGB + alpha).
    # freeglaz does not handle transparency: the alpha is dropped downstream
    # (_load_tiff_for_pdf keeps arr[..., :3]), which is why an opaque alpha — as
    # Affinity exports, tagged associated — prints correctly. No opacity/pixel
    # test (tag-only, cheap); any other channel layout (>4 channels, or a 4th
    # channel not declared as an extra/alpha) is rejected.
    is_rgb = samples == 3 and not extrasamples
    is_rgba = samples == 4 and len(extrasamples) == 1
    if not (is_rgb or is_rgba):
        raise WebappInputRejected(
            "channels",
            "Unsupported channel layout: provide an RGB TIFF (a single alpha "
            "channel is accepted and ignored).",
        )
    if dtype not in (np.uint8, np.uint16):
        raise WebappInputRejected(
            "bad_depth",
            "Unsupported bit depth. Export at 8 or 16 bits per channel "
            "(16-bit recommended).",
        )
    # NOTE: a missing embedded ICC is deliberately NOT rejected (JALON 2) — it is
    # a non-blocking downstream warning. See the module docstring.
