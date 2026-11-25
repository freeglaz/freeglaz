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

"""Profiling strategies: how a printer profile is made, in passes, with the existing tools.

Definitions come from ``data/profiling_strategies_builtin.toml`` (versioned with the code,
like the colprof presets of ``strategies.py`` — a different thing: those are colprof flag
lines). This module is pure: it loads and checks the definitions, bounds the user's
parameters, composes the targen and colprof lines of a pass, says whether FWA compensation
may be used for a set of measurements, and derives the next step of a run from facts the
caller reads. It runs nothing and writes nothing.

A run is identified by the chart id of its first pass and lives in the charts' own
``chart.json`` (``strategy`` block); there is no registry. The intermediate profile of a
preconditioning pass is a version of that chart (``builds/<id>/``) that is never ranged in
``repo/z9``, hence never installable: it only becomes targen's ``-c`` of a later pass.

What is shipped: ``two_pass_adaptive``. Other strategies (predefined RGB charts, other
generation presets, an existing ICC as preconditioner, usage-specific settings) are new
entries of the same file, using the same pass model.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

try:
    import tomllib
except ModuleNotFoundError:                      # py<3.11
    import tomli as tomllib                       # type: ignore

BUILTIN_TOML = Path(__file__).parent / "data" / "profiling_strategies_builtin.toml"

ROLE_PRECONDITIONING = "preconditioning"
ROLE_FINAL = "final"
ROLES = (ROLE_PRECONDITIONING, ROLE_FINAL)

# ArgyllCMS 3.5.0 viewing conditions (xicc/xicc.c, xicc_enum_viewcond): code → description.
VIEWING_CONDITIONS = {
    "d": "Default Viewing Condition",
    "pc": "Critical print evaluation environment (ISO-3664 P1)",
    "pp": "Practical Reflection Print (ISO-3664 P2)",
    "pe": "Print evaluation environment (CIE 116-1995)",
    "pm": "Print evaluation with partial Mid-tone adaptation",
    "mb": "Bright monitor in bright work environment",
    "mt": "Monitor in typical work environment",
    "md": "Monitor in darkened work environment",
    "jm": "Projector in dim environment",
    "jd": "Projector in dark environment",
    "tv": "Television/Film Studio",
    "pcd": "Photo CD - original scene outdoors",
    "ob": "Original scene - Bright Outdoors",
    "cx": "Cut Sheet Transparencies on a viewing box",
}
QUALITIES = ("l", "m", "h", "u")
OFPS_CHOICES = ("G", "Q")           # -G: OFPS "good"; -Q: perceptual quasi-random (fallback)
SOURCE_GAMUT_NONE = "none"


def _source_gamut_aliases() -> tuple[str, ...]:
    from .config import _SOURCE_GAMUT_ALIASES
    return tuple(_SOURCE_GAMUT_ALIASES)


# name → (kind, bounds|choices). The bounds keep a composed line within what targen and
# colprof accept; the capacity of a chart (sheet, roll) is checked by the chart route.
CHART_PARAMS = {
    "patches": ("int", (50, 5000)),
    "ofps": ("choice", OFPS_CHOICES),
    "grey_steps": ("int", (0, 100)),          # targen -g: RGB grey axis steps
    "neutral_emphasis": ("float", (0.0, 1.0)),  # targen -N: needs a preconditioning profile
    "neutral_steps": ("int", (0, 100)),       # targen -n: neutral steps, from the profile
}
PROFILE_PARAMS = {
    "quality": ("choice", QUALITIES),         # colprof -q
    "smoothing": ("float", (0.05, 5.0)),      # colprof -r: assumed average deviation, %
    "b2a": ("choice", QUALITIES),             # colprof -b
    "source_gamut": ("gamut", None),          # colprof -S <alias> (bundled ICC), or none
    "viewcond_in": ("choice", tuple(VIEWING_CONDITIONS)),   # colprof -c
    "viewcond_out": ("choice", tuple(VIEWING_CONDITIONS)),  # colprof -d
    "fwa": ("bool", None),                    # colprof -f, only when fwa_gate allows it
}
_NEEDS_PRECONDITION = ("neutral_emphasis", "neutral_steps")


class StrategyError(ValueError):
    """A definition or a parameter is refused. ``code`` is stable (the API returns it)."""

    def __init__(self, code: str, message: str, **info):
        super().__init__(message)
        self.code = code
        self.message = message
        self.info = info

    def to_detail(self) -> dict:
        return {"code": self.code, "message": self.message, **self.info}


@dataclass(frozen=True)
class PassDef:
    id: str
    precondition: str                     # "none" or the id of an earlier pass
    chart: Mapping                        # generator preset or {"ti1": …}
    profile: Mapping                      # role + colprof parameters
    variants: tuple = ()                  # final pass only

    @property
    def role(self) -> str:
        return self.profile["role"]

    @property
    def generator(self) -> Optional[str]:
        return self.chart.get("generator")


@dataclass(frozen=True)
class StrategyDef:
    id: str
    version: int
    validated: bool
    passes: tuple[PassDef, ...] = field(default_factory=tuple)

    def get_pass(self, pass_id: str) -> PassDef:
        for p in self.passes:
            if p.id == pass_id:
                return p
        raise StrategyError("unknown_pass", f"strategy {self.id} has no pass {pass_id!r}",
                            strategy=self.id, pass_id=pass_id)

    @property
    def first_pass(self) -> PassDef:
        return self.passes[0]

    @property
    def final_pass(self) -> PassDef:
        return self.passes[-1]

    def pass_index(self, pass_id: str) -> int:
        return [p.id for p in self.passes].index(self.get_pass(pass_id).id)

    def default_params(self) -> dict:
        """{pass_id: {"chart": {…}, "profile": {…}}} — the definition's values (role excluded)."""
        out = {}
        for p in self.passes:
            chart = {k: v for k, v in p.chart.items() if k != "generator"}
            out[p.id] = {"chart": chart,
                         "profile": {k: v for k, v in p.profile.items() if k != "role"}}
        return out

    def to_dict(self) -> dict:
        return {"id": self.id, "version": self.version, "validated": self.validated,
                "passes": [{"id": p.id, "precondition": p.precondition, "role": p.role,
                            "generator": p.generator, "chart": dict(p.chart),
                            "profile": dict(p.profile), "variants": [dict(v) for v in p.variants]}
                           for p in self.passes],
                "default_params": self.default_params()}


