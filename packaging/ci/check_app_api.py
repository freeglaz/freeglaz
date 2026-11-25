#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""check_app_api.py - read-only checks of a RUNNING packaged freeglaz (release workflow).

    check_app_api.py --base URL --package-root DIR --version PEP440 --engine-sha SHA
                     --expect-trusted yes|no [--desktop] --out OUT.json

GET requests only, to the app's own backend on 127.0.0.1: nothing is created, printed or
measured, and no instrument program is run. DIR is where the package's files are, as the
app sees them (the .app on macOS, /app in the Flatpak): the ArgyllCMS tools and the
measurement engine must be resolved inside it. --expect-trusted says what TRUSTED_BUILDS
says of SHA: the app must report exactly that (a candidate engine is never reported
trusted). Python >= 3.9, standard library only. Exit 0 when every required check passed."""
import argparse
import json
import sys
import urllib.error
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--base", required=True)
ap.add_argument("--package-root", required=True)
ap.add_argument("--version", required=True)
ap.add_argument("--engine-sha", required=True)
ap.add_argument("--expect-trusted", required=True, choices=["yes", "no"])
ap.add_argument("--desktop", action="store_true", help="the app runs with its window")
ap.add_argument("--out", required=True)
A = ap.parse_args()
BASE, ROOT = A.base.rstrip("/"), A.package_root.rstrip("/")
EXPECT_TRUSTED = A.expect_trusted == "yes"
checks, raw = [], {}


def get(path, timeout=60):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:  # noqa: S310 - loopback
            body, status = r.read().decode("utf-8", "replace"), r.status
    except urllib.error.HTTPError as e:
        body, status = e.read().decode("utf-8", "replace"), e.code
    except Exception as e:  # noqa: BLE001 - recorded, never raised
        body, status = repr(e), None
    try:
        data = json.loads(body)
    except ValueError:
        data = body[:2000]
    raw[path] = {"status": status, "body": data}
    return status, data


def check(name, ok, got=None, required=True):
    checks.append({"check": name, "ok": bool(ok), "required": required, "got": got})
    print(("ok   " if ok else ("FAIL " if required else "warn ")) + name
          + ("" if ok else "  got=%s" % json.dumps(got, ensure_ascii=False)[:300]))


def inside(p):
    return isinstance(p, str) and p.startswith(ROOT + "/")


st, h = get("/api/health")
h = h if isinstance(h, dict) else {}
check("health: version %s" % A.version, st == 200 and h.get("version") == A.version, h.get("version"))
check("health: ArgyllCMS available", isinstance(h.get("argyll"), dict) and h["argyll"].get("ok") is True,
      h.get("argyll"))

if A.desktop:
    st, d = get("/api/desktop/instance")
    check("desktop instance", st == 200 and isinstance(d, dict) and d.get("desktop") is True, d)

st, a = get("/api/settings/argyll")
check("ArgyllCMS tools resolved inside the package", st == 200 and (ROOT + "/") in json.dumps(a)
      and "/opt/homebrew" not in json.dumps(a) and "/usr/bin/" not in json.dumps(a), a)

st, s = get("/api/profiling-strategies")
s = s if isinstance(s, dict) else {}
ids = [x.get("id") for x in (s.get("strategies") or [])]
check("built-in strategies: two-pass present", "two_pass_adaptive" in ids, ids)
one = s.get("one_pass") or {}
check("built-in strategies: one-pass guided defaults", bool(one.get("default_params")), one)

st, c = get("/api/charts/colprof-presets")
check("built-in colprof presets present", st == 200 and bool(c), c if st != 200 else None)
st, t = get("/api/charts/targen-presets")
check("built-in targen presets present", st == 200 and bool(t), t if st != 200 else None)

st, o = get("/api/charts/printtarg-options")
o = o if isinstance(o, dict) else {}
eng = o.get("measurement_engine") or {}
check("engine: ok", eng.get("ok") is True, eng)
check("engine: the one shipped in the package (source bundle)",
      eng.get("source") == "bundle" and inside(eng.get("path")), eng)
check("engine: checkpoint patch 3.1, USB only",
      eng.get("patch_version") == "3.1" and eng.get("usb_only") is True, eng)
check("engine: sha256 = the engine measured in the package",
      eng.get("binary_sha256") == A.engine_sha, eng.get("binary_sha256"))
check("engine: trusted = %s (TRUSTED_BUILDS)" % EXPECT_TRUSTED,
      eng.get("trusted") is EXPECT_TRUSTED, eng.get("trusted"))
bins = o.get("binaries") or {}
check("printtarg available", (bins.get("printtarg") or {}).get("available") is True, bins)

st, charts = get("/api/charts")
check("charts listed", st == 200, charts if st != 200 else None)
st, cur = get("/api/external-measure/current")
check("no external measurement running",
      st == 200 and isinstance(cur, dict) and cur.get("chart_id") is None, cur)

failed = [c for c in checks if c["required"] and not c["ok"]]
result = {"base": BASE, "package_root": ROOT, "checks": checks, "raw": raw,
          "trusted_reported": eng.get("trusted"), "engine_sha256_reported": eng.get("binary_sha256"),
          "ok": not failed}
with open(A.out, "w") as f:
    json.dump(result, f, indent=1, ensure_ascii=False)
print("\n%d checks, %d required failed" % (len(checks), len(failed)))
sys.exit(1 if failed else 0)
