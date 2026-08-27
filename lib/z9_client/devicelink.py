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
# collink -d <viewcond> : DESTINATION CIECAM02 viewing conditions. Print presets
# only (the reasonable print-bench selection); monitor/projector presets and the
# fine per-parameter overrides are out of scope. None/"default" → no -d (collink
# generic defaults). Verified: collink accepts all four with -G, image-aware
# on or off.
ALLOWED_DEST_VIEWCONDS = ("pp", "pc", "pe", "pm")
_VIEWCOND_DEFAULT = ("default", "", None)


def build_collink_argv(collink_bin: str, source_icc, dest_icc, out_icc, *,
                       intent: str = "r", quality: str = "h",
                       image_gam=None, dest_viewcond=None) -> list[str]:
    """Build the ``collink -G`` argv (source profile → dest profile → DeviceLink).

    :param collink_bin: resolved collink executable path.
    :param image_gam: optional image gamut surface (``.gam``) — the IMAGE-AWARE
        axis. When given, ``-G`` maps from the gamut actually occupied by the
        image (``collink -G <image.gam>``) instead of the full source-profile
        gamut. The ``.gam`` MUST be in CIECAM02 Jab (``tiffgamut -pj``): a
        default Lab gamut makes collink abort ("Failed to make gamut map
        transform"). When None → full source gamut (unchanged JALON 1 behaviour).
    :param dest_viewcond: optional DESTINATION viewing-conditions preset
        (``-d <preset>``), one of ``ALLOWED_DEST_VIEWCONDS``. None/"default" →
        no ``-d`` (collink generic defaults, unchanged). Orthogonal to intent and
        image-aware.
    :raises ValueError: unknown intent / quality / dest_viewcond.
    """
    if intent not in ALLOWED_INTENTS:
        raise ValueError(f"unknown intent {intent!r}; expected {ALLOWED_INTENTS}")
    if quality not in ALLOWED_QUALITIES:
        raise ValueError(f"unknown quality {quality!r}; expected {ALLOWED_QUALITIES}")
    # ``-G`` alone → source gamut = full source profile. ``-G <image.gam>`` →
    # source gamut = the image's occupied gamut (image-aware). Orthogonal to
    # intent (works for r/p/lp).
    gmap = ["-G"] if image_gam is None else ["-G", str(image_gam)]
    # ``-d <preset>`` → destination CIECAM02 viewing conditions. Absent → collink
    # generic defaults (unchanged). Strict allow-list.
    if dest_viewcond in _VIEWCOND_DEFAULT:
        vc = []
    elif dest_viewcond in ALLOWED_DEST_VIEWCONDS:
        vc = ["-d", dest_viewcond]
    else:
        raise ValueError(
            f"unknown dest_viewcond {dest_viewcond!r}; "
            f"expected {ALLOWED_DEST_VIEWCONDS} or default")
    return [
        collink_bin, "-v",
        f"-q{quality}",
        *gmap,
        f"-i{intent}",
        *vc,
        str(source_icc), str(dest_icc), str(out_icc),
    ]


def run_collink(source_icc, dest_icc, out_icc, *,
                intent: str = "r", quality: str = "h", image_gam=None,
                dest_viewcond=None, timeout: int = 600) -> Path:
    """Generate a DeviceLink .icc from (source, dest) via ``collink -G``.

    :param image_gam: optional image gamut ``.gam`` (image-aware axis, cf.
        ``build_collink_argv``).
    :param dest_viewcond: optional destination viewing-conditions preset (cf.
        ``build_collink_argv``).
    :return: Path of the produced DeviceLink.
    :raises ArgyllNotFound: collink not installed.
    :raises RuntimeError: collink failed (stderr surfaced).
    """
    collink = resolve_argyll_binary("collink")
    argv = build_collink_argv(collink, source_icc, dest_icc, out_icc,
                              intent=intent, quality=quality, image_gam=image_gam,
                              dest_viewcond=dest_viewcond)
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


def apply_cctiff(link_icc, in_tiff, out_tiff, *, embed_icc=None,
                 timeout: int = 600) -> Path:
    """Apply a DeviceLink to a TIFF via ``cctiff`` (16-bit integer path).

    :param embed_icc: optional profile to EMBED in the output (``cctiff -e``).
        This is a pure ASSIGNMENT — the device pixels are the DeviceLink output,
        unchanged; ``-e`` only writes the ICC tag (proven pixel-identical). Used
        to tag the device TIFF with the loaded paper's profile so it opens
        colour-managed in an editor.
    :return: Path of the produced device TIFF.
    :raises ArgyllNotFound: cctiff not installed.
    :raises RuntimeError: cctiff failed.
    """
    cctiff = resolve_argyll_binary("cctiff")
    argv = [cctiff]
    if embed_icc is not None:
        argv += ["-e", str(embed_icc)]
    argv += [str(link_icc), str(in_tiff), str(out_tiff)]
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