# ── parameter checks ──────────────────────────────────────────────────────────
def _check_value(name: str, value, spec, where: str):
    kind, bounds = spec
    bad = StrategyError("invalid_param", f"{where}.{name}: {value!r} is not allowed",
                        param=f"{where}.{name}", value=value)
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            raise bad
        value = int(value)
        if not bounds[0] <= value <= bounds[1]:
            raise StrategyError("param_out_of_range", f"{where}.{name}={value} outside {bounds[0]}..{bounds[1]}",
                                param=f"{where}.{name}", value=value, min=bounds[0], max=bounds[1])
        return value
    if kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise bad
        value = float(value)
        if not bounds[0] <= value <= bounds[1]:
            raise StrategyError("param_out_of_range", f"{where}.{name}={value} outside {bounds[0]}..{bounds[1]}",
                                param=f"{where}.{name}", value=value, min=bounds[0], max=bounds[1])
        return value
    if kind == "choice":
        if value not in bounds:
            raise StrategyError("invalid_param", f"{where}.{name}: {value!r} not in {list(bounds)}",
                                param=f"{where}.{name}", value=value, choices=list(bounds))
        return value
    if kind == "gamut":
        allowed = _source_gamut_aliases() + (SOURCE_GAMUT_NONE,)
        if value not in allowed:
            raise StrategyError("invalid_param", f"{where}.{name}: {value!r} not in {list(allowed)}",
                                param=f"{where}.{name}", value=value, choices=list(allowed))
        return value
    if kind == "bool":
        if not isinstance(value, bool):
            raise bad
        return value
    raise bad                                                    # pragma: no cover


def _check_section(values: Mapping, specs: Mapping, where: str, *, allowed: Iterable[str]) -> dict:
    allowed = set(allowed)
    out = {}
    for k, v in values.items():
        if k not in allowed or k not in specs:
            raise StrategyError("unknown_param", f"{where}.{k} is not a parameter of this pass",
                                param=f"{where}.{k}")
        out[k] = _check_value(k, v, specs[k], where)
    return out


