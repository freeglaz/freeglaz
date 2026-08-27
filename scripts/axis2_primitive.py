#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""AXE 2 primitive feasibility — deep-shadow tonal mapping (CONCEPTION + BENCH).

FEASIBILITY exploration (anticipated, ahead of print): can we build a simple,
monotone, DEST-PROFILE-AWARE, continuously-controllable primitive that moves the
black-depth ↔ deep-shadow-separation compromise, WITHOUT breaking or confusing
AXE 1 (τ)? Output: PRIMITIVE READY / NOT READY. NO prod/UI wiring, NO hardware.

AXE 2 = a PCS→PCS tonal remap Lout=f(Lin) near the dest black point, composed with
AXE 1 (chroma compression toward Cdriver) inside the same abstract. It acts on L
only: (L,a,b)→(f(L),a,b) — neutral axis keeps a,b≈0 (Δa,Δb≈0), L may change (the
AXE-2-adapted neutral guard). It does NOT recover sub-Dmax detail; it redistributes
the still-reproducible codomain.

Structured for later extraction (NO prod touched):
  measure_destination_black(dst)   — Lb from the PROFILE (not a witness constant)
  build_axis2_mapping(...)         — f(L) ; Family B (strength) / Family A (min-slope)
  apply_axis2_mapping(f)           — policy (L,a,b)->(f(L),a,b)
  validate_axis2_mapping(f, Lt)    — analytic benches (monotone/continuous/join/…)

Comparators are WITNESSES, never targets: -G -ir is a comparator, not a goal.
τ (AXE 1) frozen: τref=1 reference. mAB/mBA refusal kept.

Run: uv run python scripts/axis2_primitive.py --out-dir <dir> [--real-chain]
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

from lib.z9_client import devicelink, xicclu                  # noqa: E402
import abstract_poc as poc                                    # noqa: E402
import convert_variants as cv                                 # noqa: E402
import rebaseline_tau1 as reb                                 # noqa: E402
from webapp.backend.services.scan_delta import ciede2000      # noqa: E402

DEST = "/Users/vinz/Library/ColorSync/Profiles/hpz9_canson-photolustre-rc_ge-on.icc"
PREPS = {
    "A_raised": "/Volumes/MARCEL-MEETS-CUNEGONDE/PHOTOS_RAWS_BACKUP/_tmp/_DSC2374_rec2020-lstar_vif-2.tif",
    "B_black0": "/Volumes/MARCEL-MEETS-CUNEGONDE/PHOTOS_RAWS_BACKUP/_tmp/_DSC2374_rec2020-lstar_vif-3_blackTo0.tif",
}
LT_DEFAULT = 20.0            # deep-shadow zone end (identity above); documented choice


# ─── black point = DATA OF THE PROFILE (not a witness constant) ───────────────
def measure_destination_black(dst_n: Path) -> dict:
    """Neutral Lb + chromatic-floor variation Lb(h). PCS Lab via B2A→A2B roundtrip
    of the darkest target. If Lb(h) spread is large → scalar-Lb is only valid on
    the near-neutral tonal axis; saturated shadows are FLAGGED (not corrected)."""
    dev = xicclu.run_xicclu(str(dst_n), [(0, 0, 0)], direction="b", intent="r", pcs="lab")
    lab = xicclu.run_xicclu(str(dst_n), dev, direction="f", intent="r", pcs="lab")
    Lb = round(lab[0][0], 3)
    floors = {}
    for h in range(0, 360, 30):
        a, b = 20 * math.cos(math.radians(h)), 20 * math.sin(math.radians(h))
        d = xicclu.run_xicclu(str(dst_n), [(0, a, b)], direction="b", intent="r", pcs="lab")
        l = xicclu.run_xicclu(str(dst_n), d, direction="f", intent="r", pcs="lab")
        floors[h] = round(l[0][0], 2)
    spread = round(max(floors.values()) - min(floors.values()), 2)
    return {"Lb_neutral": Lb, "Lb_by_hue_C20": floors, "chromatic_spread": spread,
            "scalar_valid_domain": "near-neutral tonal axis (low C)",
            "flag": ("chromatic floor varies strongly (spread %.1f) — scalar Lb INSUFFICIENT for "
                     "saturated shadows; that is AXE 1's domain + a next problem (Lb(h,C)), NOT "
                     "corrected here" % spread) if spread > 3 else "scalar Lb sufficient"}


