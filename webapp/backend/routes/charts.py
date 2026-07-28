# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""FREE chart routes — creation configurator (backend).

Reuses STRICTLY the existing functions (zero duplication, never shell):
  - sol_chart : orchestrate_free_chart, format_capacity, list_charts
  - targen_strategies : targen_help, build/run_targen, extract_f_count, user presets
  - export_icc (live resident = TAG), mluc parser (_get_icc_profile_description)

Routes :
  GET  /api/charts/formats               capacity per format (A4/A3/A2/roll…)
  GET  /api/charts/targen-help           `targen` usage (exec, never shell)
  GET  /api/charts/targen-presets        user presets (flags + description)
  POST /api/charts/targen-presets        saves a user preset
  GET  /api/charts/precondition-profiles ordered -c menu (live residents + customs)
  POST /api/charts                       creates a chart (targen OR .ti1) → orchestrate
  GET  /api/charts                       library (list_charts)
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from lib.z9_client import Z9Error
from lib.z9_client import cache as _cache
from lib.z9_client import chart_geometry_refonte as G
from lib.z9_client import sol_chart as _sc
from lib.z9_client import store as _store
from lib.z9_client import targen_strategies as _tg
from lib.z9_client.printing import _get_icc_profile_description

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/charts", tags=["charts"])

_MEDIAID_REGEX = r"^[A-F0-9]{32}$"
_GE_VALUES = ("OFF", "FULLPAGE")


def get_chart_z9(request: Request):
    """Z9 dependency (overridable in test). 503 if not configured."""
    z9 = getattr(request.app.state, "z9", None)
    if z9 is None:
        raise HTTPException(503, detail="Z9Client not configured")
    return z9


# ─── GET formats ────────────────────────────────────────────────────────────
@router.get("/formats")
def get_formats() -> dict:
    """Supported formats + capacity (max patches). Backend-driven (MEDIA) →
    extensible (44\") without touching the front."""
    out = []
    for key, m in G.MEDIA.items():
        cap = _sc.format_capacity(key)
        out.append({
            "key": key, "name": m.name, "source": m.source,
            "width_mm": m.width_mm, "height_mm": m.height_mm,
            "is_roll": cap["is_roll"], "max_cols": cap["max_cols"],
            "max_rows": cap["max_rows"], "max_patches": cap["max_patches"],
            "rows_per_m": cap.get("rows_per_m"),
        })
    return {"formats": out}