# ── loading ───────────────────────────────────────────────────────────────────
def _parse_strategy(sid: str, raw: Mapping) -> StrategyDef:
    def fail(msg):
        raise StrategyError("invalid_definition", f"strategy {sid}: {msg}", strategy=sid)

    passes_raw = raw.get("pass")
    if not isinstance(passes_raw, list) or not passes_raw:
        fail("no pass")
    passes, seen = [], []
    for i, pr in enumerate(passes_raw):
        pid = pr.get("id")
        if not isinstance(pid, str) or not pid or pid in seen:
            fail(f"pass {i}: missing or duplicate id")
        prec = pr.get("precondition", "none")
        if prec != "none" and prec not in seen:
            fail(f"pass {pid}: precondition {prec!r} is not an earlier pass")
        chart = dict(pr.get("chart") or {})
        has_gen, has_ti1 = "generator" in chart, "ti1" in chart
        if has_gen == has_ti1:
            fail(f"pass {pid}: chart must be either a generator preset or a predefined .ti1, not both")
        if has_gen:
            if chart["generator"] != "targen":
                fail(f"pass {pid}: unknown generator {chart['generator']!r}")
            params = {k: v for k, v in chart.items() if k != "generator"}
            if "patches" not in params:
                fail(f"pass {pid}: a targen preset needs patches")
            if prec == "none" and any(k in params for k in _NEEDS_PRECONDITION):
                fail(f"pass {pid}: {', '.join(_NEEDS_PRECONDITION)} need a preconditioning profile")
            chart = {"generator": "targen", **_check_section(params, CHART_PARAMS, f"{pid}.chart",
                                                             allowed=CHART_PARAMS)}
        else:
            if set(chart) != {"ti1"} or not isinstance(chart["ti1"], str):
                fail(f"pass {pid}: a predefined chart is only {{ti1 = \"…\"}}")
        profile = dict(pr.get("profile") or {})
        role = profile.pop("role", None)
        if role not in ROLES:
            fail(f"pass {pid}: profile.role must be one of {ROLES}")
        profile = {"role": role, **_check_section(profile, PROFILE_PARAMS, f"{pid}.profile",
                                                  allowed=PROFILE_PARAMS)}
        variants = tuple(dict(v) for v in (pr.get("variants") or ()))
        passes.append(PassDef(pid, prec, chart, profile, variants))
        seen.append(pid)
    final = passes[-1]
    if final.role != ROLE_FINAL or any(p.role == ROLE_FINAL for p in passes[:-1]):
        fail("only the last pass builds the final profile")
    for p in passes[:-1]:
        if p.variants:
            fail(f"pass {p.id}: variants belong to the final pass")
    if final.variants:
        ids = [v.get("id") for v in final.variants]
        if len(set(ids)) != len(ids) or sum(1 for v in final.variants if v.get("default")) != 1:
            fail("variants need unique ids and exactly one default")
        for v in final.variants:
            for s in v.get("sources") or ():
                if s not in seen:
                    fail(f"variant {v.get('id')}: unknown source pass {s!r}")
    for p in passes:
        if p.role == ROLE_PRECONDITIONING and not any(q.precondition == p.id for q in passes):
            fail(f"pass {p.id}: a preconditioning profile no later pass uses")
    return StrategyDef(sid, int(raw.get("version", 1)), bool(raw.get("validated", False)), tuple(passes))


def load_strategies(path: Optional[Path] = None) -> dict[str, StrategyDef]:
    """Every built-in strategy, checked. Raises StrategyError on a malformed file (a shipped
    file is a code defect: never silently half-loaded)."""
    p = Path(path) if path else BUILTIN_TOML
    if path is None and not p.is_file():          # a bundle without lib/z9_client/data: no strategy
        return {}
    with open(p, "rb") as f:
        data = tomllib.load(f)
    return {sid: _parse_strategy(sid, raw) for sid, raw in (data.get("strategy") or {}).items()}


