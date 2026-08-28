#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Argyll NATIVE audit — shadows / black-point / gamut-mapping intents (MEASURE).

Order enforced: documentation → binary → exact command → measure → interpretation.
NO custom, NO wiring, NO tuning. Comparators are witnesses, never targets.

Bench (change ONE dimension at a time), same src/dest/images/sampling/metrics:
  NATIVES : -G -ir · -G -ila · -G -ip · -G -ilp
  WITNESSES : -s -ir (bare colorimetric) · -s -ir + abstract voie B (τ=1)
Images A (raised blacks) and B (blacks-at-0) from the deep-shadow jalon.

STRICT distinction (never conflated):
  A. black ENDPOINT     : source black → destination black (L_out of device 0)
  B. REDISTRIBUTION     : shape Lout(Lin) ABOVE the black point
  C. SEPARATION         : dLout/dLin (+ R², prevalence of low local slope)
  D. GAMUT/CHROMA       : ΔC / Δh / ΔE00 (percentiles), shadows C>10

Run: uv run python scripts/argyll_native_audit.py --out-dir <dir>
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

from lib.z9_client import devicelink, xicclu                  # noqa: E402
import abstract_poc as poc                                    # noqa: E402
import convert_variants as cv                                 # noqa: E402
from webapp.backend.services.scan_delta import ciede2000      # noqa: E402

DEST = "/Users/vinz/Library/ColorSync/Profiles/hpz9_canson-photolustre-rc_ge-on.icc"
PREPS = {
    "A_raised": "/Volumes/MARCEL-MEETS-CUNEGONDE/PHOTOS_RAWS_BACKUP/_tmp/_DSC2374_rec2020-lstar_vif-2.tif",
    "B_black0": "/Volumes/MARCEL-MEETS-CUNEGONDE/PHOTOS_RAWS_BACKUP/_tmp/_DSC2374_rec2020-lstar_vif-3_blackTo0.tif",
}
# variant -> (mode, collink args builder). NATIVE = -G -i<intent> ; WITNESS = -s.
NATIVE_INTENTS = ["ir", "ila", "ip", "ilp"]
BANDS = [("L<8", 0, 8), ("L<12", 0, 12), ("L<20", 0, 20), ("L<25", 0, 25), ("L<40", 0, 40)]


def build_links(src_n, dst_n, dest_orig, work, out_dir):
    links = {}
    for it in NATIVE_INTENTS:
        link = work / f"link_G_{it}.icc"
        poc.collink(["-v", "-qh", "-G", f"-{it}", str(src_n), str(dst_n)], link)
        links[f"G_{it}"] = link
        print(f"[aud]   -G -{it}", flush=True)
    # witness 1: -s -ir bare (pure colorimetric)
    link = work / "link_s_ir_bare.icc"
    poc.collink(["-v", "-qh", "-s", "-ir", str(src_n), str(dst_n)], link)
    links["s_ir_bare"] = link; print("[aud]   -s -ir (bare)", flush=True)
    # witness 2: -s -ir + abstract voie B τ=1
    policy, _ = cv.build_abstract_at(dest_orig, 1.0, out_dir)
    ab = work / "ab_tau1.icc"; ab.write_bytes(poc.build_abstract(policy, 33))
    link = work / "link_s_ir_voieB.icc"
    poc.collink(["-v", "-qh", "-s", "-ir", "-p", str(ab), str(src_n), str(dst_n)], link)
    links["s_ir_voieB"] = link; print("[aud]   -s -ir + abstract voie B (τ=1)", flush=True)
    return links


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


def _local_slopes(Lin, Lout, bin_w=2.0, lo=0.0, hi=40.0):
    bins = {}
    for i in range(len(Lin)):
        if lo <= Lin[i] < hi:
            bins.setdefault(int((Lin[i] - lo) / bin_w), []).append(Lout[i])
    cs = sorted(bins); means = {b: sum(bins[b]) / len(bins[b]) for b in cs}
    sob = {}
    for j, b in enumerate(cs):
        if j == 0 and len(cs) > 1:
            sob[b] = (means[cs[1]] - means[b]) / ((cs[1] - b) * bin_w)
        elif j == len(cs) - 1:
            sob[b] = (means[b] - means[cs[j - 1]]) / ((b - cs[j - 1]) * bin_w)
        else:
            sob[b] = (means[cs[j + 1]] - means[cs[j - 1]]) / ((cs[j + 1] - cs[j - 1]) * bin_w)
    return [sob[int((Lin[i] - lo) / bin_w)] for i in range(len(Lin))
            if lo <= Lin[i] < hi and int((Lin[i] - lo) / bin_w) in sob]


