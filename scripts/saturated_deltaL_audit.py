#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""ΔL / ΔC / Δh / ΔE00 (source→output) on SATURATED colours, ALL L bands.

Fills a measurement gap: prior benches quantified ΔC/Δh (shadows) and neutral-axis
tonal, but NEVER ΔL on saturated colours. Vinz (calibrated 80 cd/m² screen) reports
that what bothers him in -G ir/ila/ilp is a LUMINANCE change, visible on flashy
blues too — not the hue rotation we centred on. If ΔL dominates on saturates, Q3
of the audit ("voie B = hue-preserving chroma compression") is misframed and the
custom strategy's UI label depends on it.

Measure only. Reuses the ALREADY-BUILT links (bench_composition_vif/_work) — no
collink. 4 strategies share the SAME source per prep → direct pairing (same pixels).
mechanistically established (to confront, not presume): the voie-B abstract is
RADIAL at constant L (policy (L, a·s, b·s) — L untouched, h preserved); -G intents
gamut-map in appearance space where pulling an OOG colour in-gamut can move L, C and
h jointly. NOT yet measured on saturates → this jalon.

Run: uv run python scripts/saturated_deltaL_audit.py --out-dir <dir>
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT)); sys.path.insert(0, str(_ROOT / "scripts"))

import tifffile

from lib.z9_client import xicclu                              # noqa: E402
import convert_variants as cv                                 # noqa: E402

WORK = _ROOT.parent / "bench_composition_vif" / "_work"       # reuse built links
PREPS = {
    "A_raised": "/Volumes/MARCEL-MEETS-CUNEGONDE/PHOTOS_RAWS_BACKUP/_tmp/_DSC2374_rec2020-lstar_vif-2.tif",
    "B_black0": "/Volumes/MARCEL-MEETS-CUNEGONDE/PHOTOS_RAWS_BACKUP/_tmp/_DSC2374_rec2020-lstar_vif-3_blackTo0.tif",
}
# strategy label -> link file suffix (voie B = s_ir_abs)
STRATS = [("G_ir", "G_ir"), ("G_ila", "G_ila"), ("G_ilp", "G_ilp"),
          ("voieB", "s_ir_abs"), ("s_ir_bare", "s_ir")]
C_BINS = [(20, 40), (40, 60), (60, 80), (80, 999)]
L_BINS = [("L<12", 0, 12), ("12-25", 12, 25), ("25-50", 25, 50), ("50-75", 50, 75), (">75", 75, 101)]


def ciede2000_comp(Lab1, Lab2):
    """Return (dE00, TL, TC, TH) where TL/TC/TH are the squared normalised
    lightness/chroma/hue terms (k=1). Fraction from L = TL/(TL+TC+TH)."""
    L1, a1, b1 = Lab1; L2, a2, b2 = Lab2
    C1 = math.hypot(a1, b1); C2 = math.hypot(a2, b2); Cb = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7))) if Cb > 0 else 0.0
    a1p = (1 + G) * a1; a2p = (1 + G) * a2
    C1p = math.hypot(a1p, b1); C2p = math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360; h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dLp = L2 - L1; dCp = C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360
    else:
        dhp = h2p - h1p + 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp) / 2)
    Lbp = (L1 + L2) / 2; Cbp = (C1p + C2p) / 2
    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    elif (h1p + h2p) < 360:
        hbp = (h1p + h2p + 360) / 2
    else:
        hbp = (h1p + h2p - 360) / 2
    T = (1 - 0.17 * math.cos(math.radians(hbp - 30)) + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6)) - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dth = 30 * math.exp(-(((hbp - 275) / 25) ** 2))
    RC = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7)) if Cbp > 0 else 0.0
    SL = 1 + (0.015 * (Lbp - 50) ** 2) / math.sqrt(20 + (Lbp - 50) ** 2)
    SC = 1 + 0.045 * Cbp; SH = 1 + 0.015 * Cbp * T
    RT = -math.sin(math.radians(2 * dth)) * RC
    tL = (dLp / SL) ** 2; tC = (dCp / SC) ** 2; tH = (dHp / SH) ** 2
    dE = math.sqrt(max(0.0, tL + tC + tH + RT * (dCp / SC) * (dHp / SH)))
    return dE, tL, tC, tH


def _pct(x, q):
    return round(x[min(len(x) - 1, int(q * (len(x) - 1)))], 2) if x else None


def _stats(vals, absolute=False):
    if not vals:
        return None
    v = sorted(abs(x) for x in vals) if absolute else sorted(vals)
    return {"n": len(vals), "mean": round(sum(vals) / len(vals), 2),
            "P50": _pct(v, .5), "P95": _pct(v, .95), "P99": _pct(v, .99)}