def get_strategy(sid: str, path: Optional[Path] = None) -> StrategyDef:
    strategies = load_strategies(path)
    if sid not in strategies:
        raise StrategyError("unknown_strategy", f"unknown profiling strategy {sid!r}",
                            strategy=sid, known=sorted(strategies))
    return strategies[sid]


def normalize_params(sdef: StrategyDef, overrides: Optional[Mapping] = None) -> dict:
    """The definition's parameters with the user's overrides, every value bounded. Only the
    parameters a pass defines may be changed (no new flag smuggled into a line)."""
    params = copy.deepcopy(sdef.default_params())
    for pid, sections in (overrides or {}).items():
        pdef = sdef.get_pass(pid)
        if not isinstance(sections, Mapping):
            raise StrategyError("invalid_param", f"{pid}: expected an object", param=pid)
        for section, values in sections.items():
            if section not in ("chart", "profile") or not isinstance(values, Mapping):
                raise StrategyError("unknown_param", f"{pid}.{section} is not a parameter section",
                                    param=f"{pid}.{section}")
            if section == "chart":
                if pdef.generator is None:
                    raise StrategyError("unknown_param", f"{pid}: a predefined chart has no generator parameter",
                                        param=f"{pid}.chart")
                allowed = [k for k in pdef.chart if k != "generator"]
                params[pid]["chart"].update(_check_section(values, CHART_PARAMS, f"{pid}.chart", allowed=allowed))
            else:
                allowed = [k for k in pdef.profile if k != "role"]
                params[pid]["profile"].update(_check_section(values, PROFILE_PARAMS, f"{pid}.profile",
                                                             allowed=allowed))
    return params


# ── composition ───────────────────────────────────────────────────────────────
def _num(v) -> str:
    if isinstance(v, float):
        return f"{v:.1f}" if v == int(v) else f"{v:g}"
    return str(v)


def compose_targen(pass_params: Mapping) -> str:
    """targen flags of a generator pass, WITHOUT -d2 (forced by build_targen_argv) and WITHOUT
    -c (resolved by the server from the preconditioning pass, then injected)."""
    c = pass_params["chart"]
    toks = ["-G"] if c.get("ofps", "G") == "G" else ["-Q"]
    if c.get("grey_steps"):
        toks += ["-g", _num(c["grey_steps"])]
    if c.get("neutral_emphasis") is not None:
        toks += ["-N", _num(float(c["neutral_emphasis"]))]
    if c.get("neutral_steps"):
        toks += ["-n", _num(c["neutral_steps"])]
    toks += ["-f", _num(int(c["patches"]))]
    return " ".join(toks)


def compose_colprof(pass_params: Mapping) -> str:
    """colprof flags of a pass build (-A/-M/-C/-D are added by ProfilingOps.build_profile).
    The viewing conditions only matter with a source gamut: left out without one."""
    p = pass_params["profile"]
    toks = ["-v", f"-q{p.get('quality', 'h')}"]
    if p.get("smoothing") is not None:
        toks += ["-r", _num(float(p["smoothing"]))]
    if p.get("b2a"):
        toks.append(f"-b{p['b2a']}")
    gamut = p.get("source_gamut")
    if gamut and gamut != SOURCE_GAMUT_NONE:
        toks += ["-S", gamut]
        if p.get("viewcond_in"):
            toks += ["-c", p["viewcond_in"]]
        if p.get("viewcond_out"):
            toks += ["-d", p["viewcond_out"]]
    if p.get("fwa"):
        toks.append("-f")
    return " ".join(toks)


def describe_source_gamut(alias: Optional[str]) -> Optional[dict]:
    """What ``-S <alias>`` really points at (the bundled file), for the UI to say it plainly:
    AdobeRGB is the bundled ClayRGB (Elle Stone), not Adobe's own file."""
    if not alias or alias == SOURCE_GAMUT_NONE:
        return None
    from .config import _SOURCE_GAMUT_ALIASES, resolve_source_gamut
    filename = _SOURCE_GAMUT_ALIASES.get(alias)
    try:
        path = resolve_source_gamut(alias)
        available = True
    except FileNotFoundError:
        path, available = None, False
    return {"alias": alias, "file": Path(path).name if path else filename, "path": path,
            "available": available}


