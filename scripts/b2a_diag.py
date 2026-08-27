#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""B2A / δLc DIAGNOSTIC — is the destination B2A sane enough for voie B?

Side diagnostic (never touches convert.py/print). Voie B (abstract profile via
collink -s) inverts PCS→device through the DEST profile's B2A. If that B2A
recouples luminance and chroma (raises L when we ask for C) BEFORE the physical
gamut boundary, an abstract precompression would be undone downstream and voie B
is compromised — whatever we encode. This measures exactly that, on the real
Canson resident, WITHOUT building any abstract.

Chain per point (two xicclu calls, batched), on the COLORIMETRIC table:
  Lab(L,C,h) --[-fb -ir -pl → B2A1]--> device --[-ff -pl → A2B1]--> Lab_return

Metric — δLc isolates the chroma→luminance coupling, removing the legitimate
common part (Dmax, paper tone mapping):
  Lref(L)          = Lreturn(L, C=0)                 # neutral response per L
  δLc(L,C,h)       = Lreturn(L,C,h) - Lref(L)        # NOT Lreturn - Lin
Also ΔC = Creturn - Cin, Δh = hreturn - hin, and Cphysical(L,h) = geometric
gamut boundary from gamut.extract_gamut_mesh (A2B geometry) sliced at (L,h),
cross-checked against the measured in-gamut edge.

Outputs (--out-dir): b2a_points.csv, b2a_summary.json, b2a_reading.txt.
No plotting dep (matplotlib absent) — CSV holds the δLc=f(C) curves.

Run: uv run python scripts/b2a_diag.py --dest <resident.icc> --out-dir <dir>
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.z9_client import gamut, xicclu          # noqa: E402


def lab_of(L, C, h):
    r = math.radians(h)
    return (L, C * math.cos(r), C * math.sin(r))


def lch_of(L, a, b):
    return (L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360.0)


def hue_delta(h_in, h_out):
    return (h_out - h_in + 180.0) % 360.0 - 180.0


# ─── geometric gamut boundary Cphysical(L,h) from the A2B mesh ────────────────
def build_boundary(dest_icc: Path, intent: str = "r"):
    """Return a query fn Cphysical(L,h) from the device-surface mesh (Lab).

    Band estimate: max chroma among boundary vertices within an L-band and a
    hue-band of the query. Simple/robust — enough to POSITION the δLc break vs
    the physical boundary (we do not need a watertight slice)."""
    mesh = gamut.extract_gamut_mesh(str(dest_icc), intent=intent)
    verts = mesh["vertices"]                       # [[L,a,b], ...] in Lab
    pts = []
    for L, a, b in verts:
        _, C, h = lch_of(L, a, b)
        pts.append((L, C, h))

    def cphysical(L0, h0, dL=6.0, dh=18.0):
        best = 0.0
        for L, C, h in pts:
            if abs(L - L0) <= dL and abs(hue_delta(h, h0)) <= dh:
                best = max(best, C)
        return round(best, 2) if best > 0 else None
    return cphysical


# ─── B2A1 → device → A2B1 chain ──────────────────────────────────────────────
def run_chain(dest_icc: Path, grid):
    labs = [g["lab_in"] for g in grid]
    dev = xicclu.run_xicclu(dest_icc, labs, direction="b", intent="r", pcs="lab")   # B2A1
    ret = xicclu.run_xicclu(dest_icc, dev, direction="f", pcs="lab")                # A2B1
    return dev, ret


