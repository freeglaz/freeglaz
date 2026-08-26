# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Image gamut extraction — thin wrapper around Argyll ``tiffgamut``.

JALON 3 (image-aware axis). Computes the gamut surface ACTUALLY occupied by an
image, so ``collink -G <image.gam>`` maps only what the image really contains
instead of the full source-profile gamut.

Orchestration mirrors ``devicelink.py``: ``resolve_argyll_binary`` (raises an
actionable ``ArgyllNotFound``) + ``subprocess.run``. ``tiffgamut`` is an OPTIONAL
binary (``argyll.OPTIONAL_BINARIES``), resolved on demand — never REQUIRED.
"""

import subprocess
from pathlib import Path

from .argyll import resolve_argyll_binary


def run_tiffgamut(source_icc, tiff_path, out_gam, *, timeout: int = 600) -> Path:
    """Extract the image's occupied gamut surface (``.gam``) via ``tiffgamut``.

    :param source_icc: profile used to interpret the image RGB into PCS.
        REQUIRED — ``tiffgamut`` refuses a bare RGB TIFF ("No profile provided
        and TIFF photometric 'RGB' isn't Lab"). Pass the SAME (Argyll-friendly,
        v2-normalized) source profile ``collink`` uses, so the image gamut lives
        in the same space as the mapping.
    :param tiff_path: the DROPPED image (the state presented to the mapper).
    :param out_gam: output ``.gam`` path (``-O`` override; the default naming is
        derived from the infile and lands next to it — we control it instead so
        the gamut stays ephemeral/job-specific in a temp dir).
    :return: Path of the produced ``.gam``.
    :raises ArgyllNotFound: tiffgamut not installed.
    :raises RuntimeError: tiffgamut failed (stderr surfaced).

    ``-pj`` = CIECAM02 Jab appearance space. REQUIRED: ``collink -G <image.gam>``
    aborts ("Failed to make gamut map transform") on a default Lab gamut, but
    accepts a Jab one (verified empirically, all intents r/p/lp).
    """
    tiffgamut = resolve_argyll_binary("tiffgamut")
    argv = [tiffgamut, "-pj", "-O", str(out_gam),
            str(source_icc), str(tiff_path)]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"tiffgamut timed out after {timeout}s")
    if proc.returncode != 0:
        raise RuntimeError(
            f"tiffgamut failed (code {proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()[:500]}"
        )
    out = Path(out_gam)
    if not out.exists():
        raise RuntimeError("tiffgamut returned 0 but produced no .gam file")
    return out