def measure_variant(link, dst_n, rgb, lab_in, Lin, Cin, black_dev):
    dev = xicclu.run_xicclu(str(link), rgb, direction="f")
    lab_out = xicclu.run_xicclu(str(dst_n), dev, direction="f", intent="r", pcs="lab")
    n = min(len(dev), len(lab_in))
    # A. black ENDPOINT: source device black → L_out
    bdev = xicclu.run_xicclu(str(link), [black_dev], direction="f")
    blab = xicclu.run_xicclu(str(dst_n), bdev, direction="f", intent="r", pcs="lab")
    endpoint = round(blab[0][0], 3)
    # C. separation per band + prevalence
    tonal = {}
    for bn, lo, hi in BANDS:
        idx = [i for i in range(n) if lo <= Lin[i] < hi]
        xs = [Lin[i] for i in idx]; ys = [lab_out[i][0] for i in idx]
        sl, r2 = _slope_r2(xs, ys)
        lift = round(sum(ys[k] - xs[k] for k in range(len(idx))) / len(idx), 3) if idx else None
        tonal[bn] = {"n": len(idx), "slope": sl, "R2": r2, "mean_Lout_minus_Lin": lift}
    loc = _local_slopes([Lin[i] for i in range(n)], [lab_out[i][0] for i in range(n)])
    m = len(loc) or 1
    prev = {f"<{t}": round(sum(1 for x in loc if x < t) / m, 4) for t in (0.9, 0.8, 0.7)}
    # B. redistribution shape on a neutral device ramp (R=G=B)
    ramp_dev = [(v, v, v) for v in (0.0, 0.02, 0.05, 0.10, 0.15, 0.20, 0.30)]
    rl = xicclu.run_xicclu(str(link), ramp_dev, direction="f")
    rlab = xicclu.run_xicclu(str(dst_n), rl, direction="f", intent="r", pcs="lab")
    shape = {f"{d[0]:.2f}": round(rlab[i][0], 2) for i, d in enumerate(ramp_dev)}
    # D. chroma in shadows C>10, L<40: ΔC/Δh/ΔE00 percentiles (out vs in)
    ci = [i for i in range(n) if Cin[i] > 10 and Lin[i] < 40]
    des, dcs, dhs = [], [], []
    for i in ci:
        Li, ai, bi = lab_in[i]; Lo, ao, bo = lab_out[i]
        des.append(ciede2000((Li, ai, bi), (Lo, ao, bo)))
        dcs.append(math.hypot(ao, bo) - math.hypot(ai, bi))
        dhs.append(abs((math.degrees(math.atan2(bo, ao)) - math.degrees(math.atan2(bi, ai)) + 180) % 360 - 180))
    des.sort(); dhs.sort()
    def p(x, q): return round(x[min(len(x) - 1, int(q * (len(x) - 1)))], 2) if x else None
    chroma = {"n": len(ci), "dE00_med": p(des, .5), "dE00_P95": p(des, .95),
              "dh_P95": p(dhs, .95), "dC_mean": round(sum(dcs) / len(dcs), 2) if dcs else None}
    return {"black_endpoint_Lout": endpoint, "tonal": tonal, "prevalence_localslope": prev,
            "redistribution_shape_ramp": shape, "chroma_shadow": chroma}


