#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Voie B consolidation — gain ATTRIBUTION (2×2) + hue-drift DIAGNOSIS.

Measure/attribution ONLY. No tuning, no policy change, no hue correction, no
production wiring. Reuses the POC bricks (scripts/abstract_poc.py). Strict
vocabulary: Cgeom / Cmeasured / Csafe never merged.

VOLET 1 — factorial 2×2 (mode {-G,-s} × abstract {absent,present}) at constant
factors, so we can say what comes from the MODE vs from the ABSTRACT:
    A = -G -ir            (no abstract)          C = -s -ir            (no abstract)
    B = -G -ir -p abst    (double-maps, caveat)  D = -s -ir -p abst    (the POC)
  A→C = mode -s effect ; C→D = policy effect (constant mode). +ref -G -ilp.

VOLET 2 — hue-drift diagnosis (3 candidate causes, NOT prejudged):
  3.1 PIVOT  Tanalytic vs Tlut on a dense between-node grid → ΔL/ΔC/Δh/ΔE00
             encoding error (isolates ICC/CLUT representation, driver cancels).
  3.2 control-1  C'=k·C constant (no driver): does a SIMPLE compression still
                 drift? → generic CLUT/Lab problem.
      control-2  Cdriver flat in h (mean over h): does the drift vanish? →
                 the angular variation of Cmeasured is the cause.
  3.3 resolution sweep (g) + regional map of Δh_encoding.

Run: uv run python scripts/poc_attribution.py --dest <canson.icc> --out-dir <dir>
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

import abstract_poc as poc                                    # noqa: E402  (reuse POC bricks)
from lib.z9_client import devicelink, xicclu                  # noqa: E402
from webapp.backend.services.scan_delta import ciede2000      # noqa: E402

_SRC = _ROOT / "lib" / "z9_client" / "assets" / "LargeRGB-elle-V2-g18.icc"


def lch(L, a, b):
    return (L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360.0)


def hue_delta(h1, h2):
    return (h2 - h1 + 180.0) % 360.0 - 180.0


def dLout_dLin_neutral(rows):
    """Tonal separation: mean slope of Lout vs Lin on the C=0 ramp."""
    neu = sorted([(r["L"], r["Lout"]) for r in rows if r["C"] == 0])
    sl = []
    for i in range(1, len(neu)):
        d = neu[i][0] - neu[i - 1][0]
        if d:
            sl.append((neu[i][1] - neu[i - 1][1]) / d)
    return round(sum(sl) / len(sl), 3) if sl else None


# ─── measurement grid (same as POC Measure B) ───────────────────────────────
def build_grid():
    Ls, hs, Cs = [5, 10, 15, 20, 30, 50], list(range(0, 360, 30)), list(range(0, 61, 5))
    grid = []
    for L in Ls:
        for h in hs:
            for C in Cs:
                if C == 0 and h != hs[0]:
                    continue
                grid.append({"L": L, "C": C, "h": h,
                             "lab": (L, C * math.cos(math.radians(h)), C * math.sin(math.radians(h)))})
    return grid


# ─── VOLET 1 ─────────────────────────────────────────────────────────────────
def volet1(tmp, src_n, dst_n, abstract, grid):
    print("[attr] VOLET 1 — factorial 2×2 …")
    cells = {
        "A_G_ir":        ["-v", "-qh", "-G", "-ir", str(src_n), str(dst_n)],
        "C_s_ir":        ["-v", "-qh", "-s", "-ir", str(src_n), str(dst_n)],
        "D_s_ir_abst":   ["-v", "-qh", "-s", "-ir", "-p", str(abstract), str(src_n), str(dst_n)],
        "B_G_ir_abst":   ["-v", "-qh", "-G", "-ir", "-p", str(abstract), str(src_n), str(dst_n)],
        "ref_G_ilp":     ["-v", "-qh", "-G", "-ilp", str(src_n), str(dst_n)],
    }
    out = {}
    for name, args in cells.items():
        link = tmp / f"{name}.icc"
        try:
            poc.collink(args, link)
        except RuntimeError as e:
            print(f"[attr]   {name}: SKIP ({e})"); out[name] = {"error": str(e)}; continue
        _, lab = poc.measure_chain(link, src_n, dst_n, grid)
        m, rows = poc.metrics(grid, lab)
        m["dLout_dLin_neutral"] = dLout_dLin_neutral(rows)
        out[name] = m
        print(f"[attr]   {name}: shadow={m['shadow_dLoutdCin_mean']} neutralΔL={m['neutral_dLout_mean']} "
              f"dLout/dLin={m['dLout_dLin_neutral']}")
    return out


