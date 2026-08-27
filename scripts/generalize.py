#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Heterogeneous generalisation — validity domain of voie B on real Z9 residents.

Diagnosis only (no correction, no tuning, no production). Reserve = the user's
real Z9 residents (webapp/data/icc_backups + Z9 Backup), NOT a production
dependency. τref=1, policy unchanged; guards compared to abstract_poc.BASELINE_TAU1.

Phase 1 : inventory + identity card per profile (LUT type via convert_lut_support;
  version/class/PCS; gamut volume; B2A roundtrip cleanliness proxy) + a typology.
  mAB/mBA → "UNSUPPORTED — expected", counted, excluded from Phase 2.
Phase 2 (--phase2): refine Cmeasured (τ=1) + rebench the 3 guards per profile.
Phase 3 : validity-domain map (in the .md report, read through the typology).

Run: uv run python scripts/generalize.py --out-dir <dir> [--phase2 P1 P2 ...]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT)); sys.path.insert(0, str(_ROOT / "scripts"))

import abstract_poc as poc                                   # noqa: E402
from lib.z9_client import devicelink, gamut, xicclu          # noqa: E402
import rebaseline_tau1 as reb                                # noqa: E402

_HPZ9 = _ROOT.parent
_BACKUPS = _HPZ9 / "freeglaz" / "webapp" / "data" / "icc_backups"
_Z9BACKUP = _HPZ9 / "Z9 Backup"


def collect_residents() -> list[Path]:
    """Latest icc per (media-id, GE) in icc_backups + all of Z9 Backup."""
    out = []
    if _BACKUPS.exists():
        for mid in _BACKUPS.iterdir():
            for ge in ("on", "off"):
                d = mid / ge
                if d.is_dir():
                    iccs = sorted(d.glob("*.icc"))
                    if iccs:
                        out.append(iccs[-1])                  # latest timestamp
    if _Z9BACKUP.exists():
        out += sorted(_Z9BACKUP.glob("*.icc"))
    return out


def _desc(icc: bytes) -> str:
    try:
        import struct
        n = struct.unpack(">I", icc[128:132])[0]
        for i in range(n):
            o = 132 + i * 12
            if icc[o:o + 4] == b"desc":
                off = struct.unpack(">I", icc[o + 4:o + 8])[0]
                if icc[off:off + 4] == b"desc":
                    ln = struct.unpack(">I", icc[off + 8:off + 12])[0]
                    return icc[off + 12:off + 12 + ln].decode("latin1", "replace").strip("\x00")[:60]
    except Exception:
        pass
    return ""


def identity_card(path: Path) -> dict:
    icc = path.read_bytes()
    support = devicelink.convert_lut_support(icc)
    card = {"file": path.name, "path": str(path), "icc_v": icc[8],
            "class": icc[12:16].decode("latin1"), "pcs": icc[20:24].decode("latin1").strip(),
            "lut_types": sorted({v.decode("latin1", "replace").strip()
                                 for v in devicelink.lut_tag_types(icc).values()}),
            "support": support, "desc": _desc(icc)}
    if support not in ("SUPPORTED_MFT", "SUPPORTED_MATRIX"):
        return card                                          # mAB/mBA → don't probe
    # gamut volume (A2B geometry, Lab)
    try:
        card["gamut_volume_lab"] = round(gamut.extract_gamut_mesh(str(path), intent="r")["volume_lab"])
    except Exception as e:
        card["gamut_volume_lab"] = None; card["gamut_err"] = str(e)[:60]
    # B2A roundtrip cleanliness: mean |C_ret - C_in| for small in-gamut chroma,
    # + shadow floor (mean residual at L=5,10 near a moderate chroma)
    try:
        Ls, hs = [5, 10, 20, 50], list(range(0, 360, 60))
        clean, floor = [], []
        for L in Ls:
            for h in hs:
                labs = [(L, C * math.cos(math.radians(h)), C * math.sin(math.radians(h))) for C in (5, 10)]
                dev = xicclu.run_xicclu(str(path), labs, direction="b", intent="r", pcs="lab")
                ret = xicclu.run_xicclu(str(path), dev, direction="f", intent="r", pcs="lab")
                for i, C in enumerate((5, 10)):
                    resid = C - math.hypot(ret[i][1], ret[i][2])
                    clean.append(abs(resid))
                    if L <= 10:
                        floor.append(resid)
        card["b2a_roundtrip_resid_mean"] = round(sum(clean) / len(clean), 3)
        card["shadow_floor_resid_mean"] = round(sum(floor) / len(floor), 3)
    except Exception as e:
        card["b2a_err"] = str(e)[:60]
    return card