def compose(sdef: StrategyDef, pass_id: str, params: Mapping) -> dict:
    """The lines of one pass from normalized parameters."""
    pdef = sdef.get_pass(pass_id)
    pp = params[pass_id]
    prof = pp["profile"]
    vc = None
    if prof.get("source_gamut") not in (None, SOURCE_GAMUT_NONE):
        vc = {k: {"code": prof[k], "description": VIEWING_CONDITIONS[prof[k]]}
              for k in ("viewcond_in", "viewcond_out") if prof.get(k)}
    return {
        "pass": pass_id, "role": pdef.role, "precondition": pdef.precondition,
        "generator": pdef.generator,
        "targen_flags": compose_targen(pp) if pdef.generator == "targen" else None,
        "ti1": pdef.chart.get("ti1"),
        "patches": pp["chart"].get("patches"),
        "colprof_flags": compose_colprof(pp),
        "source_gamut": describe_source_gamut(prof.get("source_gamut")),
        "viewing_conditions": vc,
    }


def compose_all(sdef: StrategyDef, params: Mapping) -> list[dict]:
    return [compose(sdef, p.id, params) for p in sdef.passes]


# ── the one-pass wizard's guided form (not a strategy) ───────────────────────
# No run, no pass, no ``strategy`` block: one chart and its profile, with the parameters,
# bounds and composition of a final pass, from the [one_pass] values of the built-in file.
# Its -c is the wizard's menu (resident, built by freeglaz, custom, or none), resolved by the
# chart route; without one, targen cannot place the neutral axis, so -N / -n are left out.
ONE_PASS_CHART = ("patches", "ofps", "grey_steps", "neutral_emphasis", "neutral_steps")
ONE_PASS_PROFILE = ("quality", "source_gamut", "viewcond_in", "viewcond_out")


def one_pass_defaults(path: Optional[Path] = None) -> Optional[dict]:
    """{"chart": {…}, "profile": {…}} of the guided one-pass form, checked; None when the
    built-in file (or its [one_pass] table) is absent: the wizard then only offers its free
    line. Raises StrategyError on a malformed table (a shipped file is a code defect)."""
    p = Path(path) if path else BUILTIN_TOML
    if not p.is_file():
        return None
    with open(p, "rb") as f:
        raw = tomllib.load(f).get("one_pass")
    if raw is None:
        return None
    out = {}
    for section, names, specs in (("chart", ONE_PASS_CHART, CHART_PARAMS),
                                  ("profile", ONE_PASS_PROFILE, PROFILE_PARAMS)):
        values = raw.get(section)
        if not isinstance(values, Mapping) or set(values) != set(names):
            raise StrategyError("invalid_definition", f"one_pass.{section} must give exactly {list(names)}")
        out[section] = _check_section(values, specs, f"one_pass.{section}", allowed=names)
    return out


def normalize_one_pass(overrides: Optional[Mapping] = None, *, preconditioned: bool,
                       path: Optional[Path] = None) -> dict:
    """The guided form's values with the user's overrides, every value bounded; -N / -n are
    dropped without a -c (``preconditioned`` False)."""
    params = one_pass_defaults(path)
    if params is None:
        raise StrategyError("one_pass_unavailable", "the guided one-pass form is not shipped with this build")
    for section, values in (overrides or {}).items():
        if section not in ("chart", "profile") or not isinstance(values, Mapping):
            raise StrategyError("unknown_param", f"one_pass.{section} is not a parameter section",
                                param=f"one_pass.{section}")
        names, specs = (ONE_PASS_CHART, CHART_PARAMS) if section == "chart" else (ONE_PASS_PROFILE, PROFILE_PARAMS)
        params[section].update(_check_section(values, specs, f"one_pass.{section}", allowed=names))
    if not preconditioned:
        for k in _NEEDS_PRECONDITION:
            params["chart"].pop(k, None)
    return params


