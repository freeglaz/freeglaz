#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Hue-drift VERDICT (A/B) — is the residual Δh a near-neutral artefact or a
real chromatic error? DIAGNOSIS ONLY: no correction, no tuning, no new grid.

The abstract's hue drift (jalon consolidation) is the angular variation of
Cmeasured × Cartesian CLUT interpolation. This routes the next step: h=atan2(b,a)
is ill-conditioned when C is small (tiny a/b error → big angle), so a big Δh at
low target chroma may be pure math instability (nothing to fix), not colour error.

Measures on the EXISTING POC abstract at g=33 and g=49 (Tanalytic = policy vs
Tlut = xicclu on the abstract):
  3.1 |Δh| (CIRCULAR) conditioned by TARGET chroma classes (Ctarget>2,5,10,20);
  3.2 non-singular Δa/Δb/ΔC/ΔE00 (at nodes AND between nodes);
  3.3 hotspot table (each peak: is it low-C near-neutral or truly chromatic?);
  3.4 |∂Cmeasured/∂h| vs Δh — ONLY on survivors (Ctarget≥10 & ΔE00 notable).
§4 non-regression baseline luminance re-read from the POC run (nothing changed).

Verdict is written in the report (multi-criteria, no single threshold).
Run: uv run python scripts/hue_verdict.py --out-dir <dir>
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT)); sys.path.insert(0, str(_ROOT / "scripts"))

import abstract_poc as poc                                   # noqa: E402
from lib.z9_client import xicclu                             # noqa: E402
from webapp.backend.services.scan_delta import ciede2000     # noqa: E402


def lch(L, a, b):
    return (L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360.0)


def circ_dh(h1, h2):
    return abs((h2 - h1 + 180.0) % 360.0 - 180.0)


def pct(sorted_vals, p):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, int(round(p / 100.0 * (len(sorted_vals) - 1))))
    return round(sorted_vals[i], 2)


def measure(abstract_path, policy, pts):
    """Per-point: analytic (policy) vs LUT (xicclu). Returns list of dicts."""
    lut = xicclu.run_xicclu(abstract_path, [p[:3] for p in pts], direction="f", pcs="lab")
    rows = []
    for i, (L, a, b) in enumerate(pts):
        La, aa, ba = policy(L, a, b)                          # analytic target
        _, Ct, ht = lch(La, aa, ba)                           # target chroma/hue
        Ll, al, bl = lut[i]
        _, Cl, hl = lch(Ll, al, bl)
        rows.append({
            "L": round(L, 1), "C_in": round(math.hypot(a, b), 1),
            "h_in": round(math.degrees(math.atan2(b, a)) % 360, 1),
            "C_target": round(Ct, 2), "h_target": round(ht, 1),
            "C_lut": round(Cl, 2), "h_lut": round(hl, 1),
            "da": round(al - aa, 3), "db": round(bl - ba, 3),
            "dC": round(Cl - Ct, 3), "dh": round(circ_dh(ht, hl), 3),
            "dE00": round(ciede2000((La, aa, ba), (Ll, al, bl)), 3),
        })
    return rows


def by_chroma_class(rows):
    classes = [("Ct>2", 2), ("Ct>5", 5), ("Ct>10", 10), ("Ct>20", 20)]
    out = {}
    for name, thr in classes:
        dh = sorted(r["dh"] for r in rows if r["C_target"] > thr)
        de = sorted(r["dE00"] for r in rows if r["C_target"] > thr)
        out[name] = {
            "n": len(dh),
            "dh_mean": round(sum(dh) / len(dh), 2) if dh else None,
            "dh_P95": pct(dh, 95), "dh_P99": pct(dh, 99), "dh_max": pct(dh, 100),
            "dE00_mean": round(sum(de) / len(de), 3) if de else None,
            "dE00_P95": pct(de, 95), "dE00_max": pct(de, 100),
        }
    return out


def cmeasured_grad_h(cdriver, L, h, dh=5.0):
    return abs(cdriver(L, (h + dh) % 360) - cdriver(L, (h - dh) % 360)) / (2 * dh)


