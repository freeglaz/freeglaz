#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Neutral axis + Cmax_N boundary DIAGNOSTIC (measure/geometry only).

Side diagnostic (never touches convert.py/print). Profile-driven: the dark axis
is NOT assumed neutral; NO special case per profiler; the black is never
neutralised. Characterises the destination's real neutral axis N(L), builds a
clean iso-L chroma boundary Cmax_N(L, h_N) measured FROM that axis (not the
band-slice of the B2A jalon), and validates it against the behavioural boundary.

Three neutral axes are measured and compared (not decided a priori):
  A theoretical  (L, 0, 0)
  B A2B          device R=G=B ramp → A2B1 → Lab      (what the device produces)
  C roundtrip    Lab(L,0,0) → B2A1 → device → A2B1 → Lab  (what the -s inversion does)
Architecture hint (confirmed by measurement here): C is the pipeline-relevant one.

Guards that SIGNAL rather than hide:
  - regularity of N(L) vs L (roundtrip can be noisy in deep shadows);
  - non-convexity: a ray from N may cross a non-convex iso-L slice several times.
    Convention (brief): boundary = OUTERMOST crossing (first met coming from the
    exterior toward N); FLAG every (L,h_N) where the star-shaped hypothesis breaks.

Outputs (--out-dir): na_axes.csv, na_recenter.csv, na_boundary.csv, na_summary.json,
na_reading.txt.  Run: uv run python scripts/neutral_axis_diag.py --dest <icc> --out-dir <dir>
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.z9_client import gamut, xicclu                      # noqa: E402
from lib.z9_client.inspect import HpProprietaryDecoder       # noqa: E402


def lch(L, a, b):
    return (L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360.0)


def hue_delta(h1, h2):
    return (h2 - h1 + 180.0) % 360.0 - 180.0


# ─── Étape A — three neutral axes + black point ──────────────────────────────
def axis_theoretical(L):
    return (0.0, 0.0)


def axis_a2b_ramp(dest, Ls):
    """Device R=G=B ramp → A2B1 → Lab, then interpolate (a,b) at each target L."""
    ts = [i / 100.0 for i in range(101)]
    labs = xicclu.run_xicclu(dest, [(t, t, t) for t in ts], direction="f",
                             intent="r", pcs="lab")
    ramp = sorted([(lab[0], lab[1], lab[2]) for lab in labs])   # by L
    out = {}
    for L0 in Ls:
        # linear interp in L over the ramp
        if L0 <= ramp[0][0]:
            out[L0] = (ramp[0][1], ramp[0][2]); continue
        if L0 >= ramp[-1][0]:
            out[L0] = (ramp[-1][1], ramp[-1][2]); continue
        for i in range(1, len(ramp)):
            if ramp[i][0] >= L0:
                (L1, a1, b1), (L2, a2, b2) = ramp[i - 1], ramp[i]
                f = (L0 - L1) / (L2 - L1) if L2 != L1 else 0.0
                out[L0] = (a1 + f * (a2 - a1), b1 + f * (b2 - b1)); break
    return out


def axis_roundtrip(dest, Ls):
    """Lab(L,0,0) → B2A1 → device → A2B1 → Lab. Returns {L: (a_ret, b_ret, L_ret)}."""
    dev = xicclu.run_xicclu(dest, [(L, 0.0, 0.0) for L in Ls], direction="b",
                            intent="r", pcs="lab")
    ret = xicclu.run_xicclu(dest, dev, direction="f", intent="r", pcs="lab")
    return {Ls[i]: (ret[i][1], ret[i][2], ret[i][0]) for i in range(len(Ls))}


def black_point(dest):
    lab = xicclu.run_xicclu(dest, [(0.0, 0.0, 0.0)], direction="f", intent="r", pcs="lab")[0]
    data = Path(dest).read_bytes()
    tags = HpProprietaryDecoder.parse_icc_tags(data)
    bk = HpProprietaryDecoder.extract_xyz_tag(data, b"bkpt", tags)
    return {"a2b_black_lab": [round(x, 3) for x in lab], "bkpt_tag_xyz": bk}


