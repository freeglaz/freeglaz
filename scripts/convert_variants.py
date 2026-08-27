#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert experimental — 4 comparative variants on a REAL image (voie B).

FIRST time a real image traverses the ASSEMBLED Convert chain end-to-end (until
now everything was probed with xicclu on synthetic points). This bench:

  (A) runs the real image through the assembled chain per variant → device TIFF,
      REUSING the production bricks (normalize_icc_for_argyll, apply_cctiff with
      dest embed) and only SWAPPING the link build (production -G vs voie-B -s-p);
  (B) verifies NUMERICALLY that the produced TIFF == the primitives' prediction
      (cctiff faithfully realises the link: file vs xicclu link-forward) and reads
      the behaviour on real zones (deep neutrals, dark blue/magenta, shadow chroma);
  (C) the intra-profile NEUTRAL guard: neutral(-s nu) vs neutral(-s + abstract) on
      THIS profile — the abstract must not drift neutral vs -s alone, whatever the
      absolute level (the neutral level belongs to the mode/profile, NOT the policy).

4 variants (same image, same dest):
  V1  reference   -G -ir                (Argyll standard relative — the PROD path)
  V2  voie B      τ = τref = 1          (bench reference)
  V3  voie B      τ = 2   (chroma+)     (looser boundary → more chroma, more L recouple)
  V4  voie B      τ = 0.5 (luminance+)  (tighter boundary → cut chroma earlier, protect L)

τ is a cursor we OBSERVE, never "the right value". No preset, no UI, no print.
mAB/mBA refusal stays (convert_lut_support). Any file≠prediction divergence is a
REPORTED integration result, never silently patched.

