#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Gamut mapping DIAGNOSTIC bench — measures what Argyll's collink -G links do.

Side tool, NOT part of the conversion pipeline (it never touches convert.py or
print). It pushes a synthetic L×C×h grid through the ACTUAL collink -G
DeviceLinks and measures L/C/h in→out, so the shadow behaviour (does L rise when
C rises in the pinched shadows?) can be read off numbers instead of guessed.

Measurement chain (per point), all via ``xicclu`` on real profiles/links:
  1. Lab_in  →(source profile, backward, relative colorimetric)→ source RGB
  2. srcRGB  →(collink -G link, forward)→ dest device RGB
  3. dstRGB  →(dest profile, forward)→ Lab_out
Stage 1 is a vehicle: points outside the (wide) source gamut are flagged
``src_clipped`` (round-trip check) and excluded from the derivative reads.

Sweeps intents {r, la, p, lp} — includes ``la`` (Luminance axis matched
Appearance), relevant to the luminance problem, which is NOT exposed by the
Convert feature (its ALLOWED_INTENTS stay r/p/lp) — the bench calls collink
directly, on purpose, without touching devicelink.

Outputs (--out-dir): points.csv (per point), summary.json (config + derivatives),
reading.txt (human first read). No plotting dependency (matplotlib absent) — the
CSV is ready for external plotting.

Run:
  uv run python Scripts/gamut_bench.py --dest <paper.icc> [--source <src.icc>] \
      [--intents r,la,p,lp] [--viewconds default,pp] [--quality l] --out-dir <dir>
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

# repo root on path so lib/webapp import when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.z9_client import devicelink, tiffgamut, xicclu     # noqa: E402
from lib.z9_client.argyll import resolve_argyll_binary      # noqa: E402
from webapp.backend.services.scan_delta import ciede2000    # noqa: E402  (ΔE00, reused)

# Default wide-gamut source (bundled): must contain the test points AND the dest
# gamut so stage 1 does not clip. LargeRGB-elle is very wide.
_DEFAULT_SOURCE = (Path(__file__).resolve().parents[1]
                   / "lib" / "z9_client" / "assets" / "LargeRGB-elle-V2-g18.icc")

BENCH_INTENTS = ("r", "la", "p", "lp")     # collink -i choices (incl. la)
BENCH_VIEWCONDS = ("default", "pp", "pc", "pe", "pm")
_VC_NONE = ("default", "", None)


# ─── L×C×h grid ──────────────────────────────────────────────────────────────
def lab_of(L: float, C: float, h_deg: float) -> tuple[float, float, float]:
    r = math.radians(h_deg)
    return (L, C * math.cos(r), C * math.sin(r))


def lch_of(L: float, a: float, b: float) -> tuple[float, float, float]:
    C = math.hypot(a, b)
    h = math.degrees(math.atan2(b, a)) % 360.0
    return (L, C, h)


def hue_delta(h_in: float, h_out: float) -> float:
    """Signed angular hue shift in degrees, wrapped to [-180, 180]."""
    d = (h_out - h_in + 180.0) % 360.0 - 180.0
    return d


def build_grid(Ls, Cs, hs) -> list[dict]:
    """L×C×h grid. C=0 rows carry a single (neutral) point per L (h irrelevant)."""
    grid = []
    for L in Ls:
        for h in hs:
            for C in Cs:
                if C == 0 and h != hs[0]:
                    continue                       # one neutral point per L
                a, bb = lab_of(L, C, h)[1:]
                grid.append({"L_in": L, "C_in": C, "h_in": h, "lab_in": (L, a, bb)})
    return grid


# ─── collink link building (direct — includes `la`) ──────────────────────────
def build_link(source_icc: Path, dest_icc: Path, out_icc: Path, *,
               intent: str, quality: str, viewcond: str,
               image_gam=None, timeout: int = 900) -> None:
    collink = resolve_argyll_binary("collink")
    argv = [collink, "-v", f"-q{quality}"]
    argv += ["-G"] if image_gam is None else ["-G", str(image_gam)]   # image-aware axis
    argv += [f"-i{intent}"]
    if viewcond not in _VC_NONE:
        argv += ["-d", viewcond]
    argv += [str(source_icc), str(dest_icc), str(out_icc)]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0 or not out_icc.exists():
        raise RuntimeError(f"collink failed ({intent}/{viewcond}): "
                           f"{(proc.stderr or proc.stdout or '').strip()[:300]}")