# ─── the primitive f(Lin) — lift the black toward the dest floor, smooth join ──
def build_axis2_mapping(Lb: float, Lt: float = LT_DEFAULT, *, strength=None, min_slope=None):
    """Return f(L): identity for L>=Lt; below, lift toward a raised black
    Lb_target with a smooth roll (roll(L)=(1-L/Lt)^2 → f(Lt)=Lt, f'(Lt)=1).

    Family B (geometric): strength p∈[0,1] → Lb_target = p·Lb  (p=0 OFF=identity;
        p=1 lifts source black exactly to the dest floor Lb — no sub-floor clip).
    Family A (slope-semantic): min_slope sL → Lb_target = Lt·(1-sL)/2, i.e. sL is
        the minimum tonal slope f'(0) of the map (sL=1 OFF; lower sL ⇒ more lift).
    Monotone iff Lb_target ≤ Lt/2 (guaranteed for p≤1 with Lt≥2·Lb)."""
    if (strength is None) == (min_slope is None):
        raise ValueError("give exactly one of strength / min_slope")
    if strength is not None:
        Lb_target = strength * Lb
    else:
        Lb_target = Lt * (1.0 - min_slope) / 2.0
    def f(L):
        if L >= Lt:
            return L
        return L + Lb_target * (1.0 - L / Lt) ** 2
    f.Lb_target = Lb_target; f.Lt = Lt
    return f


def apply_axis2_mapping(f):
    """Policy form for the abstract: L→f(L), a,b unchanged (chroma preserved)."""
    def policy(L, a, b):
        return (f(L), a, b)
    return policy


def validate_axis2_mapping(f, Lt: float) -> dict:
    """Analytic benches: monotone, continuity, join at Lt, min slope, overshoot,
    normal-zone identity. A primitive failing here is REJECTED before any image."""
    Ls = [i * 0.1 for i in range(0, int((Lt + 12) * 10))]
    ys = [f(L) for L in Ls]
    slopes = [(ys[i] - ys[i - 1]) / (Ls[i] - Ls[i - 1]) for i in range(1, len(Ls))]
    monotone = all(s >= -1e-9 for s in slopes)
    # join at Lt: f(Lt)=Lt and f'(Lt-)≈1
    iLt = min(range(len(Ls)), key=lambda i: abs(Ls[i] - Lt))
    join_val = abs(f(Lt) - Lt)
    join_slope = abs(slopes[max(1, iLt) - 1] - 1.0)
    # normal zone identity for L>Lt
    normal_dev = max(abs(f(L) - L) for L in Ls if L > Lt + 0.5)
    return {"monotone": monotone, "min_slope": round(min(slopes), 4), "max_slope": round(max(slopes), 4),
            "overshoot_above_input": round(max(ys[i] - Ls[i] for i in range(len(Ls))), 3),
            "join_Lt_value_err": round(join_val, 5), "join_Lt_slope_err": round(join_slope, 4),
            "normal_zone_max_dev": round(normal_dev, 5),
            "black_lift_f0": round(f(0.0), 3),
            "REJECT": (not monotone) or join_val > 1e-6 or join_slope > 0.05 or normal_dev > 1e-6}


# ─── AXE1 × AXE2 interaction, at the POLICY level (cheap, exact) ──────────────
def _cdriver_at_tau(dest_orig: Path, tau: float, out_dir: Path):
    bcsv = out_dir / f"bnd_tau{tau}.csv"
    refined = reb.refine_boundary(str(dest_orig), [5, 10, 15, 20, 30, 50], list(range(0, 360, 30)), tau)
    import csv as _csv
    with open(bcsv, "w", newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=["L", "h_N", "Cmeasured"]); w.writeheader()
        for L in [5, 10, 15, 20, 30, 50]:
            for h in range(0, 360, 30):
                w.writerow({"L": L, "h_N": h, "Cmeasured": refined[(L, h)]})
    return poc.Cdriver(bcsv)


