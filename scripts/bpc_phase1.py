#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""BPC Phase 1 — characterise the existing black-point mechanisms (bench only).

Order per mechanism: doc → binary → command → measure → interpretation. NO BPC
implementation, NO wiring, NO product dependency on lcms (oracle only).

Taxonomy (strict, never merged under "BPC"):
  (1) native black handling of the -G intents  — already characterised (la/lp).
  (2) EOTF/black-offset -I                       — display/video, out of scope.
  (3) the -b RGB→RGB forced black-point HACK     — VOLET A here.
  (4) external ICC/CMM BPC                        — the lcms oracle, VOLET C here.

Same source/dest/metrics as the audit. src = LargeRGB (bench source), dest = the
Canson témoin. Neutral device ramp R=G=B + coloured near-black points. lcms via
Pillow ImageCms (BLACKPOINTCOMPENSATION flag) — bench oracle, NEVER a product dep.

Run: uv run python scripts/bpc_phase1.py --dest <icc> --out-dir <dir>
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

from PIL import Image, ImageCms

from lib.z9_client import devicelink, xicclu
from lib.z9_client.argyll import resolve_argyll_binary
from webapp.backend.services.scan_delta import ciede2000

_SRC = _ROOT / "lib" / "z9_client" / "assets" / "LargeRGB-elle-V2-g18.icc"
_N = 64                                     # neutral ramp resolution (device R=G=B)


def _collink(args, out, tmp):
    cl = resolve_argyll_binary("collink")
    p = subprocess.run([cl, "-v", "-qh", *args, str(out)], capture_output=True, text=True, timeout=900)
    if p.returncode != 0 or not Path(out).exists():
        raise RuntimeError(f"collink {' '.join(args)} failed: {(p.stderr or p.stdout)[:200]}")
    return (p.stdout or "") + (p.stderr or "")


# ── lcms oracle (Pillow ImageCms) — Relative Colorimetric, BPC on/off ─────────
def lcms_map(src_icc: Path, dst_icc: Path, rgb01, bpc: bool):
    """Map source device rgb (0..1) → dest device (0..1) via lcms Relative
    Colorimetric, BPC on/off. 8-bit path (enough for the ramp/point shape)."""
    sp = ImageCms.ImageCmsProfile(str(src_icc))
    dp = ImageCms.ImageCmsProfile(str(dst_icc))
    flags = ImageCms.Flags.BLACKPOINTCOMPENSATION if bpc else 0
    tr = ImageCms.buildTransformFromOpenProfiles(
        sp, dp, "RGB", "RGB",
        renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC, flags=flags)
    im = Image.new("RGB", (len(rgb01), 1))
    im.putdata([tuple(int(round(max(0, min(1, c)) * 255)) for c in t) for t in rgb01])
    out = ImageCms.applyTransform(im, tr)
    return [(r / 255.0, g / 255.0, b / 255.0) for (r, g, b) in out.getdata()]


# ── measurement on a common frame ─────────────────────────────────────────────
def _neutral_ramp():
    return [(i / (_N - 1), i / (_N - 1), i / (_N - 1)) for i in range(_N)]


def _colored_points():
    """Near-black coloured Lab points (probe the chroma effect of each mechanism)."""
    pts = []
    for L in (5, 10, 15, 20):
        for h in range(0, 360, 45):
            for C in (15, 30):
                pts.append((L, C * math.cos(math.radians(h)), C * math.sin(math.radians(h))))
    return pts


def _slope(xs, ys, lo, hi):
    idx = [i for i in range(len(xs)) if lo <= xs[i] < hi]
    if len(idx) < 3:
        return None
    mx = sum(xs[i] for i in idx) / len(idx); my = sum(ys[i] for i in idx) / len(idx)
    den = sum((xs[i] - mx) ** 2 for i in idx)
    return round(sum((xs[i] - mx) * (ys[i] - my) for i in idx) / den, 4) if den else None


