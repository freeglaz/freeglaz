#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""BPC Phase 2 — validate the BPC abstract against the lcms oracle (bench only).

Implements nothing new here (the abstract engine lives in the production service
webapp.backend.services.luminance_priority.build_bpc_abstract); this script VALIDATES
it. Insertion = collink -s -ir -p <abstract_bpc> (NEVER -G). lcms = bench oracle only.

Anchor decision (ÉTAPE 0): dest black = PROFILE-DERIVED Lmin (Option 1), not lcms's
own black-point estimate. So we validate the FORM of the scaling (slope, decrease
with L, tapering), anchor-deviation-from-oracle EXPLICIT. Scope: one témoin, two
preparations (A raised blacks, B blacks-to-0 — the payoff case).

Run: uv run python scripts/bpc_phase2.py --dest <icc> --out-dir <dir>
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT)); sys.path.insert(0, str(_ROOT / "scripts"))

import tifffile

from lib.z9_client import devicelink, xicclu
import abstract_poc as poc
import convert_variants as cv
import bpc_phase1 as p1                                        # reuse lcms oracle + metrics
from webapp.backend.services import luminance_priority as lp
from webapp.backend.services.scan_delta import ciede2000

_PREPS = {
    "A_raised": "/Volumes/MARCEL-MEETS-CUNEGONDE/PHOTOS_RAWS_BACKUP/_tmp/_DSC2374_rec2020-lstar_vif-2.tif",
    "B_black0": "/Volumes/MARCEL-MEETS-CUNEGONDE/PHOTOS_RAWS_BACKUP/_tmp/_DSC2374_rec2020-lstar_vif-3_blackTo0.tif",
}
_BANDS = [("L<8", 0, 8), ("L<12", 0, 12), ("L<20", 0, 20), ("L<25", 0, 25), ("L<40", 0, 40)]


def _slope_r2(xs, ys):
    n = len(xs)
    if n < 3:
        return None, None
    mx = sum(xs) / n; my = sum(ys) / n; den = sum((x - mx) ** 2 for x in xs)
    if not den:
        return None, None
    sl = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / den
    ss = sum((ys[i] - (my + sl * (xs[i] - mx))) ** 2 for i in range(n)); tot = sum((y - my) ** 2 for y in ys)
    return round(sl, 4), (round(1 - ss / tot, 4) if tot else None)


def _validate_f_analytic(l_src, l_dst):
    """Continuity / monotonicity of the BPC L map f (analytic)."""
    f = lp._bpc_policy(l_src, l_dst)
    Ls = [i * 0.25 for i in range(0, 401)]
    ys = [f(L, 0, 0)[0] for L in Ls]
    slopes = [(ys[i] - ys[i - 1]) / 0.25 for i in range(1, len(Ls))]
    return {"monotone": all(s >= -1e-9 for s in slopes),
            "min_slope": round(min(slopes), 4), "max_slope": round(max(slopes), 4),
            "f_at_black": round(f(0, 0, 0)[0], 3), "f_at_white": round(f(100, 0, 0)[0], 3),
            "white_join_dev": round(abs(f(100, 0, 0)[0] - 100), 5)}


