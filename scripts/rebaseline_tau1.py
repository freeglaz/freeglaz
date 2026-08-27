#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Official rebaseline at τref = 1 — HYGIENE, no new science.

Recomputes and consigns the reference non-regression guards of the Canson witness
with the REFINED Cmeasured boundary (bracketing+bisection) and τref = 1, policy
STRICTLY unchanged. τref=1 is an experimental REFERENCE condition (the onset of
the chroma↔luminance trade), NOT an optimum nor a universal value — τ will be a
UI cursor later (out of scope). Reuses the existing bench (abstract_poc).

Run: uv run python scripts/rebaseline_tau1.py --dest <canson.icc> --out-dir <dir>
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
sys.path.insert(0, str(_ROOT)); sys.path.insert(0, str(_ROOT / "scripts"))

import abstract_poc as poc                                   # noqa: E402
from lib.z9_client import devicelink, xicclu                 # noqa: E402
from webapp.backend.services.scan_delta import ciede2000     # noqa: E402

_SRC = _ROOT / "lib" / "z9_client" / "assets" / "LargeRGB-elle-V2-g18.icc"
TAU_REF = 1.0                                                # reference experimental condition (NOT optimum)


def _residuals(dest, triples):
    labs = [(L, C * math.cos(math.radians(h)), C * math.sin(math.radians(h))) for L, C, h in triples]
    dev = xicclu.run_xicclu(dest, labs, direction="b", intent="r", pcs="lab")
    ret = xicclu.run_xicclu(dest, dev, direction="f", intent="r", pcs="lab")
    return [triples[i][1] - math.hypot(ret[i][1], ret[i][2]) for i in range(len(triples))]


def refine_boundary(dest, Ls, hs, tau, prec=0.25):
    """Same bracketing+bisection as the validated refinement, τ parameterised."""
    scan = [0.5, 1, 2, 4, 8, 16, 32, 64, 100]
    triples, key = [], []
    for L in Ls:
        for h in hs:
            for C in scan:
                triples.append((L, C, h)); key.append((L, h))
    res = _residuals(dest, triples)
    br = {}
    for i, k in enumerate(key):
        br.setdefault(k, []).append((triples[i][1], res[i]))
    cells = {}
    for k, rs in br.items():
        rs.sort(); lo, hi = 0.0, None
        for C, r in rs:
            if r >= tau:
                hi = C; break
            lo = C
        cells[k] = {"lo": lo, "hi": hi if hi is not None else rs[-1][0]}
    for _ in range(40):
        mids, mk = [], []
        for k, c in cells.items():
            if c["hi"] - c["lo"] > prec:
                mids.append((k[0], (c["lo"] + c["hi"]) / 2, k[1])); mk.append(k)
        if not mids:
            break
        r = _residuals(dest, mids)
        for i, k in enumerate(mk):
            (cells[k].__setitem__("hi", mids[i][1]) if r[i] >= tau
             else cells[k].__setitem__("lo", mids[i][1]))
    return {k: round((c["lo"] + c["hi"]) / 2, 2) for k, c in cells.items()}


