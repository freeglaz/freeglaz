#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Composition -G × abstract voie B (J.1) + VIF black placement × native intents.

Order kept: documented mechanism → real command → measure → attribution → (perception).
NO custom mapper, NO new primitive/S-curve, NO abstract change, NO τ change, NO wiring/UI/print.

§6 gate ESTABLISHED (doc + binary): `-p <abstract>` is a general collink option applied
"between the source and destination profiles" (collink.html); with Argyll's architecture
(source A2B colorimetric, gamut map dest-side, iccgamutmapping.html) the abstract acts on
the SOURCE-side PCS, BEFORE the dest gamut map → effective order = abstract THEN gamut map.
Clean-insertion sanity: |(-G -ila) - (-G -ila -p identity)| ≈ 3.5e-5.

VOLET 1B (J.1): does the voie-B abstract keep its hue-preserving property inside a `-G`
chain while keeping la/lp native separation? Characterise, do not validate.
VOLET 2: is VIF black raising still useful when the downstream intent (la/lp) redistributes
deep shadows itself? Stratified by L×C×h to avoid the population trap (§13).

Run: uv run python scripts/composition_vif_audit.py --out-dir <dir> [--affinity]
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
BANDS = [("L<8", 0, 8), ("L<12", 0, 12), ("L<20", 0, 20), ("L<25", 0, 25), ("L<40", 0, 40)]
# variant name -> collink argv AFTER -qh (abstract path substituted at build time)
VARIANTS = [
    ("G_ir", ["-G", "-ir"]),
    ("G_ila", ["-G", "-ila"]),
    ("G_ila_abs", ["-G", "-ila", "-p", "@ABS@"]),
    ("G_ilp", ["-G", "-ilp"]),
    ("G_ilp_abs", ["-G", "-ilp", "-p", "@ABS@"]),
    ("s_ir", ["-s", "-ir"]),
    ("s_ir_abs", ["-s", "-ir", "-p", "@ABS@"]),          # voie B actuelle
]
AFFINITY = ["G_ir", "G_ila", "G_ila_abs", "G_ilp", "G_ilp_abs"]


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


def _pct(x, q):
    return round(x[min(len(x) - 1, int(q * (len(x) - 1)))], 2) if x else None


def measure(link, dst_n, rgb, lab_in, Lin, Cin, black_dev):
    dev = xicclu.run_xicclu(str(link), rgb, direction="f")
    lab_out = xicclu.run_xicclu(str(dst_n), dev, direction="f", intent="r", pcs="lab")
    n = min(len(dev), len(lab_in))
    bdev = xicclu.run_xicclu(str(link), [black_dev], direction="f")
    endpoint = round(xicclu.run_xicclu(str(dst_n), bdev, direction="f", intent="r", pcs="lab")[0][0], 3)
    tonal = {}
    for bn, lo, hi in BANDS:
        idx = [i for i in range(n) if lo <= Lin[i] < hi]
        xs = [Lin[i] for i in idx]; ys = [lab_out[i][0] for i in idx]
        sl, r2 = _slope_r2(xs, ys)
        lift = round(sum(ys[k] - xs[k] for k in range(len(idx))) / len(idx), 3) if idx else None
        tonal[bn] = {"n": len(idx), "slope": sl, "R2": r2, "redistrib_Lout_minus_Lin": lift}
    # chroma percentiles, C>10, L<40, out vs in
    ci = [i for i in range(n) if Cin[i] > 10 and Lin[i] < 40]
    des, dcs, dhs, dls = [], [], [], []
    perpix = []                                                 # for stratified analysis
    for i in ci:
        Li, ai, bi = lab_in[i]; Lo, ao, bo = lab_out[i]
        de = ciede2000((Li, ai, bi), (Lo, ao, bo))
        dh = abs((math.degrees(math.atan2(bo, ao)) - math.degrees(math.atan2(bi, ai)) + 180) % 360 - 180)
        des.append(de); dcs.append(math.hypot(ao, bo) - math.hypot(ai, bi)); dhs.append(dh); dls.append(Lo - Li)
        perpix.append((Li, Cin[i], math.degrees(math.atan2(bi, ai)) % 360, dh))
    des.sort(); dhs2 = sorted(dhs)
    chroma = {"n": len(ci), "dE00_med": _pct(des, .5), "dE00_P95": _pct(des, .95),
              "dh_med": _pct(dhs2, .5), "dh_P95": _pct(dhs2, .95), "dh_P99": _pct(dhs2, .99),
              "dC_mean": round(sum(dcs) / len(dcs), 2) if dcs else None,
              "dL_mean": round(sum(dls) / len(dls), 2) if dls else None}
    return {"black_endpoint": endpoint, "tonal": tonal, "chroma": chroma}, perpix