# ─── Étape C — iso-L slice + radial boundary Cmax_N ──────────────────────────
def iso_L_segments(verts, tris, L0):
    """Segments (in a-b) where the triangle mesh crosses the plane L=L0."""
    segs = []
    for (i, j, k) in tris:
        p = [verts[i], verts[j], verts[k]]
        cross = []
        for m in range(3):
            v1, v2 = p[m], p[(m + 1) % 3]
            d1, d2 = v1[0] - L0, v2[0] - L0
            if (d1 <= 0 <= d2) or (d2 <= 0 <= d1):
                if d1 == d2:
                    continue
                f = d1 / (d1 - d2)
                cross.append((v1[1] + f * (v2[1] - v1[1]), v1[2] + f * (v2[2] - v1[2])))
        if len(cross) == 2:
            segs.append((cross[0], cross[1]))
    return segs


def ray_boundary(segs, origin, h_deg):
    """All ray∩segment distances from ``origin`` along direction h_deg (t>=0),
    sorted ascending. Radial boundary Cmax_N = OUTERMOST crossing (brief
    convention: first met coming from the exterior toward N)."""
    ax, ay = origin
    dx, dy = math.cos(math.radians(h_deg)), math.sin(math.radians(h_deg))
    ts = []
    for (p1, p2) in segs:
        ex, ey = p2[0] - p1[0], p2[1] - p1[1]
        den = dx * ey - dy * ex
        if abs(den) < 1e-12:
            continue
        wx, wy = p1[0] - ax, p1[1] - ay
        t = (wx * ey - wy * ex) / den          # distance along ray
        s = (wx * dy - wy * dx) / den          # param on segment
        if t >= -1e-9 and -1e-9 <= s <= 1 + 1e-9:
            ts.append(t)
    ts.sort()
    # dedup near-identical crossings (shared triangle edges)
    dedup = []
    for t in ts:
        if not dedup or abs(t - dedup[-1]) > 0.2:
            dedup.append(t)
    return dedup


