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

"""Profiling strategies (read-only routes): definitions, composed lines, runs in progress.

The work itself goes through the chart routes: a pass is a chart (POST /api/charts with a
``strategy`` block), its profile a build (POST /api/charts/{id}/profile, role decided by the
chart). Here nothing is written: a run's state is DERIVED from its charts (``strategy.run``
= chart id of the first pass) every time it is asked for.

Routes:
  GET  /api/profiling-strategies               definitions, default lines, choices
  POST /api/profiling-strategies/compose       parameters → bounded values + lines of every pass
  GET  /api/profiling-strategies/runs          runs (filter: paper, ge), with their next step
  GET  /api/profiling-strategies/runs/{run}    one run, with what its next pass needs
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from lib.z9_client import cache as _cache
from lib.z9_client import profiling_strategies as _ps
from lib.z9_client import profile_versions
from lib.z9_client import sol_chart as _sc

router = APIRouter(prefix="/api/profiling-strategies", tags=["profiling-strategies"])


def _charts():
    from webapp.backend.routes import charts       # lazy: charts imports nothing from here
    return charts


@router.get("")
def list_profiling_strategies() -> dict:
    """The built-in strategies with their default parameters and composed lines, and the
    choices the advanced settings offer (bounded the same way by the server)."""
    from lib.z9_client.config import _SOURCE_GAMUT_ALIASES
    out = []
    for sdef in _ps.load_strategies().values():
        params = _ps.normalize_params(sdef)
        out.append({**sdef.to_dict(), "passes_composed": _ps.compose_all(sdef, params)})
    return {"strategies": out,
            "choices": {
                "qualities": list(_ps.QUALITIES), "ofps": list(_ps.OFPS_CHOICES),
                "viewing_conditions": [{"code": k, "description": v} for k, v in _ps.VIEWING_CONDITIONS.items()],
                "source_gamuts": [{"alias": a, "file": f} for a, f in _SOURCE_GAMUT_ALIASES.items()]
                + [{"alias": _ps.SOURCE_GAMUT_NONE, "file": None}],
                "bounds": {"chart": {k: v[1] for k, v in _ps.CHART_PARAMS.items() if v[0] in ("int", "float")},
                           "profile": {k: v[1] for k, v in _ps.PROFILE_PARAMS.items() if v[0] in ("int", "float")}}}}


class ComposeBody(BaseModel):
    strategy: str
    params: dict = {}
    run: Optional[str] = None       # a later pass: start from the run's frozen parameters


@router.post("/compose")
def compose_strategy(body: ComposeBody) -> dict:
    """What the server WILL run for these parameters (no file, no binary): the bounded
    parameters and the targen/colprof lines of every pass. 422 with a stable code otherwise."""
    try:
        sdef = _ps.get_strategy(body.strategy)
        base = {}
        if body.run:
            c = _charts()
            fdesc = c._read_chart_json(c._chart_dir(body.run))
            fst = fdesc.get("strategy") or {}
            if fst.get("id") != sdef.id:
                raise HTTPException(409, detail={"code": "strategy_run_mismatch",
                                                 "message": f"{body.run} is not a {sdef.id} run"})
            base = fst.get("params") or {}
        params = _ps.normalize_params(sdef, _charts()._merge_params(base, body.params))
    except _ps.StrategyError as e:
        raise HTTPException(422, detail=e.to_detail())
    return {"strategy": sdef.id, "params": params, "passes": _ps.compose_all(sdef, params)}


# ── runs (derived from the charts) ───────────────────────────────────────────
def _builds(cd: Path, current_id: Optional[str]) -> list:
    out = []
    for v in profile_versions.list_versions(cd):
        e = {"build_id": v["build_id"], "created_at": v.get("created_at"), "intact": v["intact"],
             "current": v["build_id"] == current_id, "role": None, "variant": None, "label": None,
             "colprof_flags": None, "installable": False}
        try:
            m = profile_versions.read_version_manifest(cd, v["build_id"])
        except (OSError, ValueError):
            out.append(e)
            continue
        st = (m.get("request") or {}).get("strategy") or {}
        e.update(role=st.get("role"), variant=st.get("variant"),
                 label=(m.get("result") or {}).get("label"),
                 colprof_flags=(m.get("plan") or {}).get("colprof_flags"))
        try:
            import json
            refs = json.loads((profile_versions.builds_dir(cd) / v["build_id"] / profile_versions.REFERENCES_NAME)
                              .read_text(encoding="utf-8")).get("references", [])
            e["installable"] = any(r.get("kind") == "repo_z9" for r in refs)
            e["ranged"] = next(({"filename": r.get("filename"), "path": r.get("path")}
                                for r in refs if r.get("kind") == "repo_z9"), None)
        except (OSError, ValueError):
            pass
        out.append(e)
    return out


def _chart_facts(cid: str) -> Optional[dict]:
    c = _charts()
    cd = _cache.locate_chart_dir(cid)
    if cd is None:
        return None
    try:
        desc = c._read_chart_json(cd)
    except (OSError, ValueError):
        return None
    st = desc.get("strategy") or {}
    geom = desc.get("geometry") or {}
    status = c._profile_status(cid, cd)
    builds = _builds(cd, status.get("build_id"))
    included = c._included_ti3_paths(cd)
    facts = {
        "chart_id": cid, "strategy_id": st.get("id"), "pass": st.get("pass"), "role": st.get("role"),
        "run": st.get("run"),
        "created_at": desc.get("created_at"), "printed_at": desc.get("printed_at"),
        "printed": bool(desc.get("printed_at")), "lightened": bool(desc.get("lightened")),
        "n_scans": len(c._raw_ti3_paths(cd)), "n_included": len(included),
        "layout": desc.get("layout") or "honeycomb", "instrument": (desc.get("instrument") or {}).get("key"),
        "format": (desc.get("media") or {}).get("format_key"), "patch_count": geom.get("patch_count"),
        "roll_length_mm": geom.get("roll_length_mm"),
        "patch_scale": (desc.get("printtarg") or {}).get("patch_scale"),
        "high_density": bool((desc.get("printtarg") or {}).get("high_density")),
        "lines": st.get("lines"), "params": st.get("params"), "inherited": st.get("inherited"),
        "print_params": desc.get("print_params"), "precondition": desc.get("precondition"),
        "paper": desc.get("paper") or {}, "targen_argv": (desc.get("targen") or {}).get("argv"),
        "profile_status": {k: status.get(k) for k in ("state", "build_id")},
        "builds": builds,
    }
    current = next((b for b in builds if b["current"]), None)
    if st.get("role") == _ps.ROLE_PRECONDITIONING:
        built = current is not None and current["role"] == _ps.ROLE_PRECONDITIONING
        facts["intermediate"] = {"built": built, "build_id": current["build_id"] if built else None,
                                 "created_at": current["created_at"] if built else None,
                                 "colprof_flags": current["colprof_flags"] if built else None,
                                 "state": status.get("state") if built else None}
    else:
        facts["final_built"] = any(b["role"] == _ps.ROLE_FINAL and b["intact"] for b in builds)
    facts["_identities"] = [c._scan_identity(p) for p in included]
    return facts


def _public(f: Optional[dict]) -> Optional[dict]:
    return None if f is None else {k: v for k, v in f.items() if not k.startswith("_")}


def _run_summary(run: str, members: list) -> dict:
    """One run from its charts' facts (first pass may be deleted: later passes stay usable)."""
    facts = [f for f in (_chart_facts(cid) for cid in members) if f is not None]
    first = next((f for f in facts if f["chart_id"] == run), None)
    later = sorted((f for f in facts if f["chart_id"] != run), key=lambda f: f.get("created_at") or "")
    ref = first or (later[-1] if later else None)
    sid = (ref or {}).get("strategy_id")
    try:
        sdef = _ps.get_strategy(sid or "")
        meta = {"id": sdef.id, "version": sdef.version, "validated": sdef.validated}
    except _ps.StrategyError:
        meta = {"id": sid, "version": None, "validated": False}
    if first is not None:
        step = _ps.next_step(first, later)
    else:
        step = "first_pass_missing" if not later else _ps.next_step({"printed": True, "n_included": 1}, later)
    fwa = {}
    if later:
        last = later[-1]
        fwa["pass2"] = _ps.fwa_gate(last["_identities"])
        if first is not None:
            fwa["pass1_pass2"] = _ps.fwa_gate(first["_identities"] + last["_identities"])
    paper = (ref or {}).get("paper") or {}
    return {"run": run, "strategy": meta, "next_step": step,
            "paper": {"media_id": paper.get("media_id"), "name": paper.get("name"),
                      "gloss_enhancer": _charts()._norm_ge(paper.get("gloss_enhancer"))},
            "first": _public(first), "later": [_public(f) for f in later],
            "first_missing": first is None,
            "variant_available": first is not None and first["n_included"] > 0,
            "fwa": fwa,
            "updated_at": max([x for f in facts for x in (f.get("created_at"), f.get("printed_at")) if x] or [None],
                              key=lambda x: x or "")}