def build_points():
    """Dense between-node grid (input Lab) spanning low→high chroma, dense in h."""
    pts = []
    for L in (5, 8, 10, 12, 15, 18, 20, 25, 30, 40, 50):
        for h in range(0, 360, 8):
            for C in (3, 7, 13, 20, 30, 40, 50, 60):
                pts.append((L, C * math.cos(math.radians(h)), C * math.sin(math.radians(h))))
    return pts


def node_points(g):
    """A set of EXACT CLUT nodes (Lab) for the 'at nodes' check."""
    pts = []
    idx = range(0, g, max(1, g // 8))
    for i in idx:
        L = i / (g - 1) * 100
        for j in idx:
            a = j / (g - 1) * 255 - 128
            for k in idx:
                b = k / (g - 1) * 255 - 128
                if 0 <= L <= 100 and math.hypot(a, b) <= 80:
                    pts.append((L, a, b))
    return pts


def baseline_luminance(poc_csv: Path):
    """Re-read POC full-chain data (nothing changed) → dLout/dCin, neutral ΔL."""
    rows = [r for r in csv.DictReader(open(poc_csv)) if r["cond"] == "POC"]
    Lref = {}
    for r in rows:
        if r["C"] == "0":
            Lref[r["L"]] = float(r["Lout"])
    ramps = {}
    for r in rows:
        ramps.setdefault((r["L"], r["h"]), []).append(r)
    slopes = []
    for (L, h), pts in ramps.items():
        if float(L) > 20:
            continue
        pts = sorted(pts, key=lambda p: float(p["C"]))
        for i in range(1, len(pts)):
            dC = float(pts[i]["C"]) - float(pts[i - 1]["C"])
            if dC:
                slopes.append((float(pts[i]["Lout"]) - float(pts[i - 1]["Lout"])) / dC)
    neutral = [float(r["Lout"]) - float(r["L"]) for r in rows if r["C"] == "0"]
    return {"shadow_dLoutdCin_mean": round(sum(slopes) / len(slopes), 4) if slopes else None,
            "neutral_dLout_mean": round(sum(neutral) / len(neutral), 3) if neutral else None}


def run(out_dir: Path, boundary_csv: Path, poc_csv: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    cdriver = poc.Cdriver(boundary_csv)
    policy = poc.make_policy(cdriver)
    pts = build_points()

    result = {"by_resolution": {}}
    all_hot = []
    for g in (33, 49):
        ab = out_dir / f"abstract_g{g}.icc"; ab.write_bytes(poc.build_abstract(policy, g))
        rows = measure(str(ab), policy, pts)
        node_rows = measure(str(ab), policy, node_points(g))
        # 3.4 survivors: chromatic (Ct>=10) AND notable colour error
        surv = [r for r in rows if r["C_target"] >= 10 and r["dE00"] >= 1.0]
        for r in surv:
            r["grad_Cmeas_h"] = round(cmeasured_grad_h(cdriver, r["L"], r["h_in"]), 3)
        # correlation grad vs dh on survivors
        corr = None
        if len(surv) >= 3:
            xs = [r["grad_Cmeas_h"] for r in surv]; ys = [r["dh"] for r in surv]
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            num = sum((xs[i] - mx) * (ys[i] - my) for i in range(len(xs)))
            dx = math.sqrt(sum((x - mx) ** 2 for x in xs)); dy = math.sqrt(sum((y - my) ** 2 for y in ys))
            corr = round(num / (dx * dy), 3) if dx > 0 and dy > 0 else None
        hot = sorted(rows, key=lambda r: r["dh"], reverse=True)[:15]
        result["by_resolution"][g] = {
            "dh_by_chroma_class": by_chroma_class(rows),
            "nodes_dE00_max": round(max((r["dE00"] for r in node_rows), default=0), 4),
            "nodes_dh_max": round(max((r["dh"] for r in node_rows), default=0), 3),
            "n_survivors_Ct10_dE1": len(surv),
            "survivors_dE00_max": round(max((r["dE00"] for r in surv), default=0), 3),
            "survivors_dh_max": round(max((r["dh"] for r in surv), default=0), 2),
            "corr_grad_vs_dh_survivors": corr,
        }
        for r in hot:
            r["g"] = g; all_hot.append(r)
        print(f"[verdict] g={g}: nodes ΔE00_max={result['by_resolution'][g]['nodes_dE00_max']} "
              f"| survivors(Ct≥10,ΔE≥1)={len(surv)} maxΔE00={result['by_resolution'][g]['survivors_dE00_max']} "
              f"corr(grad,Δh)={corr}")

    result["baseline_luminance"] = baseline_luminance(poc_csv)
    print(f"[verdict] baseline (non-regression): {result['baseline_luminance']}")

    (out_dir / "verdict_summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    with open(out_dir / "hotspots.csv", "w", newline="") as f:
        cols = ["g", "L", "C_in", "h_in", "C_target", "h_target", "C_lut", "h_lut",
                "da", "db", "dC", "dh", "dE00"]
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader(); w.writerows(all_hot)
    _reading(out_dir / "verdict_reading.txt", result)
    print("[verdict] wrote verdict_summary.json, hotspots.csv, verdict_reading.txt")
    return result


def _reading(path, r):
    L = ["# Hue-drift VERDICT (A/B) — is the residual Δh near-neutral or a real error?", ""]
    L.append("§4 non-regression baseline luminance (POC, unchanged): "
             f"{r['baseline_luminance']}  (target ≈ 0.028 / +0.35)")
    L.append("")
    for g in (33, 49):
        d = r["by_resolution"][g]
        L.append(f"── g={g} ──")
        L.append(f"  at-node ΔE00 max = {d['nodes_dE00_max']} (≈0 ⇒ LUT exact at nodes; error is interpolation)")
        L.append(f"  {'chroma class':<8} {'n':>6} {'Δh mean':>8} {'Δh P95':>7} {'Δh P99':>7} {'Δh max':>7} "
                 f"{'ΔE00 mean':>9} {'ΔE00 P95':>9} {'ΔE00 max':>9}")
        for name, c in d["dh_by_chroma_class"].items():
            L.append(f"  {name:<8} {c['n']:>6} {str(c['dh_mean']):>8} {str(c['dh_P95']):>7} "
                     f"{str(c['dh_P99']):>7} {str(c['dh_max']):>7} {str(c['dE00_mean']):>9} "
                     f"{str(c['dE00_P95']):>9} {str(c['dE00_max']):>9}")
        L.append(f"  survivors (Ct≥10 & ΔE00≥1): n={d['n_survivors_Ct10_dE1']} "
                 f"maxΔE00={d['survivors_dE00_max']} maxΔh={d['survivors_dh_max']} "
                 f"corr(|∂Cmeas/∂h|,Δh)={d['corr_grad_vs_dh_survivors']}")
        L.append("")
    L += ["VERDICT criteria (multi, no single threshold):",
          "- A (near-neutral artefact): big Δh only at low Ctarget, collapses as chroma rises,",
          "  ΔE00 stays small at Ct≥10, few/no survivors ⇒ g=33/49 suffices, no Cmeasured reshape.",
          "- B (real limit): significant Δh AND ΔE00 persist at Ct≥10-20, correlated with |∂Cmeas/∂h|",
          "  ⇒ a real error to correct (hue-continuous driver / finer grid) under luminance non-regression.",
          "(Verdict concluded in the .md report from these figures.)"]
    path.write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Hue-drift verdict A/B (diagnosis only).")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--boundary-csv", type=Path,
                    default=Path("/Users/vinz/Documents/PHOTO Ressources/HPZ9/bench_neutral_axis/na_boundary.csv"))
    ap.add_argument("--poc-csv", type=Path,
                    default=Path("/Users/vinz/Documents/PHOTO Ressources/HPZ9/bench_abstract_poc/poc_points.csv"))
    a = ap.parse_args()
    run(a.out_dir, a.boundary_csv, a.poc_csv)


if __name__ == "__main__":
    main()
