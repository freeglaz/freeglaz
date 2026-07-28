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

"""Inter-scan ΔE mini-report — safeguard BEFORE averaging ("we don't average
blindly"). READ-ONLY: reads the ``kept`` ti3 of a session, compares the
``LAB_*`` columns patch by patch (ΔE2000), and distinguishes the two causes of an inter-scan
gap:
  - **legitimate drying**: a FAMILY of warm/saturated patches moderately shifted (~1.3);
  - **contamination / error**: an ISOLATED PEAK on 1-2 patches (dust, insect, registration).

The discriminator is not the value of the max but the SHAPE: ``isolated_outlier`` =
``max ≫ p95`` (a patch that far exceeds the 95th percentile). Ref: brief 11/06 +
Docs/Design_Webapp_Scan_Rouleau_Multi_Scan.md §4d. The "exclude this scan" toggle (mutating)
is a NEXT increment — here, diagnosis only.
"""
from __future__ import annotations

import math
from pathlib import Path

from lib.z9_client.multipass import parse_ti3


def ciede2000(lab1, lab2) -> float:
    """ΔE2000 (Sharma, Wu, Dalal formulation). ``lab`` = (L*, a*, b*)."""
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    C1 = math.hypot(a1, b1)
    C2 = math.hypot(a2, b2)
    avg_C = (C1 + C2) / 2.0
    G = 0.5 * (1 - math.sqrt(avg_C ** 7 / (avg_C ** 7 + 25 ** 7))) if avg_C > 0 else 0.0
    a1p, a2p = a1 * (1 + G), a2 * (1 + G)
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360

    dLp = L2 - L1
    dCp = C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360
    else:
        dhp = h2p - h1p + 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp) / 2.0)

    avg_Lp = (L1 + L2) / 2.0
    avg_Cp = (C1p + C2p) / 2.0
    if C1p * C2p == 0:
        avg_hp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        avg_hp = (h1p + h2p) / 2.0
    elif h1p + h2p < 360:
        avg_hp = (h1p + h2p + 360) / 2.0
    else:
        avg_hp = (h1p + h2p - 360) / 2.0

    T = (1 - 0.17 * math.cos(math.radians(avg_hp - 30))
         + 0.24 * math.cos(math.radians(2 * avg_hp))
         + 0.32 * math.cos(math.radians(3 * avg_hp + 6))
         - 0.20 * math.cos(math.radians(4 * avg_hp - 63)))
    d_ro = 30 * math.exp(-(((avg_hp - 275) / 25) ** 2))
    Rc = 2 * math.sqrt(avg_Cp ** 7 / (avg_Cp ** 7 + 25 ** 7)) if avg_Cp > 0 else 0.0
    Sl = 1 + (0.015 * (avg_Lp - 50) ** 2) / math.sqrt(20 + (avg_Lp - 50) ** 2)
    Sc = 1 + 0.045 * avg_Cp
    Sh = 1 + 0.015 * avg_Cp * T
    Rt = -math.sin(math.radians(2 * d_ro)) * Rc
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2
                     + Rt * (dCp / Sc) * (dHp / Sh))


def _read_lab_rgb(ti3_path: Path) -> dict:
    """{sample_id: {'rgb': [R,G,B]|None, 'lab': (L,a,b)}} from a ti3 (native columns)."""
    cols, fmt, rows = parse_ti3(ti3_path.read_text(encoding="ascii", errors="replace"))
    idx = {name: i for i, name in enumerate(fmt)}
    for n in ("SAMPLE_ID", "LAB_L", "LAB_A", "LAB_B"):
        if n not in idx:
            raise ValueError(f"colonne {n} absente du ti3 {ti3_path.name}")
    has_rgb = all(c in idx for c in ("RGB_R", "RGB_G", "RGB_B"))
    out = {}
    for r in rows:
        sid = r[idx["SAMPLE_ID"]]
        lab = (float(r[idx["LAB_L"]]), float(r[idx["LAB_A"]]), float(r[idx["LAB_B"]]))
        # device RGB of the ti3 = 0-100 → 0-255 for the swatch/display (compat AllPatchesModal)
        rgb = ([round(float(r[idx[c]]) / 100.0 * 255) for c in ("RGB_R", "RGB_G", "RGB_B")]
               if has_rgb else [0, 0, 0])
        out[sid] = {"rgb": rgb, "lab": lab}
    return out


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def ti3_sample_ids(ti3_path: Path) -> set[str]:
    """Set of SAMPLE_ID of a ti3 (QC anchor — matching by id, never by position)."""
    _, fmt, rows = parse_ti3(ti3_path.read_text(encoding="ascii", errors="replace"))
    sid = fmt.index("SAMPLE_ID")
    return {r[sid] for r in rows}


def filter_ti3_text(text: str, rejected_sids: set[str]) -> str:
    """Return the text of a ti3 WITHOUT the DATA lines whose SAMPLE_ID is rejected
    (anchored on SAMPLE_ID, never the line index). Preserves everything else byte for byte
    (columns, spectral bands, header); only realigns NUMBER_OF_SETS. Used to
    build the profile base EXCLUDING rejected readings (the patch is averaged over its
    remaining readings in the OTHER included scans)."""
    if not rejected_sids:
        return text
    _, fmt, _ = parse_ti3(text)
    sid_idx = fmt.index("SAMPLE_ID")
    out, in_data, kept = [], False, 0
    for line in text.splitlines():
        s = line.strip()
        if s == "BEGIN_DATA":
            in_data = True; out.append(line); continue
        if s == "END_DATA":
            in_data = False; out.append(line); continue
        if in_data and s and not s.startswith("#"):
            toks = s.split()
            if sid_idx < len(toks) and toks[sid_idx] in rejected_sids:
                continue                       # rejected line → removed
            kept += 1
        out.append(line)
    out = [f"NUMBER_OF_SETS {kept}" if l.strip().startswith("NUMBER_OF_SETS") else l
           for l in out]
    return "\n".join(out) + "\n"


