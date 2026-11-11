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

"""Single colorimetric reference for MEASUREMENT analysis (ΔE, QC).

Conventions (documented, not assumed):

* **XYZ scale.** freeglaz ti3 files carry XYZ on the 0-100 scale
  (``NORMALIZED_TO_Y_100 "YES"``): ``build_cti3`` normalizes Y to 100 and
  ``profiling.apply_spec2cie_xyz_correction`` multiplies the 0-1 spec2cie
  output by 100. A file whose largest Y is <= 2 can only be on the 0-1
  scale (paper white is Y ≈ 80-95 on the 0-100 scale): it is rescaled and
  the fact is reported (``xyz_scale = "0-1"``).
* **Lab white point.** Lab is recomputed from XYZ with the ICC / Argyll D50
  white (96.42, 100, 82.49): this is what ``colprof`` and ``profcheck`` use.
  Verified on a real freeglaz ti3: profcheck's "should be" Lab and this
  conversion agree to 0.0005, versus 0.022 with the CIE perfect-diffuser D50
  (96.422, 100, 82.521) that ``profiling._xyz_to_lab`` uses to rewrite the
  ``LAB_*`` columns. The FORMULA is the existing one (same constants as
  ``profiling._xyz_to_lab``), only vectorized — no new transformation.
* **Why recompute.** colprof reads the XYZ columns. Old ti3 files exist whose
  ``LAB_*`` columns are wrong while their XYZ are right (spec2cie Lab
  computed on 0-1 XYZ, fixed in ``profiling.py`` before the public release).
  The stored ``LAB_*`` columns are therefore only checked, never trusted when
  XYZ are present. Files without XYZ fall back to ``LAB_*`` (reported).

The original ti3 is never modified: everything here is read-only.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

ARGYLL_D50_WHITE = (96.42, 100.0, 82.49)
"""ICC / Argyll D50 white, XYZ 0-100 (icmD50)."""

LAB_COLUMN_TOLERANCE = 0.5
"""Max |ΔL|,|Δa|,|Δb| between stored LAB_* and Lab recomputed from XYZ before
the stored columns are reported as inconsistent. Legit files differ by
<= ~0.03 (white point convention + 4-decimal rounding)."""

_XYZ_COLS = ("XYZ_X", "XYZ_Y", "XYZ_Z")
_LAB_COLS = ("LAB_L", "LAB_A", "LAB_B")


def xyz_to_lab(xyz, white: Sequence[float] = ARGYLL_D50_WHITE) -> np.ndarray:
    """XYZ (0-100) → CIELAB. Same formula and constants as
    ``profiling._xyz_to_lab`` (threshold 0.008856, slope 7.787), vectorized.
    Accepts shape (3,) or (n, 3); returns the same shape."""
    a = np.asarray(xyz, dtype=float)
    t = a / np.asarray(white, dtype=float)
    f = np.where(t > 0.008856, np.cbrt(t), 7.787 * t + 16.0 / 116.0)
    L = 116.0 * f[..., 1] - 16.0
    A = 500.0 * (f[..., 0] - f[..., 1])
    B = 200.0 * (f[..., 1] - f[..., 2])
    return np.stack([L, A, B], axis=-1)


def ciede2000(lab1, lab2):
    """CIEDE2000 (Sharma, Wu, Dalal 2005), vectorized.

    Scalar inputs ``(L, a, b)`` return a float; arrays of shape (n, 3) return
    an array of n values. Validated against the Sharma test data (tests)."""
    x1 = np.asarray(lab1, dtype=float)
    x2 = np.asarray(lab2, dtype=float)
    scalar = x1.ndim == 1 and x2.ndim == 1
    x1 = np.atleast_2d(x1)
    x2 = np.atleast_2d(x2)
    L1, a1, b1 = x1[:, 0], x1[:, 1], x1[:, 2]
    L2, a2, b2 = x2[:, 0], x2[:, 1], x2[:, 2]
    C1 = np.hypot(a1, b1)
    C2 = np.hypot(a2, b2)
    Cm = (C1 + C2) / 2.0
    Cm7 = Cm ** 7
    G = 0.5 * (1.0 - np.sqrt(Cm7 / (Cm7 + 25.0 ** 7)))
    a1p = a1 * (1.0 + G)
    a2p = a2 * (1.0 + G)
    C1p = np.hypot(a1p, b1)
    C2p = np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360.0
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360.0
    dLp = L2 - L1
    dCp = C2p - C1p
    zero = (C1p * C2p) == 0
    dh = h2p - h1p
    dh = np.where(dh > 180.0, dh - 360.0, np.where(dh < -180.0, dh + 360.0, dh))
    dh = np.where(zero, 0.0, dh)
    dHp = 2.0 * np.sqrt(C1p * C2p) * np.sin(np.radians(dh) / 2.0)
    Lm = (L1 + L2) / 2.0
    Cpm = (C1p + C2p) / 2.0
    hs = h1p + h2p
    hm = np.where(np.abs(h1p - h2p) <= 180.0, hs / 2.0,
                  np.where(hs < 360.0, (hs + 360.0) / 2.0, (hs - 360.0) / 2.0))
    hm = np.where(zero, hs, hm)
    T = (1.0 - 0.17 * np.cos(np.radians(hm - 30.0)) + 0.24 * np.cos(np.radians(2.0 * hm))
         + 0.32 * np.cos(np.radians(3.0 * hm + 6.0)) - 0.20 * np.cos(np.radians(4.0 * hm - 63.0)))
    dro = 30.0 * np.exp(-(((hm - 275.0) / 25.0) ** 2))
    Cpm7 = Cpm ** 7
    Rc = 2.0 * np.sqrt(Cpm7 / (Cpm7 + 25.0 ** 7))
    Sl = 1.0 + 0.015 * (Lm - 50.0) ** 2 / np.sqrt(20.0 + (Lm - 50.0) ** 2)
    Sc = 1.0 + 0.045 * Cpm
    Sh = 1.0 + 0.015 * Cpm * T
    Rt = -np.sin(np.radians(2.0 * dro)) * Rc
    de = np.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2
                 + Rt * (dCp / Sc) * (dHp / Sh))
    return float(de[0]) if scalar else de


def _floats(rows, idx, path_hint):
    try:
        out = np.array([[float(r[i]) for i in idx] for r in rows], dtype=float)
    except (ValueError, IndexError) as e:
        raise ValueError(f"unreadable numeric value in {path_hint}: {e}") from None
    if not np.all(np.isfinite(out)):
        raise ValueError(f"non-finite value in {path_hint}")
    return out


def resolve_ti3_lab(header_lines, data_format, rows):
    """Lab D50 (Argyll white) of every row of a parsed ti3, plus an integrity report.

    Returns ``(lab, info)`` with ``lab`` of shape (n, 3) and ``info``::

        {"lab_source": "xyz" | "lab_columns", "xyz_scale": "0-100" | "0-1" | None,
         "lab_columns_present": bool,
         "lab_columns_max_diff": float | None,     # vs Lab recomputed from XYZ
         "lab_columns_n_mismatch": int | None,     # rows beyond LAB_COLUMN_TOLERANCE
         "lab_columns_consistent": bool | None}

    Raises ``ValueError`` when neither complete XYZ nor complete LAB columns are
    present, or when a value is missing / non-numeric / non-finite."""
    ix = {name: k for k, name in enumerate(data_format)}
    has_xyz = all(c in ix for c in _XYZ_COLS)
    has_lab = all(c in ix for c in _LAB_COLS)
    if not has_xyz and not has_lab:
        raise ValueError("ti3 has neither XYZ_X/Y/Z nor LAB_L/A/B columns")
    for r in rows:
        if len(r) < len(data_format):
            raise ValueError(f"ti3 row with {len(r)} fields, {len(data_format)} expected")
    lab_cols = _floats(rows, [ix[c] for c in _LAB_COLS], "LAB_* columns") if has_lab else None
    info = {"lab_source": None, "xyz_scale": None, "lab_columns_present": has_lab,
            "lab_columns_max_diff": None, "lab_columns_n_mismatch": None,
            "lab_columns_consistent": None}
    if not has_xyz:
        info["lab_source"] = "lab_columns"
        return lab_cols, info
    xyz = _floats(rows, [ix[c] for c in _XYZ_COLS], "XYZ columns")
    if float(np.max(xyz[:, 1])) <= 2.0:
        xyz = xyz * 100.0
        info["xyz_scale"] = "0-1"
    else:
        info["xyz_scale"] = "0-100"
    lab = xyz_to_lab(xyz)
    info["lab_source"] = "xyz"
    if lab_cols is not None:
        diff = np.abs(lab_cols - lab).max(axis=1)
        info["lab_columns_max_diff"] = round(float(diff.max()), 4)
        info["lab_columns_n_mismatch"] = int((diff > LAB_COLUMN_TOLERANCE).sum())
        info["lab_columns_consistent"] = bool(info["lab_columns_n_mismatch"] == 0)
    return lab, info
