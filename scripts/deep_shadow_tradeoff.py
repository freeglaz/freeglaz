#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Deep-shadow tradeoff — source black placement vs dest black point (DIAGNOSTIC).

Measures how the depth↔deep-shadow-separation tradeoff (AXE 2, distinct from τ's
luminance↔chroma AXE 1) depends on how the SOURCE blacks sit relative to the DEST
black point (Dmax). Per preparation (A=raised blacks / B=blacks-at-0):

  4.A input distribution : population L<8/12/20/25 + distance to dest Lmin
  4.B tonal per band     : dLout/dLin, R², lift for V1/V0/V2(/V3/V4)
  4.C mode vs abstract   : V0 (-s nu) ≈ V2 (-s+abstract) on deep separation?
  4.D chroma guard       : Δh/ΔC/ΔE00 (no tonal claim bought with a chroma problem)
  4.E PREVALENCE         : fraction of pixels with local slope <0.9 / <0.8, |ΔL|
  5   Affinity outputs   : device TIFFs (V1, V0, V2) embed dest, per preparation

NO solution, NO tuning, NO new cursor/primitive, NO tonal mapping change. Measures
only whether AXE 2 deserves a control. Verdict A/B/C decided WITH vinz's Affinity
inspection. mAB/mBA refusal kept. NB: prepared file — describe the dependence on
preparation, do not overgeneralise.

Run: uv run python scripts/deep_shadow_tradeoff.py --prep A=<tif> [--prep B=<tif>]
     --dest <icc> --out-dir <dir>
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
import convert_variants as cv                                 # noqa: E402 (build_abstract_at, _scale16)

# variants: V1 ref, V0 = -s nu (isolates the MODE), V4/V2/V3 = voie B at τ
VARIANTS = [
    {"name": "V1_G-ir", "mode": "ref", "tau": None},
    {"name": "V0_s-nu", "mode": "s_nu", "tau": None},
    {"name": "V4_tau0.5", "mode": "voieB", "tau": 0.5},
    {"name": "V2_tau1.0", "mode": "voieB", "tau": 1.0},
    {"name": "V3_tau2.0", "mode": "voieB", "tau": 2.0},
]
AFFINITY_VARIANTS = ("V1_G-ir", "V0_s-nu", "V2_tau1.0")       # device TIFFs for vinz
BANDS = [("L<8", 0, 8), ("L<12", 0, 12), ("L<20", 0, 20), ("L<25", 0, 25)]


def dest_lmin(dst_n: Path) -> float:
    dev = xicclu.run_xicclu(str(dst_n), [(0, 0, 0)], direction="b", intent="r", pcs="lab")
    lab = xicclu.run_xicclu(str(dst_n), dev, direction="f", intent="r", pcs="lab")
    return round(lab[0][0], 3)


def build_links(src_n: Path, dst_n: Path, dest_orig: Path, work: Path, out_dir: Path):
    ab_by_tau = {}
    for t in (0.5, 1.0, 2.0):
        policy, _ = cv.build_abstract_at(dest_orig, t, out_dir)
        p = work / f"ab_tau{t}.icc"; p.write_bytes(poc.build_abstract(policy, 33)); ab_by_tau[t] = p
    links = {}
    for v in VARIANTS:
        link = work / f"link_{v['name']}.icc"
        if v["mode"] == "ref":
            devicelink.run_collink(src_n, dst_n, link, intent="r", quality="h")
        elif v["mode"] == "s_nu":
            poc.collink(["-v", "-qh", "-s", "-ir", str(src_n), str(dst_n)], link)
        else:
            poc.collink(["-v", "-qh", "-s", "-ir", "-p", str(ab_by_tau[v["tau"]]), str(src_n), str(dst_n)], link)
        links[v["name"]] = link
        print(f"[dst]   link {v['name']}", flush=True)
    return links


def _slope_r2_lift(xs, ys):
    n = len(xs)
    if n < 3:
        return None, None, None
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if not den:
        return None, None, None
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / den
    ssres = sum((ys[i] - (my + slope * (xs[i] - mx))) ** 2 for i in range(n))
    sstot = sum((y - my) ** 2 for y in ys)
    r2 = (1 - ssres / sstot) if sstot else None
    lift = sum(ys[i] - xs[i] for i in range(n)) / n
    return round(slope, 4), (round(r2, 4) if r2 is not None else None), round(lift, 3)


