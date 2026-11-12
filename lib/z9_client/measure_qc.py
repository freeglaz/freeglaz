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

"""Intrinsic quality check of the measurements of ONE scan (one raw ti3).

ADVISORY ONLY. Reads a ti3, never modifies it, never excludes anything, never
builds a profile. It answers one question: is each reading consistent with the
OTHER colours of the same chart? Comparing several scans of the same patch is
``scan_delta``'s job, not this module's.

Scope: measurement errors only. A defect in an ICC table (e.g. zeroed CLUT
nodes) or a visualisation artefact is NOT a measurement error and is out of
scope here.

Method (validated offline on real and synthetic data):

* Lab is recomputed from XYZ with the Argyll D50 white
  (:mod:`lib.z9_client.colorimetry`); stored LAB_* columns are only checked.
* For each patch, its Lab is PREDICTED from its device-RGB neighbours by a
  robust local regression (LOESS: tricube weights, Tukey bisquare IRLS),
  excluding the patch itself and its device duplicates. Neighbours are
  balanced across octants (at most ceil(k/8) per octant) so irregular charts
  do not get one-sided neighbourhoods. Local linear model (k = 24); local
  quadratic (k = 30) for interior/face patches when its prediction is
  reliable.
* The DEVIATION (ΔE00 measured vs predicted, and a standardized score z) is
  kept separate from the RELIABILITY of the prediction (prediction variance
  factor v, local scatter s_loc, effective neighbour count n_eff). An
  unreliable prediction yields ``not_assessable`` rather than a verdict.
* Evidence order: physical checks (lighter than the paper, impossible chroma,
  negative XYZ; robust white) → repeated readings of the same device value in
  the scan (confirmation / discordance; identical copies are not treated as
  independent readings) → neighbourhood model when reliable → luminance
  inversions (greedy attribution, so one gross error does not implicate all
  its partners) → zones (connected flagged patches) and whole-set verdict.

Statuses are QC categories, NOT defect probabilities: ``strong`` does not mean
"wrong", ``not_assessable`` never means "validated". Reason codes are stable
identifiers (translated by the frontend); no UI text lives here.

Known limits (also returned in every report as ``limits``): a coherent drift of
a whole region is invisible; sparse neighbourhoods cannot be judged; strong but
legitimate non-linearity can produce large residuals; corners and edges are
less constrained; duplicated values are not proof of independent readings.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Optional

import numpy as np
from scipy.spatial import cKDTree

from . import colorimetry
from .multipass import parse_ti3

QC_ALGO_VERSION = "1"
"""Bump whenever results can change for identical input (method or defaults)."""

REPORT_FORMAT = 2
"""Bump when the report STRUCTURE changes without changing results (cache validity).
2: per-patch ``reliability.assessable`` / ``reliability.issues``."""

STATUSES = ("coherent", "to_check", "strong", "not_assessable")
POSITIONS = ("interior", "face", "edge", "corner")

REASON_CODES = (
    "PHYS_LIGHTER_THAN_PAPER", "PHYS_WHITE_SUSPECT", "PHYS_CHROMA", "PHYS_NEGATIVE_XYZ",
    "DUP_DISCORDANT", "DUP_PAIR_DIVERGENT", "DUP_TWIN_DIVERGES", "DUP_CONFIRMED",
    "MODEL_RESIDUAL", "MODEL_EXTRAPOLATION", "MODEL_INCOHERENT_NEIGHBORHOOD", "MODEL_SPARSE",
    "HIGH_DEVIATION_UNRELIABLE_PREDICTION", "MONOTONIC_INVERSION",
)
"""Per-patch reason codes (stable API; translated by the frontend)."""
FINDING_CODES = ("SET_INCOHERENT", "ZONE_INCOHERENT", "LOW_DENSITY")
"""Scan-level findings."""
WARNING_CODES = (
    "LAB_COLUMNS_INCONSISTENT_WITH_XYZ", "NO_XYZ_LAB_COLUMNS_USED", "XYZ_SCALE_0_1_RESCALED",
    "NUMBER_OF_SETS_MISMATCH", "MERGED_FILE_ANALYSED", "IDENTICAL_DUPLICATE_ROWS",
)
"""Input-integrity warnings."""
ERROR_CODES = (
    "TRUNCATED", "NO_DATA", "MERGED_FILE", "MISSING_COLUMNS", "SHORT_ROW", "DUPLICATE_SAMPLE_ID",
    "INVALID_VALUE", "RGB_OUT_OF_RANGE", "INVALID_COLORIMETRY",
)
"""QCInputError codes."""

LIMITS = (
    "COHERENT_REGIONAL_DRIFT_UNDETECTABLE",
    "SPARSE_NEIGHBOURHOOD_NOT_ASSESSABLE",
    "LEGITIMATE_NONLINEARITY_MAY_RAISE_RESIDUALS",
    "CORNERS_EDGES_LESS_CONSTRAINED",
    "DUPLICATE_VALUES_NOT_PROOF_OF_INDEPENDENCE",
    "MEASUREMENTS_ONLY_NOT_ICC_TABLES",
)

_EPS_BOUND = 0.5 / 255.0


class QCInputError(ValueError):
    """Input ti3 cannot be analysed. ``code`` is a stable identifier."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class QCParams:
    """Detector parameters (provisional defaults from the offline study).
    Not exposed to users. Any change of DEFAULTS requires a QC_ALGO_VERSION bump."""
    k: int = 24                    # neighbours (linear model), excluding the patch and its duplicates
    k_quad: int = 30               # neighbours of the quadratic model (interior / face only)
    hybrid: bool = True            # quadratic where reliable, linear elsewhere
    balanced: bool = True          # octant-balanced neighbour selection
    robust: bool = True            # Tukey bisquare IRLS
    irls_iter: int = 4
    bisquare_c: float = 4.685
    scale_floor: float = 0.3       # robust scale floor in the IRLS (ΔE76)
    sigma0: float = 0.4            # instrumental noise floor in the standardized score (ΔE00)
    v_max: float = 1.0             # prediction variance factor above which = extrapolation
    s_loc_max: float = 4.0         # local scatter (ΔE00) above which = incoherent / very curved
    neff_min: float = 6.0          # minimum effective neighbour count
    a_low: float = 4.0             # min ΔE00 for "to_check"
    z_low: float = 3.5
    a_high: float = 10.0           # min ΔE00 for "strong"
    z_high: float = 6.0
    scale_low: float = 4.0         # thresholds raised to scale_* × median residual of the chart
    scale_high: float = 8.0
    white_margin_l: float = 2.0    # L* above paper white tolerated
    chroma_max: float = 130.0
    dup_confirm: float = 1.0       # repeated readings agreeing within this (ΔE00) confirm each other
    dup_disagree: float = 2.0      # repeated readings disagreeing beyond this are flagged
    monotonic: bool = True
    mono_margin_l: float = 4.0
    mono_min_gap: float = 0.05     # device-RGB gap (0-1) on all three channels
    zone_min_size: int = 4
    set_incoherent_fraction: float = 0.10
    set_incoherent_min: int = 5
    low_density_n: int = 250       # below this, sensitivity is reduced (reported)

    def digest(self) -> str:
        """Stable short hash of the effective parameters (cache keys)."""
        blob = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ─── geometry helpers ──────────────────────────────────────────────────────