def interaction_policy(dest_orig: Path, Lb: float, out_dir: Path):
    """Compose AXE2 (L remap) with AXE1 (chroma toward Cdriver) at the POLICY level
    (pure math, no collink) over τ × AXE2 × order. Measures whether τ keeps its
    direction/semantics when AXE2 changes L, and the Cdriver(L,h) shift induced."""
    taus = [0.5, 1.0, 2.0]
    strengths = [0.0, 0.33, 0.66, 1.0]
    cdr = {t: _cdriver_at_tau(dest_orig, t, out_dir) for t in taus}
    pol1 = {t: poc.make_policy(cdr[t]) for t in taus}          # AXE 1 policy per τ
    # synthetic probes: chromatic deep shadows (where both axes could interact)
    probes = [(L, C * math.cos(math.radians(h)), C * math.sin(math.radians(h)))
              for L in (5, 8, 12, 18) for C in (10, 20, 30) for h in range(0, 360, 45)]
    out = {"tau_monotonic_in_C": {}, "axis2_shifts_cdriver": {}, "order_commutativity": {}}
    for order in ("A2_then_A1", "A1_then_A2"):
        rows = {}
        for s in strengths:
            f = build_axis2_mapping(Lb, strength=s)
            p2 = apply_axis2_mapping(f)
            for t in taus:
                dC = []
                for (L, a, b) in probes:
                    if order == "A2_then_A1":
                        L1, a1, b1 = p2(L, a, b); Lo, ao, bo = pol1[t](L1, a1, b1)
                    else:
                        L1, a1, b1 = pol1[t](L, a, b); Lo, ao, bo = p2(L1, a1, b1)
                    dC.append(math.hypot(ao, bo) - math.hypot(a, b))
                rows[(s, t)] = sum(dC) / len(dC)     # mean chroma change
        # τ monotonic in C at each AXE2 strength? (more τ → less compression → higher C kept)
        mono = {}
        for s in strengths:
            seq = [rows[(s, t)] for t in taus]        # dC at τ=0.5,1,2
            mono[s] = all(seq[i] <= seq[i + 1] + 1e-6 for i in range(len(seq) - 1))  # τ↑ ⇒ dC↑ (less loss)
        out["tau_monotonic_in_C"][order] = mono
        out.setdefault("_rows_" + order, {f"s{s}_t{t}": round(rows[(s, t)], 3) for (s, t) in rows})
    # AXE2 shifts the Cdriver(L,h) actually encountered (quantify, not judge)
    for s in strengths:
        f = build_axis2_mapping(Lb, strength=s)
        sh = []
        for (L, a, b) in probes:
            h = math.degrees(math.atan2(b, a)) % 360
            sh.append(cdr[1.0](f(L), h) - cdr[1.0](L, h))
        out["axis2_shifts_cdriver"][f"s{s}"] = {"mean_dCdriver": round(sum(sh) / len(sh), 3),
                                                "max_abs": round(max(abs(x) for x in sh), 3)}
    # order commutativity: |A2A1 - A1A2| on the probes (ΔL, ΔC, Δh, ΔE00), τ=1, s=1
    f = build_axis2_mapping(Lb, strength=1.0); p2 = apply_axis2_mapping(f)
    dL = dC = dh = de = 0.0
    for (L, a, b) in probes:
        L1, a1, b1 = p2(L, a, b); x1 = pol1[1.0](L1, a1, b1)              # A2→A1
        La, aa, ba = pol1[1.0](L, a, b); x2 = p2(La, aa, ba)             # A1→A2
        dL = max(dL, abs(x1[0] - x2[0])); dC = max(dC, abs(math.hypot(x1[1], x1[2]) - math.hypot(x2[1], x2[2])))
        dh = max(dh, abs((math.degrees(math.atan2(x1[2], x1[1])) - math.degrees(math.atan2(x2[2], x2[1])) + 180) % 360 - 180))
        de = max(de, ciede2000(x1, x2))
    out["order_commutativity"] = {"max_dL": round(dL, 3), "max_dC": round(dC, 3),
                                  "max_dh": round(dh, 3), "max_dE00": round(de, 3)}
    return out


# ─── real-chain confirmation on prep B (discriminant) ─────────────────────────
def _build_combined_link(src_n, dst_n, dest_orig, tau, strength, Lb, order, work, out_dir, tag):
    cdr = _cdriver_at_tau(dest_orig, tau, out_dir)
    pol1 = poc.make_policy(cdr)
    f = build_axis2_mapping(Lb, strength=strength); p2 = apply_axis2_mapping(f)
    if order == "A2_then_A1":
        def comb(L, a, b): return pol1(*p2(L, a, b))
    else:
        def comb(L, a, b): return p2(*pol1(L, a, b))
    ab = work / f"ab_{tag}.icc"; ab.write_bytes(poc.build_abstract(comb, 33))
    link = work / f"link_{tag}.icc"
    poc.collink(["-v", "-qh", "-s", "-ir", "-p", str(ab), str(src_n), str(dst_n)], link)
    return link