def grid_image_gam(source_icc: Path, src_rgb: list[tuple], out_gam: Path, tmp: Path) -> Path:
    """Image gamut (.gam) of the grid itself, rendered as a TIFF — the bench's
    stand-in for 'the image' in image-aware mode. Self-contained/reproducible;
    NOT vinz's real image (his visual judgement used real content) — measures the
    MECHANISM (does image-aware change the L behaviour on these points)."""
    import numpy as np
    from PIL import Image
    arr = np.array([list(t)[:3] for t in src_rgb], dtype=np.float64)
    n = len(arr)
    side = max(1, int(math.ceil(math.sqrt(n))))
    pad = np.zeros((side * side, 3))
    pad[:n] = arr
    img8 = np.clip(pad * 255.0, 0, 255).astype(np.uint8).reshape(side, side, 3)
    tif = tmp / "grid.tif"
    Image.fromarray(img8).save(tif, format="TIFF")
    return tiffgamut.run_tiffgamut(source_icc, tif, out_gam)


# ─── measurement ─────────────────────────────────────────────────────────────
def stage1_source_rgb(source_icc: Path, grid: list[dict]) -> tuple[list[tuple], list[bool]]:
    """Lab_in → source RGB (backward, relative colorimetric) + round-trip clip flag."""
    labs = [g["lab_in"] for g in grid]
    rgb = xicclu.run_xicclu(source_icc, labs, direction="b", intent="r", pcs="lab")
    # round-trip: srcRGB → Lab, compare to Lab_in (out-of-source-gamut → clipped)
    back = xicclu.run_xicclu(source_icc, rgb, direction="f", intent="r", pcs="lab")
    clipped = [ciede2000(labs[i], back[i]) > 1.0 for i in range(len(labs))]
    return rgb, clipped


def measure_link(link: Path, dest_icc: Path, src_rgb: list[tuple]) -> list[tuple]:
    """srcRGB → dstRGB → Lab_out."""
    dst_rgb = xicclu.run_xicclu(link, src_rgb, direction="f")
    lab_out = xicclu.run_xicclu(dest_icc, dst_rgb, direction="f", pcs="lab")
    return lab_out


def metrics_row(g: dict, lab_out: tuple) -> dict:
    L_in, C_in, h_in = g["L_in"], g["C_in"], g["h_in"]
    L_out, C_out, h_out = lch_of(*lab_out)
    return {
        "L_in": L_in, "C_in": C_in, "h_in": h_in,
        "L_out": round(L_out, 3), "C_out": round(C_out, 3), "h_out": round(h_out, 3),
        "dL": round(L_out - L_in, 3), "dC": round(C_out - C_in, 3),
        "dh": round(hue_delta(h_in, h_out), 3),
        "dE00": round(ciede2000(g["lab_in"], lab_out), 3),
    }


# ─── derivatives ─────────────────────────────────────────────────────────────
def derivatives(rows: list[dict], shadow_L_max: float = 20.0) -> dict:
    """dLout/dCin along each (L_in, h) C-ramp ; dLout/dLin along each (C_in, h)
    L-ramp. Summaries focus on the shadows (L_in <= shadow_L_max)."""
    def slope(points, xk, yk):
        pts = sorted(points, key=lambda p: p[xk])
        out = []
        for i in range(1, len(pts)):
            dx = pts[i][xk] - pts[i - 1][xk]
            if dx:
                out.append((pts[i - 1][xk], pts[i][xk], (pts[i][yk] - pts[i - 1][yk]) / dx))
        return out

    # group for C-ramps: (L_in, h_in)
    c_ramps, l_ramps = {}, {}
    for r in rows:
        c_ramps.setdefault((r["L_in"], r["h_in"]), []).append(r)
        l_ramps.setdefault((r["C_in"], r["h_in"]), []).append(r)

    dLdC, dLdL = [], []
    for (L, h), pts in c_ramps.items():
        for x0, x1, s in slope(pts, "C_in", "L_out"):
            dLdC.append({"L_in": L, "h_in": h, "C_from": x0, "C_to": x1, "dLout_dCin": round(s, 4)})
    for (C, h), pts in l_ramps.items():
        for x0, x1, s in slope(pts, "L_in", "L_out"):
            dLdL.append({"C_in": C, "h_in": h, "L_from": x0, "L_to": x1, "dLout_dLin": round(s, 4)})

    shadow_dLdC = [d["dLout_dCin"] for d in dLdC if d["L_in"] <= shadow_L_max]
    return {
        "dLout_dCin": dLdC,
        "dLout_dLin": dLdL,
        "shadow_summary": {
            "shadow_L_max": shadow_L_max,
            "n_shadow_slopes": len(shadow_dLdC),
            "dLout_dCin_mean": round(sum(shadow_dLdC) / len(shadow_dLdC), 4) if shadow_dLdC else None,
            "dLout_dCin_max": round(max(shadow_dLdC), 4) if shadow_dLdC else None,
        },
    }