def measure_neutral(dst_n: Path, src_n: Path, ramp_dev, dest_dev_getter):
    """Given a mapper source-device→dest-device, return endpoint/redistribution/
    separation on the neutral ramp (Lin from src A2B, Lout from dest A2B)."""
    lin = [l[0] for l in xicclu.run_xicclu(str(src_n), ramp_dev, direction="f", intent="r", pcs="lab")]
    dest_dev = dest_dev_getter(ramp_dev)
    lout = [l[0] for l in xicclu.run_xicclu(str(dst_n), dest_dev, direction="f", intent="r", pcs="lab")]
    n = min(len(lin), len(lout))
    return {
        "black_endpoint_Lout": round(lout[0], 3),
        "redistribution_ramp": {f"{ramp_dev[i][0]:.3f}": round(lout[i], 2)
                                 for i in range(0, n, max(1, n // 10))},
        "slope_L<12": _slope(lin[:n], lout[:n], 0, 12),
        "slope_L<25": _slope(lin[:n], lout[:n], 0, 25),
        "slope_L<50": _slope(lin[:n], lout[:n], 0, 50),
    }


def measure_chroma(dst_n: Path, src_n: Path, lab_pts, dest_dev_getter):
    """Coloured near-black points: ΔL/ΔC/Δh/ΔE00 (source Lab → dest, out vs in)."""
    src_dev = xicclu.run_xicclu(str(src_n), lab_pts, direction="b", intent="r", pcs="lab")
    dest_dev = dest_dev_getter(src_dev)
    lab_out = xicclu.run_xicclu(str(dst_n), dest_dev, direction="f", intent="r", pcs="lab")
    dL, dC, dh, dE = [], [], [], []
    n = min(len(lab_pts), len(lab_out))
    for i in range(n):
        Li, ai, bi = lab_pts[i]; Lo, ao, bo = lab_out[i]
        dL.append(Lo - Li); dC.append(math.hypot(ao, bo) - math.hypot(ai, bi))
        dh.append(abs((math.degrees(math.atan2(bo, ao)) - math.degrees(math.atan2(bi, ai)) + 180) % 360 - 180))
        dE.append(ciede2000((Li, ai, bi), (Lo, ao, bo)))
    dhs = sorted(dh)
    return {"n": n, "dL_mean": round(sum(dL) / n, 2), "dC_mean": round(sum(dC) / n, 2),
            "dh_P95": round(dhs[min(n - 1, int(0.95 * (n - 1)))], 2),
            "dE00_median": round(sorted(dE)[n // 2], 2)}


def run(dest: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bpc1_") as tmp:
        tmp = Path(tmp)
        src_n = tmp / "src.icc"; src_n.write_bytes(devicelink.normalize_icc_for_argyll(_SRC.read_bytes()))
        dst_n = tmp / "dst.icc"; dst_n.write_bytes(devicelink.normalize_icc_for_argyll(dest.read_bytes()))

        # dest neutral black point (Lmin) for reference
        bd = xicclu.run_xicclu(str(dst_n), [(0, 0, 0)], direction="b", intent="r", pcs="lab")
        Lmin = round(xicclu.run_xicclu(str(dst_n), bd, direction="f", intent="r", pcs="lab")[0][0], 3)

        # ── VOLET A: -b (and baselines) via collink links ──
        base = tmp / "base.icc"; _collink(["-s", "-ir", str(src_n), str(dst_n)], base, tmp)          # -s -ir
        bhack = tmp / "bhack.icc"; vb = _collink(["-s", "-ir", "-b", str(src_n), str(dst_n)], bhack, tmp)  # -s -ir -b
        gb = tmp / "gb.icc"; _collink(["-G", "-ir", "-b", str(src_n), str(dst_n)], gb, tmp)          # -G -ir -b

        def link_getter(link):
            return lambda dev: xicclu.run_xicclu(str(link), dev, direction="f")

        ramp = _neutral_ramp(); cpts = _colored_points()
        mechanisms = {
            "baseline_s_ir": link_getter(base),
            "b_hack_s_ir_b": link_getter(bhack),
            "b_hack_G_ir_b": link_getter(gb),
            "lcms_relative_noBPC": lambda dev: lcms_map(src_n, dst_n, dev, bpc=False),
            "lcms_relative_BPC": lambda dev: lcms_map(src_n, dst_n, dev, bpc=True),   # THE ORACLE
        }
        results = {}
        for name, getter in mechanisms.items():
            print(f"[bpc1] measuring {name} …", flush=True)
            results[name] = {"neutral": measure_neutral(dst_n, src_n, ramp, getter),
                             "chroma_near_black": measure_chroma(dst_n, src_n, cpts, getter)}
            e = results[name]["neutral"]; c = results[name]["chroma_near_black"]
            print(f"[bpc1]   endpoint={e['black_endpoint_Lout']} slopeL<25={e['slope_L<25']} "
                  f"chroma ΔL={c['dL_mean']} ΔC={c['dC_mean']} Δh95={c['dh_P95']}", flush=True)

        summary = {"dest": str(dest), "dest_Lmin_neutral": Lmin,
                   "b_verbose_snippet": [l for l in vb.splitlines() if "lack" in l.lower()][:6],
                   "results": results,
                   "note": "lcms = bench oracle only (Pillow ImageCms, lcms 2.18); never a product dep."}
        (out_dir / "bpc_phase1.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        _reading(out_dir / "bpc_phase1_reading.txt", summary)
        print("[bpc1] wrote bpc_phase1.json + reading", flush=True)
        return summary


def _reading(path, s):
    order = ["baseline_s_ir", "b_hack_s_ir_b", "b_hack_G_ir_b", "lcms_relative_noBPC", "lcms_relative_BPC"]
    L = ["# BPC Phase 1 — caractérisation (-b · natifs · oracle lcms)", "",
         f"dest: {Path(s['dest']).name}  Lmin(neutre)={s['dest_Lmin_neutral']}",
         "lcms = ORACLE de bench (Pillow ImageCms, lcms 2.18) — jamais dépendance produit.", "",
         f"  {'mécanisme':<22}{'endpoint':>9}{'sl L<12':>8}{'sl L<25':>8}{'ΔL nb':>7}{'ΔC nb':>7}{'Δh95':>7}{'ΔE00':>7}"]
    for name in order:
        r = s["results"][name]; nu = r["neutral"]; c = r["chroma_near_black"]
        L.append(f"  {name:<22}{nu['black_endpoint_Lout']:>9}{str(nu['slope_L<12']):>8}{str(nu['slope_L<25']):>8}"
                 f"{c['dL_mean']:>7}{c['dC_mean']:>7}{c['dh_P95']:>7}{c['dE00_median']:>7}")
    L += ["", "  redistribution ramp (device R=G=B → L_out) :"]
    for name in order:
        rr = s["results"][name]["neutral"]["redistribution_ramp"]
        L.append(f"    {name:<22} " + "  ".join(f"{k}:{v}" for k, v in list(rr.items())[:8]))
    L += ["", "LECTURE (→ rapport, avec verdict D1/D2/D3) :",
          "- endpoint : BPC/lcms remonte-t-il le noir source au-dessus du Dmax (Lmin) vs baseline (clip) ?",
          "- redistribution : le scaling global lcms vs le comportement de -b (pin d'endpoint ? scaling ?).",
          "- séparation : slope L<12/L<25 (le BPC préserve-t-il la séparation en remontant ?).",
          "- chroma : ΔC/Δh près du noir (le scaling XYZ touche la couleur — MESURÉ, pas supposé)."]
    path.write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="BPC Phase 1 characterisation (bench only).")
    ap.add_argument("--dest", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    a = ap.parse_args()
    run(a.dest, a.out_dir)


if __name__ == "__main__":
    main()
