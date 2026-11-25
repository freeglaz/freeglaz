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
                                         (honeycomb for the built-in spectro, or
                                         printtarg for an external instrument)
  GET  /api/charts                       library (list_charts)
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from lib.z9_client import Z9Error
from lib.z9_client.build_inputs import MeasurementIdentityError
from lib.z9_client import cache as _cache
from lib.z9_client import chart_geometry_refonte as G
from lib.z9_client import measurement_identity as _mi
from lib.z9_client import printer_model
from lib.z9_client import printtarg_chart as _ptc
from lib.z9_client import profiling_strategies as _ps
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


# ─── GET printtarg options (external instrument charts) ──────────────────────
@router.get("/printtarg-options")
def get_printtarg_options() -> dict:
    """What a printtarg chart can be created with: instruments, printtarg selectors,
    media, presets and roll composition, all derived from lib.z9_client.printtarg_chart
    (no copy here), plus what is installed: printtarg, the official chartread (reported for
    information, never used to measure) and the measurement engine chartread-fgK (checked
    from its manifest). Files are only resolved and read: no binary is run, no printer and
    no instrument is contacted."""
    from lib.z9_client.argyll import find_argyll_binary
    from lib.z9_client.chartread_fgk import engine as fgk
    opts = _ptc.supported_options()
    models = _mi.INSTRUMENT_MODELS
    for inst in opts["instruments"]:
        m = models.get(inst["key"])
        inst["measurement"] = ({"method": m.method, "conditions": list(m.conditions),
                                "filter_selectable": m.filter_selectable} if m else None)
    return {**opts,
            "binaries": {"printtarg": {"available": find_argyll_binary("printtarg") is not None},
                         "chartread": {"available": find_argyll_binary("chartread") is not None,
                                       "used_for_measurement": False}},
            "measurement_engine": fgk.engine_status().to_dict(),
            "requires_printer_for_measurement": False}


@router.post("/printtarg-estimate")
def estimate_printtarg_chart(body: PrinttargEstimateBody) -> dict:
    """What the printtarg chart would take BEFORE it is generated: the generator's own preset,
    options and transverse plan, from printtarg layout probes (no TIFF, nothing kept): strips,
    blocks, chart size, roll consumed, patch size. From the .ti1 when given, else a count.
    No printer and no instrument are contacted."""
    if body.ti1_text is None and body.n_patches is None:
        raise HTTPException(422, detail={"code": "estimate_needs_count",
                                         "message": "n_patches (targen -f) or ti1_text"})
    with tempfile.TemporaryDirectory(prefix="freeglaz_estimate_") as tmp:
        ti1 = None
        if body.ti1_text is not None:
            ti1 = Path(tmp) / "chart.ti1"
            ti1.write_text(body.ti1_text, encoding="utf-8")
        try:
            return _ptc.estimate_printtarg_layout(
                instrument=body.instrument, media_key=body.media_key,
                n_patches=body.n_patches, ti1_path=ti1, patch_scale=body.patch_scale,
                high_density=bool(body.high_density))
        except _ptc.PrinttargChartError as e:
            status = 503 if e.code == "printtarg_not_found" else \
                422 if e.code.startswith(_PRINTTARG_INPUT_PREFIXES) else 500
            raise HTTPException(status, detail={"code": e.code, "message": str(e)})


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
_FREEGLAZ_BUILT_ORIGINS = ("free_chart_argyll",)


def _freeglaz_precondition_profiles(paper: str, print_ge: str) -> list:
    """Profiles freeglaz built for THIS paper and THIS gloss enhancer, offered as targen -c
    without going through the printer (note 129 §7.2). Read from repo/z9 and the sidecar
    freeglaz wrote with each one (paper, GE slot, origin, md5 checked against the file): copies
    of HP profiles and imports are not "built by freeglaz", and an intermediate profile of a
    two-pass profiling is never in repo/z9."""
    out = []
    base = _cache.repo_z9_dir()
    if not base.is_dir():
        return out
    ge = _norm_ge(print_ge)
    for icc in sorted(base.rglob("*.icc")):
        if _cache.REPLACED_DIR_NAME in icc.parts or icc.parent.name != paper:
            continue                                       # another paper, or a replaced copy kept aside
        meta = _cache.read_repo_z9_profile_meta(icc) or {}
        if meta.get("origin") not in _FREEGLAZ_BUILT_ORIGINS or meta.get("media_id") != paper \
                or _norm_ge(meta.get("gloss_slot")) != ge:
            continue
        try:
            if meta.get("md5") and _cache.md5_bytes(icc.read_bytes()) != meta["md5"]:
                continue                                   # changed since freeglaz wrote it
        except OSError:
            continue
        build = meta.get("build") if isinstance(meta.get("build"), dict) else {}
        out.append({"id": f"path:{icc}", "kind": "freeglaz", "path": str(icc), "ge": ge,
                    "name": meta.get("label") or icc.stem, "chart_id": meta.get("chart_id"),
                    "build_id": meta.get("build_id"),
                    "built_at": build.get("created_at") or meta.get("created_at")})
    out.sort(key=lambda x: x.get("built_at") or "", reverse=True)
    return out


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

    items += _freeglaz_precondition_profiles(paper, print_ge)

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
class StrategyChartRequest(BaseModel):
    """A chart made as one pass of a profiling strategy (lib.z9_client.profiling_strategies).
    The server composes the targen line from the parameters and resolves the -c itself."""
    id: str
    pass_id: str
    params: dict = {}                              # overrides of the definition's parameters
    run: Optional[str] = None                      # later pass: chart id of the run's first pass
    precondition_build_id: Optional[str] = None    # later pass: the intermediate version shown
    targen_flags: Optional[str] = None             # hand-edited line (its -c is refused)


class GuidedChartRequest(BaseModel):
    """The one-pass wizard's guided form (lib.z9_client.profiling_strategies, [one_pass]): not
    a strategy. The server composes the targen line from the parameters (the -c stays the
    wizard's menu, ``c_profile``) and records the colprof line the profile step starts from."""
    params: dict = {}                              # overrides of the [one_pass] values


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
    # Chart layout: "honeycomb" (built-in spectrophotometer, SOL) or "printtarg"
    # (ArgyllCMS printtarg strips for an external instrument read by chartread).
    layout: str = "honeycomb"
    instrument: Optional[str] = None                    # printtarg only (whitelist)
    # printtarg only: -a factor and -h (high density), checked against the instrument's
    # whitelist (printtarg_chart.check_layout_options). None = the default (1.0, off).
    patch_scale: Optional[float] = None
    high_density: Optional[bool] = None
    strategy: Optional[StrategyChartRequest] = None    # None = the one-pass wizard (unchanged)
    guided: Optional[GuidedChartRequest] = None        # one pass, composed from the guided form


class PrinttargEstimateBody(BaseModel):
    instrument: str
    media_key: str
    n_patches: Optional[int] = None                     # the targen -f count…
    ti1_text: Optional[str] = None                      # …or the .ti1 the chart will be made from
    patch_scale: Optional[float] = None
    high_density: Optional[bool] = None


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


# ─── profiling strategies: a chart = one pass (lib.z9_client.profiling_strategies) ──
# The run lives in the charts' own chart.json ("strategy" block, run = chart id of the
# first pass); a later pass carries a copy of the exact intermediate profile it was made
# from ("precondition" block + precondition.icc). Nothing else is stored.
def _strategy_refusal(e: _ps.StrategyError, status: int = 422) -> HTTPException:
    return HTTPException(status, detail=e.to_detail())


def _refuse(status: int, code: str, message: str, **info) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message, **info})


def _read_chart_json(d: Path) -> dict:
    import json
    return json.loads((d / "chart.json").read_text(encoding="utf-8"))


def _scan_identity(path: Path):
    """Measurement identity of a raw scan, None when it cannot be read."""
    try:
        return _mi.read_identity(path)
    except (OSError, ValueError):
        return None


def _measurement_lock(first_dir: Path) -> dict:
    """How the run's first pass was measured (its included scans): the instrument and the
    condition later passes inherit. ``consistent`` False when the scans disagree or one is
    unreadable (nothing is then inherited, and a later pass is refused)."""
    idents = [_scan_identity(p) for p in _included_ti3_paths(first_dir)]
    keys = {(i.instrument, i.condition, i.provenance.filter_option) for i in idents if i is not None}
    if not idents or any(i is None for i in idents) or len(keys) != 1:
        return {"consistent": False, "instrument": None, "condition": None, "filter_option": None}
    instrument, condition, filter_option = keys.pop()
    return {"consistent": True, "instrument": instrument, "condition": condition, "filter_option": filter_option}


def _precondition_version(first_id: str, first_dir: Path, build_id: Optional[str]) -> dict:
    """The intermediate profile a later pass is made from: an intact version of the first
    chart built with role "preconditioning", the CURRENT one, up to date with the included
    scans. Named explicitly by the request (the version the user saw), never picked silently."""
    from lib.z9_client import profile_versions
    if not build_id:
        raise _refuse(422, "precondition_build_required", "the intermediate profile version is required",
                      chart_id=first_id)
    versions = {v["build_id"]: v for v in profile_versions.list_versions(first_dir)}
    v = versions.get(build_id)
    if v is None:
        raise _refuse(409, "precondition_build_unknown", f"no version {build_id} of the intermediate profile",
                      chart_id=first_id, build_id=build_id)
    if not v["intact"]:
        raise _refuse(409, "precondition_build_altered", f"version {build_id} is altered",
                      chart_id=first_id, build_id=build_id)
    m = profile_versions.read_version_manifest(first_dir, build_id)
    if ((m.get("request") or {}).get("strategy") or {}).get("role") != _ps.ROLE_PRECONDITIONING:
        raise _refuse(409, "precondition_not_intermediate", f"version {build_id} is not an intermediate profile",
                      chart_id=first_id, build_id=build_id)
    status = _profile_status(first_id, first_dir)
    if status.get("build_id") != build_id:
        raise _refuse(409, "precondition_not_current", "this is not the current intermediate profile of the first pass",
                      chart_id=first_id, build_id=build_id, current_build_id=status.get("build_id"))
    if status.get("state") in ("stale", "missing_input"):
        raise _refuse(409, "precondition_stale", "the intermediate profile no longer matches the included "
                      "measurements of the first pass: build it again", chart_id=first_id, build_id=build_id,
                      state=status.get("state"))
    return {"build_id": build_id, "icc_sha256": v["icc_sha256"], "created_at": v.get("created_at"),
            "icc": profile_versions.builds_dir(first_dir) / build_id / profile_versions.VERSION_ICC,
            "colprof_flags": (m.get("plan") or {}).get("colprof_flags"), "profile_state": status.get("state")}


def _merge_params(base: dict, overrides: dict) -> dict:
    import copy
    out = copy.deepcopy(base or {})
    for pid, sections in (overrides or {}).items():
        if not isinstance(sections, dict):
            out[pid] = sections                          # refused by normalize_params
            continue
        for sec, values in sections.items():
            if isinstance(values, dict):
                out.setdefault(pid, {}).setdefault(sec, {}).update(values)
            else:
                out.setdefault(pid, {})[sec] = values
    return out