def measure_prep(pl, tif, out_dir):
    a = cv._scale16(tifffile.imread(str(tif))); H, W = a.shape[:2]
    step = max(1, int(math.sqrt(W * H / 60000)))
    coords = [(x, y) for y in range(step // 2, H, step) for x in range(step // 2, W, step)]
    rgb = [(float(a[y, x, 0]), float(a[y, x, 1]), float(a[y, x, 2])) for (x, y) in coords]; del a
    src_n = WORK / f"src_{pl}.icc"; dst_n = WORK / "dest.icc"
    lab_in = xicclu.run_xicclu(str(src_n), rgb, direction="f", intent="r", pcs="lab")
    n = min(len(rgb), len(lab_in)); rgb, lab_in = rgb[:n], lab_in[:n]
    Lin = [l[0] for l in lab_in]; Cin = [math.hypot(l[1], l[2]) for l in lab_in]
    hin = [math.degrees(math.atan2(l[2], l[1])) % 360 for l in lab_in]

    per = {}
    for label, suf in STRATS:
        link = WORK / f"link_{pl}_{suf}.icc"
        dev = xicclu.run_xicclu(str(link), rgb, direction="f")
        lab_out = xicclu.run_xicclu(str(dst_n), dev, direction="f", intent="r", pcs="lab")
        m = min(n, len(lab_out))
        dL = []; dC = []; dh = []; dE = []; tL = []; tC = []; tH = []
        for i in range(m):
            Li, ai, bi = lab_in[i]; Lo, ao, bo = lab_out[i]
            dL.append(Lo - Li); dC.append(math.hypot(ao, bo) - Cin[i])
            dh.append(abs((math.degrees(math.atan2(bo, ao)) - hin[i] + 180) % 360 - 180))
            e, l_, c_, h_ = ciede2000_comp((Li, ai, bi), (Lo, ao, bo))
            dE.append(e); tL.append(l_); tC.append(c_); tH.append(h_)
        per[label] = {"dL": dL, "dC": dC, "dh": dh, "dE": dE, "tL": tL, "tC": tC, "tH": tH}
    return {"n": n, "Lin": Lin, "Cin": Cin, "hin": hin, "per": per}


def analyse(pl, d):
    Lin, Cin, hin, per = d["Lin"], d["Cin"], d["hin"], d["per"]
    n = d["n"]
    # A. global on saturated subset (C>40) : ΔL/ΔC/Δh/ΔE00 median/P95/P99
    satC = 40
    sat_idx = [i for i in range(n) if Cin[i] > satC]
    A = {"n_C>40": len(sat_idx), "strategies": {}}
    for label, _ in STRATS:
        p = per[label]
        A["strategies"][label] = {
            "dL_signed": _stats([p["dL"][i] for i in sat_idx]),
            "dL_abs": _stats([p["dL"][i] for i in sat_idx], absolute=True),
            "dC_signed": _stats([p["dC"][i] for i in sat_idx]),
            "dh": _stats([p["dh"][i] for i in sat_idx], absolute=True),
            "dE00": _stats([p["dE"][i] for i in sat_idx])}
    # B. C×L cross table: mean ΔL (signed) per cell per strategy (the core)
    B = {}
    for (clo, chi) in C_BINS:
        for (ln, llo, lhi) in L_BINS:
            idx = [i for i in range(n) if clo <= Cin[i] < chi and llo <= Lin[i] < lhi]
            if len(idx) < 20:
                continue
            cell = {"n": len(idx)}
            for label, _ in STRATS:
                p = per[label]
                cell[label] = {"dL": round(sum(p["dL"][i] for i in idx) / len(idx), 2),
                               "dC": round(sum(p["dC"][i] for i in idx) / len(idx), 2),
                               "dh": round(sum(p["dh"][i] for i in idx) / len(idx), 2),
                               "dE": round(sum(p["dE"][i] for i in idx) / len(idx), 2)}
            B[f"C{clo}-{chi if chi < 999 else '+'}_{ln}"] = cell
    # C. ΔE00 decomposition (fraction L/C/H) per region: saturated-shadow vs saturated-bright
    C = {}
    for rn, cond in (("sat_C>40_shadow_L<25", lambda i: Cin[i] > 40 and Lin[i] < 25),
                     ("sat_C>40_bright_L>50", lambda i: Cin[i] > 40 and Lin[i] > 50),
                     ("sat_C>40_all", lambda i: Cin[i] > 40)):
        idx = [i for i in range(n) if cond(i)]
        if len(idx) < 20:
            continue
        reg = {"n": len(idx)}
        for label, _ in STRATS:
            p = per[label]
            sL = sum(p["tL"][i] for i in idx); sC = sum(p["tC"][i] for i in idx); sH = sum(p["tH"][i] for i in idx)
            tot = sL + sC + sH or 1.0
            reg[label] = {"pctL": round(100 * sL / tot, 1), "pctC": round(100 * sC / tot, 1),
                          "pctH": round(100 * sH / tot, 1)}
        C[rn] = reg
    # D. blue focus: hue histogram of high-C pixels → find blue cluster from DATA
    hist = {}
    for i in range(n):
        if Cin[i] > 40:
            hist[int(hin[i] // 30) * 30] = hist.get(int(hin[i] // 30) * 30, 0) + 1
    # blue in Lab ≈ 250-310°; pick the populated bins in that window from data
    blue_bins = sorted(b for b in hist if 240 <= b <= 300 and hist[b] >= 20)
    blue_lo = min(blue_bins) if blue_bins else 250
    blue_hi = (max(blue_bins) + 30) if blue_bins else 300
    bidx = [i for i in range(n) if Cin[i] > 40 and blue_lo <= hin[i] < blue_hi]
    D = {"hue_hist_C>40_per30deg": dict(sorted(hist.items())),
         "blue_region_h": [blue_lo, blue_hi], "n_blue": len(bidx), "strategies": {}}
    for label, _ in STRATS:
        p = per[label]
        D["strategies"][label] = {
            "dL_signed": _stats([p["dL"][i] for i in bidx]),
            "dC_signed": _stats([p["dC"][i] for i in bidx]),
            "dh": _stats([p["dh"][i] for i in bidx], absolute=True),
            "dE00": _stats([p["dE"][i] for i in bidx])}
    return {"A_global_sat": A, "B_CxL_meanDL": B, "C_dE_decomposition": C, "D_blue_focus": D}


def run(out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    if not (WORK / "dest.icc").exists():
        raise SystemExit(f"reused links not found in {WORK} — run composition_vif_audit first")
    results = {}
    for pl, tif in PREPS.items():
        print(f"[sat] prep {pl} …", flush=True)
        d = measure_prep(pl, tif, out_dir)
        results[pl] = analyse(pl, d)
        print(f"[sat]   done {pl}", flush=True)
    summary = {"reused_links_from": str(WORK), "strategies": [s[0] for s in STRATS],
               "vinz_conditions": "calibrated screen 80 cd/m², matched to print", "results": results,
               "note": "measure ΔL on saturates; same source per prep → direct pairing; no causal bench."}
    (out_dir / "saturated_deltaL.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _reading(out_dir / "saturated_deltaL_reading.txt", summary)
    print("[sat] wrote saturated_deltaL.json + reading", flush=True)
    return summary


def _reading(path, s):
    order = ["G_ir", "G_ila", "G_ilp", "voieB", "s_ir_bare"]
    L = ["# ΔL/ΔC/Δh/ΔE00 sur les couleurs SATURÉES, par stratégie", "",
         f"liens réutilisés: {Path(s['reused_links_from']).parent.name}/_work · "
         f"conditions vinz: {s['vinz_conditions']}", ""]
    for pl, r in s["results"].items():
        A = r["A_global_sat"]
        L += [f"══════ {pl} — saturés C>40 (n={A['n_C>40']}) ══════",
              "  A. écarts source→sortie (signé pour ΔL/ΔC ; |.| pour Δh) :",
              f"     {'strat':<11}{'ΔL mean':>9}{'ΔL P95|':>9}{'ΔC mean':>9}{'Δh P95':>8}{'ΔE00 P50':>9}{'ΔE00 P95':>9}"]
        for lab in order:
            st = A["strategies"][lab]
            L.append(f"     {lab:<11}{st['dL_signed']['mean']:>9}{st['dL_abs']['P95']:>9}"
                     f"{st['dC_signed']['mean']:>9}{st['dh']['P95']:>8}{st['dE00']['P50']:>9}{st['dE00']['P95']:>9}")
        L.append("  B. ΔL moyen (signé) par bande C×L — OÙ la luminance dérive :")
        L.append(f"     {'cellule':<16}" + "".join(f"{lab:>9}" for lab in order))
        for cell, cd in r["B_CxL_meanDL"].items():
            L.append(f"     {cell:<16}" + "".join(f"{str(cd[lab]['dL']):>9}" for lab in order) + f"   (n={cd['n']})")
        L.append("  C. décomposition ΔE00 (%L / %C / %H) par région :")
        for rn, reg in r["C_dE_decomposition"].items():
            L.append(f"     {rn} (n={reg['n']}):")
            for lab in order:
                x = reg[lab]; L.append(f"        {lab:<11} L={x['pctL']}%  C={x['pctC']}%  H={x['pctH']}%")
        d = r["D_blue_focus"]
        L.append(f"  D. bleus saturés — région h={d['blue_region_h']} (depuis données), n={d['n_blue']} :")
        L.append(f"     {'strat':<11}{'ΔL mean':>9}{'ΔL P95|':>9}{'ΔC mean':>9}{'Δh P95':>8}{'ΔE00 P50':>9}")
        for lab in order:
            st = d["strategies"][lab]
            if st["dL_signed"]:
                L.append(f"     {lab:<11}{st['dL_signed']['mean']:>9}{st['dL_signed']['P95']:>9}"
                         f"{st['dC_signed']['mean']:>9}{st['dh']['P95']:>8}{st['dE00']['P50']:>9}")
        L.append("")
    L += ["AIGUILLAGE (rapport) : STRAT-A ΔL domine & voieB≈0 → Q3 mal orienté (vocab luminance).",
          "  STRAT-B écarts en chroma/teinte, ΔL comparable → Q3 tient. STRAT-C mixte/régional.",
          "  Appariement direct (même source par prep). Ne pas comparer A vs B au pixel."]
    path.write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="ΔL/ΔC/Δh on saturated colours by strategy (measure).")
    ap.add_argument("--out-dir", required=True, type=Path)
    a = ap.parse_args()
    run(a.out_dir)


if __name__ == "__main__":
    main()