def normalize_icc_for_argyll(icc: bytes) -> bytes:
    """Return a copy of ``icc`` that Argyll (v2-only icclib) can read.

    Argyll's icclib does not support ICC v4: it aborts when copying a v4
    multiLocalizedUnicode (``mluc``) text tag ("icmTextDescription_cpy:
    unimplemented tagtype"). Modern images (Adobe, camera exports) routinely
    embed a v4 working space as their SOURCE profile — so this must be handled.

    Fix: drop the ``mluc`` text tags (desc/cprt/dm?d — pure metadata, no colour)
    and stamp the header as ICC v2.4. Every colorimetric tag (XYZ primaries,
    white point, TRC curves, chromatic adaptation, any A2B) is copied
    byte-for-byte, so the colour definition is untouched. collink writes its own
    description (``-D``), so the removed text is not needed.

    A profile already v2 and free of ``mluc`` tags is returned unchanged. On any
    parse problem the input is returned as-is (let collink surface the real
    error). Covers matrix profiles (≈ all image working spaces); an exotic v4
    cLUT *input* profile (mAB/mBA) may still be unreadable by Argyll — out of
    scope for this milestone.
    """
    try:
        if len(icc) < 132:
            return icc
        major = icc[8]
        n = int.from_bytes(icc[128:132], "big")
        table = []
        for i in range(n):
            o = 132 + i * 12
            sig = icc[o:o + 4]
            off = int.from_bytes(icc[o + 4:o + 8], "big")
            sz = int.from_bytes(icc[o + 8:o + 12], "big")
            table.append((sig, off, sz, icc[off:off + 4]))
        if major < 4 and not any(t[3] == b"mluc" for t in table):
            return icc  # already Argyll-friendly

        header = bytearray(icc[:128])
        header[8:12] = b"\x02\x40\x00\x00"           # ICC v2.4
        kept = [(sig, icc[off:off + sz])
                for sig, off, sz, ttype in table if ttype != b"mluc"]
        tbl = bytearray(len(kept).to_bytes(4, "big"))
        data = bytearray()
        data_base = 128 + 4 + len(kept) * 12
        for sig, payload in kept:
            off = data_base + len(data)
            tbl += sig + off.to_bytes(4, "big") + len(payload).to_bytes(4, "big")
            data += payload
            data += b"\x00" * ((4 - len(payload) % 4) % 4)   # 4-byte align
        out = bytearray(header) + tbl + data
        out[0:4] = len(out).to_bytes(4, "big")               # header profile size
        return bytes(out)
    except Exception:
        return icc


# ── ICC transform LUT tag TYPES — the REAL discriminant for Argyll support ────
# Established (v4 confrontation) against the INSTALLED binaries, NOT the ICC
# version byte: Argyll (xicclu/collink) reads mft1/mft2 even inside a v4 header
# (Z9-native profiles), but CANNOT read mAB/mBA (true v4 lutAToBType, what
# i1Profiler emits) → "Unable to locate usable conversion". NEVER gate on the ICC
# version: `if ICC_major >= 4: reject` is WRONG (Z9 profiles are v4/mft2).
_ARGYLL_READABLE_LUT = (b"mft1", b"mft2")
_ARGYLL_UNREADABLE_LUT = (b"mAB ", b"mBA ")


def lut_tag_types(icc: bytes) -> dict:
    """Return ``{tag_sig: tag_type}`` (bytes) for the A2B*/B2A* transform tags."""
    out = {}
    try:
        if len(icc) < 132:
            return out
        n = int.from_bytes(icc[128:132], "big")
        for i in range(n):
            o = 132 + i * 12
            sig = icc[o:o + 4]
            if sig[:3] in (b"A2B", b"B2A"):
                off = int.from_bytes(icc[o + 4:o + 8], "big")
                out[sig] = icc[off:off + 4]
    except Exception:
        pass
    return out


def convert_lut_support(icc: bytes) -> str:
    """Classify a profile for the Convert (Argyll) engine, on the REAL LUT tag
    type — NEVER on the ICC version.

    - ``SUPPORTED_MFT``       : has an mft1/mft2 A2B/B2A LUT (Argyll reads it,
      including inside a v4 header — Z9-native and freeglaz-built profiles).
    - ``SUPPORTED_MATRIX``    : no A2B/B2A LUT but a matrix profile
      (rXYZ/gXYZ/bXYZ) — Argyll uses the matrix (source working spaces).
    - ``UNSUPPORTED_MAB_MBA`` : A2B/B2A are mAB/mBA (true v4) with no mft — Argyll
      cannot build a lookup (i1Profiler output).
    - ``NO_USABLE_TRANSFORM`` : neither a usable LUT nor a matrix.
    """
    types = set(lut_tag_types(icc).values())
    if types & set(_ARGYLL_READABLE_LUT):
        return "SUPPORTED_MFT"
    if types & set(_ARGYLL_UNREADABLE_LUT):
        return "UNSUPPORTED_MAB_MBA"
    try:
        n = int.from_bytes(icc[128:132], "big")
        tags = {icc[132 + i * 12:132 + i * 12 + 4] for i in range(n)}
    except Exception:
        tags = set()
    if {b"rXYZ", b"gXYZ", b"bXYZ"} <= tags:
        return "SUPPORTED_MATRIX"
    return "NO_USABLE_TRANSFORM"


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
