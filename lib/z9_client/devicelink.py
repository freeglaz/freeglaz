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

"""DeviceLink conversion — thin wrappers around Argyll ``collink`` + ``cctiff``.

JALON 1 (socle). Builds a DeviceLink between a SOURCE profile (the image's
embedded ICC) and a DEST profile (the loaded paper's resident characterization)
with ``collink -G`` (bypass the stored B2A, invert the A2B at link time), then
applies it to a 16-bit TIFF with ``cctiff`` (integer, "fully accurate").

Only the final Argyll conversion is exposed here — no custom OOG/shadow/paper-
black layers, no image-aware ``tiffgamut``, no linkstore (all out of scope).

Orchestration pattern mirrors ``refine.py``: ``resolve_argyll_binary`` (raises an
actionable ``ArgyllNotFound``) + ``subprocess.run``. collink/cctiff are OPTIONAL
binaries (``argyll.OPTIONAL_BINARIES``), resolved on demand.
"""

import subprocess
from pathlib import Path
from typing import Optional

from .argyll import resolve_argyll_binary

# collink -G : Gamut Mapping Mode using the inverse of the output profile A2B
# (fixed for this milestone). Intents are the collink ``-i`` *choices* — the
# argv token is ``-i`` + choice (e.g. ``-ir``), so the value here is the bare
# choice, NOT ``ir`` (``-iir`` is rejected by collink):
#   r  = White Point Matched Appearance [ICC relative colorimetric]
#   p  = Perceptual (collink default)
#   lp = Luminance Preserving Perceptual
ALLOWED_INTENTS = ("r", "p", "lp")
# collink -q<quality> : LUT resolution / effort.
ALLOWED_QUALITIES = ("l", "m", "h", "u")


def build_collink_argv(collink_bin: str, source_icc, dest_icc, out_icc, *,
                       intent: str = "r", quality: str = "h") -> list[str]:
    """Build the ``collink -G`` argv (source profile → dest profile → DeviceLink).

    :param collink_bin: resolved collink executable path.
    :raises ValueError: unknown intent / quality.
    """
    if intent not in ALLOWED_INTENTS:
        raise ValueError(f"unknown intent {intent!r}; expected {ALLOWED_INTENTS}")
    if quality not in ALLOWED_QUALITIES:
        raise ValueError(f"unknown quality {quality!r}; expected {ALLOWED_QUALITIES}")
    return [
        collink_bin, "-v",
        f"-q{quality}",
        "-G",                # bypass B2A, invert A2B
        f"-i{intent}",
        str(source_icc), str(dest_icc), str(out_icc),
    ]


def run_collink(source_icc, dest_icc, out_icc, *,
                intent: str = "r", quality: str = "h",
                timeout: int = 600) -> Path:
    """Generate a DeviceLink .icc from (source, dest) via ``collink -G``.

    :return: Path of the produced DeviceLink.
    :raises ArgyllNotFound: collink not installed.
    :raises RuntimeError: collink failed (stderr surfaced).
    """
    collink = resolve_argyll_binary("collink")
    argv = build_collink_argv(collink, source_icc, dest_icc, out_icc,
                              intent=intent, quality=quality)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"collink timed out after {timeout}s")
    if proc.returncode != 0:
        raise RuntimeError(
            f"collink failed (code {proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()[:500]}"
        )
    out = Path(out_icc)
    if not out.exists():
        raise RuntimeError("collink returned 0 but produced no DeviceLink file")
    return out


def apply_cctiff(link_icc, in_tiff, out_tiff, *, timeout: int = 600) -> Path:
    """Apply a DeviceLink to a TIFF via ``cctiff`` (16-bit integer path).

    :return: Path of the produced device TIFF.
    :raises ArgyllNotFound: cctiff not installed.
    :raises RuntimeError: cctiff failed.
    """
    cctiff = resolve_argyll_binary("cctiff")
    argv = [cctiff, str(link_icc), str(in_tiff), str(out_tiff)]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"cctiff timed out after {timeout}s")
    if proc.returncode != 0:
        raise RuntimeError(
            f"cctiff failed (code {proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()[:500]}"
        )
    out = Path(out_tiff)
    if not out.exists():
        raise RuntimeError("cctiff returned 0 but produced no output TIFF")
    return out


def extract_embedded_icc(tiff_path) -> Optional[bytes]:
    """Return the ICC profile embedded in a TIFF (tag 34675), or ``None``.

    The SOURCE profile of the DeviceLink is the image's own embedded profile —
    detection, not a frozen working space. ``None`` = the caller must refuse the
    conversion (no source profile → cannot convert), never assume a default.
    """
    from tifffile import TiffFile

    with TiffFile(str(tiff_path)) as tif:
        page = tif.pages[0]
        if 34675 in page.tags:
            return bytes(page.tags[34675].value)
    return None