def _local_slopes(Lin, Lout, bin_w=2.0, lo=0.0, hi=40.0):
    """Per-bin slope of mean(L_out) vs bin centre (finite diff), assigned to pixels."""
    bins = {}
    for i in range(len(Lin)):
        if lo <= Lin[i] < hi:
            b = int((Lin[i] - lo) / bin_w)
            bins.setdefault(b, []).append(Lout[i])
    centres = sorted(bins)
    means = {b: sum(bins[b]) / len(bins[b]) for b in centres}
    slope_of_bin = {}
    for j, b in enumerate(centres):
        if j == 0 and len(centres) > 1:
            nb = centres[1]; slope_of_bin[b] = (means[nb] - means[b]) / ((nb - b) * bin_w)
        elif j == len(centres) - 1:
            pb = centres[j - 1]; slope_of_bin[b] = (means[b] - means[pb]) / ((b - pb) * bin_w)
        else:
            pb, nb = centres[j - 1], centres[j + 1]
            slope_of_bin[b] = (means[nb] - means[pb]) / ((nb - pb) * bin_w)
    # assign to pixels in [lo,hi)
    per = []
    for i in range(len(Lin)):
        if lo <= Lin[i] < hi:
            b = int((Lin[i] - lo) / bin_w)
            per.append(slope_of_bin.get(b))
    return [s for s in per if s is not None]