def _median3(vals: list[tuple]) -> tuple:
    """Component-by-component median of a list of (L, a, b)."""
    n = len(vals)
    mid = n // 2
    out = []
    for k in range(3):
        col = sorted(v[k] for v in vals)
        out.append(col[mid] if n % 2 else (col[mid - 1] + col[mid]) / 2.0)
    return tuple(out)


def compute_scan_qc(ti3_paths: list[Path], rejected: dict[str, set[str]] | None = None) -> dict:
    """QC matrix per patch × scan (LOT 2) over the INCLUDED scans (≥2). For each patch:
    one L/a/b reading per scan, the DISAGREEMENT index (max ΔE2000 between NON-
    rejected readings), and the identification of the OUTLIER (reading farthest from the median of the others).
    Matching by SAMPLE_ID (never position). READ-ONLY. ``rejected`` = {ti3_name: {sid…}}."""
    if len(ti3_paths) < 2:
        raise ValueError("au moins 2 scans inclus requis pour la vue QC")
    rejected = rejected or {}
    names = [p.name for p in ti3_paths]
    scans = [_read_lab_rgb(p) for p in ti3_paths]
    common = [sid for sid in scans[0] if all(sid in s for s in scans[1:])]
    if not common:
        raise ValueError("aucun patch commun entre les scans (SAMPLE_ID disjoints)")

    patches = []
    for sid in common:
        readings = []
        valid_labs = []
        for name, s in zip(names, scans):
            rej = sid in rejected.get(name, set())
            lab = s[sid]["lab"]
            readings.append({"ti3": name, "lab": [round(x, 2) for x in lab], "rejected": rej})
            if not rej:
                valid_labs.append(lab)
        # disagreement = worst ΔE between NON-rejected readings
        de = 0.0
        for i in range(len(valid_labs)):
            for j in range(i + 1, len(valid_labs)):
                de = max(de, ciede2000(valid_labs[i], valid_labs[j]))
        # outlier = non-rejected reading farthest from the median (significant at ≥3)
        outlier_name = None
        if len(valid_labs) >= 3 and de > 1.5:
            ref = _median3(valid_labs)
            worst, worst_de = None, -1.0
            for r in readings:
                if r["rejected"]:
                    continue
                d = ciede2000(tuple(r["lab"]), ref)
                if d > worst_de:
                    worst, worst_de = r["ti3"], d
            outlier_name = worst
        for r in readings:
            r["outlier"] = (r["ti3"] == outlier_name and not r["rejected"])
        patches.append({
            "id": sid, "rgb": scans[0][sid]["rgb"],
            "readings": readings, "disagreement": round(de, 3),
            "n_valid": len(valid_labs),
        })

    des = sorted(p["disagreement"] for p in patches)
    n = len(des)
    worst = max(patches, key=lambda p: p["disagreement"])["id"] if patches else None
    return {
        "n_scans": len(scans),
        "scans": names,
        "comparable": True,
        "n_patches": n,
        "summary": {
            "mean": round(sum(des) / n, 3) if n else 0.0,
            "median": round(_percentile(des, 0.5), 3),
            "max": round(des[-1], 3) if n else 0.0,
            "worst_patch_id": worst,
        },
        "patches": patches,
    }


def compute_scan_delta(ti3_paths: list[Path]) -> dict:
    """Inter-scan ΔE2000 patch by patch (≥2 ti3). 2 scans → the only pair; ≥3 →
    max per pair (worst disagreement). Returns {n_scans, n_patches, summary, patches}.
    READ-ONLY (touches no file)."""
    if len(ti3_paths) < 2:
        raise ValueError("au moins 2 scans requis pour comparer")
    scans = [_read_lab_rgb(p) for p in ti3_paths]
    common = [sid for sid in scans[0] if all(sid in s for s in scans[1:])]
    if not common:
        raise ValueError("aucun patch commun entre les scans (SAMPLE_ID disjoints)")

    patches = []
    for sid in common:
        labs = [s[sid]["lab"] for s in scans]
        de = 0.0
        for i in range(len(labs)):
            for j in range(i + 1, len(labs)):
                de = max(de, ciede2000(labs[i], labs[j]))
        patches.append({
            "id": sid,
            "de2000": round(de, 3),
            "rgb": scans[0][sid]["rgb"],
            "lab_target": [round(x, 2) for x in labs[0]],     # scan #1
            "lab_achieved": [round(x, 2) for x in labs[-1]],  # scan #N (the most recent)
        })

    des = sorted(p["de2000"] for p in patches)
    n = len(des)
    mean = sum(des) / n
    median = _percentile(des, 0.5)
    p95 = _percentile(des, 0.95)
    mx = des[-1]
    worst = max(patches, key=lambda p: p["de2000"])["id"]
    # ISOLATED outlier = contamination signature: the max far exceeds the p95 (a
    # deviant patch, not a family). Floor safeguard (> 1.5) so as not to flag noise.
    isolated = bool(mx > 1.5 and (p95 <= 0 or mx > 3.0 * p95))
    return {
        "n_scans": len(scans),
        "n_patches": n,
        "summary": {
            "mean": round(mean, 3),
            "median": round(median, 3),
            "p95": round(p95, 3),
            "max": round(mx, 3),
            "worst_patch_id": worst,
            "isolated_outlier": isolated,
        },
        "patches": patches,
    }
