#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Encoding-invariance + tonal-separation DIAGNOSTIC (voie B). Measure only.

Two hypotheses to DISJOIN for the "modified shadow gradient" vinz saw in Affinity:
  H1 (algo) — voie B redistributes tonal separation while compressing chroma.
  H2 (encoding) — the whole chain was validated ONLY on L* source; if the device
     output DEPENDS on the source encoding (L* / γ2.2 / γ1.8 of the SAME content),
     that is a LINEARISATION bug, not a voie-B property.

VOLET A — source-encoding invariance (decides H2):
  Principle: device(source L*) MUST equal device(source γ2.2) == device(γ1.8).
  The encoding is only a representation of the same colorimetry. We build the REAL
  Convert links per encoding (embedded profile read → collink) and compare device
  outputs across encodings, per variant (V1 -G -ir, V2 voie B τ=1). We rely on the
  established cctiff==xicclu(link) identity (≤0.01/255, prior jalon) for the dense
  numeric sweep, and add a REAL cctiff crop cross-check in a shadow tile.

VOLET B — tonal separation dLout/dLin in shadows (quantifies H1):
  On the L* encoding, shadows: slope of L_out vs L_in (separation preservation)
  for source→V1 vs source→V2, plus black depth (absolute L lift). Objectivises the
  arbitrage vinz saw (V2 deeper black, V1 better separation?).

NB: the image is a PREPARED file (raised blacks). Findings hold for this flow, NOT
transposable as-is to a blacks-at-0 file (mentioned, not tested here).

Run: uv run python scripts/encoding_invariance.py --out-dir <dir>
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT)); sys.path.insert(0, str(_ROOT / "scripts"))

import tifffile
import numpy as np

from lib.z9_client import devicelink, xicclu                  # noqa: E402
import abstract_poc as poc                                    # noqa: E402
import convert_variants as cv                                 # noqa: E402 (build_abstract_at, _scale16)

_D = "/Volumes/MARCEL-MEETS-CUNEGONDE/PHOTOS_RAWS_BACKUP/_tmp/"
ENCODINGS = {
    "Lstar": _D + "_DSC2374_rec2020-lstar_vif-2.tif",
    "g22":   _D + "_DSC2374_rec2020-g22_vif.tif",
    "g18":   _D + "_DSC2374_rec2020-g18_vif.tif",
}
DEST = "/Users/vinz/Library/ColorSync/Profiles/hpz9_canson-photolustre-rc_ge-on.icc"