def run_diag(dest_icc: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    Ls = [5, 10, 15, 20, 30, 50]
    hs = list(range(0, 360, 30))                   # 0..330 step 30
    Cs = list(range(0, 61, 4))                     # 0..60 step 4 (boundary resolution)

    grid = []
    for L in Ls:
        for h in hs:
            for C in Cs:
                if C == 0 and h != hs[0]:
                    continue                       # one neutral per L
                grid.append({"L": L, "C": C, "h": h, "lab_in": lab_of(L, C, h)})

    print(f"[b2a] {len(grid)} points  L={Ls} h={hs} C={Cs}")
    dev, ret = run_chain(dest_icc, grid)
    print(f"[b2a] boundary geometry from gamut mesh …")
    cphysical = build_boundary(dest_icc)

    # Lref(L) = neutral return per L (C=0)
    Lref = {}
    for i, g in enumerate(grid):
        if g["C"] == 0:
            Lref[g["L"]] = round(ret[i][0], 3)

    rows = []
    for i, g in enumerate(grid):
        Lr, ar, br = ret[i]
        _, Cr, hr = lch_of(Lr, ar, br)
        rows.append({
            "L_in": g["L"], "C_in": g["C"], "h_in": g["h"],
            "L_ret": round(Lr, 3), "C_ret": round(Cr, 3), "h_ret": round(hr, 3),
            "Lref": Lref[g["L"]],
            "dLc": round(Lr - Lref[g["L"]], 3),
            "dC": round(Cr - g["C"], 3),
            "dh": round(hue_delta(g["h"], hr), 3),
            "Cphysical": cphysical(g["L"], g["h"]),
        })

    # ── per-(L,h) curve descriptors: where does δLc break vs the boundary? ──
    curves = {}
    for r in rows:
        curves.setdefault((r["L_in"], r["h_in"]), []).append(r)
    descriptors = []
    for (L, h), pts in sorted(curves.items()):
        pts = sorted(pts, key=lambda p: p["C_in"])
        # measured in-gamut edge: last Cin where C still tracks (|dC| < 2)
        edge = 0
        for p in pts:
            if abs(p["dC"]) < 2.0:
                edge = p["C_in"]
        # in-domain drift: max |δLc| for Cin <= 0.9*edge (should be ~0 if sane)
        indom = [abs(p["dLc"]) for p in pts if p["C_in"] <= 0.9 * edge]
        # δLc just beyond the edge (the break)
        beyond = [p["dLc"] for p in pts if p["C_in"] > edge]
        cphys = pts[0]["Cphysical"]
        descriptors.append({
            "L": L, "h": h,
            "edge_measured": edge, "Cphysical_geom": cphys,
            "indomain_dLc_max": round(max(indom), 3) if indom else 0.0,
            "beyond_dLc_max": round(max(beyond), 3) if beyond else None,
        })

    # ── aggregate verdict inputs ──
    indom_all = [d["indomain_dLc_max"] for d in descriptors]
    shadow_desc = [d for d in descriptors if d["L"] <= 20]
    indom_shadow = [d["indomain_dLc_max"] for d in shadow_desc]
    summary = {
        "dest": str(dest_icc), "n_points": len(grid),
        "grid": {"L": Ls, "h": hs, "C": Cs},
        "Lref": Lref,
        "indomain_dLc_max_overall": round(max(indom_all), 3),
        "indomain_dLc_mean_overall": round(sum(indom_all) / len(indom_all), 3),
        "indomain_dLc_max_shadow": round(max(indom_shadow), 3) if indom_shadow else None,
        "indomain_dLc_mean_shadow": round(sum(indom_shadow) / len(indom_shadow), 3) if indom_shadow else None,
        "descriptors": descriptors,
    }

    # write
    with open(out_dir / "b2a_points.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "L_in", "C_in", "h_in", "L_ret", "C_ret", "h_ret",
            "Lref", "dLc", "dC", "dh", "Cphysical"])
        w.writeheader(); w.writerows(rows)
    (out_dir / "b2a_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_reading(out_dir / "b2a_reading.txt", summary, descriptors)
    print(f"[b2a] in-domain |δLc| max overall={summary['indomain_dLc_max_overall']} "
          f"shadow={summary['indomain_dLc_max_shadow']}")
    print(f"[b2a] wrote b2a_points.csv ({len(rows)} rows), b2a_summary.json, b2a_reading.txt")
    return summary


def _write_reading(path, summary, descriptors):
    lines = ["# B2A / δLc diagnostic — voie B viability (does the B2A recouple L↔C?)", ""]
    lines.append(f"dest : {summary['dest']}")
    lines.append(f"grid : L={summary['grid']['L']} h={summary['grid']['h']} C={summary['grid']['C']}")
    lines.append("")
    lines.append("δLc(L,C,h) = Lreturn(L,C,h) - Lreturn(L,C=0)  [Lab→B2A1→device→A2B1→Lab]")
    lines.append(">0 = the B2A raises luminance when chroma is requested (L↔C recoupling).")
    lines.append("'in-domain' = C up to 0.9× the measured in-gamut edge — where a sane B2A")
    lines.append("should keep δLc≈0 (case 1/2). Large in-domain δLc = early recoupling (case 3).")
    lines.append("")
    lines.append(f"IN-DOMAIN |δLc|  : overall max={summary['indomain_dLc_max_overall']} "
                 f"mean={summary['indomain_dLc_mean_overall']}")
    lines.append(f"                   shadows (L≤20) max={summary['indomain_dLc_max_shadow']} "
                 f"mean={summary['indomain_dLc_mean_shadow']}")
    lines.append("")
    lines.append("Per (L,h): measured in-gamut edge vs geometric Cphysical, in-domain |δLc|,")
    lines.append("and δLc just beyond the edge (the break). Sorted by in-domain |δLc| worst→best.")
    lines.append("")
    lines.append(f"{'L':>4} {'h':>4} {'edge':>5} {'Cphys':>6} {'indom|δLc|':>11} {'beyond δLc':>11}")
    lines.append("-" * 48)
    for d in sorted(descriptors, key=lambda x: x["indomain_dLc_max"], reverse=True)[:24]:
        lines.append(f"{d['L']:>4} {d['h']:>4} {d['edge_measured']:>5} "
                     f"{str(d['Cphysical_geom']):>6} {d['indomain_dLc_max']:>11} "
                     f"{str(d['beyond_dLc_max']):>11}")
    lines.append("")
    lines.append("READ: if in-domain |δLc| stays small (~<1) and the break sits at the edge →")
    lines.append("CASE 1/2 (B2A sane to ~the boundary, voie B credible). If in-domain |δLc| is")
    lines.append("large well inside the edge → CASE 3 (early recoupling, voie B compromised).")
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="B2A / δLc diagnostic (measure only).")
    ap.add_argument("--dest", required=True, type=Path, help="destination resident ICC")
    ap.add_argument("--out-dir", required=True, type=Path)
    a = ap.parse_args()
    run_diag(a.dest, a.out_dir)


if __name__ == "__main__":
    main()
