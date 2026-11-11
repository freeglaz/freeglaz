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

"""Difference between the device→PCS (A2B) transforms of two ICC profiles, computed offline.

A change of measurements can move a profile a lot in a small region while leaving its gamut
volume almost unchanged, so this compares the transforms point by point: ΔE00 between the Lab
that profile A and profile B give for the SAME device RGB, on

* the device values actually measured (read from the measurement data embedded by colprof,
  the ``targ`` tag of both profiles), and
* a reproducible control grid of the RGB cube,

for the relative colorimetric intent and, when both profiles have one, the perceptual intent.
It reports the distribution (mean, 95th percentile, max), the most displaced colours, the
neighbourhood of chosen focus points (e.g. a reading excluded in one build only) against the
rest, and the white and black points. Lookups go through Argyll ``xicclu`` (float precision;
Pillow's 8-bit Lab would quantise away the differences that matter here).

This is a difference between two transforms, not a measurement: it does not say which profile
prints better.
"""
from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np

from .colorimetry import ciede2000, xyz_to_lab
from .multipass import parse_ti3

FOCUS_RADIUS = 0.15            # device units (0-1): neighbourhood of a focus point
LIMITS = ("transform_difference_not_measurement", "embedded_measurements_only",
          "control_grid_reproducible", "printer_not_involved")


def _tags(data: bytes) -> dict[str, bytes]:
    n = struct.unpack(">I", data[128:132])[0]
    out = {}
    for i in range(n):
        sig, off, size = struct.unpack(">4sII", data[132 + 12 * i:144 + 12 * i])
        out[sig.decode("latin-1")] = data[off:off + size]
    return out


def read_tags(path: Path) -> dict[str, bytes]:
    return _tags(Path(path).read_bytes())


def control_grid(n: int = 9) -> list[tuple[float, float, float]]:
    v = [i / (n - 1) for i in range(n)]
    return [(r, g, b) for r in v for g in v for b in v]


def embedded_device_points(tags: dict[str, bytes]) -> list[tuple[float, float, float]]:
    """Distinct device RGB (0-1) of the measurement set colprof embedded (targ tag)."""
    t = tags.get("targ")
    if not t or t[:4] != b"text":
        return []
    try:
        _, fmt, rows = parse_ti3(t[8:].rstrip(b"\0").decode("ascii", "replace"))
        ir, ig, ib = (fmt.index(c) for c in ("RGB_R", "RGB_G", "RGB_B"))
    except (ValueError, IndexError):
        return []
    return sorted({(round(float(r[ir]) / 100, 6), round(float(r[ig]) / 100, 6), round(float(r[ib]) / 100, 6)) for r in rows})


def _xyz_tag(tags: dict[str, bytes], sig: str) -> Optional[tuple[float, float, float]]:
    d = tags.get(sig)
    if not d or len(d) < 20:
        return None
    return tuple(x / 65536.0 for x in struct.unpack(">3i", d[8:20]))


def _stats(de: np.ndarray, pts, lab_a, lab_b, top_n: int) -> dict:
    order = np.argsort(-de)[:top_n]
    return {"n": int(len(de)), "mean": round(float(de.mean()), 4), "p95": round(float(np.percentile(de, 95)), 4),
            "max": round(float(de.max()), 4),
            "top": [{"rgb255": [int(round(x * 255)) for x in pts[i]], "de00": round(float(de[i]), 4),
                     "lab_a": [round(float(x), 2) for x in lab_a[i]], "lab_b": [round(float(x), 2) for x in lab_b[i]]}
                    for i in order]}


def _default_lookup(profile, points, intent):
    from .xicclu import run_xicclu
    return run_xicclu(str(profile), points, direction="f", intent=intent, pcs="lab")