def stratified_dh(perpix_A, perpix_B):
    """Compare mean Δh(A) vs Δh(B) within matched L×C×h bins (avoid population trap
    §13). Bins: L in {<8,8-15,15-25,25-40} × C in {10-20,20-35,35+} × h/45°."""
    def binkey(Li, Ci, hi):
        lb = 0 if Li < 8 else 1 if Li < 15 else 2 if Li < 25 else 3
        cb = 0 if Ci < 20 else 1 if Ci < 35 else 2
        hb = int(hi // 45) % 8
        return (lb, cb, hb)
    def agg(pp):
        d = {}
        for (Li, Ci, hi, dh) in pp:
            d.setdefault(binkey(Li, Ci, hi), []).append(dh)
        return d
    dA, dB = agg(perpix_A), agg(perpix_B)
    common = [k for k in dA if k in dB and len(dA[k]) >= 15 and len(dB[k]) >= 15]
    rows = []
    wA = wB = wn = 0.0
    for k in sorted(common):
        mA = sum(dA[k]) / len(dA[k]); mB = sum(dB[k]) / len(dB[k]); w = min(len(dA[k]), len(dB[k]))
        rows.append({"bin_LCh": k, "nA": len(dA[k]), "nB": len(dB[k]),
                     "dh_A": round(mA, 2), "dh_B": round(mB, 2), "A_minus_B": round(mA - mB, 2)})
        wA += mA * w; wB += mB * w; wn += w
    return {"n_common_bins": len(common),
            "weighted_dh_A_raised": round(wA / wn, 2) if wn else None,
            "weighted_dh_B_black0": round(wB / wn, 2) if wn else None,
            "delta_matched_A_minus_B": round((wA - wB) / wn, 2) if wn else None,
            "bins": rows[:24]}


def run(out_dir: Path, do_affinity: bool):
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "_work"; work.mkdir(exist_ok=True)
    dest_orig = Path(DEST)
    dst_n = work / "dest.icc"; dst_n.write_bytes(devicelink.normalize_icc_for_argyll(dest_orig.read_bytes()))
    Lmin = round(xicclu.run_xicclu(str(dst_n),
                 xicclu.run_xicclu(str(dst_n), [(0, 0, 0)], direction="b", intent="r", pcs="lab"),
                 direction="f", intent="r", pcs="lab")[0][0], 3)
    policy, _ = cv.build_abstract_at(dest_orig, 1.0, out_dir)
    ABS = work / "abstract_tau1.icc"; ABS.write_bytes(poc.build_abstract(policy, 33))
    black_dev = (0.0, 0.0, 0.0)

    results = {}; perpix_store = {}
    for pl, tif in PREPS.items():
        print(f"[cvf] prep {pl} …", flush=True)
        src_icc = devicelink.extract_embedded_icc(tif)
        src_n = work / f"src_{pl}.icc"; src_n.write_bytes(devicelink.normalize_icc_for_argyll(src_icc))
        a = cv._scale16(tifffile.imread(str(tif))); H, W = a.shape[:2]
        step = max(1, int(math.sqrt(W * H / 40000)))
        coords = [(x, y) for y in range(step // 2, H, step) for x in range(step // 2, W, step)]
        rgb = [(float(a[y, x, 0]), float(a[y, x, 1]), float(a[y, x, 2])) for (x, y) in coords]; del a
        lab_in = xicclu.run_xicclu(str(src_n), rgb, direction="f", intent="r", pcs="lab")
        n = min(len(rgb), len(lab_in)); rgb, lab_in = rgb[:n], lab_in[:n]
        Lin = [l[0] for l in lab_in]; Cin = [math.hypot(l[1], l[2]) for l in lab_in]
        pop = {b[0]: round(sum(1 for L in Lin if b[1] <= L < b[2]) / n, 4) for b in BANDS}
        pop["frac_below_Lmin"] = round(sum(1 for L in Lin if L < Lmin) / n, 4)
        per = {}
        for name, argv in VARIANTS:
            aa = [("-p" if x == "-p" else x) if x != "@ABS@" else str(ABS) for x in argv]
            link = work / f"link_{pl}_{name}.icc"
            poc.collink(["-v", "-qh"] + aa + [str(src_n), str(dst_n)], link)
            m, pp = measure(link, dst_n, rgb, lab_in, Lin, Cin, black_dev)
            per[name] = m
            if name in ("G_ir", "G_ila", "G_ilp"):
                perpix_store[(pl, name)] = pp
            if do_affinity and name in AFFINITY:
                outp = out_dir / f"{pl}_{name}.tif"
                devicelink.apply_cctiff(link, tif, outp, embed_icc=dest_orig)
                m["affinity_tiff"] = outp.name
            t = m["tonal"]["L<12"]
            print(f"[cvf]   {name:12} L<12 slope={t['slope']} endpt={m['black_endpoint']} "
                  f"Δh_P95={m['chroma']['dh_P95']} ΔE00med={m['chroma']['dE00_med']}", flush=True)
        results[pl] = {"pop": pop, "variants": per}

    # VOLET 2 stratified: Δh(A) vs Δh(B) within matched L×C×h bins, per intent
    strat = {}
    for intent in ("G_ila", "G_ilp", "G_ir"):
        if ("A_raised", intent) in perpix_store and ("B_black0", intent) in perpix_store:
            strat[intent] = stratified_dh(perpix_store[("A_raised", intent)], perpix_store[("B_black0", intent)])

    summary = {"dest": DEST, "dest_Lmin": Lmin, "abstract_order": "abstract(voieB) THEN gamut-map (source-PCS, doc+sanity)",
               "results": results, "vif_stratified_dh_A_vs_B": strat,
               "note": "characterise composition & VIF placement; no winner; population-trap controlled (stratified)."}
    (out_dir / "composition_vif.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _reading(out_dir / "composition_vif_reading.txt", summary)
    print("[cvf] wrote composition_vif.json + reading", flush=True)
    return summary


def _reading(path, s):
    order = ["G_ir", "G_ila", "G_ila_abs", "G_ilp", "G_ilp_abs", "s_ir", "s_ir_abs"]
    L = ["# Composition -G × abstract voie B (J.1) + placement noirs VIF", "",
         f"dest: {Path(s['dest']).name}  Lmin={s['dest_Lmin']}  ordre abstract: {s['abstract_order']}", ""]
    for pl, r in s["results"].items():
        L += [f"══════ {pl}  (pop L<8={r['pop']['L<8']*100:.1f}% L<12={r['pop']['L<12']*100:.1f}% "
              f"<Lmin={r['pop']['frac_below_Lmin']*100:.1f}%) ══════",
              f"  {'variant':<12}{'endpt':>7}{'sl L<8':>8}{'sl L<12':>8}{'sl L<25':>8}"
              f"{'Δh med':>8}{'Δh P95':>8}{'ΔC mn':>7}{'ΔE med':>7}"]
        for name in order:
            v = r["variants"][name]; t = v["tonal"]; c = v["chroma"]
            L.append(f"  {name:<12}{v['black_endpoint']:>7}{str(t['L<8']['slope']):>8}{str(t['L<12']['slope']):>8}"
                     f"{str(t['L<25']['slope']):>8}{str(c['dh_med']):>8}{str(c['dh_P95']):>8}"
                     f"{str(c['dC_mean']):>7}{str(c['dE00_med']):>7}")
        L.append("")
    L += ["── VOLET 2 : Δh(A raised) vs Δh(B black0) à population L×C×h APPARIÉE (piège §13 contrôlé) ──"]
    for it, st in s["vif_stratified_dh_A_vs_B"].items():
        L.append(f"  {it}: {st['n_common_bins']} bins communs | Δh_A={st['weighted_dh_A_raised']} "
                 f"Δh_B={st['weighted_dh_B_black0']} | Δ(A−B) apparié = {st['delta_matched_A_minus_B']}")
    L += ["", "Lecture (dans le rapport, avec attribution) :",
          " J.1 (a) la+abs conserve-t-il la séparation de la ? (b) réduit-il Δh ? (c) surcoût ?",
          " VOLET 2 : Δh apparié A−B <0 ⇒ raised réduit la rotation MÊME à population comparable (H-VIF4).",
          "           ≈0 ⇒ l'écart global n'était que de la population, pas moins de 'travail' du mapper."]
    path.write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Composition -G×abstract + VIF black placement (measure).")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--affinity", action="store_true", help="also emit full-res device TIFFs")
    a = ap.parse_args()
    run(a.out_dir, a.affinity)


if __name__ == "__main__":
    main()
