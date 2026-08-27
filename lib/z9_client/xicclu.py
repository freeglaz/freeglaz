# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""xicclu wrapper — look colours up through an ICC profile or a DeviceLink.

Diagnostic building block (measurement bench). Feeds a batch of points through
Argyll ``xicclu`` in a chosen direction / intent / PCS representation and returns
the mapped values. NOT part of the conversion pipeline — a side tool.

Orchestration mirrors ``devicelink.py``/``tiffgamut.py``: ``resolve_argyll_binary``
(raises an actionable ``ArgyllNotFound``) + ``subprocess.run``. ``xicclu`` is an
OPTIONAL binary (``argyll.OPTIONAL_BINARIES``), resolved on demand — never
REQUIRED.

Verified against the real binary (Argyll 3.5.0):
  -f f|b|if|ib   forward (device→PCS) / backward (PCS→device) / inverted variants
  -i a|r|p|s|…   intent
  -p l|L|j|J|x   PCS representation of the PCS side: l=Lab L=LCh j=Jab J=JCh x=XYZ
Output line format: ``<in…> [SPACE] -> <method> -> <out…> [<SPACE>]`` — we parse
the segment after the LAST ``->`` and strip the trailing ``[..]`` tag.
"""

import subprocess
from pathlib import Path
from typing import Optional

from .argyll import resolve_argyll_binary

# -p PCS representation: our name → xicclu letter.
_PCS_FLAG = {"lab": "l", "lch": "L", "jab": "j", "jch": "J", "xyz": "x"}
ALLOWED_DIRECTIONS = ("f", "b", "if", "ib")


def _parse_line(line: str) -> Optional[tuple]:
    """Parse one xicclu output line → tuple of output floats, or None.

    ``0.5 0.2 0.1 [RGB] -> Lut -> 35.6 23.8 24.9 [Lab]`` → (35.6, 23.8, 24.9).
    Lines without ``->`` (warnings, blanks) → None.
    """
    if "->" not in line:
        return None
    tail = line.rsplit("->", 1)[1].strip()
    tail = tail.split("[", 1)[0].strip()          # drop the trailing [Lab]/[RGB]/…
    try:
        return tuple(float(x) for x in tail.split())
    except ValueError:
        return None


def run_xicclu(profile, points, *, direction: str = "f", intent: Optional[str] = None,
               pcs: Optional[str] = None, scale: Optional[float] = None,
               timeout: int = 300) -> list[tuple]:
    """Feed ``points`` through ``xicclu`` and return the mapped output tuples.

    :param profile: ICC profile or DeviceLink path.
    :param points: iterable of numeric tuples (the input side of ``direction``).
    :param direction: f (forward, device→PCS) | b (backward, PCS→device) |
        if | ib (inverted variants). See ``ALLOWED_DIRECTIONS``.
    :param intent: xicclu ``-i`` letter (a|r|p|s|…) or None (profile default).
    :param pcs: PCS representation for the PCS side: 'lab'|'lch'|'jab'|'jch'|'xyz'.
    :param scale: device range 0..scale (``-s``) instead of 0..1.
    :return: list of output tuples, one per input point (input order preserved).
    :raises ArgyllNotFound: xicclu not installed.
    :raises ValueError: bad direction / pcs.
    :raises RuntimeError: xicclu failed.
    """
    if direction not in ALLOWED_DIRECTIONS:
        raise ValueError(f"direction {direction!r} not in {ALLOWED_DIRECTIONS}")
    if pcs is not None and pcs not in _PCS_FLAG:
        raise ValueError(f"pcs {pcs!r} not in {tuple(_PCS_FLAG)}")

    xicclu = resolve_argyll_binary("xicclu")
    argv = [xicclu, f"-f{direction}"]
    if intent:
        argv.append(f"-i{intent}")
    if pcs:
        argv.append(f"-p{_PCS_FLAG[pcs]}")
    if scale is not None:
        argv += ["-s", str(scale)]
    argv.append(str(profile))

    stdin = "".join(" ".join(f"{v:.6f}" for v in pt) + "\n" for pt in points)
    try:
        proc = subprocess.run(argv, input=stdin, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"xicclu timed out after {timeout}s")
    if proc.returncode != 0:
        raise RuntimeError(
            f"xicclu failed (code {proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()[:500]}")

    out = [t for t in (_parse_line(l) for l in proc.stdout.splitlines()) if t is not None]
    return out


def resolve_xicclu() -> str:
    """Return the resolved xicclu path (raises ArgyllNotFound if absent)."""
    return resolve_argyll_binary("xicclu")