def run(out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "_work"; work.mkdir(exist_ok=True)
    dest_orig = Path(DEST)
    dst_n = work / "dest.icc"; dst_n.write_bytes(devicelink.normalize_icc_for_argyll(dest_orig.read_bytes()))
    # dest black point (neutral) for "distance to black point"
    bd = xicclu.run_xicclu(str(dst_n), [(0, 0, 0)], direction="b", intent="r", pcs="lab")
    Lmin = round(xicclu.run_xicclu(str(dst_n), bd, direction="f", intent="r", pcs="lab")[0][0], 3)
    black_dev = (0.0, 0.0, 0.0)                                 # darkest source device

    results = {}
    for pl, tif in PREPS.items():
        print(f"[aud] prep {pl} …", flush=True)
        src_icc = devicelink.extract_embedded_icc(tif)
        src_n = work / f"src_{pl}.icc"; src_n.write_bytes(devicelink.normalize_icc_for_argyll(src_icc))
        a = cv._scale16(tifffile.imread(str(tif))); H, W = a.shape[:2]
        step = max(1, int(math.sqrt(W * H / 40000)))
        coords = [(x, y) for y in range(step // 2, H, step) for x in range(step // 2, W, step)]
        rgb = [(float(a[y, x, 0]), float(a[y, x, 1]), float(a[y, x, 2])) for (x, y) in coords]; del a
        lab_in = xicclu.run_xicclu(str(src_n), rgb, direction="f", intent="r", pcs="lab")
        n = min(len(rgb), len(lab_in)); rgb, lab_in = rgb[:n], lab_in[:n]
        Lin = [l[0] for l in lab_in]; Cin = [math.hypot(l[1], l[2]) for l in lab_in]
        links = build_links(src_n, dst_n, dest_orig, work, out_dir)
        per = {}
        for name, link in links.items():
            per[name] = measure_variant(link, dst_n, rgb, lab_in, Lin, Cin, black_dev)
            t12 = per[name]["tonal"]["L<12"]
            print(f"[aud]   {name:12} endpoint={per[name]['black_endpoint_Lout']} "
                  f"L<12 slope={t12['slope']} redistrib(0.10)={per[name]['redistribution_shape_ramp']['0.10']} "
                  f"prev<0.7={per[name]['prevalence_localslope']['<0.7']}", flush=True)
        results[pl] = {"variants": per,
                       "pop_frac": {b[0]: round(sum(1 for L in Lin if b[1] <= L < b[2]) / n, 4) for b in BANDS}}
    summary = {"dest": DEST, "dest_Lmin_neutral": Lmin, "preps": PREPS, "results": results,
               "note": "native intents vs -s witnesses; comparators are witnesses not targets."}
    (out_dir / "argyll_native_audit.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _reading(out_dir / "argyll_native_audit_reading.txt", summary)
    print("[aud] wrote argyll_native_audit.json + reading", flush=True)
    return summary


def _reading(path, s):
    order = ["G_ir", "G_ila", "G_ip", "G_ilp", "s_ir_bare", "s_ir_voieB"]
    L = ["# Audit Argyll natif — ombres / black-point / intents gamut mapping", "",
         f"dest: {Path(s['dest']).name}   Lmin neutre (Dmax) = {s['dest_Lmin_neutral']}", ""]
    for pl, r in s["results"].items():
        L += [f"══════ {pl}  (pop L<8={r['pop_frac']['L<8']*100:.1f}% L<12={r['pop_frac']['L<12']*100:.1f}% "
              f"L<25={r['pop_frac']['L<25']*100:.1f}%) ══════",
              "  A. black ENDPOINT (L_out du noir source) | B. redistrib ramp(dev0.10→L) | "
              "C. séparation L<12 slope/R² + prév<0.7 | D. chroma"]
        for name in order:
            v = r["variants"][name]; t = v["tonal"]["L<12"]; c = v["chroma_shadow"]
            L.append(f"   {name:12} A.endpt={v['black_endpoint_Lout']:>6}  B.r0.10={v['redistribution_shape_ramp']['0.10']:>6}"
                     f"  C.slope={str(t['slope']):>7} R²={str(t['R2']):>6} prev<0.7={v['prevalence_localslope']['<0.7']:>6}"
                     f"  D.ΔE00med={c['dE00_med']} P95={c['dE00_P95']} Δh95={c['dh_P95']} ΔCmean={c['dC_mean']}")
        # per-band slope table for the 4 natives + voie B
        L.append("  séparation dLout/dLin par bande :")
        L.append(f"    {'variant':<12}" + "".join(f"{b[0]:>8}" for b in BANDS))
        for name in order:
            L.append(f"    {name:<12}" + "".join(f"{str(r['variants'][name]['tonal'][b[0]]['slope']):>8}" for b in BANDS))
        L.append("")
    L += ["Distinction rappelée : A endpoint ≠ B redistribution ≠ C séparation ≠ D chroma.",
          "Interprétation → dans le rapport (doc→binaire→commande→mesure→interprétation)."]
    path.write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Argyll native shadow/black-point audit (measure only).")
    ap.add_argument("--out-dir", required=True, type=Path)
    a = ap.parse_args()
    run(a.out_dir)


if __name__ == "__main__":
    main()