Run: uv run python scripts/convert_variants.py --image <tif> --dest <icc> --out-dir <dir>
"""
from __future__ import annotations

import argparse
import csv
import gc
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT)); sys.path.insert(0, str(_ROOT / "scripts"))

import tifffile                                               # noqa: E402  (true 16-bit; PIL downconverts)

from lib.z9_client import devicelink, xicclu                  # noqa: E402
import abstract_poc as poc                                    # noqa: E402
import rebaseline_tau1 as reb                                 # noqa: E402
from webapp.backend.services.scan_delta import ciede2000      # noqa: E402

# τ points: V3 looser (chroma+), V4 tighter (luminance+). Reasonable ±1 brackets,
# NOT presets — two points either side of the reference to read the cursor's sign.
VARIANTS = [
    {"name": "V1_ref_G-ir", "mode": "ref", "tau": None},
    {"name": "V2_voieB_tau1.0", "mode": "voieB", "tau": 1.0},
    {"name": "V3_voieB_tau2.0", "mode": "voieB", "tau": 2.0},
    {"name": "V4_voieB_tau0.5", "mode": "voieB", "tau": 0.5},
]
_Ls_REFINE, _hs_REFINE = [5, 10, 15, 20, 30, 50], list(range(0, 360, 30))


# ── link building (voie B abstract at τ, or production -G reference) ───────────
def build_abstract_at(dest_orig: Path, tau: float, out_dir: Path):
    """Refine the Cmeasured boundary at τ on THIS dest, author the abstract."""
    bcsv = out_dir / f"boundary_tau{tau}.csv"
    refined = reb.refine_boundary(str(dest_orig), _Ls_REFINE, _hs_REFINE, tau)
    with open(bcsv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["L", "h_N", "Cmeasured"]); w.writeheader()
        for L in _Ls_REFINE:
            for h in _hs_REFINE:
                w.writerow({"L": L, "h_N": h, "Cmeasured": refined[(L, h)]})
    cdriver = poc.Cdriver(bcsv)
    return poc.make_policy(cdriver), bcsv


def build_link(v: dict, src_n: Path, dst_n: Path, dest_orig: Path,
               work: Path, out_dir: Path) -> Path:
    link = work / f"link_{v['name']}.icc"
    if v["mode"] == "ref":
        # PRODUCTION reference path, verbatim: collink -G -ir (via the prod brick).
        devicelink.run_collink(src_n, dst_n, link, intent="r", quality="h")
        return link
    # voie B: build abstract@τ, insert via collink -s -ir -p (the bench command).
    policy, _ = build_abstract_at(dest_orig, v["tau"], out_dir)
    ab = work / f"abstract_{v['name']}.icc"; ab.write_bytes(poc.build_abstract(policy, 33))
    poc.collink(["-v", "-qh", "-s", "-ir", "-p", str(ab), str(src_n), str(dst_n)], link)
    return link


def _neutral_dL(link: Path, src_n: Path, dst_n: Path) -> float:
    """mean ΔL_out on the neutral ramp Lab(L,0,0) through the full chain."""
    grid = [{"L": L, "C": 0, "h": 0, "lab": (L, 0.0, 0.0)} for L in range(2, 99, 3)]
    _, lab = poc.measure_chain(link, src_n, dst_n, grid)
    dl = [lab[i][0] - grid[i]["L"] for i in range(len(grid))]
    return round(sum(dl) / len(dl), 3)


def neutral_guard_intraprofile(variant_links: dict, src_n: Path, dst_n: Path,
                               work: Path) -> dict:
    """Intra-profile neutral guard: does the abstract drift the neutral axis vs
    -s ALONE on THIS profile? Replaces the absolute témoin-specific guard (0.348).

    The -s-nu reference link is τ-INDEPENDENT (built once); each voie-B variant
    link IS already ``-s -ir -p abstract@τ`` (reused, no extra collink). PASS if
    Δ(abstract vs -s nu) ≈ 0 — the absolute neutral level belongs to the
    mode/profile, the guard only asks that the abstract adds no neutral drift."""
    link_snu = work / "link_s_nu.icc"                          # -s -ir, NO -p (τ-independent)
    poc.collink(["-v", "-qh", "-s", "-ir", str(src_n), str(dst_n)], link_snu)
    base = _neutral_dL(link_snu, src_n, dst_n)
    out = {}
    for name, link in variant_links.items():
        ab_dL = _neutral_dL(link, src_n, dst_n)
        out[name] = {"s_nu": base, "s_abstract": ab_dL,
                     "delta_abstract_vs_snu": round(ab_dL - base, 3)}
    return out


# ── (B) real-image sampling + integration/behaviour verification ──────────────
def _scale16(a):
    """Normalise a tifffile array to 0..1 (true 16- or 8-bit; NOT PIL, which
    downconverts this Predictor=2 16-bit TIFF to 8-bit)."""
    import numpy as np
    return a.astype(np.float64) / (65535.0 if a.dtype == np.uint16 else 255.0)


def sample_pixels(image: Path, n_target: int = 6000):
    """Deterministic grid sample of the real image → list of (x,y,(r,g,b) 0..1)."""
    a = _scale16(tifffile.imread(str(image)))                  # (H,W,3), 0..1
    H, W = a.shape[0], a.shape[1]
    step = max(1, int(math.sqrt(W * H / n_target)))
    pts = []
    for y in range(step // 2, H, step):
        for x in range(step // 2, W, step):
            r, g, b = a[y, x, 0], a[y, x, 1], a[y, x, 2]
            pts.append((x, y, (float(r), float(g), float(b))))
    del a; gc.collect()
    return pts, (W, H)


def read_output_at(tiff: Path, coords) -> list[tuple]:
    """Read device values (0..1) from an output TIFF at given (x,y) coords.

    cctiff writes LZW 16-bit (Affinity-friendly, faithful to production); tifffile
    needs imagecodecs for LZW, which we don't add. Flatten via libtiff ``tiffcp -c
    none`` (present) into a scratch uncompressed copy, then read TRUE 16-bit."""
    with tempfile.TemporaryDirectory(prefix="cv_flat_") as td:
        flat = Path(td) / "flat.tif"
        try:
            subprocess.run(["tiffcp", "-c", "none", str(tiff), str(flat)],
                           check=True, capture_output=True, timeout=600)
            a = _scale16(tifffile.imread(str(flat)))
        except (FileNotFoundError, subprocess.CalledProcessError):
            a = _scale16(tifffile.imread(str(tiff)))            # fallback (may raise on LZW)
        vals = [(float(a[y, x, 0]), float(a[y, x, 1]), float(a[y, x, 2])) for (x, y) in coords]
        del a; gc.collect()
    return vals


def _zone(Lab):
    L, a, b = Lab
    C = math.hypot(a, b); h = math.degrees(math.atan2(b, a)) % 360.0
    if C < 4 and L < 45:
        return "deep_neutral"
    if L < 25 and C > 12 and (240 <= h <= 340):
        return "dark_blue_magenta"
    if L < 25 and C > 12:
        return "shadow_chroma"
    return "other"


def verify_variant(name: str, link: Path, tiff: Path, src_n: Path, dst_n: Path,
                   pts, lab_in: list[tuple]) -> dict:
    coords = [(x, y) for (x, y, _rgb) in pts]
    rgb = [rgb for (_x, _y, rgb) in pts]
    # prediction: link forward (device→device) per primitive
    pred_dev = xicclu.run_xicclu(str(link), rgb, direction="f")
    # actual: re-extracted from the produced device TIFF at the same coords
    act_dev = read_output_at(tiff, coords)
    n = min(len(pred_dev), len(act_dev), len(lab_in))
    # integration Δ (file vs primitive), in 8-bit-equivalent levels for readability
    dmax = dmean = 0.0
    for i in range(n):
        d = max(abs(pred_dev[i][j] - act_dev[i][j]) for j in range(3))
        dmax = max(dmax, d); dmean += d
    dmean /= n
    # behaviour: Lab_out from the ACTUAL device via dest A2B
    lab_out = xicclu.run_xicclu(str(dst_n), act_dev[:n], direction="f", intent="r", pcs="lab")
    zones = {}
    shadow_pts = []
    for i in range(n):
        Li, ai, bi = lab_in[i]; Lo, ao, bo = lab_out[i]
        Ci, Co = math.hypot(ai, bi), math.hypot(ao, bo)
        z = _zone(lab_in[i])
        zones.setdefault(z, []).append((Li, Ci, Lo, Co, ao, bo, ai, bi))
        if Li <= 20:
            shadow_pts.append((Ci, Lo))
    # dLout/dCin in shadows: slope of L_out vs C_in (least squares)
    slope = None
    if len(shadow_pts) >= 3:
        cs = [p[0] for p in shadow_pts]; ls = [p[1] for p in shadow_pts]
        mc = sum(cs) / len(cs); ml = sum(ls) / len(ls)
        num = sum((cs[i] - mc) * (ls[i] - ml) for i in range(len(cs)))
        den = sum((cs[i] - mc) ** 2 for i in range(len(cs)))
        slope = round(num / den, 4) if den else None
    zsum = {}
    for z, rows in zones.items():
        if not rows:
            continue
        dLo = sum(r[2] - r[0] for r in rows) / len(rows)      # L_out - L_in
        dC = sum(r[3] - r[1] for r in rows) / len(rows)       # C_out - C_in
        dh = [abs((math.degrees(math.atan2(r[5], r[4])) -
                   math.degrees(math.atan2(r[7], r[6])) + 180) % 360 - 180)
              for r in rows if math.hypot(r[6], r[7]) > 3 and math.hypot(r[4], r[5]) > 3]
        zsum[z] = {"n": len(rows), "mean_dL_out": round(dLo, 2),
                   "mean_dC": round(dC, 2),
                   "mean_dh": round(sum(dh) / len(dh), 2) if dh else None}
    return {"n": n,
            "integration_dev_delta_max": round(dmax, 5),
            "integration_dev_delta_mean": round(dmean, 6),
            "integration_dev_delta_max_8bit": round(dmax * 255, 2),
            "shadow_dLout_dCin_slope": slope,
            "zones": zsum}


def run(image: Path, dest: Path, out_dir: Path, fullres: bool = True):
    out_dir.mkdir(parents=True, exist_ok=True)
    src_icc = devicelink.extract_embedded_icc(image)
    if src_icc is None:
        raise SystemExit("image has no embedded ICC — cannot convert (no source space)")
    for label, icc in (("source", src_icc), ("dest", dest.read_bytes())):
        st = devicelink.convert_lut_support(icc)
        if st in ("UNSUPPORTED_MAB_MBA", "NO_USABLE_TRANSFORM"):
            raise SystemExit(f"{label} profile {st} — refused (mAB/mBA guard held)")

    work = out_dir / "_work"; work.mkdir(exist_ok=True)
    src_n = work / "source.icc"; src_n.write_bytes(devicelink.normalize_icc_for_argyll(src_icc))
    dst_n = work / "dest.icc"; dst_n.write_bytes(devicelink.normalize_icc_for_argyll(dest.read_bytes()))

    print(f"[cv] sampling real image {image.name} …", flush=True)
    pts, (W, H) = sample_pixels(image)
    lab_in = xicclu.run_xicclu(str(src_n), [rgb for (_x, _y, rgb) in pts],
                               direction="f", intent="r", pcs="lab")
    n = min(len(pts), len(lab_in)); pts = pts[:n]; lab_in = lab_in[:n]
    print(f"[cv]   {n} pixels sampled ({W}×{H})")

    results = {}
    variant_links = {}
    for v in VARIANTS:
        print(f"[cv] build link {v['name']} ({v['mode']} τ={v['tau']}) …", flush=True)
        link = build_link(v, src_n, dst_n, dest, work, out_dir)
        if v["mode"] == "voieB":
            variant_links[v["name"]] = link
        tiff = out_dir / f"{v['name']}.tif"
        print(f"[cv]   cctiff → {tiff.name} …", flush=True)
        devicelink.apply_cctiff(link, image, tiff, embed_icc=dest)   # prod brick (LZW + embed dest)
        r = verify_variant(v["name"], link, tiff, src_n, dst_n, pts, lab_in)
        results[v["name"]] = r
        print(f"[cv]   integ Δmax={r['integration_dev_delta_max_8bit']}/255 "
              f"shadow dLout/dCin={r['shadow_dLout_dCin_slope']}")

    print("[cv] intra-profile neutral guard (C) — reuse variant links + one -s-nu …", flush=True)
    guardC = neutral_guard_intraprofile(variant_links, src_n, dst_n, work)
    for name, g in guardC.items():
        print(f"[cv]   {name}: {g}")

    summary = {"image": str(image), "dest": str(dest), "size": [W, H], "n_pixels": n,
               "variants": results, "neutral_guard_intraprofile": guardC,
               "note": "τ observed as a cursor, NOT an optimum. mAB/mBA refusal held. "
                       "Any file≠prediction is a reported integration result."}
    (out_dir / "convert_variants.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _reading(out_dir / "convert_variants_reading.txt", summary)
    print(f"[cv] wrote convert_variants.json + reading + {len(VARIANTS)} device TIFFs")
    return summary


def _reading(path: Path, s: dict):
    L = ["# Convert expérimental — 4 variantes sur image réelle (voie B)", "",
         f"image: {Path(s['image']).name}  ({s['size'][0]}×{s['size'][1]}, {s['n_pixels']} px échantillonnés)",
         f"dest : {Path(s['dest']).name}", "",
         "── (B) intégrité de la chaîne assemblée : TIFF de sortie vs prédiction (link forward) ──",
         f"{'variante':<18} {'Δdev max (/255)':>16} {'Δdev mean':>12} {'shadow dLout/dCin':>18}", "-" * 68]
    for name, r in s["variants"].items():
        L.append(f"{name:<18} {r['integration_dev_delta_max_8bit']:>16} "
                 f"{r['integration_dev_delta_mean']:>12} {str(r['shadow_dLout_dCin_slope']):>18}")
    L += ["", "  Δdev = |TIFF_device − xicclu(link,rgb)| ; ~0 ⇒ cctiff réalise fidèlement le lien.",
          "  (divergence notable ⇒ bug d'intégration à SIGNALER, pas à corriger en douce.)", "",
          "── comportement par zone (mean ΔL_out, ΔC, Δh) — le SENS du curseur τ ──"]
    for name, r in s["variants"].items():
        L.append(f"\n  {name}:")
        for z, zz in sorted(r["zones"].items()):
            if z == "other":
                continue
            L.append(f"    {z:<18} n={zz['n']:<5} ΔL_out={zz['mean_dL_out']:<7} "
                     f"ΔC={zz['mean_dC']:<7} Δh={zz['mean_dh']}")
    L += ["", "── (C) garde neutre INTRA-PROFIL : l'abstract dérive-t-il le neutre vs -s nu ? ──"]
    for name, g in s["neutral_guard_intraprofile"].items():
        L.append(f"  {name}: -s nu ΔL={g['s_nu']}  |  -s+abstract ΔL={g['s_abstract']}  "
                 f"|  Δ(abstract vs -s nu)={g['delta_abstract_vs_snu']}")
    L += ["", "  Garde PASSE si Δ(abstract vs -s nu) ≈ 0 (l'abstract ne crée pas de dérive neutre propre ;",
          "  le niveau absolu du neutre appartient au mode/profil, PAS à la politique).", "",
          "READ:",
          "- Δdev ≈ 0 partout ⇒ la chaîne assemblée fait ce que les primitives annoncent (intégrité).",
          "- shadow dLout/dCin : V4(τ0.5) ≤ V2(τ1) ≤ V3(τ2) attendu (τ↑ = + de chroma = + de recouplage L).",
          "- ΔC en ombres : V4 < V2 < V3 attendu (τ↓ comprime + la chroma).",
          "- garde C : Δ≈0 ⇒ l'abstract est neutre-neutre sur CE profil (indépendant du niveau absolu)."]
    path.write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Convert experimental — 4 variants on a real image.")
    ap.add_argument("--image", required=True, type=Path)
    ap.add_argument("--dest", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--no-fullres", action="store_true", help="(reserved; cctiff runs on the full image anyway)")
    a = ap.parse_args()
    run(a.image, a.dest, a.out_dir, fullres=not a.no_fullres)


if __name__ == "__main__":
    main()