def compose_one_pass(params: Mapping) -> dict:
    """The targen flags (without -d2 and -c, like a pass) and the colprof flags of the guided
    one-pass form, from normalized parameters."""
    prof = params["profile"]
    vc = None
    if prof.get("source_gamut") not in (None, SOURCE_GAMUT_NONE):
        vc = {k: {"code": prof[k], "description": VIEWING_CONDITIONS[prof[k]]}
              for k in ("viewcond_in", "viewcond_out") if prof.get(k)}
    return {"targen_flags": compose_targen(params), "colprof_flags": compose_colprof(params),
            "patches": params["chart"]["patches"],
            "source_gamut": describe_source_gamut(prof.get("source_gamut")), "viewing_conditions": vc}


# ── targen flags given by hand for a strategy pass ───────────────────────────
def check_free_targen_line(flags: str) -> None:
    """A strategy pass may run a hand-edited targen line, but its -c stays the server's (the
    preconditioning profile of the earlier pass, or none): a typed -c is refused."""
    import shlex
    try:
        toks = shlex.split(flags or "")
    except ValueError as e:
        raise StrategyError("invalid_targen_line", f"unreadable targen line: {e}")
    if any(t == "-c" or (t.startswith("-c") and len(t) > 2) for t in toks):
        raise StrategyError("precondition_is_the_strategy", "a strategy pass gets its -c from the strategy: "
                            "remove -c from the line")


# ── FWA compensation gate ─────────────────────────────────────────────────────
def fwa_gate(identities: Sequence) -> dict:
    """May colprof -f (FWA compensation) be used on these measurements?

    ArgyllCMS models the brightener glow from the spectrum of every patch and the
    instrument's own lamp (spectro/insttypes.c inst_illuminant): no spectra → no -f; the
    ColorMunki has "No U.V." in its lamp; an instrument ArgyllCMS has no lamp for (the Z9's
    built-in spectrophotometer) cannot be compensated. The model fits readings taken under
    that lamp only (M0): M1/M2/M3/D65 readings are refused, and an unknown condition is
    never presumed to be M0. ``identities``: MeasurementIdentity (or None when unreadable)
    of every scan the build would use."""
    from . import measurement_identity as mi
    if not identities:
        return {"allowed": False, "reason": "no_measurements"}
    for ident in identities:
        # the instrument first: it rules FWA out whatever the file holds
        if ident is None or ident.instrument is None or ident.method is None:
            return {"allowed": False, "reason": "identity_unknown"}
        if ident.instrument == "colormunki":
            return {"allowed": False, "reason": "instrument_without_uv", "instrument": ident.instrument}
        if ident.method != mi.METHOD_EXTERNAL or ident.instrument == mi.SOL_MODEL:
            return {"allowed": False, "reason": "instrument_not_supported", "instrument": ident.instrument}
        if not ident.spectral_bands:
            return {"allowed": False, "reason": "no_spectra", "instrument": ident.instrument}
        if ident.condition == mi.CONDITION_UNKNOWN:
            return {"allowed": False, "reason": "condition_unknown", "instrument": ident.instrument}
        if ident.condition != "M0":
            return {"allowed": False, "reason": "condition_not_m0", "instrument": ident.instrument,
                    "condition": ident.condition}
    return {"allowed": True, "reason": None}


def flags_request_fwa(flags: Sequence[str]) -> bool:
    return any(t == "-f" or (t.startswith("-f") and len(t) > 2) for t in flags)


# ── run state (derived, never stored) ─────────────────────────────────────────
STEPS = ("pass1_print", "pass1_measure", "intermediate_build", "pass2_create",
         "pass2_print", "pass2_measure", "final_build", "done")


def next_step(first: Mapping, later: Sequence[Mapping]) -> str:
    """Next step of a two-pass run from facts about its charts (dicts with ``printed``,
    ``n_included``, and for the first pass ``intermediate`` = {"built", "state"}, for a later
    pass ``final_built``). The most recent later chart counts (an earlier attempt stays
    listed, untouched)."""
    if not first.get("printed"):
        return "pass1_print"
    if not first.get("n_included"):
        return "pass1_measure"
    if not later:
        inter = first.get("intermediate") or {}
        if not inter.get("built") or inter.get("state") in ("stale", "missing_input"):
            return "intermediate_build"
        return "pass2_create"
    last = later[-1]
    if not last.get("printed"):
        return "pass2_print"
    if not last.get("n_included"):
        return "pass2_measure"
    if not last.get("final_built"):
        return "final_build"
    return "done"