def _group_runs(paper: Optional[str] = None) -> dict:
    runs: dict[str, list] = {}
    for c in _sc.list_charts():
        st = c.get("strategy") or {}
        if not st.get("run"):
            continue
        if paper and c.get("paper_media_id") != paper:
            continue
        runs.setdefault(st["run"], []).append(c["chart_id"])
    return runs


@router.get("/runs")
def list_runs(paper: Optional[str] = Query(None, description="paper MediaId filter"),
              ge: Optional[str] = Query(None, description="OFF | FULLPAGE")) -> dict:
    """Runs of the store (most recent first) with their next step: what lets the user resume
    after closing freeglaz. Derived from the charts every time."""
    out = []
    for run, members in _group_runs(paper).items():
        s = _run_summary(run, members)
        if ge and s["paper"]["gloss_enhancer"] != _charts()._norm_ge(ge):
            continue
        out.append(s)
    out.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
    return {"runs": out}


@router.get("/runs/{run}")
def get_run(run: str) -> dict:
    """One run, with what its next pass needs: the measurement inherited from the first pass,
    the frozen parameters and their lines."""
    c = _charts()
    import re
    if not re.fullmatch(r"CHT-[0-9]{8}-[0-9]{4}-[0-9A-Z]{2}", run):
        raise HTTPException(422, detail="invalid run id")
    members = _group_runs().get(run)
    if not members:
        raise HTTPException(404, detail={"code": "unknown_run", "message": f"no profiling run {run}"})
    s = _run_summary(run, members)
    first_dir = _cache.locate_chart_dir(run)
    s["measurement_lock"] = c._measurement_lock(first_dir) if first_dir else None
    try:
        sdef = _ps.get_strategy(s["strategy"]["id"])
        params = (s["first"] or {}).get("params") or (s["later"][-1] if s["later"] else {}).get("params") or {}
        s["passes_composed"] = _ps.compose_all(sdef, _ps.normalize_params(sdef, params))
    except (_ps.StrategyError, KeyError):
        s["passes_composed"] = None
    return s