# ─── VOLET 2 ─────────────────────────────────────────────────────────────────
def dense_labs():
    """Between-node dense grid (offset from CLUT nodes; dense in h to catch drift)."""
    pts, meta = [], []
    for L in (8, 13, 18, 23, 33, 52):
        for h in range(5, 360, 10):
            for C in (7, 13, 22, 33, 47):
                a, b = C * math.cos(math.radians(h)), C * math.sin(math.radians(h))
                pts.append((L, a, b)); meta.append((L, C, h))
    return pts, meta


def tanalytic_vs_tlut(abstract_bytes_or_path, policy, pts):
    """ΔL/ΔC/Δh/ΔE00 between the analytic transform and the ICC restitution."""
    lut = xicclu.run_xicclu(abstract_bytes_or_path, pts, direction="f", pcs="lab")
    dL = dC = dh = de = 0.0
    dhs = []
    for i, (L, a, b) in enumerate(pts):
        La, aa, ba = policy(L, a, b)                          # analytic
        Ll, al, bl = lut[i]                                   # ICC restitution
        _, Ca, ha = lch(La, aa, ba)
        _, Cl, hl = lch(Ll, al, bl)
        dL = max(dL, abs(Ll - La)); dC = max(dC, abs(Cl - Ca))
        dd = abs(hue_delta(ha, hl)); dh = max(dh, dd); dhs.append(dd)
        de = max(de, ciede2000((La, aa, ba), (Ll, al, bl)))
    return {"max_dL": round(dL, 3), "max_dC": round(dC, 3), "max_dh": round(dh, 2),
            "mean_dh": round(sum(dhs) / len(dhs), 2), "max_dE00": round(de, 3)}, dhs


def volet2(tmp, cdriver, policy, pts, meta):
    print("[attr] VOLET 2 — hue-drift diagnosis …")
    res = {}

    # 3.1 PIVOT — Tanalytic vs Tlut (the real policy, g=33)
    ab33 = tmp / "poc_g33.icc"; ab33.write_bytes(poc.build_abstract(policy, 33))
    res["pivot_g33"], dhs33 = tanalytic_vs_tlut(ab33, policy, pts)
    print(f"[attr]   3.1 pivot g33: ΔE00_enc max={res['pivot_g33']['max_dE00']} "
          f"Δh max={res['pivot_g33']['max_dh']} mean={res['pivot_g33']['mean_dh']}")

    # 3.2 control-1 — C'=k·C constant (no driver)
    def policy_kC(L, a, b):
        k = 0.7
        return (L, a * k, b * k)
    abk = tmp / "ctrl_kC.icc"; abk.write_bytes(poc.build_abstract(policy_kC, 33))
    res["ctrl1_kC"], _ = tanalytic_vs_tlut(abk, policy_kC, pts)
    print(f"[attr]   3.2 ctrl-1 (C'=0.7C): Δh max={res['ctrl1_kC']['max_dh']} "
          f"mean={res['ctrl1_kC']['mean_dh']}  (≈0 ⇒ drift is NOT generic CLUT)")

    # 3.2 control-2 — Cdriver flat in h (mean over h per L)
    flat = {}
    for L in cdriver.Ls:
        vals = [cdriver.grid.get((L, h), 0.0) for h in cdriver.hs]
        vals = [v for v in vals if v > 0]
        flat[L] = (sum(vals) / len(vals)) if vals else 0.0
    def cdriver_flat(L, h):
        Ls = cdriver.Ls; L = max(Ls[0], min(Ls[-1], L))
        for i in range(1, len(Ls)):
            if Ls[i] >= L:
                L0, L1 = Ls[i - 1], Ls[i]; f = (L - L0) / (L1 - L0) if L1 != L0 else 0; break
        return (flat[L0] * (1 - f) + flat[L1] * f) * (1 - cdriver.margin)
    policy_flat = poc.make_policy(cdriver_flat)
    abf = tmp / "ctrl_flath.icc"; abf.write_bytes(poc.build_abstract(policy_flat, 33))
    res["ctrl2_flat_h"], _ = tanalytic_vs_tlut(abf, policy_flat, pts)
    print(f"[attr]   3.2 ctrl-2 (Cdriver flat in h): Δh max={res['ctrl2_flat_h']['max_dh']} "
          f"mean={res['ctrl2_flat_h']['mean_dh']}  (≈0 ⇒ cause = angular Cmeasured)")

    # 3.3 resolution dependence
    res["resolution"] = {}
    for g in (9, 17, 33, 49):
        ab = tmp / f"poc_g{g}.icc"; ab.write_bytes(poc.build_abstract(policy, g))
        r, _ = tanalytic_vs_tlut(ab, policy, pts)
        res["resolution"][g] = {"max_dh": r["max_dh"], "mean_dh": r["mean_dh"], "max_dE00": r["max_dE00"]}
        print(f"[attr]   3.3 g={g}: Δh max={r['max_dh']} mean={r['mean_dh']} ΔE00={r['max_dE00']}")

    # 3.3 regional map of per-point Δh (g33 pivot)
    rows = []
    for i, (L, C, h) in enumerate(meta):
        rows.append({"L": L, "C": C, "h": h, "dh_encoding": round(dhs33[i], 2)})
    return res, rows