# ─── run ─────────────────────────────────────────────────────────────────────
def run(dest: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    Ls = [5, 10, 15, 20, 30, 50]
    hs_N = list(range(0, 360, 30))
    dest = str(dest)

    print("[na] measuring the three neutral axes …")
    a2b = axis_a2b_ramp(dest, Ls)
    rt = axis_roundtrip(dest, Ls)
    bp = black_point(dest)
    print(f"[na] black: A2B1(0,0,0)={bp['a2b_black_lab']}  bkpt_tag={bp['bkpt_tag_xyz']}")

    # axes table + regularity of N (roundtrip) vs L
    axes_rows, N = [], {}
    for L in Ls:
        aB, bB = a2b[L]
        aC, bC, LretC = rt[L]
        N[L] = (aC, bC, LretC)                 # pipeline-relevant axis C
        axes_rows.append({
            "L": L,
            "A_a": 0.0, "A_b": 0.0,
            "B_a": round(aB, 3), "B_b": round(bB, 3), "B_C": round(math.hypot(aB, bB), 3),
            "B_h": round(math.degrees(math.atan2(bB, aB)) % 360, 1),
            "C_a": round(aC, 3), "C_b": round(bC, 3), "C_C": round(math.hypot(aC, bC), 3),
            "C_h": round(math.degrees(math.atan2(bC, aC)) % 360, 1),
            "C_Lret": round(LretC, 3),
            "dist_B_C": round(math.hypot(aB - aC, bB - bC), 3),
        })
    # regularity: successive step of N (axis C) in the a-b plane
    reg = []
    for i in range(1, len(Ls)):
        (a1, b1, _), (a2, b2, _) = N[Ls[i - 1]], N[Ls[i]]
        reg.append(round(math.hypot(a2 - a1, b2 - b1) / (Ls[i] - Ls[i - 1]), 4))
    reg_max = max(reg) if reg else 0.0

    # mesh (device surface, relative colorimetric — same PCS space as the chain)
    print("[na] extracting gamut mesh …")
    mesh = gamut.extract_gamut_mesh(dest, intent="r")
    verts, tris = mesh["vertices"], mesh["indices"]

    # ── Étapes C+D — Cgeom (mesh ray from N) vs Cmeasured (roundtrip ray from N) ──
    print("[na] boundary Cmax_N (geom) + behavioural (measured), N-frame …")
    Cs_N = list(range(0, 65, 4))
    # batch the roundtrip rays: build all Lab points, one B2A1 + one A2B1 call
    pts, index = [], []
    for L in Ls:
        aN, bN, _ = N[L]
        for h in hs_N:
            for C in Cs_N:
                a = aN + C * math.cos(math.radians(h))
                b = bN + C * math.sin(math.radians(h))
                pts.append((L, a, b)); index.append((L, h, C))
    dev = xicclu.run_xicclu(dest, pts, direction="b", intent="r", pcs="lab")
    ret = xicclu.run_xicclu(dest, dev, direction="f", intent="r", pcs="lab")

    # Lref_N(L) = L_ret at C_N=0 (roundtrip of the real neutral N(L))
    LrefN = {}
    for idx, (L, h, C) in enumerate(index):
        if C == 0 and h == hs_N[0]:
            LrefN[L] = ret[idx][0]

    # per (L,h): measured edge (C_N_ret stops tracking) + geom boundary + flag
    segs_by_L = {L: iso_L_segments(verts, tris, L) for L in Ls}
    bnd_rows, recenter_rows = [], []
    for L in Ls:
        aN, bN, _ = N[L]
        segs = segs_by_L[L]
        for h in hs_N:
            # geometric boundary (radial from N, non-convex guard)
            xs = ray_boundary(segs, (aN, bN), h)
            cgeom = round(xs[-1], 2) if xs else None
            star_broken = len(xs) > 1
            # behavioural ramp along this (L,h)
            edge = 0.0
            for idx, key in enumerate(index):
                if key[0] != L or key[1] != h:
                    continue
                C = key[2]
                Lr, ar, br = ret[idx]
                CNret = math.hypot(ar - aN, br - bN)
                dLc = Lr - LrefN[L]
                if abs(CNret - C) < 2.0:
                    edge = C
                # recentered δLc rows for the blue/magenta shadow focus
                if L <= 15 and 240 <= h <= 300:
                    recenter_rows.append({
                        "L": L, "h_N": h, "C_N_in": C,
                        "C_N_ret": round(CNret, 3), "dLc_recentered": round(dLc, 3),
                    })
            bnd_rows.append({
                "L": L, "h_N": h,
                "Cgeom": cgeom, "Cmeasured": edge,
                "diff": round(cgeom - edge, 2) if cgeom is not None else None,
                "star_broken": int(star_broken), "n_crossings": len(xs),
            })

    # ── verdict inputs ──
    valid = [r for r in bnd_rows if r["Cgeom"] is not None and r["Cmeasured"]]
    diffs = [r["diff"] for r in valid]
    shadow_bm = [r for r in bnd_rows if r["L"] <= 20 and 240 <= r["h_N"] <= 300]
    n_star_broken = sum(r["star_broken"] for r in bnd_rows)

    # recenter verdict: in-domain (C_N < ~edge) |δLc| for blue/magenta shadows
    indom_recentered = []
    for L in [5, 10, 15]:
        for h in [240, 270, 300]:
            edge = next((r["Cmeasured"] for r in bnd_rows if r["L"] == L and r["h_N"] == h), 0)
            vals = [abs(x["dLc_recentered"]) for x in recenter_rows
                    if x["L"] == L and x["h_N"] == h and x["C_N_in"] <= 0.9 * edge]
            if vals:
                indom_recentered.append(max(vals))

    summary = {
        "dest": dest,
        "black": bp,
        "axes": axes_rows,
        "N_regularity_step_per_L": reg, "N_regularity_max": reg_max,
        "boundary_diff_Cgeom_minus_Cmeasured": {
            "n": len(diffs),
            "mean": round(sum(diffs) / len(diffs), 2) if diffs else None,
            "max_abs": round(max(abs(d) for d in diffs), 2) if diffs else None,
        },
        "n_star_broken": n_star_broken, "n_boundary_cells": len(bnd_rows),
        "recenter_blue_magenta": {
            "indomain_dLc_recentered_max": round(max(indom_recentered), 3) if indom_recentered else None,
            "note": "compare to B2A jalon (0,0)-centred in-domain max 1.919",
        },
    }

    # write
    _csv(out_dir / "na_axes.csv", axes_rows)
    _csv(out_dir / "na_recenter.csv", recenter_rows)
    _csv(out_dir / "na_boundary.csv", bnd_rows)
    (out_dir / "na_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_reading(out_dir / "na_reading.txt", summary)
    print(f"[na] N regularity max step/L={reg_max} | Cgeom-Cmeasured mean="
          f"{summary['boundary_diff_Cgeom_minus_Cmeasured']['mean']} "
          f"| star-broken cells={n_star_broken}/{len(bnd_rows)} "
          f"| recentered in-domain |δLc| max={summary['recenter_blue_magenta']['indomain_dLc_recentered_max']}")
    print(f"[na] wrote na_axes.csv, na_recenter.csv, na_boundary.csv, na_summary.json, na_reading.txt")
    return summary


def _csv(path, rows):
    if not rows:
        path.write_text("", encoding="utf-8"); return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)


def _write_reading(path, s):
    L = ["# Neutral axis + Cmax_N diagnostic — reading (4 questions)", "",
         f"dest : {s['dest']}",
         f"black: A2B1(0,0,0)={s['black']['a2b_black_lab']}  bkpt_tag_xyz={s['black']['bkpt_tag_xyz']}", ""]
    L.append("Q1 — which neutral axis? (theoretical A / device-A2B B / roundtrip C)")
    L.append(f"{'L':>4} {'B_a':>7} {'B_b':>7} {'C_a':>7} {'C_b':>7} {'C_C':>6} {'C_h':>6} {'|B-C|':>6}")
    L.append("-" * 52)
    for r in s["axes"]:
        L.append(f"{r['L']:>4} {r['B_a']:>7} {r['B_b']:>7} {r['C_a']:>7} {r['C_b']:>7} "
                 f"{r['C_C']:>6} {r['C_h']:>6} {r['dist_B_C']:>6}")
    L.append("")
    L.append(f"N(L) regularity (axis C step per L*): {s['N_regularity_step_per_L']}  max={s['N_regularity_max']}")
    L.append("A=(0,0) is NOT the real neutral if C_C>0. Axis C (roundtrip) is the one the -s")
    L.append("inversion produces → pipeline-relevant. Large |B-C| = device-neutral ≠ inversion-neutral.")
    L.append("")
    L.append("Q2 — blue/magenta shadow residual: centre artefact (a) or real B2A recoupling (b)?")
    rc = s["recenter_blue_magenta"]
    L.append(f"  in-domain |δLc| RECENTRED on N(L): max={rc['indomain_dLc_recentered_max']}")
    L.append(f"  ({rc['note']})")
    L.append("  → recentred < 1.919  ⇒ (a) partial centre artefact ; ≈1.9 ⇒ (b) real recoupling.")
    L.append("")
    L.append("Q3 — Cmax_N robust with non-convexity guard?")
    L.append(f"  star-shaped hypothesis broken (multi-crossing) in {s['n_star_broken']}/{s['n_boundary_cells']} cells (FLAGGED).")
    L.append("")
    L.append("Q4 — Cgeom (mesh ray from N) vs Cmeasured (roundtrip edge from N):")
    d = s["boundary_diff_Cgeom_minus_Cmeasured"]
    L.append(f"  n={d['n']}  mean(Cgeom-Cmeasured)={d['mean']}  max|diff|={d['max_abs']}")
    L.append("  Small, unbiased diff ⇒ Cgeom can drive the future abstract. Large/biased ⇒ refine.")
    path.write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Neutral axis + Cmax_N diagnostic (measure only).")
    ap.add_argument("--dest", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    a = ap.parse_args()
    run(a.dest, a.out_dir)


if __name__ == "__main__":
    main()