def _strategy_context(body: "CreateChartBody") -> SimpleNamespace:
    """Checks a strategy chart request BEFORE targen, the printer or any file: the pass, its
    parameters (frozen in the first pass, changeable for the later ones), the composed (or
    hand-edited) targen line, and for a later pass the run's locked conditions and its
    intermediate profile. The geometry of the chart (format, density, patch size) is free."""
    sr = body.strategy
    try:
        sdef = _ps.get_strategy(sr.id)
        pdef = sdef.get_pass(sr.pass_id)
    except _ps.StrategyError as e:
        raise _strategy_refusal(e)
    if body.purpose != "profiling":
        raise _refuse(422, "strategy_not_for_validation", "a validation chart is never a strategy pass (independence)")
    if pdef.generator is None:
        raise _refuse(422, "strategy_pass_not_supported", f"pass {pdef.id}: predefined charts are not shipped yet")
    if body.ti1_text:
        raise _refuse(422, "strategy_composes_the_chart", "a strategy pass is generated by targen from its parameters")
    if body.c_profile not in (None, "", "none"):
        raise _refuse(422, "precondition_is_the_strategy", "a strategy pass gets its -c from the strategy")
    first = sdef.first_pass
    run, first_dir, fdesc, precond, lock = None, None, None, None, None
    if pdef.id == first.id:
        if sr.run or sr.precondition_build_id:
            raise _refuse(422, "strategy_run_not_expected", "the first pass starts a run")
        try:
            params = _ps.normalize_params(sdef, sr.params)
        except _ps.StrategyError as e:
            raise _strategy_refusal(e)
    else:
        if not sr.run:
            raise _refuse(422, "strategy_run_required", f"pass {pdef.id} continues a run: give its first chart")
        run = sr.run
        first_dir = _chart_dir(run)
        fdesc = _read_chart_json(first_dir)
        fst = fdesc.get("strategy") or {}
        if fst.get("id") != sdef.id or fst.get("pass") != first.id:
            raise _refuse(409, "strategy_run_mismatch", f"{run} is not the first pass of a {sdef.id} run", run=run)
        earlier = [p.id for p in sdef.passes[:sdef.pass_index(pdef.id)]]
        if any(pid in (sr.params or {}) for pid in earlier):
            raise _refuse(422, "earlier_pass_frozen", "the parameters of an earlier pass are history: not changeable",
                          passes=earlier)
        try:
            params = _ps.normalize_params(sdef, _merge_params(fst.get("params") or {}, sr.params))
        except _ps.StrategyError as e:
            raise _strategy_refusal(e)
        fpaper = fdesc.get("paper") or {}
        if body.paper_mediaid != fpaper.get("media_id") or _norm_ge(body.gloss_enhancer) != _norm_ge(fpaper.get("gloss_enhancer")):
            raise _refuse(409, "strategy_conditions_changed", "the paper and the gloss enhancer of the run are fixed",
                          expected={"paper_media_id": fpaper.get("media_id"),
                                    "gloss_enhancer": _norm_ge(fpaper.get("gloss_enhancer"))},
                          requested={"paper_media_id": body.paper_mediaid, "gloss_enhancer": _norm_ge(body.gloss_enhancer)})
        f_layout, f_inst = _mi.chart_layout(fdesc), (fdesc.get("instrument") or {}).get("key")
        if body.layout != f_layout or (body.instrument or None) != (f_inst or None):
            raise _refuse(409, "strategy_instrument_locked", "the measurement instrument of the run is inherited",
                          expected={"layout": f_layout, "instrument": f_inst},
                          requested={"layout": body.layout, "instrument": body.instrument})
        precond = _precondition_version(run, first_dir, sr.precondition_build_id)
        lock = _measurement_lock(first_dir)
        if not lock["consistent"]:
            raise _refuse(409, "strategy_measurement_unknown", "the included scans of the first pass do not tell "
                          "one instrument and condition: nothing to inherit", run=run)
    if sr.targen_flags:
        try:
            _ps.check_free_targen_line(sr.targen_flags)
        except _ps.StrategyError as e:
            raise _strategy_refusal(e)
        if _tg.extract_f_count(sr.targen_flags) is None:
            raise _refuse(422, "strategy_line_needs_count", "a hand-edited targen line needs its -f count")
        flags, free = sr.targen_flags, True
    else:
        flags, free = _ps.compose_targen(params[pdef.id]), False
    return SimpleNamespace(sdef=sdef, pdef=pdef, params=params, flags=flags, free=free, run=run,
                           first_dir=first_dir, first_desc=fdesc, precond=precond, lock=lock,
                           composed=_ps.compose(sdef, pdef.id, params))


def _guided_context(body: "CreateChartBody") -> SimpleNamespace:
    """Checks a guided one-pass request BEFORE targen, the printer or any file: bounded
    parameters, and the targen line the server composes from them (-N / -n only with a -c)."""
    if body.strategy:
        raise _refuse(422, "guided_is_not_a_strategy", "a chart is either a strategy pass or a guided one-pass chart")
    if body.targen_flags or body.ti1_text:
        raise _refuse(422, "guided_composes_the_chart", "a guided chart is generated by targen from its parameters")
    if body.purpose != "profiling":
        raise _refuse(422, "guided_not_for_validation", "a validation chart keeps its own targen line")
    preconditioned = body.c_profile not in (None, "", "none")
    try:
        params = _ps.normalize_one_pass(body.guided.params, preconditioned=preconditioned)
    except _ps.StrategyError as e:
        raise _strategy_refusal(e)
    return SimpleNamespace(params=params, composed=_ps.compose_one_pass(params), preconditioned=preconditioned)


def _guided_block(guided: SimpleNamespace) -> dict:
    return {"version": 1, "params": guided.params, "preconditioned": guided.preconditioned,
            "lines": {"targen_flags": guided.composed["targen_flags"],
                      "colprof_flags": guided.composed["colprof_flags"]}}


def _guided_out(block: dict, targen_argv) -> dict:
    """The guided block as the creation (and the print summary) returns it."""
    return {"params": block.get("params"), "preconditioned": block.get("preconditioned"),
            "lines": block.get("lines"), "targen_argv": targen_argv}


def _strategy_block(strat: SimpleNamespace, chart_id: str, body: "CreateChartBody", tag_sha: Optional[str]) -> dict:
    return {
        "id": strat.sdef.id, "version": strat.sdef.version, "validated": strat.sdef.validated,
        "pass": strat.pdef.id, "role": strat.pdef.role, "run": strat.run or chart_id,
        "params": strat.params,
        "lines": {"targen_flags": strat.flags, "free_line": strat.free,
                  "colprof_flags": strat.composed["colprof_flags"]},
        # what every later pass must share (checked again when it is printed)
        "inherited": {"paper_media_id": body.paper_mediaid, "gloss_enhancer": _norm_ge(body.gloss_enhancer),
                      "layout": body.layout, "instrument": body.instrument,
                      "measurement": strat.lock},
        "resident_tag_sha256": tag_sha,
    }


# printtarg error codes caused by the request (the .ti1, the page) vs by the install
# (no printtarg). Anything else is a failed self-check of the generated files.
_PRINTTARG_INPUT_PREFIXES = ("ti1_", "cgats_", "printtarg_failed", "page_too_wide",
                             "unsupported_", "too_many_strips")


def _create_printtarg_chart(body, ti1: Path, tag: Path, serial: str):
    """printtarg layout (external instrument): same inputs as the honeycomb path (the
    .ti1, the LIVE resident tag, the serial), a different layout engine. No scanLayout
    is ever produced, so the built-in spectrophotometer cannot be pointed at it."""
    try:
        return _ptc.orchestrate_printtarg_chart(
            ti1, instrument=body.instrument, media_key=body.media_key,
            reference_icc_path=tag, gloss_enhancer=body.gloss_enhancer,
            source=body.source or (f"targen {body.targen_flags}"
                                   if body.targen_flags else "ti1 provided"),
            paper={"name": body.paper_name or body.paper_mediaid,
                   "media_id": body.paper_mediaid,
                   "gloss_enhancer": body.gloss_enhancer,
                   "serial": serial},
            patch_scale=body.patch_scale, high_density=bool(body.high_density),
        )
    except _ptc.PrinttargChartError as e:
        if e.code == "printtarg_not_found":
            status = 503
        elif e.code.startswith(_PRINTTARG_INPUT_PREFIXES):
            status = 422
        else:
            status = 500
        logger.warning("printtarg chart refused: %s", e)
        raise HTTPException(status, detail={"code": e.code, "message": str(e)})


@router.post("")
def create_chart(request: Request, body: CreateChartBody,
                 z9=Depends(get_chart_z9)) -> dict:
    if body.media_key not in G.MEDIA:
        raise HTTPException(422, detail=f"unknown media: {body.media_key}")
    if body.gloss_enhancer not in _GE_VALUES:
        raise HTTPException(422, detail=f"gloss_enhancer must be {_GE_VALUES}")
    if not body.targen_flags and not body.ti1_text and not body.strategy and not body.guided:
        raise HTTPException(422, detail="Provide 'targen_flags' OR 'ti1_text'.")
    if body.purpose not in ("profiling", "validation"):
        raise HTTPException(422, detail="purpose must be 'profiling' or 'validation'")
    printtarg = body.layout == _ptc.LAYOUT
    if body.layout not in ("honeycomb", _ptc.LAYOUT):
        raise HTTPException(422, detail="layout must be 'honeycomb' or 'printtarg'")
    if printtarg:
        if body.instrument not in _ptc.INSTRUMENTS:
            raise HTTPException(422, detail=f"instrument must be one of {list(_ptc.INSTRUMENTS)}")
        if body.media_key not in _ptc.SUPPORTED_MEDIA:
            raise HTTPException(422, detail=(f"printtarg charts support {list(_ptc.SUPPORTED_MEDIA)} "
                                             f"only, not {body.media_key}"))
        try:                             # before targen or the printer: a refused option costs nothing
            _ptc.check_layout_options(body.instrument, body.patch_scale, body.high_density)
        except _ptc.PrinttargChartError as e:
            raise HTTPException(422, detail={"code": e.code, "message": str(e)})
    elif body.instrument is not None:
        raise HTTPException(422, detail="instrument applies to the printtarg layout only")
    elif body.patch_scale is not None or body.high_density is not None:
        raise HTTPException(422, detail="patch_scale and high_density apply to the printtarg layout only")

    # A strategy pass: checked before any work; the line is the strategy's, the -c the server's.
    strat = _strategy_context(body) if body.strategy else None
    if strat:
        body = body.model_copy(update={"targen_flags": strat.flags, "c_profile": "none"})
    # A guided one-pass chart: the line is composed from its form, the -c stays the wizard's menu.
    guided = _guided_context(body) if body.guided else None
    if guided:
        body = body.model_copy(update={"targen_flags": guided.composed["targen_flags"]})

    cap = _sc.format_capacity(body.media_key)
    warnings: list[str] = []
    strategy_notices: list[dict] = []
    targen_run = None                    # {argv, stdout, returncode} of the REAL targen run (#23)
    tag_sha = None
    workdir = Path(tempfile.mkdtemp(prefix="freeglaz_chart_"))
    try:
        # 1. .ti1 : targen (path a) OR upload (path b)
        if body.ti1_text:
            ti1 = workdir / "chart.ti1"
            ti1.write_text(body.ti1_text, encoding="utf-8")
        else:
            # UNIQUE safeguard : -f ≤ max(format) UPSTREAM (honeycomb capacity; printtarg
            # lays out what it is given and has its own failure modes, reported below)
            n = _tg.extract_f_count(body.targen_flags)
            if not printtarg and n is not None and cap["max_patches"] and n > cap["max_patches"]:
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
            if strat and strat.precond:
                # the exact intermediate version, copied and checked: targen -c only (never a
                # conversion of the chart values: the TIFF is tagged with the live resident below)
                precond = workdir / "precondition.icc"
                shutil.copyfile(strat.precond["icc"], precond)
                if hashlib_sha256(precond.read_bytes()) != strat.precond["icc_sha256"]:
                    raise _refuse(409, "precondition_build_altered", "the intermediate profile changed on disk",
                                  build_id=strat.precond["build_id"])
            else:
                precond = _resolve_precond(z9, cprof, body.paper_mediaid,
                                           body.gloss_enhancer, workdir, warnings)
            if strat or guided:
                import subprocess
                try:
                    ti1, targen_run = _tg.run_targen(body.targen_flags, workdir / "chart",
                                                     precond_icc=precond)
                except FileNotFoundError as e:
                    raise HTTPException(503, detail={"code": "targen_not_found", "message": str(e)})
                except (RuntimeError, subprocess.TimeoutExpired) as e:
                    # never another algorithm silently: the -Q fallback is offered, the user decides
                    if strat:
                        ofps = (strat.params[strat.pdef.id]["chart"] or {}).get("ofps")
                        composed_line = not strat.free
                    else:
                        ofps, composed_line = guided.params["chart"].get("ofps"), True
                    raise _refuse(422, "targen_failed", f"targen: {e}", ofps=ofps,
                                  timeout=isinstance(e, subprocess.TimeoutExpired),
                                  fallback=("Q" if ofps == "G" and composed_line else None),
                                  targen_flags=body.targen_flags)
            else:
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
        tag_sha = hashlib_sha256(tag.read_bytes())

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
        if printtarg:
            res = _create_printtarg_chart(body, ti1, tag, serial)
        else:
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
    desc = {}
    try:
        import json
        dpath = Path(res.descriptor_path)
        desc = json.loads(dpath.read_text(encoding="utf-8"))
        cm = desc.get("color_management") or {}
        desc["purpose"] = body.purpose
        if targen_run:
            desc["targen"] = targen_run   # REAL argv + stdout (#23) — audit the flags honored
        if guided:
            desc["guided"] = _guided_block(guided)
        if strat:
            desc["strategy"] = _strategy_block(strat, res.chart_id, body, tag_sha)
            if strat.precond:
                # the chart keeps the exact profile its patches were spread with: reproducible
                # even if the first chart is deleted, never replaced afterwards
                copy = Path(res.chart_dir) / "precondition.icc"
                shutil.copyfile(strat.precond["icc"], copy)
                if hashlib_sha256(copy.read_bytes()) != strat.precond["icc_sha256"]:
                    raise ValueError("precondition copy does not match its version")
                desc["precondition"] = {
                    "kind": "chart_build", "chart_id": strat.run, "build_id": strat.precond["build_id"],
                    "icc_sha256": strat.precond["icc_sha256"], "file": copy.name,
                    "role": _ps.ROLE_PRECONDITIONING, "built_at": strat.precond["created_at"],
                    "colprof_flags": strat.precond["colprof_flags"], "used_for": "targen -c only"}
                f_tag = ((strat.first_desc or {}).get("strategy") or {}).get("resident_tag_sha256")
                if f_tag and tag_sha and f_tag != tag_sha:
                    strategy_notices.append({"code": "resident_changed_since_first_pass"})
                    warnings.append("The resident profile of this paper changed since the first pass "
                                    "(another profile installed on the slot?). The values are still sent "
                                    "unconverted.")
        dpath.write_text(json.dumps(desc, indent=2, ensure_ascii=False), encoding="utf-8")
    except (OSError, ValueError):
        if strat:                        # a strategy pass without its block would be lost to the run
            shutil.rmtree(Path(res.chart_dir), ignore_errors=True)
            raise HTTPException(500, detail={"code": "strategy_record_failed",
                                             "message": "the chart could not record its strategy: not kept"})
    strategy_out = None
    if strat:
        st = desc["strategy"]
        strategy_out = {"id": st["id"], "pass": st["pass"], "role": st["role"], "run": st["run"],
                        "lines": st["lines"], "precondition": desc.get("precondition"),
                        "notices": strategy_notices,
                        "targen_argv": (targen_run or {}).get("argv")}

    if printtarg:
        out = {
            "chart_id": res.chart_id, "chart_dir": res.chart_dir,
            "n_patches": res.n_patches, "layout": _ptc.LAYOUT, "instrument": body.instrument,
            "roll_layout": res.roll_layout,
            "n_pages": res.n_pages, "steps_in_pass": res.steps_in_pass,
            "total_strips": res.total_strips,
            "tiff_w_mm": round(res.tiff_w_mm, 1), "tiff_h_mm": round(res.tiff_h_mm, 1),
            # roll consumed (chart + the roll's mechanical margins) and the printed orientation,
            # computed by the generator: the UI shows them before printing, never recomputes
            "roll_length_mm": (desc.get("geometry") or {}).get("roll_length_mm"),
            "media_key": body.media_key,
            "media_source": (desc.get("media") or {}).get("source"),     # "sheet" | "roll"
            "composition": {k: (desc.get("composition") or {}).get(k) for k in
                            ("rotation", "strips_cross_the_roll", "strip_order", "patch_1_side")},
            # printtarg -a / -h and the patches they give (nominal and found on the pages)
            "patch_scale": (desc.get("printtarg") or {}).get("patch_scale", 1.0),
            "high_density": bool((desc.get("printtarg") or {}).get("high_density")),
            "patch_geometry": desc.get("patch_geometry"),
            "purpose": body.purpose,
            "tag": {"icc_name": cm.get("icc_name"), "gloss_enhancer": cm.get("gloss_enhancer")},
            # request-level notes as text; the generator's own findings as codes (the UI words
            # them), their text stays in chart.json for the record
            "warnings": warnings,
            "generator_warnings": _printtarg_generator_warnings(desc),
        }
    else:
        out = {
            "chart_id": res.chart_id, "chart_dir": res.chart_dir,
            "n_patches": res.n_patches, "cols": res.cols, "nrows": res.nrows,
            "purpose": body.purpose,
            "feasibility": {"ok": res.feasibility.ok, "gap_mm": round(res.feasibility.gap_mm, 2)},
            "tag": {"icc_name": cm.get("icc_name"), "gloss_enhancer": cm.get("gloss_enhancer")},
            "warnings": warnings,
        }
    if strategy_out:                     # one-pass responses keep exactly their keys
        out["strategy"] = strategy_out
    if guided and desc.get("guided"):
        out["guided"] = _guided_out(desc["guided"], (targen_run or {}).get("argv"))
    return out