def run(dest: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bpc2_") as tmp:
        tmp = Path(tmp)
        # source = the A/B images' embedded profile (Rec2020 L*)
        src_icc = devicelink.extract_embedded_icc(_PREPS["A_raised"])
        src_n = tmp / "src.icc"; src_n.write_bytes(devicelink.normalize_icc_for_argyll(src_icc))
        dst_n = tmp / "dst.icc"; dst_n.write_bytes(devicelink.normalize_icc_for_argyll(dest.read_bytes()))

        # ÉTAPE A output (production engine) + the two black anchors
        bpc = lp.build_bpc_abstract(src_n, dst_n)
        l_src, l_dst = bpc["l_src"], bpc["l_dst"]
        lp.build_link.__doc__  # (no-op) build_link is reused below
        print(f"[bpc2] anchors: l_src={l_src} l_dst(Lmin)={l_dst}", flush=True)

        base = tmp / "base.icc"; poc.collink(["-v", "-qh", "-s", "-ir", str(src_n), str(dst_n)], base)
        bpc_link = tmp / "bpc.icc"; lp.build_link(src_n, dst_n, bpc_link, bpc["abstract"])

        def link_getter(link):
            return lambda dev: xicclu.run_xicclu(str(link), dev, direction="f")

        # ── B1. FORM vs oracle on the neutral ramp ──
        ramp = p1._neutral_ramp()
        mech = {
            "baseline_s_ir": link_getter(base),
            "bpc_ours": link_getter(bpc_link),
            "oracle_lcms_BPC": lambda dev: p1.lcms_map(src_n, dst_n, dev, bpc=True),
            "oracle_lcms_noBPC": lambda dev: p1.lcms_map(src_n, dst_n, dev, bpc=False),
        }
        neutral = {name: p1.measure_neutral(dst_n, src_n, ramp, g) for name, g in mech.items()}
        chroma_pts = p1._colored_points()
        chroma = {name: p1.measure_chroma(dst_n, src_n, chroma_pts, g) for name, g in mech.items()}

        analytic = _validate_f_analytic(l_src, l_dst)

        # ── B2/C. images A/B: tonal bands, payoff, chroma, neutral guard ──
        images = {}
        for pl, tif in _PREPS.items():
            a = cv._scale16(tifffile.imread(str(tif))); H, W = a.shape[:2]
            step = max(1, int(math.sqrt(W * H / 40000)))
            coords = [(x, y) for y in range(step // 2, H, step) for x in range(step // 2, W, step)]
            rgb = [(float(a[y, x, 0]), float(a[y, x, 1]), float(a[y, x, 2])) for (x, y) in coords]; del a
            lab_in = xicclu.run_xicclu(str(src_n), rgb, direction="f", intent="r", pcs="lab")
            n = min(len(rgb), len(lab_in)); rgb, lab_in = rgb[:n], lab_in[:n]
            Lin = [l[0] for l in lab_in]; Cin = [math.hypot(l[1], l[2]) for l in lab_in]
            out = {}
            for name, link in (("baseline", base), ("bpc", bpc_link)):
                dev = xicclu.run_xicclu(str(link), rgb, direction="f")
                lab_out = xicclu.run_xicclu(str(dst_n), dev, direction="f", intent="r", pcs="lab")
                tonal = {}
                for bn, lo, hi in _BANDS:
                    idx = [i for i in range(n) if lo <= Lin[i] < hi]
                    xs = [Lin[i] for i in idx]; ys = [lab_out[i][0] for i in idx]
                    sl, r2 = _slope_r2(xs, ys)
                    tonal[bn] = {"n": len(idx), "slope": sl, "R2": r2,
                                 "lift": round(sum(ys[k] - xs[k] for k in range(len(idx))) / len(idx), 3) if idx else None}
                # chroma by C band (C>10 shadows) — the measured desaturation cost
                ci = [i for i in range(n) if Cin[i] > 10 and Lin[i] < 40]
                dC = [math.hypot(lab_out[i][1], lab_out[i][2]) - Cin[i] for i in ci]
                dh = [abs((math.degrees(math.atan2(lab_out[i][2], lab_out[i][1]))
                           - math.degrees(math.atan2(lab_in[i][2], lab_in[i][1])) + 180) % 360 - 180) for i in ci]
                de = sorted(ciede2000(lab_in[i], lab_out[i]) for i in ci)
                out[name] = {"tonal": tonal,
                             "chroma_C>10_L<40": {"n": len(ci),
                                                  "dC_mean": round(sum(dC) / len(dC), 2) if dC else None,
                                                  "dh_P95": round(sorted(dh)[min(len(dh) - 1, int(.95 * (len(dh) - 1)))], 2) if dh else None,
                                                  "dE00_med": round(de[len(de) // 2], 2) if de else None}}
            # payoff (sub-floor Lin < Lmin): baseline clips, bpc lifts+separates
            sub = [i for i in range(n) if Lin[i] < l_dst]
            def sep(link):
                dev = xicclu.run_xicclu(str(link), [rgb[i] for i in sub], direction="f")
                lo = [x[0] for x in xicclu.run_xicclu(str(dst_n), dev, direction="f", intent="r", pcs="lab")]
                xs = [Lin[i] for i in sub]
                s, _ = _slope_r2(xs, lo); return s
            out["payoff_subfloor"] = {"frac_below_Lmin": round(len(sub) / n, 4),
                                      "baseline_slope": sep(base) if sub else None,
                                      "bpc_slope": sep(bpc_link) if sub else None}
            images[pl] = out

        # neutral guard (BPC-adapted): a,b stay ≈0 on neutral (L may change intentionally)
        with tempfile.NamedTemporaryFile(suffix=".icc") as f:
            f.write(bpc["abstract"]); f.flush()
            g = xicclu.run_xicclu(f.name, [(L, 0.0, 0.0) for L in range(2, 99, 8)], direction="f", pcs="lab")
        neutral_guard = {"max_abs_ab_on_neutral": round(max(max(abs(x[1]), abs(x[2])) for x in g), 3),
                         "note": "L changes by design (BPC); a,b must stay ≈0"}

        summary = {"dest": str(dest), "anchor_decision": "Option 1: profile-derived Lmin (dest)",
                   "l_src": l_src, "l_dst_Lmin": l_dst,
                   "anchor_deviation_from_oracle": f"oracle lcms anchors ~6.99 (cmsDetectBlackPoint); ours = Lmin {l_dst} (deeper)",
                   "f_analytic": analytic, "neutral_ramp": neutral, "chroma_near_black": chroma,
                   "images_A_B": images, "neutral_guard": neutral_guard,
                   "note": "lcms = bench oracle only. Scope: one témoin, two preparations."}
        (out_dir / "bpc_phase2.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        _reading(out_dir / "bpc_phase2_reading.txt", summary)
        print("[bpc2] wrote bpc_phase2.json + reading", flush=True)
        return summary


def _reading(path, s):
    L = ["# BPC Phase 2 — validation de l'abstract BPC vs oracle lcms", "",
         f"dest: {Path(s['dest']).name}  ancrage: {s['anchor_decision']}  (l_src={s['l_src']}, Lmin={s['l_dst_Lmin']})",
         f"écart d'ancrage vs oracle: {s['anchor_deviation_from_oracle']}", "",
         f"continuité f: monotone={s['f_analytic']['monotone']} min_slope={s['f_analytic']['min_slope']} "
         f"f(noir)={s['f_analytic']['f_at_black']} f(blanc)={s['f_analytic']['f_at_white']} "
         f"join_blanc_dev={s['f_analytic']['white_join_dev']}", "",
         "── B1. FORME du scaling (rampe neutre, device R=G=B → L_out) ──",
         f"  {'mécanisme':<20}{'endpoint':>9}{'sl L<12':>8}{'sl L<25':>8}{'ΔC nb':>7}{'Δh95 nb':>8}"]
    for name in ("baseline_s_ir", "bpc_ours", "oracle_lcms_BPC", "oracle_lcms_noBPC"):
        nu = s["neutral_ramp"][name]; c = s["chroma_near_black"][name]
        L.append(f"  {name:<20}{nu['black_endpoint_Lout']:>9}{str(nu['slope_L<12']):>8}{str(nu['slope_L<25']):>8}"
                 f"{c['dC_mean']:>7}{c['dh_P95']:>8}")
    L.append("  redistribution (device → L_out) baseline / bpc / oracle_BPC :")
    for name in ("baseline_s_ir", "bpc_ours", "oracle_lcms_BPC"):
        rr = s["neutral_ramp"][name]["redistribution_ramp"]
        L.append(f"    {name:<18} " + "  ".join(f"{k}:{v}" for k, v in list(rr.items())[:8]))
    for pl, im in s["images_A_B"].items():
        L += ["", f"── {pl} : tonal (slope / lift) baseline vs bpc ──"]
        for bn in ("L<8", "L<12", "L<20", "L<25"):
            b = im["baseline"]["tonal"][bn]; p = im["bpc"]["tonal"][bn]
            L.append(f"   {bn:<6} baseline slope={b['slope']} lift={b['lift']}  |  bpc slope={p['slope']} lift={p['lift']}  (n={b['n']})")
        po = im["payoff_subfloor"]
        L.append(f"   PAYOFF sous Lmin: {po['frac_below_Lmin']*100:.1f}% des px · slope baseline={po['baseline_slope']} → bpc={po['bpc_slope']}")
        cb = im["baseline"]["chroma_C>10_L<40"]; cp = im["bpc"]["chroma_C>10_L<40"]
        L.append(f"   CHROMA C>10 L<40: baseline ΔC={cb['dC_mean']} ΔE={cb['dE00_med']}  |  bpc ΔC={cp['dC_mean']} Δh95={cp['dh_P95']} ΔE={cp['dE00_med']}")
    L += ["", f"garde neutre (BPC-adaptée): max|a,b| sur neutre = {s['neutral_guard']['max_abs_ab_on_neutral']} "
          f"({s['neutral_guard']['note']})",
          "", "VERDICT (→ rapport) : BPC READY si forme ≈ oracle (à ancre près), monotone/continu,",
          "payoff réel sur B (sous-plancher récupéré+séparé), coût chroma mesuré et borné, neutre a,b≈0."]
    path.write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="BPC Phase 2 validation (bench only).")
    ap.add_argument("--dest", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    a = ap.parse_args()
    run(a.dest, a.out_dir)


if __name__ == "__main__":
    main()