def real_chain(prep_label, tif, dest_orig, dst_n, Lb, work, out_dir):
    src_icc = devicelink.extract_embedded_icc(tif)
    src_n = work / f"src_{prep_label}.icc"; src_n.write_bytes(devicelink.normalize_icc_for_argyll(src_icc))
    a = cv._scale16(__import__("tifffile").imread(str(tif)))
    H, W = a.shape[:2]; step = max(1, int(math.sqrt(W * H / 40000)))
    coords = [(x, y) for y in range(step // 2, H, step) for x in range(step // 2, W, step)]
    rgb = [(float(a[y, x, 0]), float(a[y, x, 1]), float(a[y, x, 2])) for (x, y) in coords]; del a
    lab_in = xicclu.run_xicclu(str(src_n), rgb, direction="f", intent="r", pcs="lab")
    n = min(len(rgb), len(lab_in)); rgb, lab_in, coords = rgb[:n], lab_in[:n], coords[:n]
    Lin = [l[0] for l in lab_in]
    res = {}
    for s in (0.0, 0.5, 1.0):                                  # OFF, mid, full lift
        tag = f"{prep_label}_s{s}_t1"
        link = _build_combined_link(src_n, dst_n, dest_orig, 1.0, s, Lb, "A2_then_A1", work, out_dir, tag)
        dev = xicclu.run_xicclu(str(link), rgb, direction="f")
        lab_out = xicclu.run_xicclu(str(dst_n), dev, direction="f", intent="r", pcs="lab")
        # deep-shadow slope + black lift (L<12) + prevalence (local slope<0.7 on L<40)
        idx = [i for i in range(n) if Lin[i] < 12]
        xs = [Lin[i] for i in idx]; ldeep = [lab_out[i][0] for i in idx]
        s_r2_lift = None
        if len(idx) >= 3:
            mx, my = sum(xs) / len(xs), sum(ldeep) / len(ldeep)
            den = sum((x - mx) ** 2 for x in xs)
            slope = sum((xs[i] - mx) * (ldeep[i] - my) for i in range(len(xs))) / den if den else None
            lift = sum(ldeep[i] - xs[i] for i in range(len(idx))) / len(idx)
            s_r2_lift = (round(slope, 4) if slope is not None else None, round(lift, 3))
        loc = _local_slopes([Lin[i] for i in range(n)], [lab_out[i][0] for i in range(n)])
        prev = round(sum(1 for x in loc if x < 0.7) / (len(loc) or 1), 4)
        # chroma non-regression from AXE2 (vs s=0): measured later by diff
        outp = out_dir / f"{tag}.tif"
        devicelink.apply_cctiff(link, tif, outp, embed_icc=dest_orig)
        res[f"s{s}"] = {"deep_L<12_slope": s_r2_lift[0] if s_r2_lift else None,
                        "deep_L<12_lift": s_r2_lift[1] if s_r2_lift else None,
                        "prevalence_localslope<0.7": prev, "affinity_tiff": outp.name}
        print(f"[ax2]   {tag}: slope={res[f's{s}']['deep_L<12_slope']} lift={res[f's{s}']['deep_L<12_lift']} prev<0.7={prev}", flush=True)
    return res


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


def run(out_dir: Path, do_real_chain: bool):
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "_work"; work.mkdir(exist_ok=True)
    dest_orig = Path(DEST)
    dst_n = work / "dest.icc"; dst_n.write_bytes(devicelink.normalize_icc_for_argyll(dest_orig.read_bytes()))

    print("[ax2] measure_destination_black …", flush=True)
    black = measure_destination_black(dst_n)
    Lb = black["Lb_neutral"]

    print("[ax2] validate_axis2_mapping (analytic benches) …", flush=True)
    families = {}
    for label, kw in [("B_p0.0(OFF)", {"strength": 0.0}), ("B_p0.33", {"strength": 0.33}),
                      ("B_p0.66", {"strength": 0.66}), ("B_p1.0", {"strength": 1.0}),
                      ("A_sL1.0(OFF)", {"min_slope": 1.0}), ("A_sL0.8", {"min_slope": 0.8}),
                      ("A_sL0.6", {"min_slope": 0.6})]:
        f = build_axis2_mapping(Lb, **kw)
        families[label] = {"params": kw, "Lb_target": round(f.Lb_target, 3),
                           "validation": validate_axis2_mapping(f, LT_DEFAULT),
                           "curve": {str(L): round(f(L), 3) for L in (0, 2, 4, 6, 8, 12, 16, 20, 25)}}

    print("[ax2] interaction AXE1 × AXE2 (policy level) …", flush=True)
    inter = interaction_policy(dest_orig, Lb, out_dir)

    real = {}
    if do_real_chain:
        for pl, tif in PREPS.items():
            print(f"[ax2] real-chain {pl} …", flush=True)
            real[pl] = real_chain(pl, Path(tif), dest_orig, dst_n, Lb, work, out_dir)

    summary = {"dest": DEST, "black_point": black, "Lt": LT_DEFAULT,
               "families": families, "interaction_axis1x2": inter, "real_chain": real,
               "note": "feasibility only; comparators are witnesses not targets; τ frozen."}
    (out_dir / "axis2_primitive.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _reading(out_dir / "axis2_primitive_reading.txt", summary)
    print("[ax2] wrote axis2_primitive.json + reading", flush=True)
    return summary


def _reading(path: Path, s: dict):
    b = s["black_point"]; L = ["# AXE 2 primitive — feasibility exploration (deep-shadow tonal mapping)", "",
        f"dest: {Path(s['dest']).name}   Lt(zone)={s['Lt']}", "",
        "── B. black point = donnée du profil ──",
        f"  Lb neutre = {b['Lb_neutral']}   (scalaire valide : {b['scalar_valid_domain']})",
        f"  floor chromatique Lb(h)@C20 : {min(b['Lb_by_hue_C20'].values())}..{max(b['Lb_by_hue_C20'].values())} "
        f"spread={b['chromatic_spread']}",
        f"  FLAG: {b['flag']}", "",
        "── C. validation analytique par famille (monotone/continuité/raccord) ──",
        f"  {'famille':<14}{'Lb_tgt':>7}{'mono':>6}{'minSlope':>9}{'joinErr':>9}{'normDev':>9}{'REJECT':>8}"]
    for lab, fam in s["families"].items():
        v = fam["validation"]
        L.append(f"  {lab:<14}{fam['Lb_target']:>7}{str(v['monotone']):>6}{v['min_slope']:>9}"
                 f"{v['join_Lt_slope_err']:>9}{v['normal_zone_max_dev']:>9}{str(v['REJECT']):>8}")
    L += ["", "  courbe f(Lin) (B_p1.0, lift plein) :",
          "    " + "  ".join(f"{k}→{v}" for k, v in s["families"]["B_p1.0"]["curve"].items()), "",
          "── E. interaction AXE1 × AXE2 (niveau policy) ──"]
    it = s["interaction_axis1x2"]
    for order in ("A2_then_A1", "A1_then_A2"):
        L.append(f"  τ monotone en C (τ↑⇒moins de compression) par strength [{order}]:")
        L.append(f"    {it['tau_monotonic_in_C'][order]}")
    L.append("  AXE2 décale le Cdriver(L,h) rencontré (quantifié, non jugé):")
    for k, v in it["axis2_shifts_cdriver"].items():
        L.append(f"    {k}: mean ΔCdriver={v['mean_dCdriver']}  max|Δ|={v['max_abs']}")
    oc = it["order_commutativity"]
    L.append(f"  commutativité d'ordre (A2→A1 vs A1→A2, τ=1,s=1): max ΔL={oc['max_dL']} ΔC={oc['max_dC']} "
             f"Δh={oc['max_dh']} ΔE00={oc['max_dE00']}")
    if s["real_chain"]:
        L += ["", "── D. chaîne réelle (τ=1, ordre A2→A1) : lift/slope/prévalence par strength ──"]
        for pl, r in s["real_chain"].items():
            L.append(f"  {pl}:")
            for sk, v in r.items():
                L.append(f"    {sk}: deep L<12 slope={v['deep_L<12_slope']} lift={v['deep_L<12_lift']} "
                         f"prev(slope<0.7)={v['prevalence_localslope<0.7']}  [{v['affinity_tiff']}]")
    L += ["", "── F. RECOMMANDATION ──",
          "  (à conclure dans le rapport : PRIMITIVE READY / NOT READY selon monotone+continu+contrôle",
          "   continu+interaction AXE1×AXE2 intelligible+black point profil-aware. Comparateurs=témoins.)"]
    path.write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="AXE 2 primitive feasibility (conception + bench).")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--real-chain", action="store_true", help="also run collink/cctiff on A & B")
    a = ap.parse_args()
    run(a.out_dir, a.real_chain)


if __name__ == "__main__":
    main()