def global_luminance(rows: list[dict], midhigh_L_min: float = 30.0) -> dict:
    """Second grievance: does the intent lift OUTPUT luminance globally (not just
    in shadows)? ΔL_out = L_out - L_in. >0 = luminance lifted. Measured on
    neutrals (C_in==0, pure tone curve), on near-neutrals (C_in<=10), on
    mid/high tones (L_in>=midhigh_L_min), and overall — plus the neutral ramp."""
    def mean(xs):
        return round(sum(xs) / len(xs), 3) if xs else None

    neutral = [r for r in rows if r["C_in"] == 0]
    near_neutral = [r for r in rows if r["C_in"] <= 10]
    midhigh = [r for r in rows if r["L_in"] >= midhigh_L_min]
    ramp = sorted([(r["L_in"], r["L_out"], r["dL"]) for r in neutral], key=lambda t: t[0])
    return {
        "neutral_dL_mean": mean([r["dL"] for r in neutral]),
        "near_neutral_dL_mean": mean([r["dL"] for r in near_neutral]),
        "midhigh_dL_mean": mean([r["dL"] for r in midhigh]),
        "overall_dL_mean": mean([r["dL"] for r in rows]),
        "midhigh_L_min": midhigh_L_min,
        "neutral_ramp": [{"L_in": L, "L_out": round(Lo, 2), "dL": round(dl, 2)} for L, Lo, dl in ramp],
    }


# ─── run ─────────────────────────────────────────────────────────────────────
def run_bench(dest_icc: Path, source_icc: Path, intents, viewconds, quality, out_dir: Path,
              image_aware_modes=(False,)):
    out_dir.mkdir(parents=True, exist_ok=True)
    Ls = [5, 10, 15, 20, 30, 50]
    Cs = [0, 5, 10, 15, 20, 30, 40, 50, 60]
    hs = [0, 45, 90, 135, 180, 225, 270, 315]
    grid = build_grid(Ls, Cs, hs)

    with tempfile.TemporaryDirectory(prefix="gamut_bench_") as tmp:
        tmp = Path(tmp)
        # Argyll is v2-only: normalise both profiles first.
        src_n = tmp / "source.icc"; src_n.write_bytes(
            devicelink.normalize_icc_for_argyll(source_icc.read_bytes()))
        dst_n = tmp / "dest.icc"; dst_n.write_bytes(
            devicelink.normalize_icc_for_argyll(dest_icc.read_bytes()))

        print(f"[bench] grid: {len(grid)} points  (L={Ls} C={Cs} h={hs})  quality=-q{quality}")
        src_rgb, clipped = stage1_source_rgb(src_n, grid)
        n_clip = sum(clipped)
        print(f"[bench] source stage: {n_clip}/{len(grid)} points out of source gamut (flagged)")

        # image-aware axis: the grid's own gamut stands in for 'the image'.
        image_gam = None
        if True in image_aware_modes:
            non_clipped = [rgb for rgb, c in zip(src_rgb, clipped) if not c]
            image_gam = grid_image_gam(src_n, non_clipped, tmp / "grid.gam", tmp)
            print(f"[bench] image-aware: grid gamut extracted ({image_gam.name})")

        all_rows = []
        summary = {"dest": str(dest_icc), "source": str(source_icc),
                   "quality": quality, "grid": {"L": Ls, "C": Cs, "h": hs},
                   "n_points": len(grid), "n_src_clipped": n_clip, "conditions": {}}
        for intent in intents:
            for vc in viewconds:
                for ia in image_aware_modes:
                    tag = f"{intent}_{vc}_{'ia' if ia else 'full'}"
                    link = tmp / f"link_{tag}.icc"
                    print(f"[bench] building link -i{intent} "
                          f"{'-d '+vc if vc not in _VC_NONE else '(default vc)'} "
                          f"{'image-aware' if ia else 'full-gamut'} …", flush=True)
                    try:
                        build_link(src_n, dst_n, link, intent=intent, quality=quality,
                                   viewcond=vc, image_gam=image_gam if ia else None)
                    except RuntimeError as e:
                        print(f"[bench]   SKIP {tag}: {e}")
                        summary["conditions"][tag] = {"error": str(e)}
                        continue
                    lab_out = measure_link(link, dst_n, src_rgb)
                    rows = []
                    for i, g in enumerate(grid):
                        if clipped[i] or i >= len(lab_out):
                            continue
                        row = metrics_row(g, lab_out[i])
                        row.update({"intent": intent, "viewcond": vc,
                                    "image_aware": int(ia)})
                        rows.append(row)
                        all_rows.append(row)
                    deriv = derivatives(rows)
                    glob = global_luminance(rows)
                    summary["conditions"][tag] = {
                        "intent": intent, "viewcond": vc, "image_aware": ia,
                        "n_rows": len(rows),
                        "shadow": deriv["shadow_summary"],
                        "global_luminance": {k: v for k, v in glob.items() if k != "neutral_ramp"},
                        "neutral_ramp": glob["neutral_ramp"],
                    }
                    print(f"[bench]   {tag}: shadow dLout/dCin mean="
                          f"{deriv['shadow_summary']['dLout_dCin_mean']}  "
                          f"| global ΔL_out neutral={glob['neutral_dL_mean']} "
                          f"midhigh={glob['midhigh_dL_mean']} overall={glob['overall_dL_mean']}")

        # write outputs
        csv_path = out_dir / "points.csv"
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=[
                "intent", "viewcond", "image_aware", "L_in", "C_in", "h_in",
                "L_out", "C_out", "h_out", "dL", "dC", "dh", "dE00"])
            w.writeheader()
            w.writerows(all_rows)
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        _write_reading(out_dir / "reading.txt", summary)
        print(f"[bench] wrote {csv_path} ({len(all_rows)} rows), summary.json, reading.txt")
        return summary