def _printtarg_generator_warnings(desc: dict) -> list:
    """Structured findings of the printtarg generator for the UI: patches whose place could
    not be checked on the pixels (repeated or white colour; their place is printtarg's)."""
    pm = desc.get("patch_map") or {}
    located, total = pm.get("located"), pm.get("patch_count")
    if isinstance(located, int) and isinstance(total, int) and located < total:
        return [{"code": "patches_not_located", "count": total - located}]
    return []


def _chart_print_summary(chart_id: str, d: Path, desc: dict) -> dict:
    """An EXISTING chart in the shape of the creation response, rebuilt from its chart.json, so
    that printing it later goes through the creation's own preview, checks and confirmation.
    ``existing`` says what the creation could not: when it was printed, its measurements, and
    whether it can still be printed (a lightened chart has no image any more)."""
    cm = desc.get("color_management") or {}
    geom = desc.get("geometry") or {}
    media = desc.get("media") or {}
    tag = {"icc_name": cm.get("icc_name"), "gloss_enhancer": cm.get("gloss_enhancer")}

    def _mm(v):
        return round(v, 1) if isinstance(v, (int, float)) else None
    if desc.get("layout") == _ptc.LAYOUT:
        inst = desc.get("instrument")
        comp, pt = desc.get("composition") or {}, desc.get("printtarg") or {}
        out = {
            "chart_id": chart_id, "chart_dir": str(d),
            "n_patches": geom.get("patch_count"), "layout": _ptc.LAYOUT,
            "instrument": inst.get("key") if isinstance(inst, dict) else inst,
            "roll_layout": desc.get("roll_layout"),
            "n_pages": geom.get("n_pages"), "steps_in_pass": geom.get("steps_in_pass"),
            "total_strips": geom.get("total_strips"),
            "tiff_w_mm": _mm(geom.get("tiff_w_mm")), "tiff_h_mm": _mm(geom.get("tiff_h_mm")),
            "roll_length_mm": geom.get("roll_length_mm"),
            "media_key": media.get("format_key"), "media_source": media.get("source"),
            "composition": {k: comp.get(k) for k in
                            ("rotation", "strips_cross_the_roll", "strip_order", "patch_1_side")},
            "patch_scale": pt.get("patch_scale", 1.0), "high_density": bool(pt.get("high_density")),
            "patch_geometry": desc.get("patch_geometry"),
            "purpose": desc.get("purpose", "profiling"), "tag": tag,
            "warnings": [], "generator_warnings": _printtarg_generator_warnings(desc),
        }
    else:
        feas = desc.get("feasibility") or {}
        gap = feas.get("gap_mm")
        out = {
            "chart_id": chart_id, "chart_dir": str(d),
            "n_patches": geom.get("patch_count"), "cols": geom.get("cols"), "nrows": geom.get("nrows"),
            "purpose": desc.get("purpose", "profiling"),
            "feasibility": {"ok": feas.get("ok"), "gap_mm": round(gap, 2) if isinstance(gap, (int, float)) else None},
            "tag": tag, "warnings": [],
            "media_key": media.get("format_key"),
        }
    st = desc.get("strategy") or {}
    if st.get("id"):
        out["strategy"] = {"id": st["id"], "pass": st.get("pass"), "role": st.get("role"), "run": st.get("run"),
                           "lines": st.get("lines"), "precondition": desc.get("precondition"),
                           "notices": [], "targen_argv": None}
    if desc.get("guided"):
        out["guided"] = _guided_out(desc["guided"], (desc.get("targen") or {}).get("argv"))
    files = desc.get("files") or {}
    tiff = d / files["tiff"] if files.get("tiff") else None
    if desc.get("lightened"):
        why = "lightened"
    elif tiff is None or not tiff.is_file():
        why = "tiff_missing"
    elif cm.get("tag_source") != "resident-live":
        why = "not_resident_tagged"
    else:
        why = None
    out["existing"] = {
        "created_at": desc.get("created_at"), "printed_at": desc.get("printed_at"),
        "n_prints": len(desc.get("print_history") or []) + (1 if desc.get("printed_at") else 0),
        "n_scans": len(_raw_ti3_paths(d)),
        "printable": why is None, "not_printable": why,
    }
    return out


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
    # Other keys (last_build_id, rejection_meta…) are KEPT: a route that rewrites the state
    # (role, rejection) must never drop what it does not manage. Legacy `excluded` = migrated.
    extra = {k: v for k, v in st.items() if k not in
             ("roles", "excluded", "profile_built_from", "rejected_readings", "profile_built_rejections")}
    return {
        **extra,
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
    # rejection_meta is INFORMATIVE only (why/when a reading was excluded): the registry is
    # rejected_readings; meta of a reading no longer rejected is dropped.
    meta = state.get("rejection_meta")
    if isinstance(meta, dict):
        rej = {k: set(v) for k, v in (state.get("rejected_readings") or {}).items()}
        state["rejection_meta"] = {scan: {sid: m for sid, m in sids.items() if sid in rej.get(scan, ())}
                                   for scan, sids in meta.items() if isinstance(sids, dict)}
        state["rejection_meta"] = {k: v for k, v in state["rejection_meta"].items() if v}
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


def _scans_comparability(paths: list) -> Optional[dict]:
    """Can these scans be compared as REPEATED readings (concordance ΔE, QC matrix)?
    Same measurement identity required: 409 ``scans_not_comparable`` when a known field
    differs (another instrument, another condition…), 409 ``measurement_record_invalid``
    for a bad record. An identity that is only insufficient (hand-dropped ti3) is
    compared, the returned decision says so: this view is a diagnostic, it produces no
    data. None when a ti3 is unreadable (the comparison reports it as before)."""
    members = []
    for p in paths:
        try:
            members.append((p.name, _mi.read_identity(p)))
        except _mi.MeasurementRecordError as e:
            raise HTTPException(409, detail={
                "code": "measurement_record_invalid", "scan": p.name,
                "message": f"measurement record of {p.name}: {e.message}",
                "record_error": {"code": e.code, "message": e.message}})
        except (OSError, ValueError):
            return None
    decision = _mi.check_combination(members)
    if decision.status == _mi.INCOMPATIBLE:
        raise HTTPException(409, detail={
            "code": "scans_not_comparable",
            "message": "these scans were not measured the same way (instrument or condition): "
                       "they are not repeated readings of one measurement",
            "decision": decision.to_dict(), "remedies": ["exclude_scan"]})
    return decision.to_dict()


def _same_measurement_scans(d: Path, ti3: str, names: list) -> list:
    """Among ``names``, the scans whose readings may stand in for a reading of ``ti3``: same
    measurement identity, or an identity not known well enough to tell (historical
    behaviour kept). A reading of another instrument or condition is never a replacement:
    the build would refuse to combine them. Every historical SOL scan shares one identity,
    so SOL charts are unaffected."""
    try:
        ref = _mi.read_identity(d / "measurements" / ti3)
    except (OSError, ValueError):
        return list(names)
    out = []
    for name in names:
        if name != ti3:
            try:
                other = _mi.read_identity(d / "measurements" / name)
            except (OSError, ValueError):
                other = None
            if other is not None and _mi.compare_identities(ref, other).status == _mi.INCOMPATIBLE:
                continue
        out.append(name)
    return out


def _build_request_record(body, flags: list, policy: str, strategy: Optional[dict] = None) -> dict:
    """The build REQUEST as recorded in the manifest (what a rebuild would replay). ``strategy``
    (charts of a profiling strategy only): run, pass, role and variant of this build."""
    rec = {"base": body.profile_base, "extra_chart_ids": list(body.extra_chart_ids),
           "source_profiles": [sp.path for sp in body.source_profiles],
           "colprof_flags": body.colprof_flags, "colprof_flags_resolved": list(flags),
           "name": body.name, "on_conflict": body.on_conflict, "policy": policy,
           "expected_fingerprint": getattr(body, "expected_fingerprint", None)}
    if strategy:
        rec["strategy"] = strategy
    return rec


def _build_strategy_info(chart_id: str, desc: dict, body) -> Optional[dict]:
    """Role of a build on a strategy chart, decided by the CHART (never by the client): the
    first pass builds the intermediate profile (preconditioning: never ranged, never
    installable), the final pass a normal profile. Variant = which passes it is built from
    (matched against the definition's variants: pass 2 alone, passes 1 + 2, else custom)."""
    st = desc.get("strategy") or {}
    if not st.get("id") or not st.get("pass"):
        return None
    try:
        sdef = _ps.get_strategy(st["id"])
        pdef = sdef.get_pass(st["pass"])
        role, variants = pdef.role, pdef.variants
    except _ps.StrategyError:
        sdef, role, variants = None, st.get("role"), ()
    info = {"id": st["id"], "run": st.get("run") or chart_id, "pass": st["pass"], "role": role, "variant": None}
    if role != _ps.ROLE_FINAL:
        return info
    sources = {st["pass"]}
    for xcid in getattr(body, "extra_chart_ids", None) or []:
        xd = _cache.locate_chart_dir(xcid)
        try:
            xst = _read_chart_json(xd).get("strategy") or {} if xd else {}
        except (OSError, ValueError):
            xst = {}
        if xst.get("run") == info["run"] and xst.get("pass"):
            sources.add(xst["pass"])
        else:
            sources.add(f"chart:{xcid}")
    if getattr(body, "source_profiles", None):
        sources.add("profiles")
    info["variant"] = "custom"
    for v in variants:
        want = set(v.get("sources") or [st["pass"]])
        if want == sources:
            info["variant"] = v.get("id")
            break
    return info


def _chart_snapshot(chart_id: str, chart_dir: Path, state: dict):
    """Frozen view of a chart for a profile build (lib.z9_client.build_inputs.ChartSnapshot):
    raw scans of measurements/ + roles + rejection registry, from ONE read of scan_state."""
    from lib.z9_client.build_inputs import ChartSnapshot
    return ChartSnapshot(
        chart_id=chart_id, chart_dir=chart_dir,
        raw_scans=tuple(p.name for p in _raw_ti3_paths(chart_dir)),
        roles=dict(state.get("roles") or {}),
        rejected={k: tuple(v) for k, v in (state.get("rejected_readings") or {}).items() if v})


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
    # Optional placement nudge, same semantics as the print screen's PrintParams.
    # Diagnostic: on some machines the hardcoded ROLL left margin clips the chart's
    # left edge/fiducial (reported on a Z9+ Pro wide carriage) — a positive
    # offset_x_mm lets a user find the correction empirically without a rebuild.
    offset_x_mm: float = 0.0
    offset_y_mm: float = 0.0
    # Later pass of a profiling strategy: print anyway despite the WARNINGS of the condition
    # check (resident or CLC changed since the first pass). Never overrides a blocking change.
    confirm_condition_warnings: bool = False


def _clc_state(z9, media_id: Optional[str]) -> Optional[dict]:
    """CLC (colour linearization) state of the paper as the Z9 reports it ({status, timestamp}),
    from the shared LEDM cache; None when it cannot be read. Informative only."""
    if not media_id:
        return None
    try:
        from webapp.backend.services import ledm_calibration_cache
        st = (ledm_calibration_cache.get_snapshot(z9) or {}).get(media_id)
        return dict(st) if isinstance(st, dict) else None
    except Exception:                                                # noqa: BLE001
        return None


def _print_serial(z9) -> Optional[str]:
    try:
        return _store.get_serial(z9)
    except Exception:                                                # noqa: BLE001
        return None


def _strategy_print_check(desc: dict, pp: dict, confirmed: bool) -> list:
    """Before a later pass is printed: the conditions of the run's first print must hold.
    A different paper, gloss enhancer, quality, rendering mode or printer is refused (409,
    no paper used); a resident profile or CLC changed since is a warning the user confirms.
    Returns the confirmed warnings (recorded with the print)."""
    st = desc.get("strategy") or {}
    inh = st.get("inherited") or {}
    first_dir = _cache.locate_chart_dir(st.get("run") or "")
    first = None
    if first_dir is not None:
        try:
            first = _read_chart_json(first_dir)
        except (OSError, ValueError):
            first = None
    blocking, warns = [], []
    if pp.get("paper_media_id") != inh.get("paper_media_id"):
        blocking.append({"code": "paper_differs", "expected": inh.get("paper_media_id"),
                         "actual": pp.get("paper_media_id")})
    if _norm_ge(pp.get("gloss_enhancer")) != _norm_ge(inh.get("gloss_enhancer")):
        blocking.append({"code": "gloss_enhancer_differs", "expected": _norm_ge(inh.get("gloss_enhancer")),
                         "actual": _norm_ge(pp.get("gloss_enhancer"))})
    fpp = (first or {}).get("print_params")
    if first is None:
        warns.append({"code": "first_pass_missing"})
    elif not fpp:
        warns.append({"code": "first_pass_print_unknown"})
    else:
        for key in ("quality", "rendermode"):
            if fpp.get(key) != pp.get(key):
                blocking.append({"code": f"{key}_differs", "expected": fpp.get(key), "actual": pp.get(key)})
        if fpp.get("printer_serial") and pp.get("printer_serial") and fpp["printer_serial"] != pp["printer_serial"]:
            blocking.append({"code": "printer_differs", "expected": fpp["printer_serial"],
                             "actual": pp["printer_serial"]})
        if fpp.get("resident_icc_sha256") and pp.get("resident_icc_sha256") \
                and fpp["resident_icc_sha256"] != pp["resident_icc_sha256"]:
            warns.append({"code": "resident_changed"})
        fclc, clc = fpp.get("clc") or {}, pp.get("clc") or {}
        if fclc.get("timestamp") and clc.get("timestamp") and fclc["timestamp"] != clc["timestamp"]:
            warns.append({"code": "clc_changed", "first": fclc.get("timestamp"), "now": clc.get("timestamp")})
    if blocking:
        raise _refuse(409, "strategy_print_conditions_changed",
                      "the print conditions differ from the run's first pass: not printed",
                      differences=blocking, warnings=warns)
    if warns and not confirmed:
        raise _refuse(409, "strategy_print_warnings", "the print conditions changed since the first pass: "
                      "confirm to print", warnings=warns)
    return warns


@router.get("/{chart_id}/print-summary")
def chart_print_summary(chart_id: str) -> dict:
    """An existing chart, to print (or print again): the creation response's shape plus
    ``existing``. Read only, no Z9: the print route checks everything again."""
    import json
    d = _chart_dir(chart_id)
    desc = json.loads((d / "chart.json").read_text(encoding="utf-8"))
    return _chart_print_summary(chart_id, d, desc)


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

    # An external-instrument chart is laid out for ONE format (a sheet size, or a roll): it goes
    # on that media, never on another one it happens to fit (an A4 chart would print on a
    # roll, but would no longer be the chart its descriptor describes).
    if desc.get("layout") == _ptc.LAYOUT:
        chart_media = (desc.get("media") or {}).get("format_key")
        loaded_w = loaded.sheet_width_mm if loaded.sheet_width_mm is not None else loaded.roll_width_mm
        why = _ptc.loaded_media_mismatch(chart_media, media_source=loaded.media_source,
                                         width_mm=loaded_w, height_mm=loaded.sheet_height_mm)
        if why == "chart_format_unknown":
            raise HTTPException(422, detail={
                "code": "chart_format_unknown", "chart_media": chart_media,
                "message": f"The chart names no known format ({chart_media!r}): not printed."})
        if why:
            raise HTTPException(409, detail={
                "code": "chart_media_mismatch", "reason": why, "chart_media": chart_media,
                "loaded": {"media_source": loaded.media_source, "width_mm": loaded_w,
                           "height_mm": loaded.sheet_height_mm},
                "message": (f"This chart is laid out for {chart_media}; the loaded paper "
                            f"({loaded.media_source or 'unknown source'}) is not identified as "
                            f"that format ({why}).")})

    # ── UNIQUE placement : EXACTLY the print screen pipeline ──
    # We do NOT reinvent placement (the old inline top-anchored one overflowed in GE).
    # The chart TIFF is treated like any other image : compute_geometry
    # (validated centering) → detect_geometry_issues (printable-area safeguard) →
    # _build_lib_job → PrintOps.send. Same path as refine_print_job.
    info = TiffInfo.from_path(tiff)
    gloss = "FULLPAGE" if (cm.get("gloss_enhancer") == "FULLPAGE") else "OFF"
    params = PrintParams(gloss_enhancer=gloss, quality=body.quality, rendermode="COLOR",
                         offset_x_mm=body.offset_x_mm, offset_y_mm=body.offset_y_mm)
    try:
        geometry = print_geometry.compute_geometry(loaded, params, info.width_mm, info.height_mm)
    except print_geometry.UnsupportedMediaSource as e:
        raise HTTPException(409, detail=str(e))
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

    # The parameters ACTUALLY used, kept with the chart; a later strategy pass must match the
    # first one's (checked here, before any paper is used).
    icc = job.icc_override
    print_params = {
        "quality": params.quality, "rendermode": params.rendermode, "gloss_enhancer": gloss,
        "paper_media_id": loaded.id, "paper_name": loaded.name, "media_source": geometry.media_source,
        "printer_serial": _print_serial(z9),
        "resident_icc_sha256": hashlib_sha256(bytes(icc)) if isinstance(icc, (bytes, bytearray)) else None,
        "clc": _clc_state(z9, loaded.id),
        "offset_x_mm": body.offset_x_mm, "offset_y_mm": body.offset_y_mm,
    }
    st = desc.get("strategy") or {}
    if st.get("run") and st["run"] != chart_id:
        confirmed = _strategy_print_check(desc, print_params, body.confirm_condition_warnings)
        if confirmed:
            print_params["confirmed_warnings"] = confirmed

    try:
        result = PrintOps(z9).send(job)
    except Exception as e:  # noqa: BLE001 (geometry/preflight/send → clear message)
        raise HTTPException(422, detail=f"Print refused: {e}")

    if desc.get("layout") == _ptc.LAYOUT:
        # External-instrument chart: chartread reads it from its .ti2, the built-in
        # spectrophotometer never does → there is no scanLayout to recompute. Record
        # where the pipeline put it, for the record only.
        desc["print_placement"] = {
            "offset_x_mm": geometry.image_x_mm, "offset_y_mm": geometry.image_y_mm,
            "media_source": geometry.media_source, "mode": "print_pipeline",
            # printed size (the TIFF's own: no scaling) and the media it was placed on
            "image_w_mm": geometry.image_width_mm, "image_h_mm": geometry.image_height_mm,
            "sheet_w_mm": geometry.sheet_width_mm, "sheet_h_mm": geometry.sheet_height_mm,
        }
    else:
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

    # printed_at : the chart enters the library (printed only). Printed again: the previous
    # print is kept in print_history (its measurements stay those of that print).
    from datetime import datetime
    if desc.get("printed_at"):
        prev = desc.get("print_params") or {"printed_at": desc["printed_at"]}
        desc["print_history"] = [*(desc.get("print_history") or []), prev]
    desc["printed_at"] = datetime.now().isoformat(timespec="seconds")
    desc["print_params"] = {**print_params, "printed_at": desc["printed_at"]}
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
    a snapshot ; poll GET ``/{id}/scan/status``. HARD guards : supported model (Z9
    only), anti-concurrent, cooldown ≥30 s, multi-scan session lock. The ti3 is only
    written at OP_FINISHED_OK."""
    import json
    from webapp.backend.services import chart_scan_job, scan_session

    d = _chart_dir(chart_id)
    desc = json.loads((d / "chart.json").read_text(encoding="utf-8"))
    # An external-instrument chart has no scanLayout by design: refuse it explicitly,
    # before any other guard and before any session or SOL act.
    if desc.get("layout") == _ptc.LAYOUT:
        raise HTTPException(409, detail={
            "code": "chart_for_external_instrument",
            "layout": _ptc.LAYOUT,
            "message": ("This chart is laid out for an external spectrophotometer "
                        "(read with chartread), not for the built-in one."),
        })
    fields = list((desc.get("scanLayout") or {}).get("fields", {}).items())
    if not fields:
        raise HTTPException(422, detail="scanLayout missing from descriptor")
    meas = d / "measurements"

    # HARD guards BEFORE any session write (a refusal NEVER creates a locked
    # phantom session).
    # Supported model first: the scan is proven on the Z9 only and crashes the Z9
    # Pro firmware. Read from the LIVE identification, never from store.json (an
    # entry registered before the Pro was recognised still says "validated").
    try:
        family = printer_model.printer_family(z9)
    except Z9Error as e:
        raise HTTPException(502, detail=f"cannot identify the printer: {e}")
    if not printer_model.scan_supported(family):
        raise HTTPException(409, detail={
            "code": "scan_unsupported_model",
            "family": family,
            "message": ("The built-in spectrophotometer scan is only supported "
                        "on the DesignJet Z9."),
        })
    # Anti-hammering:
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
    compat = _scans_comparability(paths)          # same measurement identity, or 409
    try:
        out = scan_delta.compute_scan_delta(paths)
    except (ValueError, OSError) as e:
        raise HTTPException(422, detail=f"ΔE comparison failed: {e}")
    out["measurement_compatibility"] = compat
    return out


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
        same = set(_same_measurement_scans(d, ti3, [p.name for p in _included_ti3_paths(d)]))
        remaining = [p.name for p in _included_ti3_paths(d)
                     if p.name != ti3 and p.name in same and sample_id in scan_delta.ti3_sample_ids(p)
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


class ExclusionBody(BaseModel):
    """Explicit exclusion API (phase 4C) — distinct from the legacy /reject route, whose rule
    ("never the last valid reading of a patch") stays unchanged for the current interface."""
    excluded: bool = True                     # False = re-include (always allowed)
    reason: Optional[str] = Field(default=None, max_length=200)
    dry_run: bool = False                     # True = impact only, nothing written
    extra_chart_ids: list[str] = []           # context: additional charts of the next build


_SEVERITY = ("ok", "info", "warning", "strong_warning", "refused")


def _exclusion_impact(chart_id: str, d: Path, state: dict, ti3: str, sid: str,
                      extra_chart_ids: list) -> dict:
    """Foreseeable consequences of excluding (ti3, sid), per base, with the SAME planning and
    coverage engine as the build. Chart-level refusal only for white/black left without any
    compatible reading in the chart's included scans (the build plan decides the rest)."""
    from lib.z9_client import build_inputs as bi
    rows = dict(bi.read_device_rows(d / "measurements" / ti3, f"{chart_id}/{ti3}"))
    rgb = rows[sid]
    key, (pos, corner) = bi.device_key(rgb), bi.device_position(rgb)
    after = {k: list(v) for k, v in state["rejected_readings"].items()}
    after[ti3] = sorted(set(after.get(ti3, [])) | {sid})
    included = _same_measurement_scans(
        d, ti3, [p.name for p in _raw_ti3_paths(d) if _scan_role(state, p.name) == "included"])
    others = 0
    for scan in included:
        rej = set(after.get(scan, []))
        others += sum(1 for r_sid, r_rgb in bi.read_device_rows(d / "measurements" / scan)
                      if bi.device_key(r_rgb) == key and r_sid not in rej)
    refusal = f"{corner.upper()}_LAST_READING" if corner in ("white", "black") and others == 0 else None
    media_id, ref_ge = _chart_paper_ge(chart_id)
    extras = []
    for x in extra_chart_ids:
        xm, xge = _chart_paper_ge(x)
        _assert_source_compatible(media_id, ref_ge, xm, xge, f"chart {x}")
        extras.append(_chart_snapshot(x, _chart_dir(x), _read_scan_state(_chart_dir(x))))
    snap_after = _chart_snapshot(chart_id, d, {**state, "rejected_readings": after})
    bases = {}
    unavailable = {}
    for base in ("last", "average"):
        try:
            plan = bi.plan_inputs(snap_after, extras, bi.BuildRequest(base=base, policy="apply_all"))
        except bi.MeasurementIdentityError as e:
            unavailable[base] = e.code             # this base cannot be built at all: no impact
            continue
        if base == "average" and plan.base_effective != "average":
            continue                                          # fewer than 2 included scans
        used = any(s.chart_id == chart_id and s.scan == ti3 for s in plan.current)
        cov = bi.analyze_coverage(plan, _qc_findings_cached(plan))
        hole = any(bi.device_key(h["rgb"]) == key for h in cov["holes"])
        lost = corner in ("white", "black") and f"{corner.upper()}_LAST_READING" in cov["refusals"]
        level = ("ok" if not used else "refused" if lost
                 else bi._LEVEL_BY_POSITION[pos] if hole else "ok")
        bases[base] = {"base_effective": plan.base_effective, "reading_used": used, "hole": hole,
                       "level": level, "refusals": cov["refusals"], "guards": cov["guards"],
                       "recommend_remeasure": cov["recommend_remeasure"], "reasons": cov["reasons"]}
    level = max([b["level"] for b in bases.values()] + (["refused"] if refusal else ["ok"]), key=_SEVERITY.index)
    return {"reading": {"chart_id": chart_id, "scan": ti3, "sid": sid, "rgb": list(rgb), "position": pos,
                        "corner": corner},
            "other_compatible_readings_in_chart": others, "chart_level_refusal": refusal,
            "level": level, "bases": bases, "bases_unavailable": unavailable}


@router.post("/{chart_id}/scans/{ti3}/readings/{sample_id}/exclusion")
def set_reading_exclusion(chart_id: str, ti3: str, sample_id: str, body: ExclusionBody) -> dict:
    """Explicit, reversible exclusion of ONE reading (scan × SAMPLE_ID), also possible with a
    single scan. Writes only scan_state.json (rejected_readings = the registry, rejection_meta =
    informative). Returns the foreseeable impact per base; refuses (409) only the last
    compatible reading of white or black in the chart. ``dry_run`` writes nothing. Never
    automatic, no Z9 act, the ti3 is never touched."""
    from webapp.backend.services import scan_delta
    d = _chart_dir(chart_id)
    if ti3 not in {p.name for p in _raw_ti3_paths(d)}:
        raise HTTPException(404, detail={"error": "unknown_scan", "message": f"unknown scan: {ti3}"})
    st = _read_scan_state(d)
    if _scan_role(st, ti3) != "included":
        raise HTTPException(409, detail={"error": "scan_excluded",
                                         "message": "the scan is excluded: exclusions apply to included scans"})
    try:
        sids = scan_delta.ti3_sample_ids(d / "measurements" / ti3)
    except (OSError, ValueError) as e:
        raise HTTPException(422, detail={"error": "unreadable", "message": f"unreadable ti3: {e}"})
    if sample_id not in sids:
        raise HTTPException(404, detail={"error": "unknown_reading", "message": f"unknown reading in {ti3}: {sample_id}"})
    cur = set(st["rejected_readings"].get(ti3, []))
    if not body.excluded:
        if not body.dry_run and sample_id in cur:
            cur.discard(sample_id)
            st["rejected_readings"] = {**st["rejected_readings"], ti3: sorted(cur)}
            st["rejected_readings"] = {k: v for k, v in st["rejected_readings"].items() if v}
            _write_scan_state(d, st)
        return {"ok": True, "chart_id": chart_id, "scan": ti3, "sid": sample_id, "excluded": False,
                "applied": not body.dry_run, "decision": "accepted"}
    try:
        impact = _exclusion_impact(chart_id, d, st, ti3, sample_id, body.extra_chart_ids)
    except (ValueError, HTTPException) as e:
        raise HTTPException(422, detail={"error": "impact_failed", "message": str(getattr(e, "detail", e))})
    if impact["chart_level_refusal"]:
        raise HTTPException(409, detail={"error": "exclusion_refused", "code": impact["chart_level_refusal"],
                                         "message": "last compatible reading of a structural point",
                                         "impact": impact})
    if not body.dry_run and sample_id not in cur:
        from datetime import datetime, timezone
        st["rejected_readings"] = {**st["rejected_readings"], ti3: sorted(cur | {sample_id})}
        meta = st.get("rejection_meta") if isinstance(st.get("rejection_meta"), dict) else {}
        meta.setdefault(ti3, {})[sample_id] = {
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "origin": "user",
            "api": "exclusion/v1", "reason": body.reason, "impact_level": impact["level"]}
        st["rejection_meta"] = meta
        _write_scan_state(d, st)
    return {"ok": True, "chart_id": chart_id, "scan": ti3, "sid": sample_id, "excluded": True,
            "applied": not body.dry_run, "decision": "accepted", "impact": impact}


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
    compat = _scans_comparability(paths)          # same measurement identity, or 409
    rmap = _rejected_map(_read_scan_state(d))
    try:
        out = scan_delta.compute_scan_qc(paths, rmap)
    except (ValueError, OSError) as e:
        raise HTTPException(422, detail=f"QC view unavailable: {e}")
    out["measurement_compatibility"] = compat
    return out


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
        data, lab_integrity = scan_delta.read_scan(p)
    except (OSError, ValueError) as e:
        # Raw cause kept in the logs (freeglaz.log); the UI localizes from the stable code.
        logger.warning("measurements unreadable — chart %s scan %s: %s", chart_id, ti3, e)
        raise HTTPException(422, detail={"code": "UNREADABLE", "message": f"unreadable ti3: {e}"})
    # Use state (phase 4D, read-only): excluded or not, informative reason, and the foreseeable
    # risk if this reading were excluded (same chart-level rule as the exclusion API).
    st = _read_scan_state(d)
    rej = set(st["rejected_readings"].get(ti3, []))
    meta = ((st.get("rejection_meta") or {}).get(ti3) or {}) if isinstance(st.get("rejection_meta"), dict) else {}
    risks = _exclusion_risks(d, st, ti3)
    patches = [{"id": sid, "rgb": v["rgb"], "lab": [round(x, 2) for x in v["lab"]],
                "excluded": sid in rej, "exclusion_reason": (meta.get(sid) or {}).get("reason"),
                "exclusion_risk": risks.get(sid)}
               for sid, v in data.items()]
    # lab_integrity: Lab recomputed from XYZ (Argyll D50) when present; stored LAB_* only checked.
    return {"chart_id": chart_id, "ti3": ti3, "n_patches": len(patches), "patches": patches,
            "lab_integrity": lab_integrity, "scan_role": _scan_role(st, ti3),
            "n_excluded": sum(1 for x in patches if x["excluded"])}


def _exclusion_risks(d: Path, state: dict, ti3: str) -> dict:
    """{sid: {position, corner, other_compatible_readings, level}} for every reading of ``ti3``:
    the level the exclusion API would announce at chart level — "ok" when another compatible
    reading (same device value, any SAMPLE_ID) remains in the chart's included scans, else by
    cube position (info / warning / strong_warning), "refused" for the last white or black.
    The per-base consequences are computed by the API at the moment of the action."""
    from collections import Counter
    from lib.z9_client import build_inputs as bi
    try:
        rows = bi.read_device_rows(d / "measurements" / ti3)
    except (OSError, ValueError):
        return {}
    pool: Counter = Counter()
    same = set(_same_measurement_scans(
        d, ti3, [p.name for p in _raw_ti3_paths(d) if _scan_role(state, p.name) == "included"]))
    for p in _raw_ti3_paths(d):
        if p.name not in same:
            continue
        rej = set(state["rejected_readings"].get(p.name, []))
        try:
            pool.update(bi.device_key(rgb) for sid, rgb in bi.read_device_rows(p) if sid not in rej)
        except (OSError, ValueError):
            continue
    own_rej = set(state["rejected_readings"].get(ti3, []))
    included = _scan_role(state, ti3) == "included"
    out = {}
    for sid, rgb in rows:
        key = bi.device_key(rgb)
        pos, corner = bi.device_position(rgb)
        others = pool[key] - (1 if (included and sid not in own_rej) else 0)
        if others > 0:
            level = "ok"
        elif corner in ("white", "black"):
            level = "refused"
        else:
            level = bi._LEVEL_BY_POSITION[pos]
        out[sid] = {"position": pos, "corner": corner, "other_compatible_readings": others, "level": level}
    return out


@router.get("/{chart_id}/scans/{ti3}/qc")
def scan_measure_qc(chart_id: str, ti3: str) -> dict:
    """INTRINSIC measurement QC of ONE raw scan (single-scan, advisory): each reading is
    compared with the prediction from the OTHER colours of the chart (lib.z9_client.measure_qc).
    READ-ONLY: no exclusion, no profile, no Z9 act; cached under measurements/.qc/ (keyed by
    ti3 sha256 + algorithm version + parameters). Comparing scans stays /scan/qc's job."""
    from webapp.backend.services import measure_qc as qc_service
    d = _chart_dir(chart_id)
    p = next((x for x in _raw_ti3_paths(d) if x.name == ti3), None)   # RAW scans only (anti-traversal)
    if p is None:
        raise HTTPException(404, detail={"code": "SCAN_NOT_FOUND", "message": f"unknown scan: {ti3}"})
    try:
        out = qc_service.get_report(p)
    except qc_service.ScanQCError as e:
        status = 404 if e.code == "SCAN_NOT_FOUND" else 422
        logger.info("measure QC not possible — chart %s scan %s: %s (%s)", chart_id, ti3, e.code, e)
        raise HTTPException(status, detail={"code": e.code, "message": str(e)})
    return {"chart_id": chart_id, "ti3": ti3, **out}


@router.delete("/{chart_id}/scans/{ti3}")
def delete_scan(chart_id: str, ti3: str) -> dict:
    """PERMANENTLY deletes a measurement set (ti3 + cgats + measurement record) — DESTRUCTIVE sibling of "exclude"
    (exclude KEEPS the ti3, reversible ; delete ERASES it, IRREVERSIBLE). UI-side confirmation.
    Cleans the role + rejections of the sidecar for this ti3 (no dangling reference). The ICC profile
    (SEPARATE file, embedded measurements) is NOT touched, but becomes "stale" if it was built
    from this set (built_from ≠ current set — detected by the detail). LOCAL, no Z9."""
    d = _chart_dir(chart_id)
    raw = {p.name for p in _raw_ti3_paths(d)}
    if ti3 not in raw:
        raise HTTPException(404, detail=f"unknown scan: {ti3}")
    meas = d / "measurements"
    from webapp.backend.services import measure_qc as qc_service
    qc_service.purge(meas / ti3)                                  # derived QC cache of this scan
    (meas / ti3).unlink(missing_ok=True)
    (meas / f"{Path(ti3).stem}.cgats").unlink(missing_ok=True)   # twin cgats (best-effort)
    _mi.record_path(meas / ti3).unlink(missing_ok=True)          # twin measurement record
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
    # Rejection policy (phase 4C). None/"legacy" = historical behaviour (base "last" ignores the
    # current chart's rejections). "apply_all" applies them in every mode; it must be requested
    # explicitly AND carry the fingerprint returned by POST /profile/preview.
    policy: Optional[str] = None
    expected_fingerprint: Optional[str] = None


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
    info = _build_strategy_info(chart_id, desc, body)
    if info and info["role"] == _ps.ROLE_PRECONDITIONING:
        return None                                  # an intermediate profile is never ranged
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
    # Phase 4C: "apply_all" only on explicit request, and only for the inputs the user previewed.
    try:
        policy = _build_policy(body)
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    if policy == "apply_all" and not body.expected_fingerprint:
        raise HTTPException(422, detail={"error": "fingerprint_required", "message":
                                         "policy apply_all requires expected_fingerprint (POST /profile/preview first)"})
    if body.expected_fingerprint:
        try:
            ctx = _prepare_build(chart_id, body)
        except MeasurementIdentityError as e:
            raise _identity_refusal(e)
        except ValueError as e:
            raise HTTPException(422, detail=str(e))
        if ctx.plan.fingerprint != body.expected_fingerprint:
            raise HTTPException(409, detail={"error": "inputs_changed", "expected": body.expected_fingerprint,
                                             "actual": ctx.plan.fingerprint,
                                             "message": "the inputs changed since the preview: preview again"})
        if policy == "apply_all":
            refusals = _coverage_for(ctx).get("refusals") or []
            if refusals:
                raise HTTPException(409, detail={"error": "coverage_refused", "refusals": refusals,
                                                 "message": "a structural point (white/black) would have no reading left"})
    else:
        _assert_build_identity(chart_id, body)   # incompatible readings: refused before the job
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


def _identity_refusal(e: MeasurementIdentityError) -> HTTPException:
    """409 with the stable payload of build_inputs.MeasurementIdentityError (code, scans
    and their identities, conflicts, remedies). The backend is the authority: the build
    job refuses the same inputs again if this route is bypassed."""
    return HTTPException(409, detail=e.to_detail())


def _assert_build_identity(chart_id: str, body: "ProfileChartBody") -> None:
    """Same planning as the build, only to refuse incompatible readings synchronously.
    Any other planning error is left to the build job, unchanged."""
    try:
        _prepare_build(chart_id, body)
    except MeasurementIdentityError as e:
        raise _identity_refusal(e)
    except (ValueError, HTTPException, OSError):
        pass


def _selection_key(request: dict) -> tuple:
    """What a user CHOOSES for a build (compared with the current build's request)."""
    return (request.get("base") or "last", tuple(request.get("extra_chart_ids") or ()),
            tuple(request.get("source_profiles") or ()), tuple(request.get("colprof_flags_resolved") or ()),
            request.get("policy") or "legacy")


def _current_build_policy(chart_id: str, d: Path, ptr: dict) -> Optional[str]:
    """Rejection policy of the CURRENT profile's build: None = no current profile; "unknown" =
    no reliable manifest (same criteria as profile.status v2: a build manifest with request and
    fingerprint whose recorded ICC sha256 is the current file's); else "legacy" | "apply_all"
    (a build manifest without a policy predates 4C, when every build was legacy)."""
    icc = d / f"{chart_id}.icc"
    if not icc.is_file():
        return None
    req, fp = ptr.get("request"), (ptr.get("plan") or {}).get("inputs_fingerprint")
    if not (req and fp) or ptr.get("kind") != "build" \
            or (ptr.get("pointer") or {}).get("icc_sha256") != hashlib_sha256(icc.read_bytes()):
        return "unknown"
    return req.get("policy") or "legacy"


@router.post("/{chart_id}/profile/preview")
def profile_preview(chart_id: str, body: ProfileChartBody) -> dict:
    """What a build with this request WOULD use, without building: same planning engine as the
    build (build_inputs.plan_inputs), no working ti3, no colprof, no write. Returns the inputs
    fingerprint to send back as ``expected_fingerprint``; the build refuses if it changed."""
    from lib.z9_client import profile_versions
    d = _chart_dir(chart_id)
    try:
        ctx = _prepare_build(chart_id, body)
        cov = _coverage_for(ctx)
    except MeasurementIdentityError as e:
        raise _identity_refusal(e)
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    req = _build_request_record(body, ctx.flags, ctx.plan.policy, ctx.strategy)
    ptr = profile_versions.read_pointer(d) or {}
    cur_req = ptr.get("request")
    plan = ctx.plan.to_dict()
    # Rejections that the legacy policy (previous builds) ignored and that apply_all applies:
    # explained to the user as a change of rule, not as a detector finding.
    newly = []
    if ctx.plan.policy == "apply_all":
        try:
            legacy = _prepare_build(chart_id, body.model_copy(update={"policy": "legacy"})).plan
            leg = {(s.chart_id, s.scan): set(s.rejections_applied) for s in legacy.scans}
            for s in ctx.plan.scans:
                extra = sorted(set(s.rejections_applied) - leg.get((s.chart_id, s.scan), set()))
                if extra:
                    newly.append({"chart_id": s.chart_id, "scan": s.scan, "sids": extra})
        except (ValueError, HTTPException):
            newly = []
    # Strongly suspect readings still INCLUDED (cached QC reports only; never computed here):
    # a warning, never an obligation to exclude.
    from webapp.backend.services import measure_qc as qc_service
    strong = []
    for s in ctx.plan.scans:
        rep = qc_service.cached_report(s.path)
        if rep:
            sids = [p["sample_id"] for p in rep.get("patches", []) if p.get("status") == "strong"
                    and p["sample_id"] not in set(s.rejections_applied)]
            if sids:
                strong.append({"chart_id": s.chart_id, "scan": s.scan, "sids": sids})
    return {"chart_id": chart_id, "policy": ctx.plan.policy, "base_requested": ctx.plan.base_requested,
            "base_effective": ctx.plan.base_effective, "inputs_fingerprint": ctx.plan.fingerprint,
            "sources": {"current_scans": plan["current_scans"], "extra_scans": plan["extra_scans"],
                        "source_profiles": plan["source_profiles"]},
            "rejections_applied": [{"chart_id": x["chart_id"], "scan": x["scan"], "sids": x["rejections_applied"]}
                                   for x in plan["current_scans"] + plan["extra_scans"] if x["rejections_applied"]],
            "rejections_not_applied": plan["rejections_not_applied"],
            "rejections_unmatched": plan["rejections_unmatched"],
            "holes": cov.get("holes", []), "guards": cov.get("guards", []), "refusals": cov.get("refusals", []),
            "would_be_refused": ctx.plan.policy == "apply_all" and bool(cov.get("refusals")),
            "recommend_remeasure": cov.get("recommend_remeasure", False), "reasons": cov.get("reasons", []),
            "coverage": cov, "colprof_flags": list(ctx.flags),
            "current_build_id": (ptr.get("pointer") or {}).get("build_id"),
            "current_inputs_fingerprint": (ptr.get("plan") or {}).get("inputs_fingerprint"),
            "differs_from_current_build": (None if not cur_req else _selection_key(cur_req) != _selection_key(req)),
            "newly_applied_vs_legacy": newly, "qc_strong_included": strong,
            # identities of the used scans and the decision that allowed combining them
            "measurement_compatibility": plan.get("measurement_compatibility"),
            # decides how the UI words newly_applied_vs_legacy: a historical notice only when the
            # current build really used the legacy rule, a neutral one without a reliable manifest
            "current_build_policy": _current_build_policy(chart_id, d, ptr),
            # profiling strategy charts: role/variant of this build, and whether -f may be used
            "strategy": ctx.strategy, "fwa": ctx.fwa}


def _profile_status(chart_id: str, d: Path) -> dict:
    """New semantics (phase 4C, not displayed yet): the current profile is STALE if the same
    request, replanned now, would use different inputs. unknown = no reliable manifest (never
    presumed up to date). missing_input = a used source no longer exists. toolchain_changed =
    colprof version differs from the one recorded."""
    from lib.z9_client import build_inputs, profile_versions
    ptr = profile_versions.read_pointer(d) or {}
    bid = (ptr.get("pointer") or {}).get("build_id")
    base = {"state": "unknown", "stale": None, "missing_input": [], "toolchain_changed": None, "build_id": bid}
    if not (d / f"{chart_id}.icc").is_file():
        return {**base, "state": "no_profile"}
    req, fp = ptr.get("request"), (ptr.get("plan") or {}).get("inputs_fingerprint")
    icc_sha = hashlib_sha256((d / f"{chart_id}.icc").read_bytes())
    if not (req and fp) or ptr.get("kind") != "build" or (ptr.get("pointer") or {}).get("icc_sha256") != icc_sha:
        return base
    missing = []
    plan = ptr["plan"]
    for x in plan.get("current_scans", []) + plan.get("extra_scans", []):
        cd = d if x["chart_id"] == chart_id else _cache.locate_chart_dir(x["chart_id"])
        if cd is None or not (cd / "measurements" / x["scan"]).is_file():
            missing.append({"kind": "scan", "chart_id": x["chart_id"], "scan": x["scan"]})
    missing += [{"kind": "profile", "path": p} for p in req.get("source_profiles") or [] if not Path(p).is_file()]
    cur_colprof = build_inputs.software_versions().get("colprof")
    built_colprof = (ptr.get("software") or {}).get("colprof")
    # None = unknown (a version missing on either side): never asserted either way
    toolchain = (cur_colprof != built_colprof) if (cur_colprof and built_colprof) else None
    if missing:
        return {**base, "state": "missing_input", "stale": True, "missing_input": missing, "toolchain_changed": toolchain}
    replay = ProfileChartBody(colprof_flags=req.get("colprof_flags"), profile_base=req.get("base"),
                              extra_chart_ids=req.get("extra_chart_ids") or [],
                              source_profiles=[SourceProfileRef(path=p) for p in req.get("source_profiles") or []],
                              policy=req.get("policy"))
    try:
        now_fp = _prepare_build(chart_id, replay).plan.fingerprint
    except (ValueError, HTTPException) as e:
        return {**base, "state": "missing_input", "stale": True, "toolchain_changed": toolchain,
                "missing_input": [{"kind": "replan_failed", "detail": str(getattr(e, "detail", e)),
                                   "code": getattr(e, "code", None)}]}
    stale = now_fp != fp
    return {**base, "state": "stale" if stale else "up_to_date", "stale": stale, "toolchain_changed": toolchain}


def hashlib_sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _build_summary(chart_dir: Path, v: dict, current_id: Optional[str]) -> dict:
    """One version of the history, from what is STORED (manifest + references): nothing is
    recomputed or invented; a preserved profile keeps its unknown provenance."""
    import json
    from lib.z9_client import profile_versions
    out = {"build_id": v["build_id"], "kind": v["kind"], "created_at": v.get("created_at"),
           "current": v["build_id"] == current_id, "intact": v["intact"], "problem": v["problem"],
           "icc_sha256": v["icc_sha256"], "manifest_available": v["manifest_sha256"] is not None,
           "provenance": None, "label": None, "base_requested": None, "base_effective": None, "policy": None,
           "n_scans": None, "n_extra_charts": None, "n_source_profiles": None, "n_excluded": None,
           "n_holes": None, "colprof_flags": None, "references": [], "role": None, "strategy_variant": None}
    try:
        m = profile_versions.read_version_manifest(chart_dir, v["build_id"])
    except (OSError, ValueError):
        return out
    plan = m.get("plan") or {}
    if m.get("kind") == "preserved":
        out["provenance"] = m.get("provenance") or "unknown"
    else:
        out["provenance"] = "complete"
    out["label"] = (m.get("result") or {}).get("label") or (m.get("request") or {}).get("name")
    st = (m.get("request") or {}).get("strategy") or {}
    out["role"], out["strategy_variant"] = st.get("role"), st.get("variant")
    if plan:
        scans = plan.get("current_scans", []) + plan.get("extra_scans", [])
        out.update({"base_requested": plan.get("base_requested"), "base_effective": plan.get("base_effective"),
                    "policy": plan.get("policy"), "n_scans": len(scans),
                    "n_extra_charts": len({x["chart_id"] for x in plan.get("extra_scans", [])}),
                    "n_source_profiles": len(plan.get("source_profiles", [])),
                    "n_excluded": sum(len(x.get("rejections_applied", [])) for x in scans),
                    "colprof_flags": plan.get("colprof_flags")})
    cov = m.get("coverage") or {}
    if "holes" in cov:
        out["n_holes"] = len(cov["holes"])
    try:
        refs = json.loads((profile_versions.builds_dir(chart_dir) / v["build_id"] / profile_versions.REFERENCES_NAME)
                          .read_text(encoding="utf-8")).get("references", [])
        out["references"] = [{"kind": r.get("kind"), "filename": r.get("filename")} for r in refs]
    except (OSError, ValueError):
        pass
    return out


@router.get("/{chart_id}/builds")
def list_builds(chart_id: str) -> dict:
    """History of the profiles built for this chart (immutable versions, newest first) and the
    publication state. READ-ONLY: exposes what is stored, never rebuilds, never deletes."""
    from lib.z9_client import profile_versions
    d = _chart_dir(chart_id)
    rep = profile_versions.inspect(d, chart_id)
    cur = rep["current_build_id"]
    builds = [_build_summary(d, v, cur) for v in reversed(rep["versions"])]
    return {"chart_id": chart_id, "current_build_id": cur, "pointer_build_id": rep["pointer_build_id"],
            "publication": {"state": rep["state"], "problems": rep["problems"]}, "builds": builds}


@router.get("/{chart_id}/builds/{build_id}/manifest")
def get_build_manifest(chart_id: str, build_id: str) -> dict:
    """The stored manifest of one version (read-only)."""
    from lib.z9_client import profile_versions
    d = _chart_dir(chart_id)
    if build_id not in {v["build_id"] for v in profile_versions.list_versions(d)}:
        raise HTTPException(404, detail={"error": "unknown_build", "message": f"unknown build: {build_id}"})
    try:
        return profile_versions.read_version_manifest(d, build_id)
    except (OSError, ValueError) as e:
        raise HTTPException(422, detail={"error": "unreadable_manifest", "message": str(e)})


class CompareBuildsBody(BaseModel):
    a: str                                    # reference version (build id)
    b: str                                    # new version (build id)
    grid_n: int = Field(default=9, ge=3, le=17)
    top_n: int = Field(default=10, ge=1, le=50)


def _excluded_points(chart_id: str, manifest: dict) -> dict:
    """{(chart, scan, sid): rgb 0-100} of the readings a build EXCLUDED (from its manifest)."""
    from lib.z9_client import build_inputs as bi
    out = {}
    plan = manifest.get("plan") or {}
    for x in plan.get("current_scans", []) + plan.get("extra_scans", []):
        if not x.get("rejections_applied"):
            continue
        cd = _cache.locate_chart_dir(x["chart_id"])
        try:
            rows = dict(bi.read_device_rows(cd / "measurements" / x["scan"])) if cd else {}
        except (OSError, ValueError):
            rows = {}
        for sid in x["rejections_applied"]:
            if sid in rows:
                out[(x["chart_id"], x["scan"], sid)] = list(rows[sid])
    return out


@router.post("/{chart_id}/builds/compare")
def compare_builds(chart_id: str, body: CompareBuildsBody) -> dict:
    """ΔE00 between the A2B transforms of two STORED versions of this chart's profile (never
    rebuilt, never installed): measured device values + control grid, relative and perceptual
    intents, most displaced colours, neighbourhood of the readings excluded in one version only,
    white and black points. Offline (Argyll xicclu). A transform difference, not a measurement."""
    from lib.z9_client import profile_diff, profile_versions
    from lib.z9_client.argyll import ArgyllNotFound
    d = _chart_dir(chart_id)
    versions = {v["build_id"]: v for v in profile_versions.list_versions(d)}
    for bid in (body.a, body.b):
        if bid not in versions:
            raise HTTPException(404, detail={"error": "unknown_build", "message": f"unknown build: {bid}"})
        if not versions[bid]["intact"]:
            raise HTTPException(409, detail={"error": "altered_build", "message": f"version {bid} is altered"})
    ma, mb = (profile_versions.read_version_manifest(d, x) for x in (body.a, body.b))
    ea, eb = _excluded_points(chart_id, ma), _excluded_points(chart_id, mb)
    focus = ([{"rgb": rgb, "label": f"{k[0]}/{k[1]}/{k[2]}", "excluded_in": "b"} for k, rgb in eb.items() if k not in ea]
             + [{"rgb": rgb, "label": f"{k[0]}/{k[1]}/{k[2]}", "excluded_in": "a"} for k, rgb in ea.items() if k not in eb])
    ia, ib = (profile_versions.builds_dir(d) / x / profile_versions.VERSION_ICC for x in (body.a, body.b))
    try:
        res = profile_diff.compare_profiles(ia, ib, focus=focus, grid_n=body.grid_n, top_n=body.top_n)
    except ArgyllNotFound as e:
        raise HTTPException(503, detail={"error": "argyll_missing", "message": str(e)})
    except RuntimeError as e:
        raise HTTPException(422, detail={"error": "lookup_failed", "message": str(e)})
    for f, src in zip(res["focus"], focus):
        f["excluded_in"] = src["excluded_in"]
    return {"chart_id": chart_id, "a": {"build_id": body.a, "label": (ma.get("result") or {}).get("label"),
                                         "kind": ma.get("kind"), **profile_diff.file_identity(ia)},
            "b": {"build_id": body.b, "label": (mb.get("result") or {}).get("label"), "kind": mb.get("kind"),
                  **profile_diff.file_identity(ib)},
            "identical_files": profile_diff.file_identity(ia)["sha256"] == profile_diff.file_identity(ib)["sha256"],
            **res}


@router.get("/{chart_id}/profile/status")
def profile_status(chart_id: str) -> dict:
    """State of the CURRENT/last build for THIS chart (background job) → re-attaches the front
    after reload. {state, phase, result|error}. idle if no build for this chart."""
    from webapp.backend.services import chart_profile_job
    st = chart_profile_job.status()
    if st and st.get("chart_id") == chart_id:
        return st
    return {"state": "idle", "chart_id": chart_id, "phase": None, "result": None, "error": None}


def _build_policy(body) -> str:
    """Rejection policy of a build: "legacy" unless "apply_all" is EXPLICITLY requested."""
    p = (getattr(body, "policy", None) or "legacy").strip().lower()
    if p not in ("legacy", "apply_all"):
        raise ValueError(f"unknown build policy: {p!r}")
    return p


def _prepare_build(chart_id: str, body: "ProfileChartBody"):
    """Shared by the build, the preview and the profile status: frozen snapshot, paper/GE
    guards of the additional sources, resolved flags, and the PLAN (build_inputs.plan_inputs).
    No side effect: reads only (no repair, no build directory, no ti3 written).
    Raises ValueError (or HTTPException for an unknown chart) like the build did."""
    import json
    import shlex
    from datetime import date
    d = _chart_dir(chart_id)
    # FROZEN snapshot (read ONCE): the same state decides the inputs AND writes the trace —
    # a role/rejection changed during colprof never pretends to have been used.
    _state_for_build = _read_scan_state(d)
    snap = _chart_snapshot(chart_id, d, _state_for_build)
    if not snap.included():
        raise ValueError("no included scan")
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
    strategy = _build_strategy_info(chart_id, desc, body)
    if strategy and strategy["role"] == _ps.ROLE_PRECONDITIONING:
        # never ranged, never installed: a plain description says so (ASCII, < 64 chars)
        custom_name, label = None, f"Intermediate {chart_id} (targen -c only)"
    flags = shlex.split(body.colprof_flags) if body.colprof_flags else _chart_default_colprof_flags()
    # Resolve -s/-S source-gamut ALIASES (e.g. "AdobeRGB") to absolute bundled ICC
    # paths (assets/), exactly like the CLI. Percentage forms (-s 90) are left as-is.
    from lib.z9_client.config import resolve_gamut_aliases_in_flags
    try:
        flags = resolve_gamut_aliases_in_flags(flags)
    except FileNotFoundError as e:
        raise ValueError(f"invalid colprof source gamut (-s/-S): {e}") from e
    out_icc = d / f"{chart_id}.icc"                       # current profile in the chart folder
    serial = _resolve_serial_for_ranging(desc)
    media_id = paper.get("media_id")

    # ── ADDITIONAL sources (paper+GE guard, authoritative server-side identity) ──
    # (b) other charts: their included scans with THEIR rejections ; (c) existing profiles
    # (measurements extracted from CIED/targ/CxF). Any exception → ValueError (no HTTPException
    # outside request context).
    ref_ge = _norm_ge(paper.get("gloss_enhancer"))
    extra_snaps = []
    for xcid in body.extra_chart_ids:
        try:
            xm, xge = _chart_paper_ge(xcid)
        except HTTPException:
            raise ValueError(f"source chart not found/invalid: {xcid}")
        _assert_source_compatible(media_id, ref_ge, xm, xge, f"chart {xcid}")
        xd = _chart_dir(xcid)
        extra_snaps.append(_chart_snapshot(xcid, xd, _read_scan_state(xd)))
    profile_paths = []
    for sp in body.source_profiles:
        sm, sge = _profile_identity(sp.path)
        _assert_source_compatible(media_id, ref_ge, sm, sge, f"profile {Path(sp.path).name}")
        profile_paths.append(sp.path)

    # ── ONE preparation for every mode (lib/z9_client/build_inputs.py) ──
    # Policy "legacy" = historical behaviour, unchanged: base "last" ignores the current chart's
    # rejections (now TRACED as not applied), "average" applies them, additional charts always
    # apply theirs, source profiles are used as extracted. Every write happens in a private
    # `.build/<build_id>/` directory (never in measurements/, never in another chart).
    from lib.z9_client import build_inputs
    plan = build_inputs.plan_inputs(snap, extra_snaps, build_inputs.BuildRequest(
        base=body.profile_base, source_profiles=tuple(profile_paths), colprof_flags=tuple(flags),
        policy=_build_policy(body)))
    # FWA compensation on a strategy chart: only on measurements ArgyllCMS can compensate
    # (spectra, an instrument with a UV lamp, M0); unknown conditions are never presumed fit.
    fwa = None
    if strategy:
        fwa = _ps.fwa_gate([_scan_identity(s.path) for s in plan.scans] + [None] * len(profile_paths))
        if _ps.flags_request_fwa(flags) and not fwa["allowed"]:
            raise ValueError(f"FWA compensation (-f) refused for these measurements: {fwa['reason']}")
    return SimpleNamespace(d=d, snap=snap, state=_state_for_build, desc=desc, paper=paper,
                           paper_name=paper_name, custom_name=custom_name, label=label, flags=flags,
                           out_icc=out_icc, serial=serial, media_id=media_id, extra_snaps=extra_snaps,
                           profile_paths=profile_paths, plan=plan, strategy=strategy, fwa=fwa)


def _qc_findings_cached(plan) -> dict:
    """{(chart, scan): [finding codes]} from the CACHED measurement-QC summaries only."""
    from webapp.backend.services import measure_qc as qc_service
    out = {}
    for s in plan.scans:
        summ = qc_service.cached_summary(s.path)
        if summ:
            out[(s.chart_id, s.scan)] = list(summ.get("findings") or [])
    return out


def _coverage_for(ctx) -> dict:
    """Coverage of the effective set (build_inputs.analyze_coverage). Policy legacy: informative
    and best-effort (never changes a legacy build); apply_all: errors propagate."""
    from lib.z9_client import build_inputs
    try:
        return build_inputs.analyze_coverage(ctx.plan, _qc_findings_cached(ctx.plan))
    except Exception as e:                                         # noqa: BLE001
        if ctx.plan.policy == "apply_all":
            raise
        logger.warning("coverage not computed (legacy build): %s", e)
        return {"error": str(e), "refusals": [], "guards": []}


def _build_profile_now(chart_id: str, body: ProfileChartBody, set_phase) -> dict:
    """Builds the ICC (concat included−rejections → colprof → disk storage). Run by the
    chart_profile_job worker (background thread, or inline in test). Raises SIMPLE exceptions
    (ValueError/RuntimeError), NEVER HTTPException (outside request context). set_phase(str)
    reports progress (preparing → building). NO Z9 : colprof + disk copy."""
    import hashlib
    import json
    import os
    import shlex
    from datetime import date
    from lib.z9_client.profiling import ProfilingOps

    set_phase("preparing")
    from lib.z9_client import build_inputs, profile_versions
    ctx = _prepare_build(chart_id, body)
    d, snap, _state_for_build, desc, paper = ctx.d, ctx.snap, ctx.state, ctx.desc, ctx.paper
    paper_name, custom_name, label, flags = ctx.paper_name, ctx.custom_name, ctx.label, ctx.flags
    out_icc, serial, media_id, plan = ctx.out_icc, ctx.serial, ctx.media_id, ctx.plan
    origin = "free_chart_argyll"
    # The inputs shown in a preview are the ones built, or nothing is built.
    if body.expected_fingerprint and plan.fingerprint != body.expected_fingerprint:
        raise ValueError(f"inputs changed since the preview (expected {body.expected_fingerprint}, "
                         f"now {plan.fingerprint}): preview again")
    coverage = _coverage_for(ctx)
    if plan.policy == "apply_all" and coverage.get("refusals"):
        raise ValueError("build refused: " + ", ".join(coverage["refusals"]))
    averaged = plan.base_effective == "average"
    build_inputs.sweep_stale_build_dirs(d)                 # leftovers of an interrupted build only
    # Recovery of an interrupted publication + preservation of a pre-versions profile (safe
    # operations only: never modifies or deletes a version).
    legacy_trace = {"profile_built_from": _state_for_build.get("profile_built_from"),
                    "profile_built_rejections": _state_for_build.get("profile_built_rejections")}
    for _action in profile_versions.repair(d, chart_id, legacy_trace=legacy_trace):
        logger.info("profile publication repair (%s): %s", chart_id, _action)
    build_id, workdir = build_inputs.create_build_dir(d)
    try:
        eff = build_inputs.materialize(plan, workdir)
        set_phase("building")          # colprof — ~2.5 min on ~1400 measurements (the job holds the duration)
        tmp_icc = workdir / f"{chart_id}.icc"
        try:
            result = ProfilingOps().build_profile(
                eff.profile_ti3.with_suffix(""), descriptor=label, output_icc_path=tmp_icc,
                colprof_flags=flags)
        except (FileNotFoundError, RuntimeError) as e:
            raise RuntimeError(f"colprof failed: {e}")
        # n_patches = REAL count of the ti3 that fed colprof (header NUMBER_OF_SETS, as before).
        n_patches = _ti3_n_sets(eff.profile_ti3) or (desc.get("geometry") or {}).get("patch_count")
        icc_size = result.output_icc_size_bytes
        manifest = build_inputs.build_manifest(
            plan, eff, build_id=build_id, request=_build_request_record(body, flags, plan.policy, ctx.strategy),
            coverage=coverage,
            snapshot={"included": list(snap.included()),
                      "rejected": {k: list(v) for k, v in snap.rejected.items()}},
            software=build_inputs.software_versions(),
            result={"icc": out_icc.name, "icc_sha256": hashlib.sha256(tmp_icc.read_bytes()).hexdigest(),
                    "icc_size_bytes": icc_size, "n_patches": n_patches, "label": label})
        # Publication (lib/z9_client/profile_versions.py): a current profile missing from the
        # history is preserved first, then version (atomic directory rename) → current profile
        # (os.replace) → pointer. The previous profile is never lost; colprof failure = nothing
        # published; an interruption between steps is detected and repaired at the next build.
        profile_versions.preserve_current(d, chart_id, legacy_trace)
        publication = profile_versions.publish(d, chart_id, build_id, tmp_icc, manifest)
    finally:
        build_inputs.remove_build_dir(workdir)            # success, failure or exception

    # Trace (legacy keys, still read by `stale`): written FROM THE SNAPSHOT. Rejections only
    # bite the "average" base in the legacy policy → traced in that case only. Best-effort ;
    # the CURRENT state is re-read only to preserve the user's concurrent edits (roles…).
    try:
        _st = _read_scan_state(d)
        _st["profile_built_from"] = sorted(snap.included())
        _st["profile_built_rejections"] = ({k: sorted(v) for k, v in snap.rejected.items() if v}
                                           if averaged else {})
        _st["last_build_id"] = build_id
        _write_scan_state(d, _st)
    except OSError:
        logger.warning("profile_built_from trace not written (%s)", chart_id)

    # Per-paper storage (COPY — the original stays in the chart folder). Best-effort :
    # never breaks profiling if the serial is unknown or the copy fails.
    ranged_path = None
    intermediate = bool(ctx.strategy) and ctx.strategy["role"] == _ps.ROLE_PRECONDITIONING
    if serial and media_id and not intermediate:     # an intermediate profile is never ranged
        try:
            cm = desc.get("color_management") or {}
            # n_patches = REAL count of the ti3 that fed colprof (computed above, before the
            # build directory was removed) — not the chart geometry.
            rp, _ = _cache.save_repo_z9_profile(
                icc_bytes=out_icc.read_bytes(),
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
                # exact name. replace → full overwrite (icc + fresh sidecar, no tag/note carried
                # over — see cache.save_repo_z9_profile) ; keep_both → -N suffix.
                on_conflict=((body.on_conflict or "").strip().lower() or None),
                # provenance of the stored copy: the build and its manifest (kept with the copy,
                # which survives the deletion of the chart)
                extra_meta={"build_id": build_id, "chart_id": chart_id,
                            "icc_sha256": manifest["result"]["icc_sha256"],
                            "manifest_sha256": publication["manifest_sha256"], "build": manifest})
            ranged_path = str(rp)
            profile_versions.add_reference(d, build_id, {"kind": "repo_z9", "path": ranged_path,
                                                         "filename": Path(ranged_path).name})
        except (OSError, ValueError):
            logger.exception("repo z9 storage failed (non-blocking) — chart %s", chart_id)

    for _w in publication["warnings"]:
        logger.warning("profile publication (%s): %s", chart_id, _w)
    return {"ok": True, "chart_id": chart_id, "icc": out_icc.name, "build_id": build_id,
            "publication_warnings": publication["warnings"],
            "icc_size_bytes": icc_size,
            "ranged_icc_path": ranged_path,             # None if serial unknown (backward-compat)
            # repo/z9 coordinates of the stored profile → enables "Install" on the front side
            "ranged": ({"serial": serial, "media_id": media_id,
                        "filename": Path(ranged_path).name} if ranged_path else None),
            "paper_name": paper_name,
            "averaged": averaged,                       # base = average of the stable scans (§4d)
            "n_scans_averaged": len(plan.current) if averaged else None,
            "installable": ranged_path is not None,     # → installable from Papers/Profiles
            "strategy": ctx.strategy}                   # role (preconditioning | final) + variant


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
    # Validating with another instrument (or condition) than the build is ALLOWED: it is
    # not an average. Both identities are reported, with a warning when they differ (the
    # result then also contains the gap between instruments).
    try:
        report["measurement_identity"] = _validation_identity_report(prof, ti3)
    except (OSError, ValueError) as e:
        logger.warning("validation identity report failed (%s): %s", chart_id, e)
        report["measurement_identity"] = {"error": str(e)}
    return report


def _profile_build_identities(prof: Path) -> dict:
    """Identities of the scans a profile was built from, read from its build manifest
    (the repo copy's sidecar ``build``, or the pointer of the chart folder when the file is
    the chart's current profile). ``recorded`` in the manifest; ``reconstructed`` from the
    raw scan when it is unchanged (same sha256, manifests written before identities were
    recorded); ``unknown`` otherwise. Source profiles are not tracked."""
    from lib.z9_client import profile_versions
    manifest, source = None, None
    meta = _cache.read_repo_z9_profile_meta(prof) or {}
    if isinstance(meta.get("build"), dict):
        manifest, source = meta["build"], "repo_z9_sidecar"
    elif prof.stem == prof.parent.name and (prof.parent / "chart.json").is_file():
        ptr = profile_versions.read_pointer(prof.parent) or {}
        if (ptr.get("pointer") or {}).get("icc_sha256") == hashlib_sha256(prof.read_bytes()):
            manifest, source = ptr, "chart_profile_pointer"
    if not manifest:
        return {"status": "unknown", "manifest": None, "build_id": None, "scans": [],
                "source_profiles_not_tracked": 0}
    plan = manifest.get("plan") or {}
    scans = []
    for x in (plan.get("current_scans") or []) + (plan.get("extra_scans") or []):
        ident, how = x.get("measurement_identity"), "recorded"
        if not ident:
            ident, how = None, "unknown"
            name, cid = str(x.get("scan") or ""), str(x.get("chart_id") or "")
            cd = _cache.locate_chart_dir(cid) if cid else None
            p = cd / "measurements" / name if (cd and name and Path(name).name == name) else None
            if p is not None and p.is_file() and hashlib_sha256(p.read_bytes()) == x.get("sha256"):
                ident = _mi.describe_identity(p)
                how = "unknown" if "error" in ident else "reconstructed"
        scans.append({"chart_id": x.get("chart_id"), "scan": x.get("scan"), "role": x.get("role"),
                      "identity": ident, "identity_source": how})
    how_all = {sc["identity_source"] for sc in scans}
    status = ("unknown" if not scans or "unknown" in how_all else
              "reconstructed" if "reconstructed" in how_all else "recorded")
    return {"status": status, "manifest": source, "build_id": manifest.get("build_id"), "scans": scans,
            "source_profiles_not_tracked": len(plan.get("source_profiles") or [])}


def _validation_identity_report(prof: Path, ti3: Path) -> dict:
    """Identity of the validation scan vs the identities the profile was built from:
    ``same`` | ``differs`` (another instrument, condition…: warning, never a refusal) |
    ``unverified`` (an identity is unknown on either side)."""
    build = _profile_build_identities(prof)
    validation = _mi.describe_identity(ti3)
    try:
        v_ident = _mi.read_identity(ti3)
    except (OSError, ValueError):
        v_ident = None
    differences, unverified = [], not build["scans"] or v_ident is None
    for sc in build["scans"]:
        ident = sc["identity"]
        try:
            b_ident = _mi.identity_from_dict(ident) if ident and "error" not in ident else None
        except ValueError:
            b_ident = None
        if b_ident is None or v_ident is None:
            unverified = True
            continue
        c = _mi.compare_identities(b_ident, v_ident)
        if c.status == _mi.INCOMPATIBLE:
            differences.append({"chart_id": sc["chart_id"], "scan": sc["scan"],
                                "differences": [d.to_dict() for d in c.differences]})
        elif c.status == _mi.INSUFFICIENT:
            unverified = True
    warnings = []
    if differences:
        status = "differs"
        warnings.append({"code": "validation_identity_differs",
                         "message": "the validation scan was not measured like the build's scans: "
                                    "the result also contains the gap between them"})
    elif unverified:
        status = "unverified"
        warnings.append({"code": "validation_identity_unverified",
                         "message": "the measurement identity of the build or of the validation "
                                    "scan is not known: they may differ"})
    else:
        status = "same"
    if build["source_profiles_not_tracked"]:
        warnings.append({"code": "build_source_profiles_not_tracked",
                         "message": "the profile also used measurements extracted from other "
                                    "profiles, whose instrument is not known"})
    return {"validation": {"scan": ti3.name, "identity": validation}, "build": build,
            "comparison": {"status": status, "differences": differences}, "warnings": warnings}


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
    # Reference identity for the measurement check: the current chart's last included scan
    # (used by both bases). Informative here — the build plan is the authority.
    try:
        _cur_incl = _included_ti3_paths(_chart_dir(chart_id))
        ref_ident = _mi.read_identity(_cur_incl[-1]) if _cur_incl else None
    except (OSError, ValueError):
        ref_ident = None

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
        try:
            compat = (_mi.check_combination([("current", ref_ident)] + [
                (f"{cid}/{p.name}", _mi.read_identity(p)) for p in incl]).status
                if ref_ident is not None else None)
        except (OSError, ValueError):
            compat = "record_invalid"
        extra_charts.append({
            # would its included scans combine with this chart's? (compatible | incompatible |
            # insufficient | record_invalid | None = not computable)
            "measurement_compatibility": compat,
            "layout": _mi.chart_layout(c), "instrument": c.get("instrument"),
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
    from webapp.backend.services import external_measure
    d = _chart_dir(chart_id)
    if external_measure.get_service().is_running(chart_id):
        raise HTTPException(409, detail={
            "code": "external_measure_running",
            "message": "A measurement of this chart is running: pause it before deleting the chart."})
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
    from webapp.backend.services import measure_qc as qc_service

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
        # how this chart is measured (layout absent = honeycomb → built-in scan)
        "layout": _mi.chart_layout(desc),
        "instrument": (desc.get("instrument") or {}).get("key"),
        "measurement": _mi.expected_measurement(desc),
        # profiling strategy pass (None for a one-pass chart), the intermediate profile a later
        # pass was made from, and the print parameters actually used
        "strategy": desc.get("strategy"), "precondition": desc.get("precondition"),
        "print_params": desc.get("print_params"),
        # a one-pass chart made from the guided form: its parameters and the colprof line the
        # profile step starts from (None for a free line or a .ti1)
        "guided": desc.get("guided"),
    }
    # External measurement (chartread-fgK): session and live run, as the snapshot shows them
    external = None
    if identity["layout"] == _mi.LAYOUT_PRINTTARG:
        from webapp.backend.services import external_measure
        try:
            snap = external_measure.get_service().snapshot(chart_id)
            external = {k: snap.get(k) for k in ("state", "running", "session", "publishable", "seq")}
        except external_measure.ExternalMeasureError:
            external = None
    identity["external_measure"] = external

    # Scans (A3) : RAW ti3 of measurements/ (derivatives _avg/_qcfilt/_multisource excluded). The ROLE (included/excluded)
    # comes from the durable SIDECAR. scanned_at = TIMESTAMP from the filename (the ti3 IS
    # timestamped) ; delay_seconds = print→scan delay (the FACT that says "too fresh" without judgment).
    sess_scans: dict = {}
    for s in scan_session.list_sessions():
        if s.get("chart_id") == chart_id:
            for m in (s.get("scans") or []):
                sess_scans[m.get("ti3")] = m
    state = _read_scan_state(d)
    # print times (a chart printed again keeps the previous ones in print_history): a scan's
    # delay counts from the last print before it, never from a later reprint
    _print_dts = []
    for _pa in [*((h or {}).get("printed_at") for h in (desc.get("print_history") or [])), desc.get("printed_at")]:
        try:
            _print_dts.append(datetime.fromisoformat(_pa))
        except (TypeError, ValueError):
            pass

    def _printed_before(sa):
        before = [t for t in _print_dts if t <= sa]
        return max(before) if before else (min(_print_dts) if _print_dts else None)
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
            "delay_seconds": (int((sa - _printed_before(sa)).total_seconds())
                              if (sa and _print_dts) else None),  # None if delay unknown
            "rejected_readings": list(rej_map.get(p.name) or []),  # rejected SAMPLE_ID
            "size_bytes": p.stat().st_size,
            # intrinsic QC summary ONLY if a valid cached report exists (never computed here)
            "qc_summary": qc_service.cached_summary(p),
            # measurement identity (historical SOL scans: from the ti3 header, no record)
            "measurement_identity": _mi.describe_identity(p),
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
            # phase 4C semantics (not displayed yet): replan of the same request → stale /
            # missing_input / toolchain_changed / unknown (no reliable manifest)
            "status": _profile_status(chart_id, d),
        }

    return {"identity": identity, "scans": scans, "profile": profile,
            "included_scans_compatibility": _included_compatibility(d, state)}


def _included_compatibility(d: Path, state: dict) -> Optional[dict]:
    """Could the INCLUDED scans be averaged together? None below two included scans;
    {"status": "record_invalid", …} when a record cannot be read."""
    paths = [p for p in _raw_ti3_paths(d) if _scan_role(state, p.name) == "included"]
    if len(paths) < 2:
        return None
    members = []
    for p in paths:
        try:
            members.append((p.name, _mi.read_identity(p)))
        except _mi.MeasurementRecordError as e:
            return {"status": "record_invalid", "scan": p.name, "code": e.code, "message": e.message}
        except (OSError, ValueError) as e:
            return {"status": "unreadable", "scan": p.name, "message": str(e)}
    return _mi.check_combination(members).to_dict()