def run(dest: Path, out_dir: Path, boundary_csv: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="poc_attr_") as tmp:
        tmp = Path(tmp)
        src_n = tmp / "src.icc"; src_n.write_bytes(devicelink.normalize_icc_for_argyll(_SRC.read_bytes()))
        dst_n = tmp / "dst.icc"; dst_n.write_bytes(devicelink.normalize_icc_for_argyll(dest.read_bytes()))
        cdriver = poc.Cdriver(boundary_csv)
        policy = poc.make_policy(cdriver)
        abstract = tmp / "poc_g33.icc"; abstract.write_bytes(poc.build_abstract(policy, 33))
        grid = build_grid()

        v1 = volet1(tmp, src_n, dst_n, abstract, grid)
        pts, meta = dense_labs()
        v2, hue_rows = volet2(tmp, cdriver, policy, pts, meta)

        # attribution deltas (constant factors)
        def g(name, key):
            return v1.get(name, {}).get(key)
        attribution = {}
        if g("A_G_ir", "neutral_dLout_mean") is not None and g("C_s_ir", "neutral_dLout_mean") is not None:
            attribution["mode_effect_neutralΔL_AtoC"] = round(
                g("C_s_ir", "neutral_dLout_mean") - g("A_G_ir", "neutral_dLout_mean"), 3)
            attribution["mode_effect_shadow_AtoC"] = round(
                g("C_s_ir", "shadow_dLoutdCin_mean") - g("A_G_ir", "shadow_dLoutdCin_mean"), 4)
        if g("C_s_ir", "neutral_dLout_mean") is not None and g("D_s_ir_abst", "neutral_dLout_mean") is not None:
            attribution["policy_effect_neutralΔL_CtoD"] = round(
                g("D_s_ir_abst", "neutral_dLout_mean") - g("C_s_ir", "neutral_dLout_mean"), 3)
            attribution["policy_effect_shadow_CtoD"] = round(
                g("D_s_ir_abst", "shadow_dLoutdCin_mean") - g("C_s_ir", "shadow_dLoutdCin_mean"), 4)

        summary = {"dest": str(dest), "matrix": v1, "attribution": attribution,
                   "hue_diag": v2,
                   "note_B": "B (-G -ir -p) double-maps: abstract PCS compression + -G gamut mapping stacked; interpret with caution."}
        (out_dir / "attribution_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        with open(out_dir / "hue_encoding_map.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["L", "C", "h", "dh_encoding"]); w.writeheader(); w.writerows(hue_rows)
        _reading(out_dir / "attribution_reading.txt", summary)
        print("[attr] wrote attribution_summary.json, hue_encoding_map.csv, attribution_reading.txt")
        return summary


