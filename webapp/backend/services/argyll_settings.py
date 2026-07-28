# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Argyll settings — probe, validate a candidate root, persist to the TOML.

The webapp lets a tester point freeglaz at its Argyll install from the GUI
without touching env vars / TOML by hand. Critical: we write the SAME store the
resolution cascade reads — ``~/.freeglazrc.toml [argyll]`` (tier 2, below the
``FREEGLAZ_ARGYLL_*`` env overrides, above OS autodetect) — NOT the app-level
``settings.json`` (which the cascade ignores). See lib.z9_client.argyll.

Only ``root`` is GUI-managed (``<root>/bin`` + ``<root>/ref``); split installs
stay the domain of the env vars.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Optional

import tomli_w

# Coupling with the resolver (import, not duplicate): we reuse its required
# binary list + the exact validity predicates so the GUI verdict matches
# `freeglaz check` / the startup banner (single source: check_argyll).
from lib.z9_client.argyll import (
    REQUIRED_BINARIES,
    _is_exe,
    _ref_has_witness,
    check_argyll,
)
from lib.z9_client.config import get_config_path
from webapp.backend.models import ArgyllStatus

# env vars that override the TOML root (tier 1). If any is set, the GUI value is
# shadowed → the UI signals it so the user isn't confused by "I changed it but
# nothing moved".
_ENV_OVERRIDE_KEYS = (
    "FREEGLAZ_ARGYLL_ROOT", "FREEGLAZ_ARGYLL_BIN", "FREEGLAZ_ARGYLL_REF",
    "ARGYLL_BIN",
)


def probe() -> ArgyllStatus:
    """Current effective Argyll availability (cascade-resolved), as ArgyllStatus.

    Same source as `freeglaz check` / the banner → the GUI never diverges."""
    r = check_argyll()
    return ArgyllStatus(
        ok=r["ok"], bin_ok=r["bin_ok"], ref_ok=r["ref_ok"],
        missing=r["missing"], bin_dir=r["bin_dir"], ref_dir=r["ref_dir"],
    )


def validate_root(root: str) -> dict:
    """Validate a CANDIDATE root (``<root>/bin`` + ``<root>/ref``) WITHOUT
    persisting. Same predicates as check_argyll (executable binaries + a ref
    dir holding *.cht). Returns ok/bin_ok/ref_ok/missing/bin_dir/ref_dir."""
    root_p = Path(root).expanduser()
    bin_dir = root_p / "bin"
    ref_dir = root_p / "ref"
    missing_bins = [n for n in REQUIRED_BINARIES if not _is_exe(bin_dir / n)]
    bin_ok = not missing_bins
    ref_ok = ref_dir.is_dir() and _ref_has_witness(str(ref_dir))
    missing = list(missing_bins) + ([] if ref_ok else ["ref"])
    return {
        "ok": bin_ok and ref_ok, "bin_ok": bin_ok, "ref_ok": ref_ok,
        "missing": missing,
        "bin_dir": str(bin_dir) if bin_ok else None,
        "ref_dir": str(ref_dir) if ref_ok else None,
    }


def read_root() -> Optional[str]:
    """The ``[argyll].root`` currently in ~/.freeglazrc.toml, or None."""
    p = get_config_path()
    if not p.exists():
        return None
    try:
        with open(p, "rb") as f:
            return tomllib.load(f).get("argyll", {}).get("root")
    except (OSError, ValueError):
        return None


def write_root(root: str) -> None:
    """Persist ``[argyll].root`` to ~/.freeglazrc.toml, PRESERVING every other
    section ([colprof], [multipass], …). We re-read the raw TOML, set only the
    [argyll] section, and rewrite. GUI manages ``root`` only → we drop any stale
    ``bin_dir``/``ref_dir`` in [argyll] so ``root`` stays authoritative.

    NB: like config.save_config, tomli_w loses comments — acceptable here.
    """
    p = get_config_path()
    data: dict = {}
    if p.exists():
        try:
            with open(p, "rb") as f:
                data = tomllib.load(f)
        except (OSError, ValueError):
            data = {}
    argyll = dict(data.get("argyll", {}))
    argyll["root"] = str(Path(root).expanduser())
    argyll.pop("bin_dir", None)
    argyll.pop("ref_dir", None)
    data["argyll"] = argyll
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        tomli_w.dump(data, f)


def clear_root() -> None:
    """Remove the GUI-managed Argyll root → fall back to auto-detection.

    Drops ``root``/``bin_dir``/``ref_dir`` from ``[argyll]``; removes the whole
    ``[argyll]`` section if it becomes empty. Preserves every OTHER section
    ([colprof], [multipass], …). No-op if the TOML doesn't exist (already auto).
    """
    p = get_config_path()
    if not p.exists():
        return
    try:
        with open(p, "rb") as f:
            data = tomllib.load(f)
    except (OSError, ValueError):
        return
    argyll = data.get("argyll")
    if not isinstance(argyll, dict):
        return
    for k in ("root", "bin_dir", "ref_dir"):
        argyll.pop(k, None)
    if argyll:
        data["argyll"] = argyll
    else:
        data.pop("argyll", None)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        tomli_w.dump(data, f)


def env_override_active() -> bool:
    """True if a FREEGLAZ_ARGYLL_* / ARGYLL_BIN env var shadows the GUI/TOML."""
    if any(os.environ.get(k) for k in _ENV_OVERRIDE_KEYS):
        return True
    return any(os.environ.get(f"FREEGLAZ_ARGYLL_{n.upper()}") for n in REQUIRED_BINARIES)