def _position(rgb01: np.ndarray) -> np.ndarray:
    nb = ((rgb01 <= _EPS_BOUND) | (rgb01 >= 1 - _EPS_BOUND)).sum(axis=1)
    return np.array(POSITIONS)[nb]


def _dup_groups(rgb01: np.ndarray) -> np.ndarray:
    keys = np.round(rgb01 * 1e4).astype(np.int64)
    _, inv = np.unique(keys, axis=0, return_inverse=True)
    return inv.ravel()


_ROTATION = None


def _rotation() -> np.ndarray:
    """Fixed 'generic' rotation: avoids exact zero components of regular grids
    when assigning a neighbour to an octant (deterministic)."""
    global _ROTATION
    if _ROTATION is None:
        q, _ = np.linalg.qr(np.random.default_rng(12345).normal(size=(3, 3)))
        _ROTATION = q
    return _ROTATION


def _select_neighbors(i, cand, rgb01, grp, k, balanced):
    cand = [int(j) for j in cand if grp[j] != grp[i]]
    if not balanced:
        return cand[:k]
    per_octant = -(-k // 8)
    rot = _rotation()
    cnt = np.zeros(8, int)
    sel, rest = [], []
    for j in cand:
        dx = (rgb01[j] - rgb01[i]) @ rot
        o = int((dx[0] > 0) + 2 * (dx[1] > 0) + 4 * (dx[2] > 0))
        if cnt[o] < per_octant and len(sel) < k:
            sel.append(j)
            cnt[o] += 1
        else:
            rest.append(j)
    if len(sel) < k:
        sel += rest[:k - len(sel)]
    return sel


def _design(dx: np.ndarray, deg: int) -> np.ndarray:
    cols = [np.ones(len(dx)), dx[:, 0], dx[:, 1], dx[:, 2]]
    if deg == 2:
        cols += [dx[:, 0] ** 2, dx[:, 1] ** 2, dx[:, 2] ** 2,
                 dx[:, 0] * dx[:, 1], dx[:, 0] * dx[:, 2], dx[:, 1] * dx[:, 2]]
    return np.stack(cols, axis=1)


def _fit_one(rgb_i, nb_rgb, nb_lab, deg: int, p: QCParams):
    """Prediction of the Lab at rgb_i from its neighbours. NEVER receives the
    measurement of the evaluated patch."""
    dx = nb_rgb - rgb_i
    d = np.linalg.norm(dx, axis=1)
    dmax = d.max() * 1.1 + 1e-9
    w0 = (1 - (d / dmax) ** 3) ** 3                       # tricube (LOESS)
    X = _design(dx, deg)
    w = w0.copy()
    fitted = None
    coef = None
    for _ in range(p.irls_iter if p.robust else 1):
        sw = np.sqrt(w)[:, None]
        coef, *_ = np.linalg.lstsq(X * sw, nb_lab * sw, rcond=1e-8)
        fitted = X @ coef
        if not p.robust:
            break
        r = np.linalg.norm(nb_lab - fitted, axis=1)
        s = max(1.4826 * float(np.median(r)), p.scale_floor)
        u = r / (p.bisquare_c * s)
        w = w0 * np.where(u < 1, (1 - u ** 2) ** 2, 0.0)
    try:
        A = np.linalg.pinv((X.T * w) @ X)
        v = float((A @ (X.T * w ** 2) @ X @ A)[0, 0])     # WLS sandwich variance at dx = 0
    except np.linalg.LinAlgError:
        v = float("inf")
    neff = float(w.sum() ** 2 / max(float((w ** 2).sum()), 1e-12))
    r00 = colorimetry.ciede2000(nb_lab, fitted)
    keep = w > 1e-3 * w.max() if w.max() > 0 else np.zeros(len(w), bool)
    s_loc = float(np.sqrt(np.average(r00[keep] ** 2, weights=w[keep]))) if keep.any() else float("inf")
    u_dir = dx / np.maximum(d[:, None], 1e-12)
    asym = float(np.linalg.norm((u_dir * w0[:, None]).sum(axis=0)) / max(float(w0.sum()), 1e-12))
    return coef[0], {"v": v, "neff": neff, "s_loc": s_loc, "asym": asym}


class _Detector:
    """Neighbour structure + local predictions for one chart geometry."""

    def __init__(self, rgb01: np.ndarray, k: int, deg: int, p: QCParams):
        self.rgb = np.asarray(rgb01, float)
        self.k, self.deg, self.p = k, deg, p
        self.n = len(self.rgb)
        self.grp = _dup_groups(self.rgb)
        self.pos = _position(self.rgb)
        self.nb: list[np.ndarray] = []
        if self.n > 1:
            maxdup = int(np.bincount(self.grp).max())
            kq = min(self.n, (k * 4 if p.balanced else k) + maxdup + 1)
            _, nb = cKDTree(self.rgb).query(self.rgb, k=kq)
            nb = np.asarray(nb).reshape(self.n, -1)
        for i in range(self.n):
            sel = (_select_neighbors(i, list(nb[i]), self.rgb, self.grp, k, p.balanced)
                   if self.n > 1 else [])
            assert i not in sel and all(self.grp[j] != self.grp[i] for j in sel)
            self.nb.append(np.array(sel, int))
        self.users: list[list[int]] = [[] for _ in range(self.n)]
        for i, s in enumerate(self.nb):
            for j in s:
                self.users[int(j)].append(i)

    def predict(self, lab: np.ndarray, idx=None, excluded=frozenset()):
        out = {}
        min_pool = 5 if self.deg == 1 else 12
        for i in (range(self.n) if idx is None else idx):
            pool = self.nb[i]
            if excluded:
                pool = np.array([j for j in pool if j not in excluded], int)
            if len(pool) < min_pool:
                out[i] = (np.full(3, np.nan), {"v": float("inf"), "neff": 0.0,
                                               "s_loc": float("inf"), "asym": 1.0})
                continue
            out[i] = _fit_one(self.rgb[i], self.rgb[pool], lab[pool], self.deg, self.p)
        return out


class _HybridDetector:
    """Quadratic (k_quad) for interior / face patches when reliable, linear (k) elsewhere."""

    def __init__(self, rgb01: np.ndarray, p: QCParams):
        self.lin = _Detector(rgb01, p.k, 1, p)
        self.quad = _Detector(rgb01, p.k_quad, 2, p) if p.hybrid else None
        self.p = p
        self.rgb, self.n, self.grp, self.pos = self.lin.rgb, self.lin.n, self.lin.grp, self.lin.pos
        self.nb = self.lin.nb
        if self.quad is not None:
            self.users = [sorted(set(a) | set(b)) for a, b in zip(self.lin.users, self.quad.users)]
        else:
            self.users = self.lin.users
        self.use_quad = np.isin(self.pos, ["interior", "face"])

    def predict(self, lab: np.ndarray, idx=None, excluded=frozenset()):
        idx = list(range(self.n)) if idx is None else list(idx)
        out = {i: (pred, {**rel, "model": "linear"})
               for i, (pred, rel) in self.lin.predict(lab, idx=idx, excluded=excluded).items()}
        if self.quad is not None:
            q_idx = [i for i in idx if self.use_quad[i]]
            if q_idx:
                for i, (pred, rel) in self.quad.predict(lab, idx=q_idx, excluded=excluded).items():
                    if (np.all(np.isfinite(pred)) and rel["v"] <= self.p.v_max
                            and rel["s_loc"] <= self.p.s_loc_max):
                        out[i] = (pred, {**rel, "model": "quadratic"})
        return out


# ─── evidence ──────────────────────────────────────────────────────────────
def _unreliable(rel, p: QCParams) -> list:
    u = []
    if not np.isfinite(rel["v"]) or rel["v"] > p.v_max:
        u.append({"code": "MODEL_EXTRAPOLATION", "v": _r(rel["v"], 3)})
    if rel["s_loc"] > p.s_loc_max:
        u.append({"code": "MODEL_INCOHERENT_NEIGHBORHOOD", "s_loc": _r(rel["s_loc"], 2)})
    if rel["neff"] < p.neff_min:
        u.append({"code": "MODEL_SPARSE", "neff": _r(rel["neff"], 1)})
    return u


def _physical(rgb01, lab, xyz, p: QCParams) -> dict:
    out: dict[int, list] = {}
    s = rgb01.sum(axis=1)
    whites = np.where(s >= s.max() - 1e-6)[0]
    lw = float(np.median(lab[whites, 0]))
    notw = np.ones(len(lab), bool)
    notw[whites] = False
    chroma = np.hypot(lab[:, 1], lab[:, 2])
    bad_l = notw & (lab[:, 0] > lw + p.white_margin_l)
    bad_c = chroma > p.chroma_max
    bad_x = (xyz < -0.5).any(axis=1) if xyz is not None else np.zeros(len(lab), bool)
    if int(bad_l.sum()) > 2:
        # Many patches "lighter than the paper": the WHITE reading is suspect, not all
        # the others (avoids a cascade when the white patch itself is corrupted).
        ref = float(np.percentile(lab[notw, 0], 99.5)) if notw.any() else lw
        for w in whites:
            out.setdefault(int(w), []).append({"code": "PHYS_WHITE_SUSPECT", "white_l": _r(lab[w, 0], 2),
                                               "n_lighter": int(bad_l.sum()), "reference_l": _r(ref, 2)})
        bad_l = np.zeros_like(bad_l)
    for i in np.where(bad_l | bad_c | bad_x)[0]:
        if bad_l[i]:
            out.setdefault(int(i), []).append({"code": "PHYS_LIGHTER_THAN_PAPER", "l": _r(lab[i, 0], 2),
                                               "white_l": _r(lw, 2)})
        if bad_c[i]:
            out.setdefault(int(i), []).append({"code": "PHYS_CHROMA", "chroma": _r(chroma[i], 1)})
        if bad_x[i]:
            out.setdefault(int(i), []).append({"code": "PHYS_NEGATIVE_XYZ"})
    return out


def _dup_info(lab, grp, xyz) -> dict:
    out = {}
    for g in np.unique(grp):
        m = np.where(grp == g)[0]
        if len(m) < 2:
            continue
        L = lab[m]
        D = colorimetry.ciede2000(np.repeat(L, len(m), axis=0), np.tile(L, (len(m), 1))).reshape(len(m), len(m))
        med = np.median(L, axis=0)
        dm = colorimetry.ciede2000(L, np.repeat(med[None], len(m), axis=0))
        src = xyz[m] if xyz is not None else L
        identical = bool(np.all(np.abs(src - src[0]) < 1e-9))
        for a, i in enumerate(m):
            out[int(i)] = {"n": int(len(m)), "max_dis": float(D.max()), "dist_med": float(dm[a]),
                           "members": [int(x) for x in m], "identical": identical}
    return out


def _monotonic(rgb01, lab, margin_l, min_gap) -> dict:
    """Luminance inversions (less ink on all channels but darker). Greedy cover:
    the patch involved in most inversions is designated, its pairs removed, repeat."""
    pairs = []
    for i in range(len(rgb01)):
        dom = np.all(rgb01[i] >= rgb01 + min_gap, axis=1)
        bad = np.where(dom & (lab[:, 0] - lab[i, 0] > margin_l))[0]
        pairs += [(i, int(j)) for j in bad]
    out = {}
    while pairs:
        cnt: dict[int, int] = {}
        for a, b in pairs:
            cnt[a] = cnt.get(a, 0) + 1
            cnt[b] = cnt.get(b, 0) + 1
        w = max(cnt, key=lambda k: (cnt[k], -k))
        out[int(w)] = cnt[w]
        pairs = [(a, b) for a, b in pairs if a != w and b != w]
    return out


def _clusters(flagged, det) -> list:
    flag = set(flagged)
    seen: set = set()
    comps = []
    for s in sorted(flag):
        if s in seen:
            continue
        comp, stack = [], [s]
        seen.add(s)
        while stack:
            a = stack.pop()
            comp.append(a)
            for b in list(det.nb[a]) + list(det.users[a]):
                b = int(b)
                if b in flag and b not in seen:
                    seen.add(b)
                    stack.append(b)
        comps.append(sorted(comp))
    return sorted(comps, key=lambda c: (-len(c), c[0]))


def _classify(res, z, rel, phys, dup, a_low, a_high, p: QCParams):
    if phys:
        return "strong", list(phys)
    if dup and not dup["identical"] and dup["max_dis"] > p.dup_disagree:
        if dup["n"] >= 3 and dup["dist_med"] > p.dup_disagree:
            return "strong", [{"code": "DUP_DISCORDANT", "dist_to_median": _r(dup["dist_med"], 2), "n": dup["n"]}]
        if dup["n"] == 2:
            other = dup.get("other_res")
            if other is not None and res < a_low <= other:
                # the twin deviates from the neighbourhood, this patch does not
                return "coherent", [{"code": "DUP_TWIN_DIVERGES", "disagreement": _r(dup["max_dis"], 2),
                                     "twin_residual": _r(other, 2), "residual": _r(res, 2)}]
            status = "strong" if (res >= a_high and z >= p.z_high) else "to_check"
            return status, [{"code": "DUP_PAIR_DIVERGENT", "disagreement": _r(dup["max_dis"], 2),
                             "residual": _r(res, 2)}]
    if dup and not dup["identical"] and (
            (dup["n"] == 2 and dup["max_dis"] <= p.dup_confirm)
            or (dup["n"] >= 3 and dup["dist_med"] <= p.dup_confirm)):
        return "coherent", ([{"code": "DUP_CONFIRMED", "n": dup["n"], "residual": _r(res, 2)}]
                            if res >= a_low else [])
    unrel = _unreliable(rel, p)
    if unrel:
        reasons = list(unrel)
        if res >= a_high:
            reasons.append({"code": "HIGH_DEVIATION_UNRELIABLE_PREDICTION", "residual": _r(res, 2)})
        return "not_assessable", reasons
    if res >= a_high and z >= p.z_high:
        return "strong", [{"code": "MODEL_RESIDUAL", "residual": _r(res, 2), "score": _r(z, 2)}]
    if res >= a_low and z >= p.z_low:
        return "to_check", [{"code": "MODEL_RESIDUAL", "residual": _r(res, 2), "score": _r(z, 2)}]
    return "coherent", []


def _r(x, nd):
    x = float(x)
    return round(x, nd) if np.isfinite(x) else None


def _decide(lab, rgb01, xyz, det, preds, p: QCParams):
    """Classification of every patch from predictions. Returns
    (status_reasons, pred, rel, res, z, dups, mono, meta)."""
    n = det.n
    P = np.array([preds[i][0] for i in range(n)], float).reshape(n, 3)
    rel = [preds[i][1] for i in range(n)]
    ok = np.all(np.isfinite(P), axis=1)
    res = np.zeros(n)
    if ok.any():
        res[ok] = colorimetry.ciede2000(lab[ok], P[ok])
    s = np.array([r["s_loc"] for r in rel], float)
    v = np.array([r["v"] for r in rel], float)
    with np.errstate(invalid="ignore", over="ignore"):
        se = np.sqrt(s ** 2 * (1 + v) + p.sigma0 ** 2)
        z = np.where(np.isfinite(se) & ok, res / se, 0.0)
    phys = _physical(rgb01, lab, xyz, p)
    dups = _dup_info(lab, det.grp, xyz)
    rel_ok = np.array([not _unreliable(r, p) for r in rel], bool)
    med = float(np.median(res[rel_ok])) if rel_ok.any() else float(np.median(res)) if n else 0.0
    a_low = max(p.a_low, p.scale_low * med)
    a_high = max(p.a_high, p.scale_high * med)
    for i, dd in dups.items():
        if dd["n"] == 2:
            j = [m for m in dd["members"] if m != i][0]
            dd["other_res"] = float(res[j]) if not _unreliable(rel[j], p) else None
    out = [_classify(res[i], z[i], rel[i], phys.get(i, []), dups.get(i), a_low, a_high, p) for i in range(n)]
    mono = _monotonic(rgb01, lab, p.mono_margin_l, p.mono_min_gap) if p.monotonic else {}
    for i, c in mono.items():
        reason = {"code": "MONOTONIC_INVERSION", "n_inversions": c}
        if out[i][0] in ("coherent", "not_assessable"):
            out[i] = ("to_check", [reason] + out[i][1])
        else:                                   # already flagged: keep status, add the evidence
            out[i] = (out[i][0], out[i][1] + [reason])
    meta = {"median_residual": med, "a_low": a_low, "a_high": a_high}
    return out, P, rel, res, z, dups, mono, meta


# ─── input ─────────────────────────────────────────────────────────────────
@dataclass
class _Input:
    sample_ids: list
    rgb100: np.ndarray
    lab: np.ndarray
    xyz: Optional[np.ndarray]
    integrity: dict = field(default_factory=dict)


def _read(text: str, allow_merged: bool) -> _Input:
    try:
        header, fmt, rows = parse_ti3(text)
    except IndexError:
        raise QCInputError("TRUNCATED", "truncated ti3 (missing END_DATA_FORMAT / END_DATA)") from None
    except ValueError as e:
        raise QCInputError("NO_DATA", str(e)) from None
    merged = any(l.split(None, 1)[0] == "ORIGINATOR" and "multipass" in l.lower()
                 for l in header if l.strip())
    if merged and not allow_merged:
        raise QCInputError("MERGED_FILE", "ti3 merges several sources (multipass): analyse each raw scan instead")
    ix = {name: k for k, name in enumerate(fmt)}
    missing = [c for c in ("SAMPLE_ID", "RGB_R", "RGB_G", "RGB_B") if c not in ix]
    if missing:
        raise QCInputError("MISSING_COLUMNS", f"missing columns: {missing}")
    for r in rows:
        if len(r) < len(fmt):
            raise QCInputError("SHORT_ROW", f"row with {len(r)} fields, {len(fmt)} expected")
    sids = [r[ix["SAMPLE_ID"]] for r in rows]
    if len(set(sids)) != len(sids):
        raise QCInputError("DUPLICATE_SAMPLE_ID", "SAMPLE_ID values are not unique")
    try:
        rgb = np.array([[float(r[ix[c]]) for c in ("RGB_R", "RGB_G", "RGB_B")] for r in rows], float)
    except ValueError as e:
        raise QCInputError("INVALID_VALUE", f"non-numeric RGB value: {e}") from None
    if not np.all(np.isfinite(rgb)) or rgb.min() < -1e-6 or rgb.max() > 100.0 + 1e-6:
        raise QCInputError("RGB_OUT_OF_RANGE", "device RGB must be finite values in [0, 100]")
    try:
        lab, info = colorimetry.resolve_ti3_lab(header, fmt, rows)
    except ValueError as e:
        raise QCInputError("INVALID_COLORIMETRY", str(e)) from None
    xyz = None
    if info["lab_source"] == "xyz":
        xyz = np.array([[float(r[ix[c]]) for c in ("XYZ_X", "XYZ_Y", "XYZ_Z")] for r in rows], float)
        if info["xyz_scale"] == "0-1":
            xyz = xyz * 100.0
    declared = next((l.split()[1] for l in header if l.strip().startswith("NUMBER_OF_SETS")
                     and len(l.split()) > 1), None)
    warnings = []
    if not info["lab_columns_consistent"] and info["lab_columns_present"] and info["lab_source"] == "xyz":
        warnings.append("LAB_COLUMNS_INCONSISTENT_WITH_XYZ")
    if info["lab_source"] == "lab_columns":
        warnings.append("NO_XYZ_LAB_COLUMNS_USED")
    if info["xyz_scale"] == "0-1":
        warnings.append("XYZ_SCALE_0_1_RESCALED")
    if declared is not None and declared.isdigit() and int(declared) != len(rows):
        warnings.append("NUMBER_OF_SETS_MISMATCH")
    if merged:
        warnings.append("MERGED_FILE_ANALYSED")
    integrity = {**info, "number_of_sets_declared": int(declared) if declared and declared.isdigit() else None,
                 "warnings": warnings}
    return _Input(sids, rgb, lab, xyz, integrity)


# ─── public API ────────────────────────────────────────────────────────────
def analyze_ti3(text: str, params: Optional[QCParams] = None, *, allow_merged: bool = False) -> dict:
    """Analyse the measurements of ONE raw scan (ti3 text). Returns a JSON-
    serializable report (deterministic for identical input and parameters).

    ``allow_merged`` is for offline validation only: a ti3 merging several
    sources (ORIGINATOR "... multipass ...") is refused by default because its
    duplicates are readings from different scans (scan_delta's domain).

    Raises :class:`QCInputError` when the ti3 cannot be analysed."""
    p = params or QCParams()
    inp = _read(text, allow_merged)
    rgb01 = inp.rgb100 / 100.0
    det = _HybridDetector(rgb01, p)
    preds = det.predict(inp.lab)
    out, P, rel, res, z, dups, mono, meta = _decide(inp.lab, rgb01, inp.xyz, det, preds, p)
    return _report(inp, p, det, out, P, rel, res, z, dups, mono, meta)


def _report(inp, p, det, out, P, rel, res, z, dups, mono, meta) -> dict:
    n = det.n
    sid = inp.sample_ids
    unrel = [_unreliable(r, p) for r in rel]           # reliability of the PREDICTION (not of the reading)
    patches = []
    for i in range(n):
        d = dups.get(i)
        patches.append({
            "sample_id": sid[i],
            "rgb": [_r(x, 4) for x in inp.rgb100[i]],
            "rgb8": [int(round(x / 100.0 * 255)) for x in inp.rgb100[i]],
            "lab": [_r(x, 3) for x in inp.lab[i]],
            "lab_pred": [_r(x, 3) for x in P[i]],
            "de00": _r(res[i], 3) if np.all(np.isfinite(P[i])) else None,
            "score": _r(z[i], 3) if np.all(np.isfinite(P[i])) else None,
            "position": str(det.pos[i]),
            "n_neighbors": int(len(det.nb[i])),
            "reliability": {"assessable": not unrel[i], "issues": [u["code"] for u in unrel[i]],
                            "v": _r(rel[i]["v"], 4), "s_loc": _r(rel[i]["s_loc"], 3),
                            "neff": _r(rel[i]["neff"], 2), "asymmetry": _r(rel[i]["asym"], 3),
                            "model": rel[i].get("model", "linear")},
            "status": out[i][0],
            "reasons": out[i][1],
            "duplicates": None if d is None else {
                "n": d["n"], "sample_ids": [sid[m] for m in d["members"]],
                "max_disagreement": _r(d["max_dis"], 3), "dist_to_median": _r(d["dist_med"], 3),
                "identical_values": d["identical"]},
        })
    counts = {s: sum(1 for x in out if x[0] == s) for s in STATUSES}
    n_incoh = sum(1 for _, reasons in out for r in reasons if r["code"] == "MODEL_INCOHERENT_NEIGHBORHOOD")
    flagged = [i for i, (s, _) in enumerate(out) if s != "coherent"]
    findings = []
    if n_incoh > max(p.set_incoherent_min, p.set_incoherent_fraction * n):
        findings.append({"code": "SET_INCOHERENT", "n_incoherent_neighborhoods": n_incoh,
                         "fraction": _r(n_incoh / n, 3)})
    else:
        for c in _clusters(flagged, det):
            if len(c) >= p.zone_min_size:
                findings.append({
                    "code": "ZONE_INCOHERENT", "n": len(c), "sample_ids": [sid[i] for i in c],
                    "rgb8_center": [int(round(x)) for x in (inp.rgb100[c].mean(axis=0) / 100.0 * 255)],
                    "n_incoherent_neighborhoods": sum(
                        1 for i in c for r in out[i][1] if r["code"] == "MODEL_INCOHERENT_NEIGHBORHOOD"),
                    "n_inversions": sum(1 for i in c if i in mono)})
    if n < p.low_density_n:
        findings.append({"code": "LOW_DENSITY", "n_patches": n, "threshold": p.low_density_n})
    n_dup_groups = len({tuple(d["members"]) for d in dups.values()})
    n_identical = len({tuple(d["members"]) for d in dups.values() if d["identical"]})
    integrity = dict(inp.integrity)
    integrity["duplicate_groups"] = n_dup_groups
    integrity["identical_duplicate_groups"] = n_identical
    if n_identical:
        integrity["warnings"] = list(integrity["warnings"]) + ["IDENTICAL_DUPLICATE_ROWS"]
    return {
        "algo_version": QC_ALGO_VERSION,
        "report_format": REPORT_FORMAT,
        "params": asdict(p),
        "params_digest": p.digest(),
        "n_patches": n,
        "counts": counts,
        "median_residual": _r(meta["median_residual"], 3),
        "thresholds": {"to_check": _r(meta["a_low"], 3), "strong": _r(meta["a_high"], 3)},
        "integrity": integrity,
        "findings": findings,
        "limits": list(LIMITS),
        "patches": patches,
    }


def summarize(report: dict) -> dict:
    """Compact summary of a report (for list views): counts + findings codes."""
    return {"algo_version": report["algo_version"], "params_digest": report["params_digest"],
            "n_patches": report["n_patches"], "counts": report["counts"],
            "median_residual": report["median_residual"],
            "findings": [f["code"] for f in report["findings"]],
            "warnings": list(report["integrity"].get("warnings", []))}