# ─── targen : help + user presets ────────────────────────────────────────────
@router.get("/targen-help")
def get_targen_help() -> dict:
    from lib.z9_client.argyll import ArgyllNotFound
    try:
        return {"ok": True, "help": _tg.targen_help()}
    except (FileNotFoundError, ArgyllNotFound) as e:    # Argyll absent → service unavailable
        raise HTTPException(503, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(500, detail=str(e))


@router.get("/targen-presets")
def get_targen_presets() -> dict:
    return {"presets": _tg.list_targen_presets()}


class TargenPresetBody(BaseModel):
    key: str = Field(min_length=1)
    flags: str = Field(min_length=1)
    description: str = ""


@router.post("/targen-presets")
def post_targen_preset(body: TargenPresetBody) -> dict:
    try:
        _tg.save_targen_preset(body.key, body.flags, body.description)
    except RuntimeError as e:
        raise HTTPException(500, detail=str(e))
    return {"ok": True, "presets": _tg.list_targen_presets()}


# ─── colprof help + presets (targen SYMMETRY ; reuses strategies.py + refine) ──
def _chart_default_colprof_flags() -> list:
    """Simple neutral colprof default : -v -qh (high quality, no imposed gamut mapping).
    No -S by default : the source gamut (-S) is a user CHOICE, not a default
    (consistent with -nc cleanup ; portable — no macOS ColorSync path). The user
    can type their own -S <path/alias> in the "colprof options" field of the UI."""
    return ["-v", "-qh"]


@router.get("/colprof-help")
def get_colprof_help() -> dict:
    """Real help of the installed colprof (`colprof -?`, exec never shell). Reuses
    the same helper as refinement → a single colprof language in the app."""
    from lib.z9_client import refine as _refine
    from lib.z9_client.argyll import ArgyllNotFound
    try:
        return {"ok": True, "help": _refine.colprof_help()}
    except (FileNotFoundError, ArgyllNotFound) as e:    # Argyll absent → service unavailable
        raise HTTPException(503, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(500, detail=str(e))


@router.get("/colprof-presets")
def get_colprof_presets() -> dict:
    """colprof presets (strategies.py builtins + user overrides) + opinionated default
    line (= refinement default). The front prefills the line with the default."""
    from lib.z9_client.strategies import StrategyOps
    return {"presets": [s.to_dict() for s in StrategyOps().list_strategies()],
            "default_flags": " ".join(_chart_default_colprof_flags())}


@router.post("/colprof-presets")
def post_colprof_preset(body: TargenPresetBody) -> dict:
    """Saves a user colprof preset (reuses StrategyOps — same file
    ~/Documents/freeglaz/colprof_strategies.toml as the rest of the app)."""
    from lib.z9_client.strategies import StrategyOps
    ops = StrategyOps()
    try:
        ops.create_user_strategy(body.key, body.flags, description=body.description)
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    return {"ok": True, "presets": [s.to_dict() for s in ops.list_strategies()]}


# ─── GET precondition-profiles (ordered -c menu) ─────────────────────────────
@router.get("/precondition-profiles")
def get_precondition_profiles(
    request: Request,
    paper: str = Query(..., pattern=_MEDIAID_REGEX, description="MediaId of the slot"),
    print_ge: str = Query("OFF", description="print GE (default -c underlined)"),
    z9=Depends(get_chart_z9),
) -> dict:
    """Ordered -c menu : LIVE residents (export_icc GE OFF then GE ON if present),
    then customs/Ingenium (repo/printers), each named via mluc, + "none".
    The default = the resident with the same GE as the print (soft consistency)."""
    items: list[dict] = []
    for ge in ("OFF", "FULLPAGE"):                       # fixed order : OFF then ON
        with tempfile.NamedTemporaryFile(suffix=".icc", delete=False) as t:
            tp = Path(t.name)
        try:
            z9.paper.export_icc(ref=paper, output_path=str(tp), gloss_enhancer=ge,
                                quality="BEST", color_space="RGB")
            name = _get_icc_profile_description(tp.read_bytes()) or f"Resident GE={ge}"
            items.append({"id": f"resident:{ge}", "kind": "resident", "ge": ge,
                          "name": name, "default": ge == print_ge})
        except Z9Error:
            pass                                          # this GE does not exist on this slot
        finally:
            tp.unlink(missing_ok=True)

    pr = _cache.repo_printers_dir()
    if pr.exists():
        for icc in sorted(pr.rglob("*.icc")):
            try:
                name = _get_icc_profile_description(icc.read_bytes()) or icc.stem
            except OSError:
                name = icc.stem
            items.append({"id": f"path:{icc}", "kind": "custom", "name": name,
                          "path": str(icc)})

    items.append({"id": "none", "kind": "none", "name": "None (standard chart)"})
    return {"profiles": items}


# ─── POST create ─────────────────────────────────────────────────────────────
class CreateChartBody(BaseModel):
    media_key: str
    # IGNORED (non-fixed model : native density imposed, cols derived). Kept optional
    # for call compat ; the patch count drives the rows, not the columns.
    columns: int = Field(default=18, ge=1)
    paper_mediaid: str = Field(pattern=_MEDIAID_REGEX)
    paper_name: Optional[str] = None
    gloss_enhancer: str = "OFF"                          # OFF | FULLPAGE
    targen_flags: Optional[str] = None                  # free line (includes -f)
    ti1_text: Optional[str] = None                      # provided .ti1 (bypass targen)
    c_profile: Optional[str] = None                     # resident:OFF|resident:FULLPAGE|path:…|none
    source: Optional[str] = None
    purpose: str = "profiling"                          # profiling | validation (profcheck b)


def _resolve_precond(z9, c_profile, paper_mediaid, print_ge, workdir, warnings):
    """Resolves the -c : live resident (of the paper) or custom path. None if "none"."""
    if not c_profile or c_profile == "none":
        return None
    if c_profile.startswith("resident:"):
        ge = c_profile.split(":", 1)[1]
        if ge not in _GE_VALUES:
            raise HTTPException(422, detail=f"invalid GE -c: {ge}")
        if ge != print_ge:
            warnings.append(
                f"Preconditioning GE={ge} != print GE={print_ge}: "
                f"potentially inconsistent profiling (allowed)."
            )
        out = workdir / f"precond_{ge}.icc"
        try:
            z9.paper.export_icc(ref=paper_mediaid, output_path=str(out),
                                gloss_enhancer=ge, quality="BEST", color_space="RGB")
        except Z9Error as e:
            raise HTTPException(502, detail=f"cannot read -c profile (resident {ge}): {e}")
        return out
    if c_profile.startswith("path:"):
        p = Path(c_profile.split(":", 1)[1])
        if not p.exists():
            raise HTTPException(404, detail=f"-c profile not found: {p}")
        return p
    raise HTTPException(422, detail=f"invalid c_profile: {c_profile}")


@router.post("")
def create_chart(request: Request, body: CreateChartBody,
                 z9=Depends(get_chart_z9)) -> dict:
    if body.media_key not in G.MEDIA:
        raise HTTPException(422, detail=f"unknown media: {body.media_key}")
    if body.gloss_enhancer not in _GE_VALUES:
        raise HTTPException(422, detail=f"gloss_enhancer must be {_GE_VALUES}")
    if not body.targen_flags and not body.ti1_text:
        raise HTTPException(422, detail="Provide 'targen_flags' OR 'ti1_text'.")
    if body.purpose not in ("profiling", "validation"):
        raise HTTPException(422, detail="purpose must be 'profiling' or 'validation'")

    cap = _sc.format_capacity(body.media_key)
    warnings: list[str] = []
    targen_run = None                    # {argv, stdout, returncode} of the REAL targen run (#23)
    workdir = Path(tempfile.mkdtemp(prefix="freeglaz_chart_"))
    try:
        # 1. .ti1 : targen (path a) OR upload (path b)
        if body.ti1_text:
            ti1 = workdir / "chart.ti1"
            ti1.write_text(body.ti1_text, encoding="utf-8")
        else:
            # UNIQUE safeguard : -f ≤ max(format) UPSTREAM
            n = _tg.extract_f_count(body.targen_flags)
            if n is not None and cap["max_patches"] and n > cap["max_patches"]:
                raise HTTPException(
                    422, detail=(f"-f {n} > max {cap['max_patches']} patches on "
                                 f"{G.MEDIA[body.media_key].name} — reduce -f or "
                                 f"change format."))
            # Validation (profcheck b) : NEVER a -c (preconditioning would bias
            # the distribution toward the creation chart → would break independence).
            cprof = body.c_profile
            if body.purpose == "validation":
                if cprof and cprof not in ("none",):
                    warnings.append("purpose=validation: -c preconditioning ignored "
                                    "(independence of the validation chart).")
                cprof = "none"
            precond = _resolve_precond(z9, cprof, body.paper_mediaid,
                                       body.gloss_enhancer, workdir, warnings)
            try:
                ti1, targen_run = _tg.run_targen(body.targen_flags, workdir / "chart",
                                                 precond_icc=precond)
            except (FileNotFoundError, RuntimeError) as e:
                raise HTTPException(500, detail=f"targen: {e}")

        # 2. LIVE resident TAG (export_icc) — never sRGB, never cache
        tag = workdir / "resident_tag.icc"
        try:
            z9.paper.export_icc(ref=body.paper_mediaid, output_path=str(tag),
                                gloss_enhancer=body.gloss_enhancer,
                                quality="BEST", color_space="RGB")
        except Z9Error as e:
            raise HTTPException(502, detail=f"cannot read live resident (tag): {e}")

        # 2b. Printer SERIAL = per-serial HOME of the chart
        #     (charts/<serial>/<chart_id>/) AND storage key of the profile
        #     (repo/z9/<serial>/papers/<media_id>/). UNIQUE bridge store.get_serial
        #     (memoized live read) — the Z9 just answered export_icc, so
        #     reliable here. Required : no per-serial placement without serial.
        try:
            serial = _store.get_serial(z9)
        except Z9Error as e:
            raise HTTPException(502, detail=f"cannot read Z9 serial number: {e}")

        # 3. core (orchestrate) — reused as-is
        try:
            res = _sc.orchestrate_free_chart(
                ti1, body.media_key, body.columns,
                reference_icc_path=tag, gloss_enhancer=body.gloss_enhancer,
                source=body.source or (f"targen {body.targen_flags}"
                                       if body.targen_flags else "ti1 provided"),
                paper={"name": body.paper_name or body.paper_mediaid,
                       "media_id": body.paper_mediaid,
                       "gloss_enhancer": body.gloss_enhancer,
                       "serial": serial},
            )
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(422, detail=str(e))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    # color tag (mluc name of the embedded resident) for the result screen + persists
    # purpose in chart.json (drives the UI : colprof terminal vs profcheck).
    cm = {}
    try:
        import json
        dpath = Path(res.descriptor_path)
        desc = json.loads(dpath.read_text(encoding="utf-8"))
        cm = desc.get("color_management") or {}
        desc["purpose"] = body.purpose
        if targen_run:
            desc["targen"] = targen_run   # REAL argv + stdout (#23) — audit the flags honored
        dpath.write_text(json.dumps(desc, indent=2, ensure_ascii=False), encoding="utf-8")
    except (OSError, ValueError):
        pass

    return {
        "chart_id": res.chart_id, "chart_dir": res.chart_dir,
        "n_patches": res.n_patches, "cols": res.cols, "nrows": res.nrows,
        "purpose": body.purpose,
        "feasibility": {"ok": res.feasibility.ok, "gap_mm": round(res.feasibility.gap_mm, 2)},
        "tag": {"icc_name": cm.get("icc_name"), "gloss_enhancer": cm.get("gloss_enhancer")},
        "warnings": warnings,
    }


# ─── POST print (hardware act, guided path) ──────────────────────────────────
def _chart_dir(chart_id: str) -> Path:
    """Locates ``charts/<serial>/<chart_id>/`` by FS enumeration (client-free) :
    globally unique chart_id → no need for the serial or the connected Z9."""
    import re
    if not re.fullmatch(r"CHT-[0-9]{8}-[0-9]{4}-[0-9A-Z]{2}", chart_id):
        raise HTTPException(422, detail="invalid chart_id")
    d = _cache.locate_chart_dir(chart_id)
    if d is None:
        raise HTTPException(404, detail=f"unknown chart: {chart_id}")
    return d


def _raw_ti3_paths(chart_dir: Path) -> list[Path]:
    """RAW ti3 of a chart (one per scan), sorted. DURABLE TRUTH of the measurement
    history — anchors the scan count + concordance in `measurements/` (not the
    session layer, recent live overlay). Excludes derivatives (_avg, _qcfilt, _multisource).
    → works for ANY chart, including pre-session."""
    meas = chart_dir / "measurements"
    if not meas.is_dir():
        return []
    return sorted(p for p in meas.glob("*.ti3")
                  if not (p.stem.endswith("_avg") or p.stem.endswith("_qcfilt")
                          or p.stem.endswith("_multisource")))


def _ti3_n_sets(ti3_path: Path) -> Optional[int]:
    """Patch count of a ti3 (NUMBER_OF_SETS) — LIGHT read of the header (fallback
    when session meta is absent, e.g. pre-session chart)."""
    try:
        with ti3_path.open("r", encoding="ascii", errors="replace") as f:
            for line in f:
                if line.startswith("NUMBER_OF_SETS"):
                    return int(line.split()[1])
                if line.strip() == "BEGIN_DATA":
                    break
    except (OSError, ValueError, IndexError):
        return None
    return None


# ─── Scan role (included/excluded from profile) — DURABLE chart level (sidecar) ─
# `scan_state.json` lives with the chart (not the session) → works session AND pre-session.
# Role = DECISION (FLAG, NEVER delete of the ti3) : "included" (feeds profile + concordance) ·
# "excluded" (out of profile, but ti3 KEPT + displayed). The *why* (too fresh, failed) is
# READ from the displayed print→scan delay, not from a 3rd state. ti3 without role = "included" (default).
_SCAN_ROLES = ("included", "excluded")


def _read_scan_state(chart_dir: Path) -> dict:
    import json
    try:
        st = json.loads((chart_dir / "scan_state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    roles = {k: v for k, v in (st.get("roles") or {}).items() if v in _SCAN_ROLES}
    # MIGRATION backward-compat : old binary toggle `excluded:[ti3]` → role "excluded".
    for ti3 in (st.get("excluded") or []):
        roles.setdefault(ti3, "excluded")
    # rejected_readings : {ti3_name: [rejected SAMPLE_ID…]} — rejection per patch × scan,
    # anchored on SAMPLE_ID (never line index). Lists (serializable) ; sets on the logic side.
    rejected = {k: [str(s) for s in (v or [])]
                for k, v in (st.get("rejected_readings") or {}).items() if v}
    return {
        "roles": roles,
        "profile_built_from": list(st.get("profile_built_from") or []),
        "rejected_readings": rejected,
        "profile_built_rejections": {k: list(v or []) for k, v in
                                     (st.get("profile_built_rejections") or {}).items()},
    }


def _rejected_map(state: dict) -> dict:
    """{ti3_name: set(rejected SAMPLE_ID)} from the state (QC matching by SAMPLE_ID)."""
    return {k: set(v) for k, v in (state.get("rejected_readings") or {}).items()}


def _write_scan_state(chart_dir: Path, state: dict) -> None:
    import json
    import os
    p = chart_dir / "scan_state.json"
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _scan_role(state: dict, ti3: str) -> str:
    return (state.get("roles") or {}).get(ti3, "included")


def _included_ti3_paths(chart_dir: Path) -> list[Path]:
    """INCLUDED ti3 = the set that feeds concordance (repeatability) + the average. The
    excluded ones stay kept/displayed elsewhere (with their delay), out of the computation."""
    st = _read_scan_state(chart_dir)
    return [p for p in _raw_ti3_paths(chart_dir) if _scan_role(st, p.name) == "included"]


def _apply_patch_rejections(chart_dir: Path, paths: list[Path]) -> tuple[list[Path], list[Path]]:
    """Applies a chart's patch rejections (scan_state.rejected_readings) to ITS ti3 :
    for each scan with rejected readings, writes a filtered `_qcfilt` COPY (rejected
    rows removed, SAMPLE_ID anchoring — SAME `scan_delta.filter_ti3_text` mechanism as the
    "average" base of the current chart). Returns (paths TO CONCATENATE, temporary copies
    TO CLEAN UP). → a source contributes "included scans × kept patches", symmetric to the
    current chart (the patch averages over its remaining readings ; keeps ≥1-reading/patch upstream)."""
    from webapp.backend.services import scan_delta
    rej = _rejected_map(_read_scan_state(chart_dir))
    used: list[Path] = []
    tmp: list[Path] = []
    for p in paths:
        rj = rej.get(p.name)
        if rj:
            filt = chart_dir / "measurements" / f"{p.stem}_qcfilt.ti3"
            filt.write_text(scan_delta.filter_ti3_text(
                p.read_text(encoding="ascii", errors="replace"), rj), encoding="ascii")
            used.append(filt)
            tmp.append(filt)
        else:
            used.append(p)
    return used, tmp


def _ti3_scanned_at(ti3_name: str):
    """LOCAL (naive) datetime of the scan, from the filename YYYYMMDD_HHMMSS… (the ti3 IS
    timestamped) ; None if not parsable. Comparable to `printed_at` (also local naive) → delay."""
    from datetime import datetime
    try:
        return datetime.strptime(ti3_name.rsplit(".", 1)[0][:15], "%Y%m%d_%H%M%S")
    except (ValueError, IndexError):
        return None


class PrintChartBody(BaseModel):
    quality: str = "HIGH"


@router.post("/{chart_id}/print")
def print_chart(chart_id: str, body: PrintChartBody, request: Request,
                z9=Depends(get_chart_z9)) -> dict:
    """Prints the chart via the UNIQUE PRINT PIPELINE (the same as the print
    screen : centered compute_geometry + detect_geometry_issues + _build_lib_job
    + PrintOps.send). No reinvented placement : the chart TIFF is treated like
    any other image (the inline top-anchored one overflowed in GE → total offset). The
    scanLayout is recomputed on the EFFECTIVE placement to stay consistent with the
    scan. Hardware act : confirmation on the front side. Synchronous."""
    import json
    from lib.z9_client.printing import PrintOps, TiffInfo, fetch_resident_icc
    from lib.z9_client.sol_scanlayout import build_scan_fields_refonte
    from webapp.backend.routes.status import build_loaded_paper
    from webapp.backend.services import print_geometry, print_worker
    from webapp.backend.models import PrintParams

    d = _chart_dir(chart_id)
    desc = json.loads((d / "chart.json").read_text(encoding="utf-8"))
    cm = desc.get("color_management") or {}
    if cm.get("tag_source") != "resident-live":
        raise HTTPException(409, detail="chart not resident-tagged — regenerate it before printing")
    tiff = d / desc["files"]["tiff"]
    if not tiff.is_file():
        raise HTTPException(404, detail="chart TIFF not found")

    # ACTUALLY loaded paper (dims/id/source) — the chart must match it
    try:
        dashboard = z9.device.status()
    except Z9Error as e:
        raise HTTPException(502, detail=f"Z9 status unavailable: {e}")
    caps_cache = getattr(request.app.state, "capabilities_cache", {})
    loaded = build_loaded_paper(z9, dashboard, caps_cache)
    if loaded is None:
        raise HTTPException(409, detail="no paper loaded on the Z9")

    # ── UNIQUE placement : EXACTLY the print screen pipeline ──
    # We do NOT reinvent placement (the old inline top-anchored one overflowed in GE).
    # The chart TIFF is treated like any other image : compute_geometry
    # (validated centering) → detect_geometry_issues (printable-area safeguard) →
    # _build_lib_job → PrintOps.send. Same path as refine_print_job.
    info = TiffInfo.from_path(tiff)
    gloss = "FULLPAGE" if (cm.get("gloss_enhancer") == "FULLPAGE") else "OFF"
    params = PrintParams(gloss_enhancer=gloss, quality=body.quality, rendermode="COLOR")
    geometry = print_geometry.compute_geometry(loaded, params, info.width_mm, info.height_mm)
    g_blocking, _ = print_geometry.detect_geometry_issues(geometry, geometry.media_source)
    if g_blocking:
        raise HTTPException(422, detail="Chart outside printable area: " + " ; ".join(g_blocking))

    job = print_worker._build_lib_job(
        tiff, params, loaded, geometry, original_filename=f"freeglaz-chart-{chart_id}.tif")

    # Embed the resident read FRESH at the print go (L == F), like the photo
    # path. The chart TIFF still carries its generation-time tag (descriptive
    # only); build_pdfx4 uses this fresh override in priority. Block franc if the
    # resident cannot be read — never fall back to a possibly-stale tag.
    try:
        job.icc_override = fetch_resident_icc(z9, loaded.id, gloss, "COLOR")
    except Z9Error as e:
        raise HTTPException(502, detail=f"cannot read live resident (chart print): {e}")

    try:
        result = PrintOps(z9).send(job)
    except Exception as e:  # noqa: BLE001 (geometry/preflight/send → clear message)
        raise HTTPException(422, detail=f"Print refused: {e}")

    # ── CRITICAL scan consistency : the scanLayout MUST reflect the EFFECTIVE placement
    # (centered, on the measured sheet) — otherwise the scan looks for the patches at the
    # wrong place. We recompute from the SAME geometry as the print (single source).
    media = G.MEDIA[desc["media"]["format_key"]]
    layout = G.compute_layout(media, patch_count=desc["geometry"]["patch_count"],
                              columns=desc["geometry"]["cols"])
    scan_fields = build_scan_fields_refonte(
        layout, offset_x_mm=geometry.image_x_mm, offset_y_mm=geometry.image_y_mm)
    desc["scanLayout"]["fields"] = dict(scan_fields)
    desc["scanLayout"]["placement"] = {
        "offset_x_mm": geometry.image_x_mm, "offset_y_mm": geometry.image_y_mm,
        "mode": "print_pipeline",
        "note": "EFFECTIVE placement of the print pipeline (compute_geometry); "
                "scanLayout recomputed consistently with this real placement",
    }

    # printed_at : the chart enters the library (printed only)
    from datetime import datetime
    desc["printed_at"] = datetime.now().isoformat(timespec="seconds")
    (d / "chart.json").write_text(json.dumps(desc, indent=2, ensure_ascii=False) + "\n",
                                  encoding="utf-8")

    return {
        "ok": True, "chart_id": chart_id, "gloss": gloss,
        "paper": loaded.name, "duration_s": round(getattr(result, "duration_seconds", 0.0), 1),
    }


# ─── GET preview (preview PNG of the created chart) ──────────────────────────
@router.get("/{chart_id}/preview")
def get_chart_preview(chart_id: str):
    """PNG preview of the chart (result screen). Anti-traversal : chart_id
    fixed to the pattern CHT-…, file resolved under charts/."""
    import re
    if not re.fullmatch(r"CHT-[0-9]{8}-[0-9]{4}-[0-9A-Z]{2}", chart_id):
        raise HTTPException(422, detail="invalid chart_id")
    d = _cache.locate_chart_dir(chart_id)
    preview = (d / "chart_preview.png") if d else None
    if preview is None or not preview.is_file():
        raise HTTPException(404, detail="preview not found")
    from fastapi.responses import FileResponse
    return FileResponse(str(preview), media_type="image/png")


# ─── POST scan (hardware act) + POST profile — mode B (guided path) ──────────
class ScanChartBody(BaseModel):
    pass


@router.post("/{chart_id}/scan")
def scan_chart(chart_id: str, body: ScanChartBody, z9=Depends(get_chart_z9)) -> dict:
    """Launches the SOL scan of the LOADED chart in the BACKGROUND (NON-BLOCKING : sol_native
    ~10-25 min NEVER holds the handler → freeze pattern avoided). Returns immediately
    a snapshot ; poll GET ``/{id}/scan/status``. HARD guards : anti-concurrent,
    cooldown ≥30 s, multi-scan session lock. The ti3 is only written at OP_FINISHED_OK."""
    import json
    from webapp.backend.services import chart_scan_job, scan_session

    d = _chart_dir(chart_id)
    desc = json.loads((d / "chart.json").read_text(encoding="utf-8"))
    fields = list((desc.get("scanLayout") or {}).get("fields", {}).items())
    if not fields:
        raise HTTPException(422, detail="scanLayout missing from descriptor")
    meas = d / "measurements"

    # HARD anti-hammering guards BEFORE any session write (a refusal NEVER creates
    # a locked phantom session) :
    if chart_scan_job.is_busy():
        raise HTTPException(409, detail="A SOL scan is already running")
    rem = chart_scan_job.cooldown_remaining()
    if rem > 0:
        raise HTTPException(429, detail=f"SOL cooldown: retry in {rem:.0f} s",
                            headers={"Retry-After": str(int(rem) + 1)})

    # SERIAL of the connected Z9 = per-serial home of the sessions (the spectro is on
    # this Z9). Unique bridge store.get_serial (memoized live).
    try:
        serial = _store.get_serial(z9)
    except Z9Error as e:
        raise HTTPException(502, detail=f"cannot read Z9 serial number: {e}")

    # Session lock : reuses the active one if SAME chart ; refuses if ANOTHER chart (§3.2).
    act = scan_session.active_session(serial)
    if act is not None and act.get("chart_id") != chart_id:
        # STRUCTURED 409 → the UI offers a direct "Finish the session of CHT-X"
        # action (abandonScan) instead of a dead-end text refusal (the aggravating
        # factor of the orphan bug: no in-UI way to release an inter-chart lock).
        other = act.get("chart_id")
        raise HTTPException(409, detail={
            "code": "session_active_other_chart",
            "chart_id": other,
            "message": (f"A scan session is active on another chart ({other}). "
                        f"Finish it before scanning another chart."),
        })
    if act is not None:
        session = act
        scan_session.update_stage(session["session_id"], scan_session.STAGE_SCANNING)
    else:
        session = scan_session.create_session(
            chart_id=chart_id, serial=serial,
            door=1, stage=scan_session.STAGE_SCANNING)

    try:
        snap = chart_scan_job.start(
            host=z9.host, chart_id=chart_id, fields=fields, desc=desc,
            meas_dir=meas, session_id=session["session_id"])
    except chart_scan_job.ScanBusyError as e:        # safety net (race almost impossible single-user)
        raise HTTPException(409, detail=str(e))
    except chart_scan_job.ScanCooldownError as e:
        raise HTTPException(429, detail=str(e),
                            headers={"Retry-After": str(int(e.remaining) + 1)})
    return {"ok": True, "chart_id": chart_id,
            "session_id": session["session_id"], "job": snap}


@router.get("/{chart_id}/scan/status")
def scan_chart_status(chart_id: str) -> dict:
    """State of the background scan (state/phase/percent, ti3 at done) + active session + cooldown.
    Local (no Z9 act) → freely pollable.

    ``resume_confirmation_required`` (§3.1) : the backend EXPOSES the state "resumable session
    → chart-in-place confirmation required" (true as soon as a session has ≥1 scan and awaits the
    rest), it does NOT decide the physical reality (frontend decision/UX)."""
    from webapp.backend.services import chart_scan_job, scan_session
    # SCOPED TO THE CHART : the active session is global ("only one at a time" lock) ; we only
    # expose it here IF it belongs to THIS chart (same pattern as scan_chart_abandon).
    # Otherwise session=None → a chart's status never sees another chart's session.
    act = scan_session.active_session()
    if act is not None and act.get("chart_id") != chart_id:
        act = None
    resume_confirmation_required = bool(
        act and act.get("stage") == scan_session.STAGE_AWAITING_NEXT
        and act.get("scans"))
    # n_scans = DURABLE TRUTH from disk (measurements/*.ti3 excluding derivatives), not the session :
    # the wizard decides resume/keep on this count → works even with a finished/absent session (the
    # ti3 stays on disk). No 404 if unknown chart (_raw_ti3_paths → [] if absent).
    _cd = _cache.locate_chart_dir(chart_id)
    n_scans = len(_raw_ti3_paths(_cd)) if _cd else 0
    return {
        "job": chart_scan_job.status(),
        "session": act,
        "n_scans": n_scans,
        "resume_confirmation_required": resume_confirmation_required,
        "cooldown_remaining_s": round(chart_scan_job.cooldown_remaining(), 1),
    }


@router.post("/{chart_id}/scan/abandon")
def scan_chart_abandon(chart_id: str) -> dict:
    """Ends the active session of THIS chart (→ STAGE_CLOSED, releases the "only one
    active" lock). Single terminal : whether we built the profile or gave up, closing = releasing the
    lock. (Historic URL /scan/abandon kept — the displayed label says "Finish".)"""
    from webapp.backend.services import scan_session
    act = scan_session.active_session()
    if act is None or act.get("chart_id") != chart_id:
        raise HTTPException(404, detail="no active session on this chart")
    return scan_session.close_session(act["session_id"])


@router.get("/{chart_id}/scan/delta")
def scan_chart_delta(chart_id: str) -> dict:
    """Inter-scan ΔE2000 mini-report of the active session (≥2 ``kept`` scans) — safeguard
    BEFORE averaging ("we don't average blindly" : legitimate drying vs contamination).
    READ-ONLY (diagnostic, mutates nothing). Local, no Z9 act."""
    from webapp.backend.services import scan_delta
    d = _chart_dir(chart_id)
    # Concordance/repeatability on the INCLUDED scans only (excluded ones don't feed the
    # profile → out of delta computation). Anchored on measurements/, works pre-session.
    paths = _included_ti3_paths(d)
    if len(paths) < 2:
        raise HTTPException(409, detail="at least 2 included scans required to compare")
    try:
        return scan_delta.compute_scan_delta(paths)
    except (ValueError, OSError) as e:
        raise HTTPException(422, detail=f"ΔE comparison failed: {e}")


class ScanRoleBody(BaseModel):
    role: str   # "included" (feeds the profile) | "excluded" (out of profile but KEPT)


@router.post("/{chart_id}/scans/{ti3}/role")
def set_scan_role(chart_id: str, ti3: str, body: ScanRoleBody) -> dict:
    """Sets the ROLE of a scan : included (included, feeds the profile) · excluded (excluded, OUT of
    profile but KEPT/displayed). The REASON (too fresh, failed) is read from the displayed print→scan
    DELAY, not from a 3rd role. SOFTWARE MUTATION (feeds the profile), NO Z9 act → no
    isLoaded lock. The ti3 is NEVER deleted (role = durable flag, reversible). The
    "≥1 included scan" guard applies at the profile BUILD (cf POST /profile), not here (free role)."""
    d = _chart_dir(chart_id)
    raw = {p.name for p in _raw_ti3_paths(d)}
    if ti3 not in raw:
        raise HTTPException(404, detail=f"unknown scan: {ti3}")
    if body.role not in _SCAN_ROLES:
        raise HTTPException(422, detail=f"invalid role (expected {_SCAN_ROLES})")
    st = _read_scan_state(d)
    st["roles"][ti3] = body.role
    st["roles"] = {k: v for k, v in st["roles"].items() if k in raw}   # cleans orphan roles
    _write_scan_state(d, st)
    n_included = sum(1 for p in raw if _scan_role(st, p) == "included")
    return {"ok": True, "chart_id": chart_id, "ti3": ti3, "role": body.role, "n_included": n_included}


class RejectReadingBody(BaseModel):
    rejected: bool = True   # True = reject this reading ; False = re-include it


@router.post("/{chart_id}/scans/{ti3}/readings/{sample_id}/reject")
def set_reading_rejected(chart_id: str, ti3: str, sample_id: str, body: RejectReadingBody) -> dict:
    """Rejects/re-includes ONE reading (patch × scan) — the QC scalpel. The patch then
    averages over its remaining readings in the OTHER included scans. Anchored on SAMPLE_ID
    (NEVER the line index). SOFTWARE MUTATION, NO Z9 act, reversible (ti3 never touched).
    GUARD : forbids rejecting the LAST valid reading of a patch (≥1 non-rejected included
    reading → never a hole in the profile). The rejection only bites the "average" base (≥2 included)."""
    from webapp.backend.services import scan_delta
    d = _chart_dir(chart_id)
    raw = {p.name for p in _raw_ti3_paths(d)}
    if ti3 not in raw:
        raise HTTPException(404, detail=f"unknown scan: {ti3}")
    st = _read_scan_state(d)
    if _scan_role(st, ti3) != "included":
        raise HTTPException(409, detail="scan excluded — per-patch rejection only applies to included scans")
    try:
        if sample_id not in scan_delta.ti3_sample_ids(d / "measurements" / ti3):
            raise HTTPException(404, detail=f"unknown patch in {ti3}: {sample_id}")
    except (OSError, ValueError) as e:
        raise HTTPException(422, detail=f"unreadable ti3: {e}")
    rej = st["rejected_readings"]
    cur = set(rej.get(ti3, []))
    if body.rejected:
        # ≥1 valid reading for THIS patch across the INCLUDED scans (otherwise patch without measurement)
        rmap = _rejected_map(st)
        remaining = [p.name for p in _included_ti3_paths(d)
                     if p.name != ti3 and sample_id in scan_delta.ti3_sample_ids(p)
                     and sample_id not in rmap.get(p.name, set())]
        if not remaining:
            raise HTTPException(409, detail="last valid reading of this patch — rejection forbidden (otherwise patch without measurement)")
        cur.add(sample_id)
    else:
        cur.discard(sample_id)
    if cur:
        rej[ti3] = sorted(cur)
    else:
        rej.pop(ti3, None)
    st["rejected_readings"] = {k: v for k, v in rej.items() if k in raw}   # cleans orphans
    _write_scan_state(d, st)
    n_rejected = sum(len(v) for v in st["rejected_readings"].values())
    return {"ok": True, "chart_id": chart_id, "ti3": ti3, "sample_id": sample_id,
            "rejected": body.rejected, "n_rejected": n_rejected}


@router.get("/{chart_id}/scan/qc")
def scan_qc(chart_id: str) -> dict:
    """QC matrix patch × scan on the INCLUDED scans (≥2) : one L/a/b reading per scan,
    disagreement index per patch (on non-rejected readings), outlier flagged, rejections marked.
    READ-ONLY. Anchored on measurements/, works pre-session."""
    from webapp.backend.services import scan_delta
    d = _chart_dir(chart_id)
    paths = _included_ti3_paths(d)
    if len(paths) < 2:
        raise HTTPException(409, detail="at least 2 included scans required for the QC view")
    rmap = _rejected_map(_read_scan_state(d))
    try:
        return scan_delta.compute_scan_qc(paths, rmap)
    except (ValueError, OSError) as e:
        raise HTTPException(422, detail=f"QC view unavailable: {e}")


@router.get("/{chart_id}/scans/{ti3}/patches")
def scan_patches(chart_id: str, ti3: str) -> dict:
    """Measurements of ONE scan (≥1) : patch by patch (SAMPLE_ID + RGB device + Lab) → "see the
    measurements" + CSV export, DECOUPLED from the comparison (which requires ≥2 included). READ-ONLY."""
    from webapp.backend.services import scan_delta
    d = _chart_dir(chart_id)
    p = d / "measurements" / ti3
    if ti3 not in {x.name for x in _raw_ti3_paths(d)} or not p.is_file():
        raise HTTPException(404, detail=f"unknown scan: {ti3}")
    try:
        data = scan_delta._read_lab_rgb(p)
    except (OSError, ValueError) as e:
        raise HTTPException(422, detail=f"unreadable ti3: {e}")
    patches = [{"id": sid, "rgb": v["rgb"], "lab": [round(x, 2) for x in v["lab"]]}
               for sid, v in data.items()]
    return {"chart_id": chart_id, "ti3": ti3, "n_patches": len(patches), "patches": patches}


@router.delete("/{chart_id}/scans/{ti3}")
def delete_scan(chart_id: str, ti3: str) -> dict:
    """PERMANENTLY deletes a measurement set (ti3 + cgats) — DESTRUCTIVE sibling of "exclude"
    (exclude KEEPS the ti3, reversible ; delete ERASES it, IRREVERSIBLE). UI-side confirmation.
    Cleans the role + rejections of the sidecar for this ti3 (no dangling reference). The ICC profile
    (SEPARATE file, embedded measurements) is NOT touched, but becomes "stale" if it was built
    from this set (built_from ≠ current set — detected by the detail). LOCAL, no Z9."""
    d = _chart_dir(chart_id)
    raw = {p.name for p in _raw_ti3_paths(d)}
    if ti3 not in raw:
        raise HTTPException(404, detail=f"unknown scan: {ti3}")
    meas = d / "measurements"
    (meas / ti3).unlink(missing_ok=True)
    (meas / f"{Path(ti3).stem}.cgats").unlink(missing_ok=True)   # twin cgats (best-effort)
    st = _read_scan_state(d)
    st["roles"].pop(ti3, None)
    st["rejected_readings"].pop(ti3, None)
    _write_scan_state(d, st)
    return {"ok": True, "chart_id": chart_id, "ti3": ti3, "deleted": "measurements",
            "remaining_scans": len(raw) - 1}


class SourceProfileRef(BaseModel):
    """Reference to an existing PROFILE as a measurement source (source c).
    ONLY `path` is provided : the paper/GE identity is derived SERVER-SIDE and
    authoritatively from the adjacent manifest (_profile_identity) — zero trust in the
    client (hardening of guard c, symmetric to chart guard b). The ti3
    is extracted from the CIED tag via extract_cgats_from_icc (Z9 firmware profiles)."""
    path: str


class ProfileChartBody(BaseModel):
    colprof_flags: Optional[str] = None     # default = fixed recipe -v -qh -S AdobeRGB (cf _chart_default_colprof_flags)
    # Profile base for a multi-scan ROLL scan session (§4d) :
    #   "average" = AVERAGE of the stable scans (concat of the session's "kept" ti3 →
    #               colprof averages the repeated measurements per patch, below the mono-scan floor) ;
    #   "last"/None = last ti3 (historic behavior, unchanged).
    profile_base: Optional[str] = None
    # ADDITIONAL sources (default empty → mono-chart build UNCHANGED) :
    #   extra_chart_ids = other charts (their measurements/ ti3, multi-sheet) ;
    #   source_profiles = existing profiles (ti3 extracted from CIED). All subject
    #   to the paper+GE guard before aggregation.
    extra_chart_ids: list[str] = []
    source_profiles: list[SourceProfileRef] = []
    # User-CHOSEN name (optional). Absent → auto nomenclature (strict non-regression).
    # Present → filename base + desc (slugified, ASCII + length ≤63 validated
    # on the route side). max_length=63 = ICC V2 desc limit (cf _validate_profile_name).
    name: Optional[str] = Field(default=None, max_length=63)
    # Collision resolution intent (OS paradigm) : None/"cancel" = SAFE (collision → 409,
    # no write) ; "replace" = overwrite + preserve tags/notes ; "keep_both" = -N suffix.
    on_conflict: Optional[str] = None


_VALID_ON_CONFLICT = {"cancel", "replace", "keep_both"}


def _norm_on_conflict(value: Optional[str]) -> Optional[str]:
    """Normalizes/validates the collision intent (raises 422 if unknown value). None = safe default."""
    v = (value or "").strip().lower() or None
    if v is not None and v not in _VALID_ON_CONFLICT:
        raise HTTPException(422, detail=f"invalid on_conflict: {value!r}")
    return v


def _resolve_serial_for_ranging(desc: dict) -> Optional[str]:
    """Serial for per-paper storage. Priority to the descriptor (set at
    creation — option A). BACKWARD-COMPAT (charts from before the addition, e.g.
    CHT-20260607-1202-IY) : if absent, we take the "last known serial" from disk
    — a SINGLE Z9 known in repo/z9/ OR, failing that, in the mirror (populated by sync).
    Single-printer case (ours). Otherwise None → storage cleanly skipped (never
    an error, profiling not broken)."""
    s = (desc.get("paper") or {}).get("serial")
    if s:
        return s
    # "last known" : repo/z9 first (personal profiles), then mirror (Z9 sync).
    for base in (_cache.repo_z9_dir(), _cache.mirror_dir()):
        if base.is_dir():
            serials = [p.name for p in base.iterdir() if p.is_dir()]
            if len(serials) == 1:
                return serials[0]
    return None


def _build_profile_conflict(chart_id: str, body: "ProfileChartBody") -> Optional[dict]:
    """Collision of the target filename (custom OR auto) in repo/z9 → {name, suggestion} else None.
    Computes EXACTLY the name that ``_build_profile_now`` will write (slugified custom, else
    ``HPZ9_<paper>_GE-<slot>_<date>``) and delegates detection to ``cache.repo_z9_name_conflict``
    (single source). → enables the OS 409 for ALL names (no more silent -N without intent)."""
    import json
    from datetime import date
    try:
        desc = json.loads((_chart_dir(chart_id) / "chart.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    paper = desc.get("paper") or {}
    serial = _resolve_serial_for_ranging(desc)
    media_id = paper.get("media_id")
    if not (serial and media_id):
        return None                                  # no storage → no FS collision
    custom = (body.name or "").strip() or None
    paper_name = paper.get("name") or paper.get("media_id") or chart_id
    return _cache.repo_z9_name_conflict(
        serial, media_id, paper.get("gloss_enhancer"),
        label=(custom or paper_name),
        date_str=(None if custom else date.today().isoformat()),
        basename=(custom or None))


@router.post("/{chart_id}/profile")
def profile_chart(chart_id: str, body: ProfileChartBody) -> dict:
    """Profile build as a BACKGROUND JOB (non-blocking) : colprof (~2.5 min on ~1400 measurements)
    NEVER holds the HTTP handler ("gray screen" lesson). Returns {job} ; follow via
    GET /{id}/profile/status. Build = SOFTWARE (concat + colprof + disk storage, NO Z9).
    Anti-concurrent (a single build at a time). In test (FREEGLAZ_PROFILE_BUILD_SYNC) the build
    runs inline → the result is also flattened into the response (backward-compat)."""
    from webapp.backend.services import chart_profile_job
    d = _chart_dir(chart_id)
    oc = _norm_on_conflict(body.on_conflict)  # 422 if invalid intent (early, before any work)
    if not _included_ti3_paths(d):           # fast guard BEFORE launching the job (synchronous)
        raise HTTPException(409, detail="no included scan — keep at least one included scan")
    # Chosen name : ASCII validation (reuses B). Then, WITHOUT intent (None/cancel = safe default),
    # collision refusal BEFORE the job for ANY name (custom OR auto) → 409 + suggestion (OS
    # paradigm : the front proposes Cancel/Replace/Keep-both, replays with on_conflict). With an
    # intent (replace/keep_both), we let save_repo apply it (no 409).
    if body.name and body.name.strip():
        from webapp.backend.routes.papers import _validate_profile_name
        _validate_profile_name(body.name.strip())     # 400 if non-ASCII / forbidden chars / >63
    if oc in (None, "cancel"):
        coll = _build_profile_conflict(chart_id, body)
        if coll:
            raise HTTPException(409, detail={
                "error": "name_conflict", "name": coll["name"], "suggestion": coll["suggestion"],
                "message": f"A profile « {coll['name']} » already exists for this paper.",
            })
    try:
        snap = chart_profile_job.start(
            chart_id, lambda set_phase: _build_profile_now(chart_id, body, set_phase))
    except chart_profile_job.ProfileBuildBusyError as e:
        raise HTTPException(409, detail=str(e))
    if snap.get("state") == "error":         # sync mode : build failure → HTTP error (backward-compat)
        raise HTTPException(422, detail=snap.get("error") or "profile build failed")
    resp = {"chart_id": chart_id, "job": snap}
    if snap.get("state") == "done" and snap.get("result"):
        resp.update(snap["result"])          # backward-compat : result fields flattened
    return resp


@router.get("/{chart_id}/profile/status")
def profile_status(chart_id: str) -> dict:
    """State of the CURRENT/last build for THIS chart (background job) → re-attaches the front
    after reload. {state, phase, result|error}. idle if no build for this chart."""
    from webapp.backend.services import chart_profile_job
    st = chart_profile_job.status()
    if st and st.get("chart_id") == chart_id:
        return st
    return {"state": "idle", "chart_id": chart_id, "phase": None, "result": None, "error": None}


def _build_profile_now(chart_id: str, body: ProfileChartBody, set_phase) -> dict:
    """Builds the ICC (concat included−rejections → colprof → disk storage). Run by the
    chart_profile_job worker (background thread, or inline in test). Raises SIMPLE exceptions
    (ValueError/RuntimeError), NEVER HTTPException (outside request context). set_phase(str)
    reports progress (preparing → building). NO Z9 : colprof + disk copy."""
    import json
    import shlex
    from datetime import date
    from lib.z9_client.profiling import ProfilingOps

    set_phase("preparing")
    d = _chart_dir(chart_id)
    included_paths = _included_ti3_paths(d)    # INCLUDED scans (excluded ones don't feed the profile)
    if not included_paths:
        raise ValueError("no included scan")
    ti3 = included_paths[-1]              # last INCLUDED scan (base "last"/default)
    desc = json.loads((d / "chart.json").read_text(encoding="utf-8"))
    paper = desc.get("paper") or {}
    paper_name = paper.get("name") or paper.get("media_id") or chart_id
    # User-CHOSEN name (optional). Absent → auto nomenclature (non-regression).
    # Present → custom base (slugified) of the filename + desc. The ASCII validation + the collision
    # refusal are done AT THE ROUTE (profile_chart) BEFORE launching the job.
    custom_name = (body.name or "").strip() or None
    # ICC desc tag = base of the profile name (slugify ASCII = filename without `.icc` or suffix).
    # colprof writes it DIRECTLY via -D → no binary rewrite. (Var named `label` because
    # `desc` already refers to the chart.json dict above.)
    label = _cache.slugify(custom_name) if custom_name else _cache.repo_z9_profile_basename(
        paper.get("gloss_enhancer"), paper_name, date_str=date.today().isoformat())
    flags = shlex.split(body.colprof_flags) if body.colprof_flags else _chart_default_colprof_flags()
    # Resolve -s/-S source-gamut ALIASES (e.g. "AdobeRGB") to absolute bundled ICC
    # paths (assets/), exactly like the CLI. Percentage forms (-s 90) are left as-is.
    from lib.z9_client.config import resolve_gamut_aliases_in_flags
    try:
        flags = resolve_gamut_aliases_in_flags(flags)
    except FileNotFoundError as e:
        raise ValueError(f"invalid colprof source gamut (-s/-S): {e}") from e
    out_icc = d / f"{chart_id}.icc"                       # original in the chart folder
    serial = _resolve_serial_for_ranging(desc)
    media_id = paper.get("media_id")

    profile_ti3 = ti3
    origin = "free_chart_argyll"

    # Base "average" (§4d) : AVERAGE of the INCLUDED scans (durable measurements/ set, excluding excluded).
    # concat of the ti3 (≥2) dedup="keep" → colprof averages the repeated measurements per patch (below
    # the mono-scan floor ≈0.10 ΔE). Reuses concat_ti3 (validated). base="last"/None →
    # last included. Works session AND pre-session chart.
    # Patch rejections : applied ONLY to the "average" base (the multi-scan scalpel).
    # Each included scan with rejected readings is concatenated via a filtered COPY (rejected
    # rows removed, SAMPLE_ID anchoring) → colprof averages the patch over its remaining readings
    # in the other scans. The ≥1-valid-reading-per-patch guard (POST reject) prevents any hole.
    _state_for_build = _read_scan_state(d)
    _rej_for_build = _rejected_map(_state_for_build)
    averaged = False
    if body.profile_base == "average":
        paths = included_paths
        if len(paths) >= 2:
            from webapp.backend.services import scan_delta
            from lib.z9_client.multipass import concat_ti3
            concat_paths: list[Path] = []
            for p in paths:
                rj = _rej_for_build.get(p.name)
                if rj:
                    filt = d / "measurements" / f"{p.stem}_qcfilt.ti3"
                    filt.write_text(scan_delta.filter_ti3_text(
                        p.read_text(encoding="ascii", errors="replace"), rj), encoding="ascii")
                    concat_paths.append(filt)
                else:
                    concat_paths.append(p)
            merged = d / "measurements" / f"{chart_id}_avg.ti3"
            try:
                concat_ti3(concat_paths, merged,
                           labels=[f"scan{i + 1}" for i in range(len(concat_paths))],
                           dedup_strategy="keep", descriptor=f"freeglaz average {chart_id}")
            except (ValueError, FileNotFoundError) as e:
                raise ValueError(f"Scan averaging refused (inconsistent measurements): {e}")
            finally:
                for cp in concat_paths:                       # cleans temporary filtered copies
                    if cp.stem.endswith("_qcfilt"):
                        cp.unlink(missing_ok=True)
            profile_ti3 = merged
            averaged = True
        # < 2 "kept" scans → cleanly falls back to the last ti3 (average of 1 = itself)

    # ── multi-source foundation (non-regressive) ────────────────────────────
    # The CURRENT CHART's contribution is `profile_ti3` (last scan /
    # _avg depending on the branch above). We expose it as the 1st entry of a
    # source list ; the additional charts/profiles are added there (after
    # paper+GE guard). Rule : 1 source → PASSTHROUGH (strict file identity
    # of the current case) ; ≥2 → A single concat_ti3(keep) (renumbers the SAMPLE_ID,
    # dedup by RGB device — cf. findings). Here the list always has 1 entry
    # → colprof receives the SAME `profile_ti3` as before → identical .icc by
    # construction (average/last-scan unchanged).
    source_paths: list[Path] = [profile_ti3]
    # ── resolution of the ADDITIONAL sources (paper+GE guard) ──
    # Any exception is converted to ValueError (no HTTPException outside request).
    ref_ge = _norm_ge(paper.get("gloss_enhancer"))
    _tmp_src = None
    _extra_qcfilt: list[Path] = []     # temporary _qcfilt copies of the source charts (to clean up)
    if body.extra_chart_ids or body.source_profiles:
        # (b) other charts : INCLUDED ti3 of their measurements/, AFTER paper+GE guard
        # AND AFTER applying THEIR OWN patch rejections (symmetry with current chart :
        # a source contributes included scans × kept patches, not the hand-discarded readings).
        for xcid in body.extra_chart_ids:
            try:
                xm, xge = _chart_paper_ge(xcid)
            except HTTPException:
                raise ValueError(f"source chart not found/invalid: {xcid}")
            _assert_source_compatible(media_id, ref_ge, xm, xge, f"chart {xcid}")
            xpaths = _included_ti3_paths(_chart_dir(xcid))
            if not xpaths:
                raise ValueError(f"source chart {xcid}: no included scan")
            xused, xtmp = _apply_patch_rejections(_chart_dir(xcid), xpaths)
            source_paths.extend(xused)
            _extra_qcfilt.extend(xtmp)
        # (c) existing profiles : paper/GE identity derived SERVER-SIDE from the
        # adjacent manifest (_profile_identity, authoritative — zero client
        # trust), guard, then ti3 extracted from the CIED. Clean refusal if not
        # authenticatable (raises BEFORE extraction/concat).
        for i, sp in enumerate(body.source_profiles):
            sm, sge = _profile_identity(sp.path)
            _assert_source_compatible(media_id, ref_ge, sm, sge, f"profile {Path(sp.path).name}")
            if _tmp_src is None:
                import tempfile
                _tmp_src = Path(tempfile.mkdtemp(prefix="freeglaz_src_"))
            sp_ti3 = _tmp_src / f"src_profile_{i}.ti3"
            try:
                ProfilingOps().extract_cgats_from_icc(
                    sp.path, sp_ti3, descriptor=f"freeglaz source {Path(sp.path).name}")
            except (FileNotFoundError, ValueError) as e:
                raise ValueError(f"source profile {Path(sp.path).name}: extraction failed ({e})")
            source_paths.append(sp_ti3)

    if len(source_paths) > 1:
        from lib.z9_client.multipass import concat_ti3
        merged_ms = d / "measurements" / f"{chart_id}_multisource.ti3"
        try:
            concat_ti3(source_paths, merged_ms, dedup_strategy="keep",
                       descriptor=f"freeglaz multi-source {chart_id}")
        except (ValueError, FileNotFoundError) as e:
            raise ValueError(f"multi-source concat refused (inconsistent measurements): {e}")
        finally:
            if _tmp_src is not None:
                import shutil as _sh
                _sh.rmtree(_tmp_src, ignore_errors=True)
            for cp in _extra_qcfilt:                     # temporary filtered copies of the sources
                cp.unlink(missing_ok=True)
        profile_ti3 = merged_ms

    set_phase("building")              # colprof — ~2.5 min on ~1400 measurements (the job holds the duration)
    base_icc = profile_ti3.with_suffix(".icc")   # <base>.icc written by colprof BEFORE the atomic move
    try:
        result = ProfilingOps().build_profile(
            profile_ti3.with_suffix(""), descriptor=label, output_icc_path=out_icc,
            colprof_flags=flags)
    except (FileNotFoundError, RuntimeError) as e:
        raise RuntimeError(f"colprof failed: {e}")
    finally:
        # atomic hygiene : build_profile moves <base>.icc → out_icc ON SUCCESS. If colprof was
        # interrupted (reap), a PARTIAL <base>.icc (0 bytes) may remain → we remove it (never
        # a file that looks like a valid ICC ; out_icc stays complete-or-absent).
        if base_icc != out_icc and base_icc.exists():
            base_icc.unlink(missing_ok=True)

    # Trace of the EFFECTIVE SET that fed this profile → "stale" if we change included/excluded OR
    # the patch rejections afterward (the detail compares to the current set). Rejections only bite
    # the "average" base → we only trace rejections IN THAT CASE (otherwise rejection set = empty). Best-effort.
    try:
        _st = _read_scan_state(d)
        _st["profile_built_from"] = sorted(p.name for p in _included_ti3_paths(d))
        _st["profile_built_rejections"] = ({k: sorted(v) for k, v in _rej_for_build.items() if v}
                                           if averaged else {})
        _write_scan_state(d, _st)
    except OSError:
        logger.warning("profile_built_from trace not written (%s)", chart_id)

    # Per-paper storage (COPY — the original stays in the chart folder). Best-effort :
    # never breaks profiling if the serial is unknown or the copy fails.
    ranged_path = None
    if serial and media_id:
        try:
            cm = desc.get("color_management") or {}
            # n_patches = REAL count of the ti3 that fed colprof (final profile_ti3, after
            # passthrough / _avg / _multisource / rejection-filtered) — single source of truth, not
            # the chart geometry (which ignores the additional sources and the rejections).
            n_patches = _ti3_n_sets(profile_ti3) or (desc.get("geometry") or {}).get("patch_count")
            rp, _ = _cache.save_repo_z9_profile(
                icc_bytes=Path(result.output_icc_path).read_bytes(),
                serial=serial, media_id=media_id, paper_name=paper_name,
                # Custom name (basename, slugified, WITHOUT date) OR auto <paper>_GE-<slot>_<date>.
                label=(custom_name or paper_name),
                basename=(custom_name or None),
                date_str=(None if custom_name else date.today().isoformat()),
                gloss_slot=paper.get("gloss_enhancer"),
                color_space="PRINTER_RGB", method="argyll", method_flags=" ".join(flags),
                n_patches=n_patches, source_profile=cm.get("icc_name"),
                origin=origin,
                notes=f"chart {chart_id}",
                # OS intent validated at the route ; without collision (default case) save_repo writes the
                # exact name. replace → overwrite+preserve tags/notes ; keep_both → -N suffix.
                on_conflict=((body.on_conflict or "").strip().lower() or None))
            ranged_path = str(rp)
        except (OSError, ValueError):
            logger.exception("repo z9 storage failed (non-blocking) — chart %s", chart_id)

    return {"ok": True, "chart_id": chart_id, "icc": Path(result.output_icc_path).name,
            "icc_size_bytes": result.output_icc_size_bytes,
            "ranged_icc_path": ranged_path,             # None if serial unknown (backward-compat)
            # repo/z9 coordinates of the stored profile → enables "Install" on the front side
            "ranged": ({"serial": serial, "media_id": media_id,
                        "filename": Path(ranged_path).name} if ranged_path else None),
            "paper_name": paper_name,
            "averaged": averaged,                       # base = average of the stable scans (§4d)
            "n_scans_averaged": len(paths) if averaged else None,
            "installable": ranged_path is not None}     # → installable from Papers/Profiles


# ─── POST validate — profcheck (b) : forward A2B terminal ────────────────────
class ValidateChartBody(BaseModel):
    profile_path: str = Field(..., description="Absolute path of the printer .icc to validate")
    # AUTHENTIC identity of the profile (as the UI stores it under its Z9 paper).
    # Passed by the front because it is NOT reliably derivable from the FILE for a
    # MIRROR profile (stored by paper_name, not by hashed media_id ; names without GE-<slot>).
    media_id: Optional[str] = None
    gloss_enhancer: Optional[str] = None


def _norm_ge(v) -> str:
    """OFF/FULLPAGE normalized (tolerates 'GE-OFF', case, None)."""
    s = (v or "").upper().replace("GE-", "").strip()
    return s or "OFF"


# ─── HARD consistency guard of the multi-source build ────────────────────────
# Concatenating measurements from DIFFERENT PAPERS or GE SLOTS = physically
# corrupted profile (different substrate / gloss inking). We compare
# STRUCTURED fields (chart.json desc['paper']['media_id']/['gloss_enhancer'],
# profile manifest) — NEVER a filename parse. The guard lives at the
# BUILD level (the source resolver), NOT in concat_ti3.
def _chart_paper_ge(chart_id: str) -> tuple[Optional[str], str]:
    """(paper_media_id, normalized GE OFF/FULLPAGE) of a chart, from chart.json
    (structured). GE default OFF. Raises FileNotFoundError if chart.json absent."""
    import json
    desc = json.loads((_chart_dir(chart_id) / "chart.json").read_text(encoding="utf-8"))
    paper = desc.get("paper") or {}
    return paper.get("media_id"), _norm_ge(paper.get("gloss_enhancer"))


def _assert_source_compatible(ref_media: Optional[str], ref_ge: str,
                              src_media: Optional[str], src_ge: str, label: str) -> None:
    """Refuses a source whose paper OR GE slot differs from the current
    chart. Raises ValueError (the /profile endpoint maps it to HTTP 400).
    NON-negotiable guard (physical integrity of the measurements)."""
    if src_media != ref_media or _norm_ge(src_ge) != _norm_ge(ref_ge):
        raise ValueError(
            f"incompatible source ({label}): paper/GE different from the current "
            f"chart ({ref_media}/{_norm_ge(ref_ge)} != {src_media}/{_norm_ge(src_ge)}) "
            f"— concatenating measurements from different papers/GE would corrupt the profile")


def _profile_identity(profile_path: str) -> tuple[Optional[str], str]:
    """(paper_media_id, normalized GE OFF/FULLPAGE) AUTHORITATIVE of a profile, derived
    SERVER-SIDE from the ADJACENT `_paper.json` manifest (uniform mirror + repo_z9) :
    `paper_id` (= media_id) + `profiles[]` entry whose `filename == path.name`
    (lookup by filename = index KEY, NOT a semantic parse of the name ; the GE
    comes from the entry's structured field). ZERO client trust.

    Raises ValueError (clean refusal — NEVER guess) if the identity is not
    authenticatable : no adjacent manifest (orphan profile outside mirror/repo) /
    manifest without `paper_id` (legacy sync) / no entry with the right `filename`."""
    import json
    p = Path(profile_path)
    manifest_path = p.parent / _cache.PAPER_MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise ValueError(f"unauthenticatable profile ({p.name}): no adjacent "
                         f"{_cache.PAPER_MANIFEST_FILENAME} manifest (profile outside mirror/repo)")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"unauthenticatable profile ({p.name}): unreadable manifest ({e})")
    media_id = manifest.get("paper_id")
    if not media_id:
        raise ValueError(f"unauthenticatable profile ({p.name}): manifest without paper_id (legacy)")
    entry = next((e for e in (manifest.get("profiles") or []) if e.get("filename") == p.name), None)
    if entry is None:
        raise ValueError(f"unauthenticatable profile ({p.name}): absent from the manifest profiles[]")
    return media_id, _norm_ge(entry.get("gloss_enhancer"))




@router.post("/{chart_id}/validate")
def validate_chart(chart_id: str, body: ValidateChartBody) -> dict:
    """INDEPENDENT VALIDATION (profcheck b, forward A2B) : profcheck of the selected
    profile on the ti3 of the validation chart (independent, already printed in raw
    device + scanned via the spine). LOCAL — NO Z9 act here (printing/scan
    done by the /print and /scan routes). Reuses verify_profile_independent
    (profcheck detailed=True + independence cross-check) → presentation.

    SAFEGUARD : the chart MUST be on the paper/GE OF the validated profile (otherwise we compare
    the prediction of a profile to the physical response of ANOTHER paper = absurd)."""
    import json
    from lib.z9_client.profile_compare import verify_profile_independent

    d = _chart_dir(chart_id)
    ti3s = _raw_ti3_paths(d)   # RAW scans (derivatives _avg/_qcfilt/_multisource excluded, via the canonical accessor)
    if not ti3s:
        raise HTTPException(409, detail="no ti3 — print then scan the chart first")
    ti3 = ti3s[-1]

    # profile : under the store root (anti-traversal), existing .icc
    prof = Path(body.profile_path).resolve()
    try:
        prof.relative_to(_cache.root_dir().resolve())
    except ValueError:
        raise HTTPException(403, detail="profile path outside the store refused")
    if prof.suffix.lower() != ".icc":
        raise HTTPException(422, detail="The path does not point to a .icc")
    if not prof.is_file():
        raise HTTPException(404, detail=f"profile not found: {prof.name}")

    # paper/GE safeguard : the chart (chart.json) MUST be on the paper/GE of the profile.
    # We compare to the AUTHENTIC identity passed by the UI (hashed media_id + GE), NOT to a
    # re-derivation from the file — unreliable for the mirror (false positives). If the UI
    # provides nothing, we don't invent a refusal (defensive, no false positive).
    desc = json.loads((d / "chart.json").read_text(encoding="utf-8"))
    paper = desc.get("paper") or {}
    chart_mid = paper.get("media_id")
    chart_ge = _norm_ge(paper.get("gloss_enhancer"))
    if body.media_id and chart_mid and body.media_id != chart_mid:
        raise HTTPException(422, detail=(
            "Validation chart on a DIFFERENT paper than the profile "
            f"(chart={chart_mid}, profile={body.media_id}) — absurd validation."))
    if body.gloss_enhancer is not None and _norm_ge(body.gloss_enhancer) != chart_ge:
        raise HTTPException(422, detail=(
            f"Different GE (chart GE={chart_ge}, profile GE={_norm_ge(body.gloss_enhancer)}) — "
            "the physical response differs; redo the chart on the right GE."))

    report = verify_profile_independent(prof, ti3)
    meta = _cache.read_repo_z9_profile_meta(prof) or {}
    report["label"] = meta.get("label") or prof.stem
    report["paper_name"] = meta.get("paper_name") or paper.get("name")
    report["chart_id"] = chart_id
    return report


# ─── GET list (library : paper filter + printed) ─────────────────────────────
@router.get("")
def list_charts(paper: Optional[str] = Query(None, description="paper MediaId filter"),
                printed: Optional[bool] = Query(None, description="printed only"),
                scanned: Optional[bool] = Query(None, description="scanned only"),
                purpose: Optional[str] = Query(None, description="profiling | validation")) -> dict:
    """Library. `paper` → charts of this slot ; `printed=true` → printed only
    (bridge of the decoupled workflow) ; `scanned=true` + `purpose=validation` → RESUME of a
    validation chart already scanned (profcheck b, without reprinting)."""
    charts = _sc.list_charts()                 # reads chart.json ONCE (purpose included)
    if paper:
        charts = [c for c in charts if c.get("paper_media_id") == paper]
    if printed:
        charts = [c for c in charts if c.get("printed")]
    if scanned:
        charts = [c for c in charts if c.get("scanned")]
    if purpose is not None:
        charts = [c for c in charts if c.get("purpose") == purpose]
    # n_scans + profiled ANCHORED in `measurements/` (durable truth, not the session) → a
    # pre-session chart displays its REAL scan count (no more "(?)") + its stage.
    for c in charts:
        cd = _cache.locate_chart_dir(c["chart_id"])
        c["n_scans"] = len(_raw_ti3_paths(cd)) if cd else 0
        c["profiled"] = bool(cd) and (cd / f"{c['chart_id']}.icc").is_file()
        # Structured GE (OFF/FULLPAGE) → display + ON/OFF subgroup on the front side (presentation
        # via geLabel). Exposed at the ROUTE level (sol_chart.list_charts unchanged).
        try:
            c["gloss_enhancer"] = _chart_paper_ge(c["chart_id"])[1]
        except (FileNotFoundError, HTTPException):
            c["gloss_enhancer"] = "OFF"
    return {"charts": charts}


@router.get("/{chart_id}/build-sources")
def list_build_sources(chart_id: str) -> dict:
    """ADDITIONAL sources eligible for the multi-source build of this
    chart : (b) other charts of the SAME paper+GE with ≥1 scan, (c) existing
    profiles (repo_z9 + mirror) of the SAME paper+GE. AUTHORITATIVE server-side filtering
    (chart.json / _paper.json via _chart_paper_ge / _profile_identity) — identical
    to the build guards (the front will only send chart_id / path). Offline (no Z9).
    """
    ref_media, ref_ge = _chart_paper_ge(chart_id)   # 404/422 if unknown/invalid chart

    # (b) other charts of the same paper + GE, with real measurements.
    extra_charts = []
    for c in _sc.list_charts():
        cid = c.get("chart_id")
        if not cid or cid == chart_id or c.get("paper_media_id") != ref_media:
            continue
        cd = _cache.locate_chart_dir(cid)
        if cd is None:
            continue
        incl = _included_ti3_paths(cd)         # what would ACTUALLY enter (included scans)
        if not incl:
            continue                            # no included scan → would bring nothing
        try:
            _, cge = _chart_paper_ge(cid)
        except HTTPException:
            continue
        if cge != ref_ge:
            continue
        # n_rejected = hand-discarded readings (rejected_readings) on the included scans → we
        # display WHAT the source brings (included scans × kept patches), not the raw ti3.
        _rej = _rejected_map(_read_scan_state(cd))
        n_rejected = sum(len(_rej.get(p.name) or ()) for p in incl)
        extra_charts.append({
            "chart_id": cid, "n_scans": len(incl), "n_rejected": n_rejected,
            "patch_count": (c.get("geometry") or {}).get("patch_count") or c.get("patch_count"),
            "paper_name": c.get("paper"), "created_at": c.get("created_at"),
            "profiled": (cd / f"{cid}.icc").is_file()})

    # (c) existing profiles of the SAME paper + GE (repo_z9 + mirror), identity
    # derived from the adjacent manifest (_profile_identity) → we only expose the path.
    profiles = []
    seen: set[str] = set()
    for base in (_cache.repo_z9_dir(), _cache.mirror_dir()):
        if not base.is_dir():
            continue
        for icc in sorted(base.rglob("*.icc")):
            sp = str(icc)
            if sp in seen:
                continue
            try:
                m, g = _profile_identity(sp)
            except ValueError:
                continue                      # not authenticatable → never proposed
            if m == ref_media and g == ref_ge:
                seen.add(sp)
                profiles.append({"path": sp, "label": icc.stem})

    # SUGGESTED profile name (= auto nomenclature of the build) → prefills the editable field.
    import json
    from datetime import date
    try:
        _d = json.loads((_chart_dir(chart_id) / "chart.json").read_text(encoding="utf-8"))
        _pname = (_d.get("paper") or {}).get("name") or ref_media or chart_id
    except (OSError, ValueError):
        _pname = ref_media or chart_id
    suggested = _cache.repo_z9_profile_basename(ref_ge, _pname, date_str=date.today().isoformat())
    return {"paper_media_id": ref_media, "gloss_enhancer": ref_ge, "suggested_name": suggested,
            "extra_charts": extra_charts, "profiles": profiles}


@router.delete("/{chart_id}")
def delete_chart(chart_id: str) -> dict:
    """FULLY deletes a chart (target TIFF + ti3 + metadata). LOCAL, no Z9.
    Recovers all the disk but LOSES the measurements (ti3). MANUAL + confirmed deletion
    on the UI side (never automatic). N.B. : the ICC profile possibly built from this
    chart is a SEPARATE file (repo/z9) and carries ITS OWN embedded measurements — it
    is NOT affected."""
    import shutil as _sh
    d = _chart_dir(chart_id)
    _sh.rmtree(d, ignore_errors=True)
    return {"ok": True, "chart_id": chart_id, "deleted": "all"}


@router.post("/{chart_id}/lighten")
def lighten_chart(chart_id: str) -> dict:
    """Lightens a chart : removes the big target (TIFF ~24-38 MB), KEEPS the ti3
    (measurements), the PNG thumbnail and the metadata. Recovers ~99 % of the disk while
    keeping the value (re-profile / re-validate without rescanning). LOCAL, no Z9.
    The chart is no longer reprintable (recompose to reprint)."""
    import json
    d = _chart_dir(chart_id)
    desc = json.loads((d / "chart.json").read_text(encoding="utf-8"))
    tiff_name = (desc.get("files") or {}).get("tiff")
    tiff = d / tiff_name if tiff_name else None
    if not tiff or not tiff.is_file():
        raise HTTPException(409, detail="chart already lightened (no target to delete)")
    freed = tiff.stat().st_size
    tiff.unlink()
    desc["lightened"] = True
    (d / "chart.json").write_text(json.dumps(desc, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "chart_id": chart_id, "deleted": "tiff", "freed_bytes": freed}


# ─── GET chart detail (management layer — Measurements tab, A3+A4 aggregated) ──
# Declared AFTER the literal GET routes (/formats, …) → does not capture those.
@router.get("/{chart_id}")
def chart_detail(chart_id: str) -> dict:
    """Chart detail aggregate (READ-ONLY, Measurements tab) : identity + scans (A3) + derived
    profile EVEN ORPHAN (A4 : reads `charts/<id>/<id>.icc`, not only the repo). The
    ΔE concordance (A1) stays on `/scan/delta` (heavy, lazy, shared). No Z9."""
    import json
    from datetime import datetime
    from webapp.backend.services import scan_session

    d = _chart_dir(chart_id)   # 404 if unknown chart
    desc = json.loads((d / "chart.json").read_text(encoding="utf-8"))
    paper = desc.get("paper") or {}
    geom = desc.get("geometry") or {}
    identity = {
        "chart_id": chart_id,
        "paper": paper.get("name"), "paper_media_id": paper.get("media_id"),
        "gloss_enhancer": paper.get("gloss_enhancer"),   # displayed via geLabel (presentation)
        "format": (desc.get("media") or {}).get("format_key"),
        "patch_count": geom.get("patch_count"), "cols": geom.get("cols"),
        "created_at": desc.get("created_at"), "printed_at": desc.get("printed_at"),
        "purpose": desc.get("purpose") or "profiling",
        "source": desc.get("source"), "lightened": bool(desc.get("lightened")),
    }

    # Scans (A3) : RAW ti3 of measurements/ (derivatives _avg/_qcfilt/_multisource excluded). The ROLE (included/excluded)
    # comes from the durable SIDECAR. scanned_at = TIMESTAMP from the filename (the ti3 IS
    # timestamped) ; delay_seconds = print→scan delay (the FACT that says "too fresh" without judgment).
    sess_scans: dict = {}
    for s in scan_session.list_sessions():
        if s.get("chart_id") == chart_id:
            for m in (s.get("scans") or []):
                sess_scans[m.get("ti3")] = m
    state = _read_scan_state(d)
    _printed_dt = None
    if desc.get("printed_at"):
        try:
            _printed_dt = datetime.fromisoformat(desc["printed_at"])
        except ValueError:
            _printed_dt = None
    rej_map = state.get("rejected_readings") or {}
    scans = []
    for p in _raw_ti3_paths(d):
        m = sess_scans.get(p.name, {})
        role = _scan_role(state, p.name)
        sa = _ti3_scanned_at(p.name)
        scans.append({
            "ti3": p.name,
            "n_patches": m.get("n_patches") or _ti3_n_sets(p),   # session OR counted from the ti3
            "role": role,                                        # included | excluded (durable sidecar)
            "kept": role == "included",                         # backward-compat (kept = included)
            "scanned_at": (sa.isoformat(timespec="seconds") if sa else None),
            "delay_seconds": (int((sa - _printed_dt).total_seconds())
                              if (sa and _printed_dt) else None),  # None if delay unknown
            "rejected_readings": list(rej_map.get(p.name) or []),  # rejected SAMPLE_ID
            "size_bytes": p.stat().st_size,
        })

    # Chart profile (A4) : the ICC in the chart folder (charts/<id>/<id>.icc).
    profile = {"built": False, "icc_path": None}
    icc = d / f"{chart_id}.icc"
    if icc.is_file():
        # built_from / stale : EFFECTIVE set ≠ current set — INCLUDED scans changed (excluded/re-included)
        # or patch REJECTIONS changed, compared via profile_built_from +
        # profile_built_rejections (sidecar). Unknown (old profiles without trace) → False (no
        # reference). Semantic NB : means "additional measurements available", not "stale".
        kept_now = sorted(pp.name for pp in _included_ti3_paths(d))
        built_from = sorted(state.get("profile_built_from") or [])
        rej_now = {k: sorted(v) for k, v in (state.get("rejected_readings") or {}).items() if v}
        rej_built = {k: sorted(v) for k, v in
                     (state.get("profile_built_rejections") or {}).items() if v}
        stale = bool(built_from) and (built_from != kept_now or rej_now != rej_built)
        profile = {
            "built": True, "icc_path": str(icc),
            "built_from": built_from,
            "stale": stale,
        }

    return {"identity": identity, "scans": scans, "profile": profile}