def _reading(path, s):
    m, a, h = s["matrix"], s["attribution"], s["hue_diag"]
    L = ["# Voie B consolidation — attribution + hue-drift diagnosis", "",
         f"dest: {s['dest']}", "",
         "== VOLET 1 — factorial 2×2 (mode × abstract) ==",
         f"{'cell':<14} {'shadow dLout/dCin':>18} {'neutral ΔL':>12} {'dLout/dLin':>11}", "-" * 58]
    labels = {"A_G_ir": "A -G -ir", "C_s_ir": "C -s -ir", "D_s_ir_abst": "D -s -ir +abst",
              "B_G_ir_abst": "B -G +abst*", "ref_G_ilp": "ref -G -ilp"}
    for k, lab in labels.items():
        c = m.get(k, {})
        if "error" in c:
            L.append(f"{lab:<14} {'(skip: '+c['error'][:30]+')':>42}"); continue
        L.append(f"{lab:<14} {str(c.get('shadow_dLoutdCin_mean')):>18} "
                 f"{str(c.get('neutral_dLout_mean')):>12} {str(c.get('dLout_dLin_neutral')):>11}")
    L += ["", "* B double-maps (abstract PCS + -G gamut mapping) — interpret with caution.", "",
          "Attribution (constant factors):",
          f"  MODE (A→C): neutral ΔL change = {a.get('mode_effect_neutralΔL_AtoC')} ; "
          f"shadow change = {a.get('mode_effect_shadow_AtoC')}",
          f"  POLICY (C→D): neutral ΔL change = {a.get('policy_effect_neutralΔL_CtoD')} ; "
          f"shadow change = {a.get('policy_effect_shadow_CtoD')}",
          "  → what comes from -s vs from the abstract, on neutral AND shadows.", "",
          "== VOLET 2 — hue drift: encoding / driver / resolution ==",
          f"3.1 PIVOT Tanalytic vs Tlut (g33): ΔE00_enc max={h['pivot_g33']['max_dE00']} "
          f"Δh max={h['pivot_g33']['max_dh']} mean={h['pivot_g33']['mean_dh']}",
          f"3.2 ctrl-1 C'=0.7C (no driver):    Δh max={h['ctrl1_kC']['max_dh']} mean={h['ctrl1_kC']['mean_dh']}",
          f"3.2 ctrl-2 Cdriver flat in h:      Δh max={h['ctrl2_flat_h']['max_dh']} mean={h['ctrl2_flat_h']['mean_dh']}",
          "3.3 resolution (Δh mean by grid g):"]
    for g, r in h["resolution"].items():
        L.append(f"      g={g:<3} Δh max={r['max_dh']:>6} mean={r['mean_dh']:>6} ΔE00={r['max_dE00']}")
    L += ["", "READ:",
          "- ctrl-1 ≈0 ⇒ drift is NOT generic CLUT (a uniform compression is faithful).",
          "- ctrl-2 ≈0 ⇒ cause = angular variation of Cmeasured (driver) × linear Lab interp.",
          "- Δh shrinks with g ⇒ resolution-fixable (finer grid) ; ΔE00_enc quantifies the ICC error.",
          "- (Abney/Lab-blue non-uniformity is a DESIGN issue, out of scope — not this drift.)"]
    path.write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Voie B attribution + hue-drift diagnosis (measure only).")
    ap.add_argument("--dest", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--boundary-csv", type=Path,
                    default=Path("/Users/vinz/Documents/PHOTO Ressources/HPZ9/bench_neutral_axis/na_boundary.csv"))
    a = ap.parse_args()
    run(a.dest, a.out_dir, a.boundary_csv)


if __name__ == "__main__":
    main()