def compare_profiles(icc_a: Path, icc_b: Path, *, focus: Sequence[dict] = (), grid_n: int = 9, top_n: int = 10,
                     lookup: Callable = _default_lookup, tag_reader: Callable = read_tags) -> dict:
    """See the module docstring. ``focus`` = [{"rgb": [r, g, b] (0-100), "label": str}]."""
    ta, tb = tag_reader(icc_a), tag_reader(icc_b)
    measured = sorted(set(embedded_device_points(ta)) | set(embedded_device_points(tb)))
    grid = control_grid(grid_n)
    intents = [("relative", "r")]
    if "A2B0" in ta and "A2B0" in tb:
        intents.append(("perceptual", "p"))
    out = {"intents": {}, "focus": [], "white_black": {}, "limits": list(LIMITS),
           "method": {"tool": "xicclu", "grid_n": grid_n, "n_measured": len(measured), "focus_radius": FOCUS_RADIUS,
                      "perceptual_compared": len(intents) == 2}}
    rel_cache = {}
    for name, letter in intents:
        res = {}
        for set_name, pts in (("measured", measured), ("grid", grid)):
            if not pts:
                res[set_name] = None
                continue
            la = np.asarray(lookup(icc_a, pts, letter), dtype=float)
            lb = np.asarray(lookup(icc_b, pts, letter), dtype=float)
            de = np.asarray(ciede2000(la, lb), dtype=float)
            res[set_name] = _stats(de, pts, la, lb, top_n)
            if name == "relative":
                rel_cache[set_name] = (pts, de)
        out["intents"][name] = res
    # neighbourhood of each focus point (relative intent, measured + grid points), against the rest
    pool_pts = [p for s in ("measured", "grid") if s in rel_cache for p in rel_cache[s][0]]
    pool_de = np.concatenate([rel_cache[s][1] for s in ("measured", "grid") if s in rel_cache]) if rel_cache else np.array([])
    P = np.asarray(pool_pts, dtype=float) if pool_pts else np.zeros((0, 3))
    for f in focus:
        c = np.asarray(f["rgb"], dtype=float) / 100.0
        la, lb = (np.asarray(lookup(x, [tuple(c)], "r"), dtype=float) for x in (icc_a, icc_b))
        at = float(np.asarray(ciede2000(la, lb)).ravel()[0])
        if len(P):
            dist = np.sqrt(((P - c) ** 2).sum(1))
            near, far = pool_de[dist <= FOCUS_RADIUS], pool_de[dist > FOCUS_RADIUS]
        else:
            near = far = np.array([])
        out["focus"].append({
            "label": f.get("label"), "rgb": list(f["rgb"]), "de00_at_point": round(at, 4),
            "near": {"n": int(len(near)), "mean": round(float(near.mean()), 4) if len(near) else None,
                     "max": round(float(near.max()), 4) if len(near) else None},
            "elsewhere": {"n": int(len(far)), "mean": round(float(far.mean()), 4) if len(far) else None,
                          "p95": round(float(np.percentile(far, 95)), 4) if len(far) else None,
                          "max": round(float(far.max()), 4) if len(far) else None}})
    # white and black points: the tags, and the device white/black through the transforms
    for sig, name in (("wtpt", "white_point_tag"), ("bkpt", "black_point_tag")):
        xa, xb = _xyz_tag(ta, sig), _xyz_tag(tb, sig)
        if xa and xb:
            la, lb = xyz_to_lab(np.asarray([xa]) * 100), xyz_to_lab(np.asarray([xb]) * 100)
            out["white_black"][name] = {"lab_a": [round(float(x), 2) for x in la[0]], "lab_b": [round(float(x), 2) for x in lb[0]],
                                        "de00": round(float(np.asarray(ciede2000(la, lb)).ravel()[0]), 4)}
    dev = [(1.0, 1.0, 1.0), (0.0, 0.0, 0.0)]
    la, lb = np.asarray(lookup(icc_a, dev, "r"), dtype=float), np.asarray(lookup(icc_b, dev, "r"), dtype=float)
    de = np.asarray(ciede2000(la, lb), dtype=float).ravel()
    for i, name in enumerate(("device_white", "device_black")):
        out["white_black"][name] = {"lab_a": [round(float(x), 2) for x in la[i]], "lab_b": [round(float(x), 2) for x in lb[i]],
                                    "de00": round(float(de[i]), 4)}
    return out


def file_identity(path: Path) -> dict:
    data = Path(path).read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