def typologize(cards: list[dict]) -> dict:
    ex = [c for c in cards if c["support"] in ("SUPPORTED_MFT", "SUPPORTED_MATRIX")
          and c.get("gamut_volume_lab")]
    if not ex:
        return {}
    vols = sorted(c["gamut_volume_lab"] for c in ex)
    vmed = vols[len(vols) // 2]
    cleans = sorted(c.get("b2a_roundtrip_resid_mean", 0) for c in ex)
    cmed = cleans[len(cleans) // 2]
    fam = {}
    for c in ex:
        g = "wide" if c["gamut_volume_lab"] >= vmed else "narrow"
        b = "cleanB2A" if c.get("b2a_roundtrip_resid_mean", 9) <= cmed else "roughB2A"
        fam.setdefault(f"{g}_{b}", []).append(c["file"])
    return {"gamut_median": vmed, "b2a_resid_median": round(cmed, 3), "families": fam}


def phase1(out_dir: Path):
    profiles = collect_residents()
    print(f"[gen] Phase 1 — {len(profiles)} residents (icc_backups + Z9 Backup)")
    cards = []
    for p in profiles:
        c = identity_card(p)
        cards.append(c)
        print(f"[gen]   {c['support']:<20} {c['file'][:48]:<48} "
              f"vol={c.get('gamut_volume_lab')} resid={c.get('b2a_roundtrip_resid_mean')} "
              f"floor={c.get('shadow_floor_resid_mean')}")
    n_mab = sum(1 for c in cards if c["support"] == "UNSUPPORTED_MAB_MBA")
    typ = typologize(cards)
    summary = {"n_profiles": len(cards), "n_mab_mba_excluded": n_mab, "typology": typ, "cards": cards}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "phase1_inventory.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with open(out_dir / "phase1_cards.csv", "w", newline="") as f:
        cols = ["file", "support", "icc_v", "class", "pcs", "gamut_volume_lab",
                "b2a_roundtrip_resid_mean", "shadow_floor_resid_mean", "desc"]
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(cards)
    print(f"[gen] mAB/mBA excluded (UNSUPPORTED — expected): {n_mab}")
    print(f"[gen] typology: {json.dumps(typ.get('families', {}), ensure_ascii=False)}")
    print(f"[gen] wrote phase1_inventory.json, phase1_cards.csv")
    return summary


def phase2(out_dir: Path, files: list[str]):
    """Rebench (refine τ=1 + guards) per named profile, compared to BASELINE_TAU1."""
    prof_by_name = {p.name: p for p in collect_residents()}
    base = poc.BASELINE_TAU1
    rows = []
    for name in files:
        p = prof_by_name.get(name)
        if p is None:
            print(f"[gen]   (not found: {name})"); continue
        print(f"[gen] Phase 2 — {name} …", flush=True)
        try:
            b = reb.run(p, out_dir / f"p2_{name}")
        except Exception as e:
            print(f"[gen]   FAIL {name}: {e}"); rows.append({"file": name, "error": str(e)[:120]}); continue
        g = b["guards"]
        rows.append({
            "file": name,
            "shadow_dLoutdCin_mean": g["shadow_dLoutdCin_mean"],
            "d_shadow_vs_baseline": round(g["shadow_dLoutdCin_mean"] - base["shadow_dLoutdCin_mean"], 4),
            "neutral_dLout_mean": g["neutral_dLout_mean"],
            "d_neutral_vs_baseline": round(g["neutral_dLout_mean"] - base["neutral_dLout_mean"], 3),
            "hue_dh_P95_Ct20": g["hue_dh_P95_Ct20"],
            "L5C60_dE00": b["L5_hotspot_sanity"]["dE00"],
        })
        print(f"[gen]   {name}: shadow={g['shadow_dLoutdCin_mean']} (Δ{rows[-1]['d_shadow_vs_baseline']:+}) "
              f"neutral={g['neutral_dLout_mean']} hueCt20P95={g['hue_dh_P95_Ct20']} L5ΔE00={rows[-1]['L5C60_dE00']}")
    (out_dir / "phase2_guards.json").write_text(
        json.dumps({"baseline": base, "profiles": rows}, indent=2), encoding="utf-8")
    with open(out_dir / "phase2_guards.csv", "w", newline="") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction="ignore")
            w.writeheader(); w.writerows(rows)
    print(f"[gen] wrote phase2_guards.json/csv")
    return rows


def main():
    ap = argparse.ArgumentParser(description="Voie B heterogeneous generalisation (diagnosis).")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--phase2", nargs="*", default=None, help="profile file names to rebench")
    a = ap.parse_args()
    phase1(a.out_dir)
    if a.phase2:
        phase2(a.out_dir, a.phase2)


if __name__ == "__main__":
    main()