def _write_reading(path: Path, summary: dict):
    """Human first read: shadow dLout/dCin per condition, sorted worst→best."""
    lines = ["# Gamut bench — first read (shadow luminance behaviour)", ""]
    lines.append(f"dest   : {summary['dest']}")
    lines.append(f"source : {summary['source']}   quality: -q{summary['quality']}")
    lines.append(f"points : {summary['n_points']}  (source-clipped, excluded: {summary['n_src_clipped']})")
    lines.append("")
    lines.append("Shadow region L*<=20 — dLout/dCin = how much OUTPUT luminance rises")
    lines.append("per unit of requested chroma. >0 = the shadow problem (L climbs with C).")
    lines.append("Goal of a future luminance-priority policy: drive this toward ~0.")
    lines.append("")
    lines.append(f"{'condition':<12} {'mean dLout/dCin':>16} {'max dLout/dCin':>16} {'rows':>6}")
    lines.append("-" * 54)
    conds = [(k, v) for k, v in summary["conditions"].items() if "shadow" in v]
    conds.sort(key=lambda kv: (kv[1]["shadow"]["dLout_dCin_mean"] is None,
                               kv[1]["shadow"]["dLout_dCin_mean"] or 0), reverse=True)
    for k, v in conds:
        s = v["shadow"]
        lines.append(f"{k:<16} {str(s['dLout_dCin_mean']):>14} "
                     f"{str(s['dLout_dCin_max']):>14} {v['n_rows']:>6}")
    lines.append("")
    lines.append("Lower (closer to 0) = shadows keep their luminance as chroma rises.")

    # ── global luminance (2nd grievance): does the intent lift L EVERYWHERE? ──
    glob_conds = [(k, v) for k, v in summary["conditions"].items() if "global_luminance" in v]
    if glob_conds:
        lines += ["", "", "=== Global luminance — ΔL_out = L_out - L_in (>0 = luminance lifted) ===",
                  "Second grievance: is L raised everywhere (neutrals / mid-high), not only shadows?", ""]
        lines.append(f"{'condition':<16} {'neutral':>9} {'near-neut':>10} {'mid-high':>9} {'overall':>9}")
        lines.append("-" * 56)
        glob_conds.sort(key=lambda kv: kv[1]["global_luminance"]["overall_dL_mean"] or 0, reverse=True)
        for k, v in glob_conds:
            g = v["global_luminance"]
            lines.append(f"{k:<16} {str(g['neutral_dL_mean']):>9} "
                         f"{str(g['near_neutral_dL_mean']):>10} {str(g['midhigh_dL_mean']):>9} "
                         f"{str(g['overall_dL_mean']):>9}")
        lines.append("")
        lines.append("neutral = C*=0 (pure tone curve). >0 there = the whole tone scale is lifted,")
        lines.append("independent of chroma → the 'raises luminance everywhere' grievance.")
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Gamut mapping diagnostic bench (measure only).")
    ap.add_argument("--dest", required=True, type=Path, help="real paper characterisation ICC")
    ap.add_argument("--source", type=Path, default=_DEFAULT_SOURCE, help="wide-gamut source ICC")
    ap.add_argument("--intents", default="r,la,p,lp")
    ap.add_argument("--viewconds", default="default,pp")
    ap.add_argument("--quality", default="l", choices=list("lmhu"))
    ap.add_argument("--image-aware", default="off", choices=("off", "on", "both"),
                    help="collink -G image-aware: off | on | both (grid gamut stands in for the image)")
    ap.add_argument("--out-dir", required=True, type=Path)
    a = ap.parse_args()
    intents = [x.strip() for x in a.intents.split(",") if x.strip()]
    vcs = [x.strip() for x in a.viewconds.split(",") if x.strip()]
    ia_modes = {"off": (False,), "on": (True,), "both": (False, True)}[a.image_aware]
    run_bench(a.dest, a.source, intents, vcs, a.quality, a.out_dir, image_aware_modes=ia_modes)


if __name__ == "__main__":
    main()
