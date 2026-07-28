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

"""
freeglaz — ICC profile curvature/smoothness analyzer (profile-compare).

⚠ EXPERIMENTAL — not yet validated against an independent spectrophotometer.

Detects "potential defect" zones of an ICC profile by analyzing the
CURVATURE (Laplacian) of its A2B0 LUT, WITHOUT an independent spectrophotometer.

Based on:
  - Morovič et al.: the smoothness of LUT transitions (especially in L*)
    correlates with the perceived visual quality of gradients.
  - Patent US8441691 (Optimal Node Placement): densify the zones of
    strong curvature that are under-sampled.

KEY DISTINCTION — NOISE vs STRUCTURE curvature (empirically validated):
  By comparing the curvature of a profile built with weak smoothing
  (-r0.5) vs strong smoothing (-r4.0), we separate:
    * zones SENSITIVE to smoothing (curvature collapses) = measurement NOISE
      -> fix by colprof -r smoothing, or re-measure/averaging.
    * zones RESISTANT to smoothing (curvature persists) = true device
      NON-LINEARITY -> densify with a targeted pass 2 (if useful + addressable zone).

LIMITS (never to forget):
  - Smoothness != accuracy. A smooth LUT can be smooth AND wrong.
  - We measure the curvature of the LUT, not the real error (no independent spectro).
  - The curvature metric is comparable ONLY AT EQUAL GRID, or after
    resampling onto a common grid (which introduces an interpolation bias).
  - The final judge of gradient quality remains the EYE on printed ramps.

Source: user-validated prototype (Docs/profile curvature.py,
integrated as-is — functions and behavior preserved).
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Optional

import numpy as np


__all__ = [
    "load_a2b_clut",
    "_xyz_to_lab",
    "laplacian_3d",
    "curvature_field",
    "stats_core",
    "resample_clut",
    "analyze_single",
    "smoothing_pair",
    "rgb_of_node",
    "order_label",
]


# ── Reading the A2B0 CLUT (lut16Type 'mft2') ──────────────────────


_D50_XYZ = (0.9642, 1.0, 0.8249)


def _xyz_to_lab(X, Y, Z):
    """Convert XYZ arrays (D50, scale 0..1, white≈0.9642/1/0.8249)
    into CIE Lab D50. Same conversion as the one served by lcms2 at the 2D
    slice, in array version (float precision, not the Pillow 8-bit Lab)."""
    xn, yn, zn = _D50_XYZ
    d = 6.0 / 29.0

    def f(t):
        return np.where(t > d ** 3, np.cbrt(np.clip(t, 0.0, None)),
                        t / (3 * d * d) + 4.0 / 29.0)

    fx, fy, fz = f(X / xn), f(Y / yn), f(Z / zn)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _apply_curve_u16(values, curve):
    """Pass u16 values (0..65535) through an mft2 output curve
    (table of n points) by linear interpolation."""
    n = len(curve)
    x = values / 65535.0 * (n - 1)
    i = np.clip(x.astype(np.int64), 0, n - 2)
    fr = x - i
    return curve[i] * (1 - fr) + curve[i + 1] * fr


def load_a2b_clut(path):
    """Return (g, L, a, b): grid size + 3 Lab fields (g×g×g).

    Assumes an A2B0 tag of type 'mft2' (lut16Type), with trivial
    input curves (the case of Argyll and firmware
    Z9 print RGB->PCS profiles). The returned values are ALWAYS Lab,
    whatever the connection space (PCS) of the profile:

      - PCS Lab: standard ICC Lab decoding of the raw nodes ::

            L = v / 65535 × 100
            a = v / 65535 × 255 - 128
            b = v / 65535 × 255 - 128

      - PCS XYZ: the raw nodes encode XYZ (u1Fixed15, 0x8000 = 1.0)
        AFTER passing through the output curves. So we apply the
        output curves, decode XYZ = v / 32768, then convert
        into Lab D50. Without this, XYZ nodes read with the Lab
        formula give aberrant ranges (and a distorted curvature).
    """
    path_str = str(path)
    d = Path(path_str).read_bytes()
    pcs = d[20:24].decode('latin1').strip()
    n = struct.unpack(">I", d[128:132])[0]
    off = 132
    tags = {}
    for _ in range(n):
        sig = d[off:off + 4].decode('latin1')
        o = struct.unpack(">I", d[off + 4:off + 8])[0]
        s = struct.unpack(">I", d[off + 8:off + 12])[0]
        tags[sig] = (o, s)
        off += 12
    if 'A2B0' not in tags:
        raise ValueError(f"{path_str}: no A2B0 tag")
    o, s = tags['A2B0']
    typ = d[o:o + 4].decode('latin1')
    if typ != 'mft2':
        raise ValueError(
            f"{path_str}: A2B0 type {typ!r} unsupported (expected mft2)"
        )
    ic = d[o + 8]
    oc = d[o + 9]
    g = d[o + 10]
    n_in = struct.unpack(">H", d[o + 48:o + 50])[0]
    n_out = struct.unpack(">H", d[o + 50:o + 52])[0]
    clut_off = o + 52 + ic * n_in * 2
    total = g ** ic * oc
    vals = struct.unpack(">" + "H" * total, d[clut_off:clut_off + total * 2])
    arr = np.array(vals, dtype=np.float64).reshape(g, g, g, oc)

    if pcs == 'XYZ':
        out_off = clut_off + total * 2
        outp = np.array(
            struct.unpack(">" + "H" * (oc * n_out),
                          d[out_off:out_off + oc * n_out * 2]),
            dtype=np.float64,
        ).reshape(oc, n_out)
        X = _apply_curve_u16(arr[..., 0], outp[0]) / 32768.0
        Y = _apply_curve_u16(arr[..., 1], outp[1]) / 32768.0
        Z = _apply_curve_u16(arr[..., 2], outp[2]) / 32768.0
        return g, *_xyz_to_lab(X, Y, Z)

    L = arr[..., 0] / 65535.0 * 100.0
    a = arr[..., 1] / 65535.0 * 255.0 - 128.0
    b = arr[..., 2] / 65535.0 * 255.0 - 128.0
    return g, L, a, b


# ── Curvature (3D 6-neighbor Laplacian) ─────────────────────────────────


def laplacian_3d(f):
    lap = np.zeros_like(f)
    lap[1:-1, :, :] += f[2:, :, :] + f[:-2, :, :] - 2 * f[1:-1, :, :]
    lap[:, 1:-1, :] += f[:, 2:, :] + f[:, :-2, :] - 2 * f[:, 1:-1, :]
    lap[:, :, 1:-1] += f[:, :, 2:] + f[:, :, :-2] - 2 * f[:, :, 1:-1]
    return lap


def curvature_field(L, a, b, weight_L=2.0):
    """L*-weighted curvature magnitude (Morovič).
    Returns a g×g×g field."""
    cL = laplacian_3d(L)
    ca = laplacian_3d(a)
    cb = laplacian_3d(b)
    return np.sqrt((weight_L * cL) ** 2 + ca ** 2 + cb ** 2)


def stats_core(field):
    """Stats ignoring the edges (Laplacian undefined at the edge)."""
    core = field[1:-1, 1:-1, 1:-1]
    return dict(
        moy=float(core.mean()),
        med=float(np.median(core)),
        p95=float(np.percentile(core, 95)),
        mx=float(core.max()),
    )


# ── Resampling onto a common grid (inter-grid comparison) ──


def resample_clut(L, a, b, g_src, g_dst):
    """Interpolate a g_src³ CLUT to g_dst³ (trilinear).

    WARNING: introduces artificial smoothing; the most
    reliable comparison remains at equal native grid, without resampling.
    """
    from scipy.interpolate import RegularGridInterpolator
    clut = np.stack([L, a, b], axis=-1)
    axes = [np.linspace(0, 1, g_src)] * 3
    interp = RegularGridInterpolator(axes, clut, method='linear')
    lin = np.linspace(0, 1, g_dst)
    R, G, B = np.meshgrid(lin, lin, lin, indexing='ij')
    pts = np.stack([R.ravel(), G.ravel(), B.ravel()], axis=-1)
    out = interp(pts).reshape(g_dst, g_dst, g_dst, 3)
    return out[..., 0], out[..., 1], out[..., 2]


# ── Interpretation helpers ────────────────────────────────────────────


def rgb_of_node(idx, g):
    """RGB 0-255 coord of a node (idx in the core, +1 for the edge offset)."""
    return tuple((i + 1) / (g - 1) * 255 for i in idx)


def order_label(R, G, B):
    if R >= G >= B:
        return "R≥G≥B"
    if B >= G >= R:
        return "B≥G≥R"
    if abs(R - G) < 12 and abs(G - B) < 12:
        return "R=G=B"
    return "mixte"


# ── High-level analyses ───────────────────────────────────────────


def analyze_single(path, g_common: Optional[int] = None):
    """Curvature analysis of a profile. If g_common, resample first.

    :return: tuple ``(g_native, g_effective, field, stats)`` where ``stats``
        is a dict {moy, med, p95, mx}.
    """
    g, L, a, b = load_a2b_clut(path)
    if g_common and g != g_common:
        L, a, b = resample_clut(L, a, b, g, g_common)
        g_eff = g_common
    else:
        g_eff = g
    field = curvature_field(L, a, b)
    st = stats_core(field)
    return g, g_eff, field, st


def smoothing_pair(path_low, path_high, top=8):
    """Compare a lightly-smoothed profile (low r) to a heavily-smoothed one (high r)
    to separate NOISE (sensitive to smoothing) from STRUCTURE (resistant).

    The two profiles must have the SAME native grid size.

    :return: dict with ``noise``, ``structure`` (lists of tuples
        (R, G, B, magnitude, order_label)), ``drop_mean``, ``drop_max``.
    """
    g1, L1, a1, b1 = load_a2b_clut(path_low)
    g2, L2, a2, b2 = load_a2b_clut(path_high)
    if g1 != g2:
        raise ValueError(
            f"different grids ({g1} vs {g2}): not directly comparable"
        )
    g = g1
    c_low = curvature_field(L1, a1, b1)
    c_high = curvature_field(L2, a2, b2)
    drop = (c_low - c_high)[1:-1, 1:-1, 1:-1]
    resid = c_high[1:-1, 1:-1, 1:-1]

    # NOISE zones: strong drop under smoothing
    idx = np.argsort(drop.ravel())[::-1][:top]
    co = np.unravel_index(idx, drop.shape)
    noise = []
    for k in range(top):
        R, G, B = rgb_of_node((co[0][k], co[1][k], co[2][k]), g)
        noise.append(
            (R, G, B, float(drop[co[0][k], co[1][k], co[2][k]]),
             order_label(R, G, B))
        )

    # STRUCTURE zones: high residual curvature after strong smoothing
    idx2 = np.argsort(resid.ravel())[::-1][:top]
    co2 = np.unravel_index(idx2, resid.shape)
    structure = []
    for k in range(top):
        R, G, B = rgb_of_node((co2[0][k], co2[1][k], co2[2][k]), g)
        structure.append(
            (R, G, B, float(resid[co2[0][k], co2[1][k], co2[2][k]]),
             order_label(R, G, B))
        )

    return dict(
        noise=noise,
        structure=structure,
        drop_mean=float(drop.mean()),
        drop_max=float(drop.max()),
    )