def _coords_grid(shape, n_target=8000):
    H, W = shape[0], shape[1]
    step = max(1, int(math.sqrt(W * H / n_target)))
    return [(x, y) for y in range(step // 2, H, step) for x in range(step // 2, W, step)]


def _rgb_at(arr01, coords):
    return [(float(arr01[y, x, 0]), float(arr01[y, x, 1]), float(arr01[y, x, 2])) for (x, y) in coords]


def build_links(dest: Path, work: Path, out_dir: Path):
    """Per-encoding V1 (-G -ir) and V2 (voie B τ=1) links. Abstract depends only on
    dest → built once; only the source profile in collink changes per encoding."""
    dst_n = work / "dest.icc"; dst_n.write_bytes(devicelink.normalize_icc_for_argyll(Path(dest).read_bytes()))
    policy, _ = cv.build_abstract_at(dest, 1.0, out_dir)       # τref=1
    ab = work / "abstract_tau1.icc"; ab.write_bytes(poc.build_abstract(policy, 33))
    links = {}
    for enc, p in ENCODINGS.items():
        src_icc = devicelink.extract_embedded_icc(p)
        src_n = work / f"src_{enc}.icc"; src_n.write_bytes(devicelink.normalize_icc_for_argyll(src_icc))
        v1 = work / f"link_{enc}_V1.icc"
        devicelink.run_collink(src_n, dst_n, v1, intent="r", quality="h")     # -G -ir (prod)
        v2 = work / f"link_{enc}_V2.icc"
        poc.collink(["-v", "-qh", "-s", "-ir", "-p", str(ab), str(src_n), str(dst_n)], v2)
        links[enc] = {"src_n": src_n, "V1": v1, "V2": v2}
        print(f"[enc] links built for {enc}", flush=True)
    return links, dst_n


def volet_A(links, dst_n, coords, rgb_by_enc, lab_in_by_enc, out_dir):
    """device invariance across encodings, per variant + WHERE (by L_in band)."""
    ref = "Lstar"
    res = {"content_alignment_lab_in": {}, "device_invariance": {}}
    # (0) content aligned? Lab_in across encodings vs Lstar
    for enc in ENCODINGS:
        if enc == ref:
            continue
        d = [max(abs(lab_in_by_enc[enc][i][k] - lab_in_by_enc[ref][i][k]) for k in range(3))
             for i in range(len(coords))]
        res["content_alignment_lab_in"][f"{enc}_vs_{ref}"] = {
            "dLab_max": round(max(d), 3), "dLab_mean": round(sum(d) / len(d), 4)}
    # (A) device invariance per variant
    dev = {enc: {} for enc in ENCODINGS}
    for enc in ENCODINGS:
        for var in ("V1", "V2"):
            dev[enc][var] = xicclu.run_xicclu(str(links[enc][var]), rgb_by_enc[enc], direction="f")
    Lref = [l[0] for l in lab_in_by_enc[ref]]
    for var in ("V1", "V2"):
        per = {}
        for enc in ENCODINGS:
            if enc == ref:
                continue
            alld, shad, mid, hi = [], [], [], []
            m = min(len(dev[enc][var]), len(dev[ref][var]), len(Lref))
            for i in range(m):
                dd = max(abs(dev[enc][var][i][k] - dev[ref][var][i][k]) for k in range(3))
                alld.append(dd)
                (shad if Lref[i] < 20 else mid if Lref[i] < 50 else hi).append(dd)
            def band(x):
                return {"n": len(x), "max_8bit": round(max(x) * 255, 2) if x else None,
                        "mean_8bit": round(sum(x) / len(x) * 255, 4) if x else None} if x else None
            per[f"{enc}_vs_{ref}"] = {"all": band(alld), "shadows_L<20": band(shad),
                                      "mid_20-50": band(mid), "high_L>50": band(hi)}
        res["device_invariance"][var] = per
    return res, dev


def volet_A_realchain(links, out_dir, work):
    """REAL cctiff cross-check on a shadow tile: device invariance from the actual
    writer (not xicclu). Picks the darkest 1200² tile from the L* image (aligned
    coords across encodings), cctiff each encoding's crop through V1 & V2."""
    # locate darkest tile via a coarse luma scan on the L* array
    aL = cv._scale16(tifffile.imread(ENCODINGS["Lstar"]))
    H, W = aL.shape[:2]; T = 1200
    ys = range(0, H - T, (H - T) // 6 or 1); xs = range(0, W - T, (W - T) // 6 or 1)
    best, bx, by = 9, 0, 0
    for y in ys:
        for x in xs:
            mluma = float(aL[y:y + T:64, x:x + T:64].mean())
            if mluma < best:
                best, bx, by = mluma, x, y
    del aL
    out = {}
    for var in ("V1", "V2"):
        crops = {}
        for enc, p in ENCODINGS.items():
            a = cv._scale16(tifffile.imread(p))[by:by + T, bx:bx + T]
            u16 = (a * 65535.0 + 0.5).astype(np.uint16)
            cin = work / f"crop_{enc}.tif"; tifffile.imwrite(str(cin), u16)
            cout = work / f"cropdev_{enc}_{var}.tif"
            devicelink.apply_cctiff(links[enc][var], cin, cout)   # REAL cctiff (no embed needed)
            crops[enc] = cv._scale16(tifffile.imread(str(_flat(cout, work))))
            del a, u16
        ref = crops["Lstar"]
        for enc in ("g22", "g18"):
            d = np.abs(crops[enc].astype(np.int32) - ref.astype(np.int32))
            out.setdefault(var, {})[f"{enc}_vs_Lstar"] = {
                "tile": [bx, by, T], "tile_mean_luma": round(best, 4),
                "dev_delta_max_16bit": int(d.max()), "dev_delta_max_8bit": round(int(d.max()) / 257, 3),
                "dev_delta_mean_16bit": round(float(d.mean()), 3)}
    return out


def _flat(tiff: Path, work: Path) -> Path:
    """cctiff writes LZW; flatten via tiffcp for tifffile (no imagecodecs)."""
    f = work / (tiff.stem + "_flat.tif")
    try:
        subprocess.run(["tiffcp", "-c", "none", str(tiff), str(f)], check=True, capture_output=True)
        return f
    except Exception:
        return tiff


def volet_B(links, dst_n, coords, rgb_by_enc, lab_in_by_enc, dev):
    """Tonal separation dLout/dLin in shadows on the L* encoding + black depth."""
    ref = "Lstar"
    Lin = [l[0] for l in lab_in_by_enc[ref]]
    out = {}
    lab_out = {}
    for var in ("V1", "V2"):
        lab_out[var] = xicclu.run_xicclu(str(dst_n), dev[ref][var], direction="f", intent="r", pcs="lab")
    for band_name, lo, hi in (("deep_shadow_L<12", 0, 12), ("shadow_L<25", 0, 25), ("shadow_L<40", 0, 40)):
        idx = [i for i in range(len(Lin)) if lo <= Lin[i] < hi]
        row = {"n": len(idx)}
        for var in ("V1", "V2"):
            xs = [Lin[i] for i in idx]; ys = [lab_out[var][i][0] for i in idx]
            if len(idx) >= 3:
                mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
                num = sum((xs[i] - mx) * (ys[i] - my) for i in range(len(xs)))
                den = sum((xs[i] - mx) ** 2 for i in range(len(xs)))
                slope = num / den if den else None
                # R² of L_out~L_in (monotone separation preservation)
                ssres = sum((ys[i] - (my + slope * (xs[i] - mx))) ** 2 for i in range(len(xs))) if slope is not None else None
                sstot = sum((y - my) ** 2 for y in ys)
                r2 = (1 - ssres / sstot) if (ssres is not None and sstot) else None
                lift = sum(ys[i] - xs[i] for i in range(len(idx))) / len(idx)   # black depth: L_out-L_in
                row[var] = {"dLout_dLin_slope": round(slope, 4) if slope is not None else None,
                            "R2": round(r2, 4) if r2 is not None else None,
                            "mean_L_lift": round(lift, 3)}
            else:
                row[var] = None
        out[band_name] = row
    return out


def run(out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    for enc, p in ENCODINGS.items():
        if not Path(p).exists():
            raise SystemExit(f"missing encoding {enc}: {p}")
    work = out_dir / "_work"; work.mkdir(exist_ok=True)

    # common coord grid (all encodings share dims) + rgb + lab_in per encoding
    shape = tifffile.TiffFile(ENCODINGS["Lstar"]).pages[0].shape
    coords = _coords_grid(shape)
    print(f"[enc] {len(coords)} common coords", flush=True)
    rgb_by_enc, lab_in_by_enc = {}, {}
    for enc, p in ENCODINGS.items():
        a = cv._scale16(tifffile.imread(p))
        rgb_by_enc[enc] = _rgb_at(a, coords); del a
        src_icc = devicelink.extract_embedded_icc(p)
        src_n = work / f"srcread_{enc}.icc"; src_n.write_bytes(devicelink.normalize_icc_for_argyll(src_icc))
        lab_in_by_enc[enc] = xicclu.run_xicclu(str(src_n), rgb_by_enc[enc], direction="f", intent="r", pcs="lab")
        print(f"[enc]   read {enc}", flush=True)

    links, dst_n = build_links(Path(DEST), work, out_dir)
    print("[enc] VOLET A — device invariance (xicclu(link), proven==cctiff) …", flush=True)
    A, dev = volet_A(links, dst_n, coords, rgb_by_enc, lab_in_by_enc, out_dir)
    print("[enc] VOLET A — REAL cctiff crop cross-check (shadow tile) …", flush=True)
    A_real = volet_A_realchain(links, out_dir, work)
    print("[enc] VOLET B — tonal separation dLout/dLin (L* encoding) …", flush=True)
    B = volet_B(links, dst_n, coords, rgb_by_enc, lab_in_by_enc, dev)

    summary = {"dest": DEST, "encodings": ENCODINGS, "n_coords": len(coords),
               "volet_A_invariance": A, "volet_A_realchain_cctiff": A_real, "volet_B_separation": B,
               "note": "prepared file (raised blacks); not transposable to blacks-at-0."}
    (out_dir / "encoding_invariance.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _reading(out_dir / "encoding_invariance_reading.txt", summary)
    print("[enc] wrote encoding_invariance.json + reading", flush=True)
    return summary


def _reading(path: Path, s: dict):
    L = ["# Invariance à l'encodage source + séparation tonale (diagnostic)", "",
         f"dest: {Path(s['dest']).name} ; {s['n_coords']} coords communs (3 encodages alignés)", "",
         "── contenu aligné ? Lab_in entre encodages vs L* (doit être ≈0) ──"]
    for k, v in s["volet_A_invariance"]["content_alignment_lab_in"].items():
        L.append(f"  {k}: ΔLab max={v['dLab_max']}  mean={v['dLab_mean']}")
    L += ["", "── VOLET A : device invariant à l'encodage ? (Δdevice /255, par bande de L_in) ──",
          "  [xicclu(link), == cctiff à 0.01/255 prouvé au jalon précédent]"]
    for var in ("V1", "V2"):
        L.append(f"\n  {var}:")
        for pair, bands in s["volet_A_invariance"]["device_invariance"][var].items():
            L.append(f"    {pair}:")
            for bn in ("all", "shadows_L<20", "mid_20-50", "high_L>50"):
                b = bands[bn]
                if b:
                    L.append(f"      {bn:<14} n={b['n']:<5} Δmax={b['max_8bit']}/255  Δmean={b['mean_8bit']}/255")
    L += ["", "── VOLET A : cross-check cctiff RÉEL (tuile d'ombre) ──"]
    for var, pairs in s["volet_A_realchain_cctiff"].items():
        for pair, v in pairs.items():
            L.append(f"  {var} {pair}: Δdev max={v['dev_delta_max_8bit']}/255 "
                     f"(16-bit {v['dev_delta_max_16bit']}) mean16={v['dev_delta_mean_16bit']} "
                     f"tuile luma={v['tile_mean_luma']}")
    L += ["", "── VOLET B : séparation tonale dLout/dLin (L*), source→V1 vs source→V2 ──",
          "  slope≈1 ⇒ séparation préservée (fidèle à la source) ; lift = profondeur du noir (L_out-L_in)"]
    for band, row in s["volet_B_separation"].items():
        L.append(f"\n  {band} (n={row['n']}):")
        for var in ("V1", "V2"):
            r = row.get(var)
            if r:
                L.append(f"    {var}: dLout/dLin={r['dLout_dLin_slope']}  R²={r['R2']}  lift(L_out-L_in)={r['mean_L_lift']}")
    L += ["", "VERDICT (à lire) :",
          "- VOLET A Δdevice ≈ 0 partout ⇒ H2 ÉCARTÉE (device invariant à l'encodage ; linéarisation OK).",
          "  VOLET A Δdevice notable (surtout ombres) ⇒ H2 (au moins partielle) : bug de linéarisation.",
          "- VOLET B : si slope(V2) s'écarte de 1 plus que slope(V1) ⇒ voie B redistribue la séparation (H1),",
          "  arbitrage vs profondeur (lift plus faible en V2 = noir plus profond).",
          "",
          "NB : fichier préparé (noirs relevés) — non transposable aux noirs-à-0."]
    path.write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Encoding-invariance + tonal-separation diagnostic.")
    ap.add_argument("--out-dir", required=True, type=Path)
    a = ap.parse_args()
    run(a.out_dir)


if __name__ == "__main__":
    main()
