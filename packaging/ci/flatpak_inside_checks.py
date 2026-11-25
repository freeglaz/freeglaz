#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""flatpak_inside_checks.py - checks run INSIDE the installed freeglaz Flatpak (release workflow).

    flatpak run --command=python3 --env=PYTHONPATH=/app/share/freeglaz \\
        --filesystem=<this folder>:ro io.github.freeglaz.freeglaz \\
        flatpak_inside_checks.py --expect-trusted yes|no

The checks of D1 (note 143) on the real package: the engine found at /app/chartread-fgK, its
origin, the permissions the app was installed with, /app read-only, the Corresponding Source
and the ArgyllCMS licence next to the binaries. Reads files and lists directory NAMES only:
never opens a device node, never runs chartread-fgK. Prints one JSON object; exit 1 when a
required check fails."""
import argparse
import configparser
import json
import os
import sys
from pathlib import Path

from lib.z9_client.chartread_fgk import engine as E
from lib.z9_client.chartread_fgk import session as S

ap = argparse.ArgumentParser()
ap.add_argument("--expect-trusted", required=True, choices=["yes", "no"])
a = ap.parse_args()
expect = a.expect_trusted == "yes"
out: dict = {"checks": []}


def check(name, ok, got=None):
    out["checks"].append({"check": name, "ok": bool(ok), "got": got})


check("inside a Flatpak sandbox", Path("/.flatpak-info").is_file())
info = configparser.ConfigParser(interpolation=None)
info.read("/.flatpak-info")
ctx = info["Context"] if info.has_section("Context") else {}
devices = [d for d in ctx.get("devices", "").split(";") if d]
filesystems = [f for f in ctx.get("filesystems", "").split(";") if f]
out["context"] = {"devices": devices, "filesystems": filesystems}
check("permission --device=usb", "usb" in devices, devices)
check("no --device=all", "all" not in devices, devices)
check("permission --filesystem=xdg-run/freeglaz:create",
      "xdg-run/freeglaz:create" in filesystems, filesystems)

st = E.engine_status()
d = st.to_dict()
out["engine"] = {k: d.get(k) for k in ("ok", "code", "source", "path", "binary_sha256",
                                        "patch_version", "usb_only", "trusted")}
check("engine ok, found at /app/chartread-fgK", st.ok and d.get("source") == "bundle"
      and d.get("path") == str(E.FLATPAK_DIR / E.ENGINE_NAME), out["engine"])
check("engine: patch 3.1, USB only", d.get("patch_version") == "3.1" and d.get("usb_only") is True,
      out["engine"])
origin = S.EngineSpec.from_status(st).origin if st.ok else None
out["origin"] = origin
check("engine trusted = %s (TRUSTED_BUILDS)" % expect, d.get("trusted") is expect, d.get("trusted"))
check("engine origin = %s" % ("approved" if expect else "development"),
      origin == ("approved" if expect else "development"), origin)

path, scope = S.user_lock_path()
out["user_lock"] = {"path": str(path) if path else None, "scope": scope}
check("per-user instrument lock shared with the host", scope == "user", out["user_lock"])

try:
    Path("/app/chartread-fgK/chartread-fgK.manifest.json").write_text("{}")
    check("/app read-only", False, "the manifest could be replaced")
except OSError as e:
    check("/app read-only", True, e.strerror)

src = sorted(p.name for p in Path("/app/chartread-fgK/source").glob("*"))
out["engine_source"] = src
check("engine Corresponding Source next to the binary",
      "chartread-fgK-3.5.0-fg3.1-src.tar.gz" in src and "COPYING" in src, src)
check("ArgyllCMS licence and source note", Path("/app/argyll/License.txt").is_file()
      and Path("/app/argyll/ARGYLL_SOURCE.txt").is_file())
check("ArgyllCMS udev rules shipped", Path("/app/argyll/usb/55-Argyll.rules").is_file())


def names(p):
    try:
        return sorted(os.listdir(p))
    except OSError as e:
        return "absent (%s)" % e.strerror


out["dev_bus_usb"] = names("/dev/bus/usb")       # report only: the runner has no instrument
out["ok"] = all(c["ok"] for c in out["checks"])
print(json.dumps(out, indent=1))
sys.exit(0 if out["ok"] else 1)