def run(dest: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    Ls, hs = [5, 10, 15, 20, 30, 50], list(range(0, 360, 30))
    print(f"[rebase] refine Cmeasured at τref={TAU_REF} (bracketing+bisection) …")
    refined = refine_boundary(str(dest), Ls, hs, TAU_REF)
    bcsv = out_dir / "refined_boundary_tau1.csv"
    with open(bcsv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["L", "h_N", "Cmeasured"]); w.writeheader()
        for L in Ls:
            for h in hs:
                w.writerow({"L": L, "h_N": h, "Cmeasured": refined[(L, h)]})

    with tempfile.TemporaryDirectory(prefix="rebase_") as tmp:
        tmp = Path(tmp)
        src_n = tmp / "src.icc"; src_n.write_bytes(devicelink.normalize_icc_for_argyll(_SRC.read_bytes()))
        dst_n = tmp / "dst.icc"; dst_n.write_bytes(devicelink.normalize_icc_for_argyll(dest.read_bytes()))
        cdriver = poc.Cdriver(bcsv)                           # ONLY the driver changes (τ=1)
        policy = poc.make_policy(cdriver)                    # SAME policy
        ab = tmp / "ab.icc"; ab.write_bytes(poc.build_abstract(policy, 33))

        # guards 1&2 (full chain) — SAME collink command as the POC
        print("[rebase] rebench full chain (policy unchanged) …")
        poc.collink(["-v", "-qh", "-s", "-ir", "-p", str(ab), str(src_n), str(dst_n)], tmp / "poc.icc")
        grid = [{"L": L, "C": C, "h": h,
                 "lab": (L, C * math.cos(math.radians(h)), C * math.sin(math.radians(h)))}
                for L in Ls for h in hs for C in range(0, 61, 5) if not (C == 0 and h != hs[0])]
        _, lab = poc.measure_chain(tmp / "poc.icc", src_n, dst_n, grid)
        g, _ = poc.metrics(grid, lab)

        # guard 3 (hue, abstract-alone) — Δh P95/P99 + ΔE00 at Ct>10 / >20
        vp, vm = [], []
        for L in (8, 13, 18, 23, 33, 52):
            for h in range(0, 360, 10):
                for C in (13, 22, 33, 47):
                    vp.append((L, C * math.cos(math.radians(h)), C * math.sin(math.radians(h)))); vm.append((L, C, h))
        vlut = xicclu.run_xicclu(str(ab), vp, direction="f", pcs="lab")
        dh10, dh20, de10 = [], [], []
        for i, (L, a, b) in enumerate(vp):
            La, aa, ba = policy(L, a, b); Ct = math.hypot(aa, ba)
            Ll, al, bl = vlut[i]
            dh = abs((math.degrees(math.atan2(bl, al)) - math.degrees(math.atan2(ba, aa)) + 180) % 360 - 180)
            de = ciede2000((La, aa, ba), (Ll, al, bl))
            if Ct > 10:
                dh10.append(dh); de10.append(de)
            if Ct > 20:
                dh20.append(dh)
        dh10.sort(); dh20.sort()
        def pct(x, p): return round(x[min(len(x) - 1, int(p / 100 * (len(x) - 1)))], 2) if x else None

        # sanity: L5/C60 hotspot (abstract alone)
        hp = [(5.0, C * math.cos(math.radians(h)), C * math.sin(math.radians(h)))
              for h in range(0, 360, 8) for C in (40, 50, 60)]
        hl = xicclu.run_xicclu(str(ab), hp, direction="f", pcs="lab")
        worst = {"dE00": 0}
        for i, (L, a, b) in enumerate(hp):
            La, aa, ba = policy(L, a, b); Ll, al, bl = hl[i]
            de = ciede2000((La, aa, ba), (Ll, al, bl))
            if de > worst["dE00"]:
                worst = {"C_in": round(math.hypot(a, b)), "dC": round(math.hypot(al, bl) - math.hypot(aa, ba), 2),
                         "dE00": round(de, 2)}

        baseline = {
            "witness": str(dest), "condition": "refined Cmeasured (bracketing+bisection) · τref=1",
            "guards": {
                "shadow_dLoutdCin_mean": g["shadow_dLoutdCin_mean"],
                "neutral_dLout_mean": g["neutral_dLout_mean"],
                "hue_dh_P95_Ct10": pct(dh10, 95), "hue_dh_P99_Ct10": pct(dh10, 99),
                "hue_dh_P95_Ct20": pct(dh20, 95), "hue_dh_P99_Ct20": pct(dh20, 99),
                "hue_dE00_P95_Ct10": pct(sorted(de10), 95),
            },
            "L5_hotspot_sanity": worst,
            "note": "τref=1 is a REFERENCE experimental condition, NOT an optimum nor a universal value; "
                    "the chroma↔luminance trade depends on the image; τ will be exposed as a cursor (future).",
        }
    (out_dir / "baseline_tau1.json").write_text(json.dumps(baseline, indent=2), encoding="utf-8")
    _write_baseline(out_dir / "BASELINE_tau1.txt", baseline)
    print("[rebase] guards:", baseline["guards"])
    print("[rebase] L5 hotspot:", baseline["L5_hotspot_sanity"])
    print(f"[rebase] wrote refined_boundary_tau1.csv, baseline_tau1.json, BASELINE_tau1.txt")
    return baseline


def _write_baseline(path, b):
    g = b["guards"]; w = b["L5_hotspot_sanity"]
    L = ["Baseline de non-régression — Canson Photolustre RC GE-ON (témoin)",
         f"Condition : frontière Cmeasured raffinée (bracketing+dichotomie) · τref = 1", "",
         f"  dLout/dCin (ombres)              = {g['shadow_dLoutdCin_mean']}",
         f"  neutre ΔL                        = {g['neutral_dLout_mean']}",
         f"  teinte Δh P95/P99 (Ct>10)        = {g['hue_dh_P95_Ct10']} / {g['hue_dh_P99_Ct10']}",
         f"  teinte Δh P95/P99 (Ct>20)        = {g['hue_dh_P95_Ct20']} / {g['hue_dh_P99_Ct20']}",
         f"  teinte ΔE00 P95 (Ct>10)          = {g['hue_dE00_P95_Ct10']}", "",
         f"  sanity hotspot L5/C60            = ΔC {w['dC']:+} · ΔE00 {w['dE00']} (résolu ; PAS ΔC≈-53/ΔE00≈21)",
         "",
         "NOTE : τref=1 est une CONDITION EXPÉRIMENTALE DE RÉFÉRENCE, PAS un optimum ni une valeur universelle.",
         "       Le compromis chroma↔luminance dépend de l'image ; τ sera exposé comme curseur (jalon futur).",
         "       Politique et frontière (raffinée) inchangées ; seul le point de comparaison est fixé à τref=1."]
    path.write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Official rebaseline at τref=1 (hygiene).")
    ap.add_argument("--dest", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    a = ap.parse_args()
    run(a.dest, a.out_dir)


if __name__ == "__main__":
    main()