def measure_prep(label: str, tif: Path, dest_orig: Path, dst_n: Path, out_dir: Path, work: Path,
                 n_target=40000):
    src_icc = devicelink.extract_embedded_icc(tif)
    for who, icc in (("source", src_icc), ("dest", dest_orig.read_bytes())):
        st = devicelink.convert_lut_support(icc)
        if st in ("UNSUPPORTED_MAB_MBA", "NO_USABLE_TRANSFORM"):
            raise SystemExit(f"{label} {who} {st} — refused (mAB/mBA guard held)")
    src_n = work / f"src_{label}.icc"; src_n.write_bytes(devicelink.normalize_icc_for_argyll(src_icc))

    print(f"[dst] prep {label}: sampling {tif.name} …", flush=True)
    a = cv._scale16(tifffile.imread(str(tif)))
    H, W = a.shape[:2]
    step = max(1, int(math.sqrt(W * H / n_target)))
    coords = [(x, y) for y in range(step // 2, H, step) for x in range(step // 2, W, step)]
    rgb = [(float(a[y, x, 0]), float(a[y, x, 1]), float(a[y, x, 2])) for (x, y) in coords]
    del a
    lab_in = xicclu.run_xicclu(str(src_n), rgb, direction="f", intent="r", pcs="lab")
    n = min(len(rgb), len(lab_in)); rgb = rgb[:n]; lab_in = lab_in[:n]; coords = coords[:n]
    Lin = [l[0] for l in lab_in]
    Cin = [math.hypot(l[1], l[2]) for l in lab_in]
    print(f"[dst]   {n} px", flush=True)

    Lmin = dest_lmin(dst_n)
    # 4.A input distribution
    distrib = {"n": n, "dest_Lmin": Lmin,
               "population": {b[0]: {"n": sum(1 for L in Lin if b[1] <= L < b[2]),
                                     "frac": round(sum(1 for L in Lin if b[1] <= L < b[2]) / n, 5)}
                             for b in BANDS}}
    near = sorted(L - Lmin for L in Lin if L < 25)
    distrib["dist_to_Lmin_L<25"] = {"n": len(near),
                                    "p05": round(near[len(near) // 20], 2) if near else None,
                                    "median": round(near[len(near) // 2], 2) if near else None} if near else None

    links = build_links(src_n, dst_n, dest_orig, work, out_dir)
    dev = {name: xicclu.run_xicclu(str(link), rgb, direction="f") for name, link in links.items()}
    lab_out = {name: xicclu.run_xicclu(str(dst_n), dev[name], direction="f", intent="r", pcs="lab")
               for name in links}

    # 4.B tonal per band + 4.C mode vs abstract
    tonal = {}
    for bname, lo, hi in BANDS:
        idx = [i for i in range(n) if lo <= Lin[i] < hi]
        row = {"n": len(idx)}
        for name in links:
            xs = [Lin[i] for i in idx]; ys = [lab_out[name][i][0] for i in idx]
            s, r2, lift = _slope_r2_lift(xs, ys)
            row[name] = {"slope": s, "R2": r2, "lift": lift}
        tonal[bname] = row

    # 4.E prevalence (local slope on L<40), per variant
    prevalence = {}
    for name in links:
        sl = _local_slopes(Lin, [lab_out[name][i][0] for i in range(n)])
        m = len(sl) or 1
        prevalence[name] = {"n_pixels_L<40": len(sl),
                            "frac_localslope<0.9": round(sum(1 for s in sl if s < 0.9) / m, 4),
                            "frac_localslope<0.8": round(sum(1 for s in sl if s < 0.8) / m, 4),
                            "frac_localslope<0.7": round(sum(1 for s in sl if s < 0.7) / m, 4)}
    # fraction of WHOLE image in deep bands (prevalence of the constrained population)
    prevalence["_population_frac_image"] = {b[0]: distrib["population"][b[0]]["frac"] for b in BANDS}

    # 4.D chroma guard: for chromatic pixels, ΔE00/Δh/ΔC out-vs-in, V2 vs V1
    from webapp.backend.services.scan_delta import ciede2000
    chroma = {}
    chrom_idx = [i for i in range(n) if Cin[i] > 10 and Lin[i] < 40]
    for name in ("V1_G-ir", "V2_tau1.0", "V0_s-nu"):
        des, dhs, dcs = [], [], []
        for i in chrom_idx:
            Li, ai, bi = lab_in[i]; Lo, ao, bo = lab_out[name][i]
            des.append(ciede2000((Li, ai, bi), (Lo, ao, bo)))
            dhs.append(abs((math.degrees(math.atan2(bo, ao)) - math.degrees(math.atan2(bi, ai)) + 180) % 360 - 180))
            dcs.append(math.hypot(ao, bo) - math.hypot(ai, bi))
        des.sort(); dhs.sort()
        chroma[name] = {"n": len(chrom_idx),
                        "dE00_median": round(des[len(des) // 2], 2) if des else None,
                        "dE00_P95": round(des[min(len(des) - 1, int(0.95 * len(des)))], 2) if des else None,
                        "dh_P95": round(dhs[min(len(dhs) - 1, int(0.95 * len(dhs)))], 2) if dhs else None,
                        "dC_mean": round(sum(dcs) / len(dcs), 2) if dcs else None}

    # 5. Affinity device TIFFs (full-res, embed dest)
    affinity = {}
    for name in AFFINITY_VARIANTS:
        outp = out_dir / f"{label}_{name}.tif"
        devicelink.apply_cctiff(links[name], tif, outp, embed_icc=dest_orig)
        affinity[name] = str(outp)
        print(f"[dst]   Affinity {outp.name}", flush=True)

    return {"file": str(tif), "distribution": distrib, "tonal": tonal,
            "prevalence": prevalence, "chroma_guard": chroma, "affinity_tiffs": affinity}


def run(preps: dict, dest: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "_work"; work.mkdir(exist_ok=True)
    dst_n = work / "dest.icc"; dst_n.write_bytes(devicelink.normalize_icc_for_argyll(dest.read_bytes()))
    results = {}
    for label, tif in preps.items():
        results[label] = measure_prep(label, Path(tif), dest, dst_n, out_dir, work)
    summary = {"dest": str(dest), "preparations": preps, "results": results,
               "note": "AXE 2 (black depth ↔ deep-shadow separation), distinct from τ. Measure only; "
                       "verdict A/B/C needs Affinity inspection. Prepared files — describe dependence."}
    (out_dir / "deep_shadow_tradeoff.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _reading(out_dir / "deep_shadow_tradeoff_reading.txt", summary)
    print("[dst] wrote deep_shadow_tradeoff.json + reading", flush=True)
    return summary


def _reading(path: Path, s: dict):
    L = ["# Deep-shadow tradeoff — source black placement vs dest black point (diagnostic)", "",
         f"dest: {Path(s['dest']).name}", ""]
    for label, r in s["results"].items():
        d = r["distribution"]
        L += [f"══════ PRÉPARATION {label} : {Path(r['file']).name} ══════",
              f"  dest Lmin (noir imprimable) = {d['dest_Lmin']}   ({d['n']} px échantillonnés)", "",
              "  ── 4.A population soumise à la contrainte ──"]
        for b, v in d["population"].items():
            L.append(f"     {b:<6} n={v['n']:<6} frac={v['frac']*100:.2f}%")
        if d.get("dist_to_Lmin_L<25"):
            dd = d["dist_to_Lmin_L<25"]
            L.append(f"     distance L_in→Lmin (L<25): p05={dd['p05']} median={dd['median']}")
        L += ["", "  ── 4.B tonal (dLout/dLin · lift) par bande ──"]
        for bname, row in r["tonal"].items():
            L.append(f"     {bname} (n={row['n']}):")
            for name in ("V1_G-ir", "V0_s-nu", "V2_tau1.0", "V4_tau0.5", "V3_tau2.0"):
                x = row[name]
                L.append(f"        {name:<11} slope={x['slope']}  R²={x['R2']}  lift={x['lift']}")
        L += ["", "  ── 4.C mode vs abstract : V0 (-s nu) ≈ V2 (-s+abstract) ? ──"]
        for bname in ("L<8", "L<12"):
            v0 = r["tonal"][bname]["V0_s-nu"]; v2 = r["tonal"][bname]["V2_tau1.0"]; v1 = r["tonal"][bname]["V1_G-ir"]
            L.append(f"     {bname}: V0 slope={v0['slope']}/lift={v0['lift']}  V2 slope={v2['slope']}/lift={v2['lift']}"
                     f"  (V1 slope={v1['slope']}/lift={v1['lift']})")
        L += ["", "  ── 4.E PRÉVALENCE (fraction de pixels L<40 à pente locale faible) ──"]
        for name in ("V1_G-ir", "V0_s-nu", "V2_tau1.0"):
            p = r["prevalence"][name]
            L.append(f"     {name:<11} <0.9:{p['frac_localslope<0.9']*100:.1f}%  "
                     f"<0.8:{p['frac_localslope<0.8']*100:.1f}%  <0.7:{p['frac_localslope<0.7']*100:.1f}%  "
                     f"(n_L<40={p['n_pixels_L<40']})")
        L += ["", "  ── 4.D garde chroma (pixels C>10, L<40 ; out vs in) ──"]
        for name in ("V1_G-ir", "V0_s-nu", "V2_tau1.0"):
            c = r["chroma_guard"][name]
            L.append(f"     {name:<11} ΔE00 med={c['dE00_median']} P95={c['dE00_P95']}  Δh P95={c['dh_P95']}  ΔC mean={c['dC_mean']}")
        L += ["", "  ── 5. TIFFs Affinity ──"] + [f"     {k}: {Path(v).name}" for k, v in r["affinity_tiffs"].items()] + [""]
    L += ["══════ AIGUILLAGE (à conclure avec Affinity de vinz) ══════",
          "  CAS A préparation-dépendant : noirs-à-0 → population critique fortement réduite + pente moins",
          "         problématique + effet visuel faible → sujet amont, pas de contrôle freeglaz.",
          "  CAS B général mais peu perceptible : compromis numérique subsiste mais pas gênant en Affinity",
          "         → propriété physique documentée du mode -s près du Dmax ; pas de curseur.",
          "  CAS C général ET perceptible : persiste noirs-à-0 + population significative + perte visible",
          "         → ouverture d'un FUTUR jalon deep-shadow tonal control (STOP, ne rien implémenter).",
          "",
          "  Comparer population (4.A) + prévalence (4.E) + tonal (4.B) ENTRE A et B ; V0≈V2 (4.C) attribue",
          "  au mode -s ; garde chroma (4.D) doit rester saine. Le perceptuel tranche (Affinity vinz).",
          "  NB : fichiers préparés — décrire la dépendance à la préparation, ne pas surgénéraliser."]
    path.write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Deep-shadow tradeoff diagnostic (measure only).")
    ap.add_argument("--prep", action="append", required=True, help="label=path (repeatable), e.g. A=/x.tif")
    ap.add_argument("--dest", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    a = ap.parse_args()
    preps = {}
    for spec in a.prep:
        label, _, p = spec.partition("=")
        preps[label] = p
    run(preps, a.dest, a.out_dir)


if __name__ == "__main__":
    main()
