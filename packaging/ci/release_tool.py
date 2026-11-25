#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""release_tool.py - helpers of the package workflow (.github/workflows/build-packages.yml).

Standard library only, Python >= 3.9: it runs on a bare runner (macOS, Ubuntu) and in the
Flatpak container. It never imports freeglaz: TRUSTED_BUILDS is read from the source of
lib/z9_client/chartread_fgk/engine.py (ast), the same file the packaged app trusts with.

  release_tool.py prepare --ref-name REF --mode auto|report|strict
      Prints key=value lines for $GITHUB_OUTPUT: version, pep440, mode, pins, fgk_url,
      fgk_repo, fgk_commit. Fails when the version of the ref is not the one of
      pyproject.toml, when the Flatpak manifest asks for --device=all or lacks a permission
      the engine needs, or when the engine source is not a fixed commit.
      auto = strict when every packaged platform has a pinned engine, report otherwise.
  release_tool.py pinned --platform P --arch A --sha SHA
      Prints yes or no: is SHA in TRUSTED_BUILDS for (P, A)?
  release_tool.py ci-manifest --tarball PATH --version V
      Writes packaging/flatpak/.ci-manifest.yaml: the manifest, with freeglaz built from the
      tarball of this run instead of a published release, and makes sure the metainfo
      declares V.
  release_tool.py report --out FILE [--set KEY=VALUE ...] [--json KEY=FILE ...]
      Writes one build's report.json (dotted keys; "true"/"false" become booleans).
  release_tool.py qualify --mode M --version V --reports DIR --out DIR
      Compares the reports of the two builds of each platform and of the sources, and writes
      qualification.json, qualification.md and SHA256SUMS. Exit 1 when the verdict fails.
"""
from __future__ import annotations

import argparse
import ast
import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE_PY = ROOT / "lib" / "z9_client" / "chartread_fgk" / "engine.py"
MANIFEST = ROOT / "packaging" / "flatpak" / "io.github.freeglaz.freeglaz.yaml"
METAINFO = ROOT / "packaging" / "flatpak" / "io.github.freeglaz.freeglaz.metainfo.xml"
CI_MANIFEST = MANIFEST.parent / ".ci-manifest.yaml"
PLATFORMS = (("darwin", "arm64"), ("linux", "x86_64"))     # the packages freeglaz ships
BUILDS = ("a", "b")                                          # two independent builds each
REQUIRED_FINISH_ARGS = ("--device=usb", "--filesystem=xdg-run/freeglaz:create")
FORBIDDEN_FINISH_ARGS = ("--device=all",)


def die(msg: str) -> None:
    print(f"release_tool: {msg}", file=sys.stderr)
    raise SystemExit(2)


# --- TRUSTED_BUILDS, read without importing freeglaz ---------------------------------------

def trusted_builds(path: Path = ENGINE_PY) -> dict[tuple[str, str], set[str]]:
    """The literal value of TRUSTED_BUILDS in engine.py: {(platform, arch): {sha256}}.

    Accepts only the form the file uses: a dict of 2-tuples of strings to frozenset({...}) of
    strings (or an empty dict). Anything else is an error, never an empty answer."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = None
    for st in tree.body:
        if isinstance(st, ast.AnnAssign) and getattr(st.target, "id", None) == "TRUSTED_BUILDS":
            node = st.value
        elif isinstance(st, ast.Assign) and any(getattr(t, "id", None) == "TRUSTED_BUILDS"
                                                for t in st.targets):
            node = st.value
    if not isinstance(node, ast.Dict):
        die(f"TRUSTED_BUILDS is not a dict literal in {path}")
    out: dict[tuple[str, str], set[str]] = {}
    for k, v in zip(node.keys, node.values):
        key = ast.literal_eval(k)
        if not (isinstance(key, tuple) and len(key) == 2 and all(isinstance(x, str) for x in key)):
            die(f"unexpected TRUSTED_BUILDS key {ast.dump(k)}")
        if not (isinstance(v, ast.Call) and getattr(v.func, "id", None) == "frozenset"
                and len(v.args) == 1):
            die(f"unexpected TRUSTED_BUILDS value for {key}")
        shas = ast.literal_eval(v.args[0])
        if not (isinstance(shas, (set, list, tuple))
                and all(isinstance(s, str) and re.fullmatch(r"[0-9a-f]{64}", s) for s in shas)):
            die(f"TRUSTED_BUILDS{key}: not a set of sha256")
        out[key] = set(shas)
    return out


def is_pinned(platform: str, arch: str, sha: str) -> bool:
    return sha in trusted_builds().get((platform, arch), set())


# --- versions --------------------------------------------------------------------------------

def pyproject_version() -> str:
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text())
    if not m:
        die("no version in pyproject.toml")
    return m.group(1)


def pep440(version: str) -> str:
    """0.1.17-rc.2 → 0.1.17rc2 (the form of pyproject.toml and of /api/health)."""
    m = re.fullmatch(r"(\d+\.\d+\.\d+)(?:-rc\.(\d+))?", version)
    if not m:
        die(f"unexpected version {version!r}")
    return m.group(1) + (f"rc{m.group(2)}" if m.group(2) else "")


def tag_form(p: str) -> str:
    """0.1.17rc2 → 0.1.17-rc.2 (the form of the tag and of the file names)."""
    m = re.fullmatch(r"(\d+\.\d+\.\d+)(?:rc(\d+))?", p)
    if not m:
        die(f"unexpected pyproject version {p!r}")
    return m.group(1) + (f"-rc.{m.group(2)}" if m.group(2) else "")


def version_of_ref(ref: str) -> str:
    m = re.fullmatch(r"(?:release/)?v(\d+\.\d+\.\d+(?:-rc\.\d+)?)", ref)
    return m.group(1) if m else tag_form(pyproject_version())


# --- the Flatpak manifest ----------------------------------------------------------------------

def manifest_text() -> str:
    return MANIFEST.read_text(encoding="utf-8")


def finish_args(text: str) -> list[str]:
    block = re.search(r"(?ms)^finish-args:\n(.*?)^\S", text)
    if not block:
        die("no finish-args in the Flatpak manifest")
    return re.findall(r"(?m)^\s*-\s*(--\S+)", block.group(1))


def module_block(text: str, name: str) -> str:
    """The text of one top-level module (`  - name: <name>` up to the next one)."""
    m = re.search(rf"(?ms)^  - name: {re.escape(name)}\n(.*?)(?=^  - |\Z)", text)
    if not m:
        die(f"no module {name} in the Flatpak manifest")
    return m.group(1)


def fgk_source(text: str) -> tuple[str, str, str]:
    block = module_block(text, "chartread-fgK")
    m = re.search(r"(?m)^\s*-?\s*type: git\n\s*url: (\S+)\n\s*commit: (\S+)\s*$", block)
    if not m:
        die("the chartread-fgK module has no git source with url and commit")
    url, commit = m.group(1), m.group(2)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        die(f"the chartread-fgK source is not a fixed commit: {commit!r}")
    if re.search(r"(?m)^\s*(branch|tag):", block):
        die("the chartread-fgK source must name a commit only, not a branch or tag")
    r = re.fullmatch(r"https://github\.com/([\w.-]+/chartread-fgK)(?:\.git)?", url)
    if not r:
        die(f"unexpected chartread-fgK url {url!r}")
    return url, r.group(1), commit


def check_permissions(text: str) -> None:
    args = finish_args(text)
    for a in REQUIRED_FINISH_ARGS:
        if a not in args:
            die(f"the Flatpak manifest lacks {a}")
    for a in FORBIDDEN_FINISH_ARGS:
        if a in args:
            die(f"the Flatpak manifest must not ask for {a}")


# --- commands --------------------------------------------------------------------------------

def cmd_prepare(a) -> int:
    version = version_of_ref(a.ref_name)
    p = pep440(version)
    if p != pyproject_version():
        die(f"{a.ref_name} is version {p}, pyproject.toml says {pyproject_version()}")
    text = manifest_text()
    check_permissions(text)
    url, repo, commit = fgk_source(text)
    pins = trusted_builds()
    mode = a.mode
    if mode == "auto":
        mode = "strict" if all(pins.get(pl) for pl in PLATFORMS) else "report"
    out = {"version": version, "pep440": p, "mode": mode,
           "pins": json.dumps({f"{k[0]}/{k[1]}": sorted(v) for k, v in pins.items()}),
           "fgk_url": url, "fgk_repo": repo, "fgk_commit": commit}
    for k, v in out.items():
        print(f"{k}={v}")
    return 0


def cmd_pinned(a) -> int:
    print("yes" if is_pinned(a.platform, a.arch, a.sha) else "no")
    return 0


def cmd_ci_manifest(a) -> int:
    text = manifest_text()
    check_permissions(text)
    tarball = Path(a.tarball).resolve()
    sha = hashlib.sha256(tarball.read_bytes()).hexdigest()
    rel = Path(os.path.relpath(tarball, MANIFEST.parent))
    # The freeglaz module's release tarball (url + sha256 on the next line) becomes the
    # tarball of this run. The ArgyllCMS archive has an argyllcms.com url: not matched.
    new, n = re.subn(
        r"(?m)^(\s*)url:\s*https://github\.com/\S+/releases/download/\S+\n(\s*)sha256:\s*[0-9a-f]{64}$",
        lambda m: f"{m.group(1)}path: {rel.as_posix()}\n{m.group(2)}sha256: {sha}", text)
    if n != 1:
        die(f"expected one freeglaz release tarball in the manifest, found {n}")
    CI_MANIFEST.write_text(new, encoding="utf-8")
    # AppStream advertises the version of metainfo.xml, not of the tarball. A hand-written
    # entry is expected; a placeholder is injected (with a warning) when it is missing, so a
    # package never reports a wrong version.
    s = METAINFO.read_text(encoding="utf-8")
    if re.search(r'<release version="%s"' % re.escape(a.version), s):
        print(f"metainfo.xml declares {a.version}")
    else:
        entry = ('  <releases>\n    <release version="%s" date="%s">\n      <description>\n'
                 '        <p>See the release notes on GitHub.</p>\n      </description>\n'
                 '    </release>' % (a.version, datetime.date.today().isoformat()))
        s, k = re.subn(r"  <releases>", entry, s, count=1)
        if k != 1:
            die("no <releases> block in metainfo.xml")
        METAINFO.write_text(s, encoding="utf-8")
        print(f"::warning::metainfo.xml had no entry for {a.version}; injected a placeholder")
    print(f"{CI_MANIFEST.relative_to(ROOT)}: freeglaz from {rel} ({sha})")
    return 0


def _load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


TOOLCHAIN_KEYS = {
    "darwin": ("runner_image", "macos", "xcode", "clang", "ld", "sdk", "deployment_target"),
    "linux": ("container_image", "sdk_commit", "cc", "ld"),
}
PLATFORM_OF = {"macos": ("darwin", "arm64"), "flatpak": ("linux", "x86_64")}


def cmd_qualify(a) -> int:
    reports = Path(a.reports)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pins = trusted_builds()
    problems: list[str] = []
    notes: list[str] = []
    sources = _load(reports / "report-sources" / "sources.json")
    if not sources:
        problems.append("sources: no report")
        sources = {}
    result = {"version": a.version, "mode": a.mode, "date": datetime.datetime.now(
        datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "sources": sources,
        "trusted_builds": {f"{k[0]}/{k[1]}": sorted(v) for k, v in pins.items()},
        "platforms": {}}
    deliverables: dict[str, str] = {}
    for kind, (plat, arch) in PLATFORM_OF.items():
        builds = {b: _load(reports / f"report-{kind}-{b}" / "report.json") for b in BUILDS}
        entry = {"platform": plat, "arch": arch, "builds": builds}
        result["platforms"][kind] = entry
        missing = [b for b, r in builds.items() if not r]
        if missing:
            problems.append(f"{kind}: no report for build(s) {', '.join(missing)}")
            continue
        for b, r in builds.items():
            if not r.get("ok"):
                problems.append(f"{kind}-{b}: build or package checks failed ({r.get('failed')})")
        shas = {b: (r.get("engine") or {}).get("in_package_sha256") for b, r in builds.items()}
        candidate = shas["a"]
        entry["candidate_sha256"] = candidate
        if not candidate or len(set(shas.values())) != 1:
            problems.append(f"{kind}: the engines of the two builds differ: {shas}")
        tc = {b: {k: (r.get("toolchain") or {}).get(k) for k in TOOLCHAIN_KEYS[plat]}
              for b, r in builds.items()}
        if tc["a"] != tc["b"]:
            notes.append(f"{kind}: the two builds report different toolchains: {tc}")
        entry["toolchain"] = tc["a"]
        pinned = bool(candidate) and candidate in pins.get((plat, arch), set())
        entry["pinned"] = pinned
        for b, r in builds.items():
            reported = (r.get("app_check") or {}).get("trusted_reported")
            if reported is not pinned:
                problems.append(f"{kind}-{b}: the app reports trusted={reported}, "
                                f"TRUSTED_BUILDS says {pinned}")
            if r.get("fgk_commit") != sources.get("fgk_commit"):
                problems.append(f"{kind}-{b}: built from chartread-fgK {r.get('fgk_commit')}, "
                                f"sources from {sources.get('fgk_commit')}")
            src = r.get("engine_source_archive") or {}
            if src.get("sha256") != (sources.get("fgk_source") or {}).get("sha256"):
                if src.get("same_content"):
                    notes.append(f"{kind}-{b}: the engine's source archive in the package has "
                                 "the same files as the release one, in other archive bytes")
                else:
                    problems.append(f"{kind}-{b}: the engine's source archive in the package is "
                                    "not the release one")
        if a.mode == "strict" and not pinned:
            problems.append(f"{kind}: engine {candidate} is not in TRUSTED_BUILDS{(plat, arch)}"
                            " (strict mode)")
        pkg = (builds["a"].get("package") or {})
        if pkg.get("file") and pkg.get("sha256"):
            deliverables[pkg["file"]] = pkg["sha256"]
        else:
            problems.append(f"{kind}-a: no package file in the report")
    for key in ("freeglaz_tarball", "fgk_source", "argyll_source"):
        f = sources.get(key) or {}
        if f.get("file") and f.get("sha256"):
            deliverables[f["file"]] = f["sha256"]
        else:
            problems.append(f"sources: no {key}")
    verdict = "pass" if not problems else "fail"
    approved = all(e.get("pinned") for e in result["platforms"].values())
    result.update(verdict=verdict, approved=approved, problems=problems, notes=notes,
                  deliverables=deliverables)
    (out / "qualification.json").write_text(json.dumps(result, indent=1) + "\n")
    (out / "SHA256SUMS").write_text("".join(f"{s}  {f}\n" for f, s in sorted(deliverables.items())))
    md = [f"# Qualification of freeglaz {a.version} ({a.mode} mode): **{verdict}**", ""]
    md.append("| Package | Engine sha256 (in the package, both builds) | Pinned | Toolchain |")
    md.append("|---|---|---|---|")
    for kind, e in result["platforms"].items():
        tcs = ", ".join(f"{k} {v}" for k, v in (e.get("toolchain") or {}).items())
        md.append(f"| {kind} | `{e.get('candidate_sha256')}` | {'yes' if e.get('pinned') else 'NO'} | {tcs} |")
    md += ["", f"chartread-fgK commit `{sources.get('fgk_commit')}`, freeglaz commit "
           f"`{sources.get('freeglaz_commit')}`.", ""]
    if not approved:
        md += ["**Engines not approved: these packages are candidates only.** freeglaz reports "
               "their engine as not trusted and publishes no measurement made with it. To "
               "approve them, a reviewed commit adds these values to `TRUSTED_BUILDS` "
               "(never automatically):", "", "```python"]
        for kind, e in result["platforms"].items():
            md.append(f"    (\"{e['platform']}\", \"{e['arch']}\"): frozenset({{\"{e.get('candidate_sha256')}\"}}),")
        md += ["```", ""]
    if problems:
        md += ["## Problems", ""] + [f"- {p}" for p in problems] + [""]
    if notes:
        md += ["## Notes", ""] + [f"- {n}" for n in notes] + [""]
    md += ["## Deliverables", "", "```"] + [f"{s}  {f}" for f, s in sorted(deliverables.items())] + ["```", ""]
    (out / "qualification.md").write_text("\n".join(md))
    print("\n".join(md))
    return 0 if verdict == "pass" else 1


def _set(d: dict, dotted: str, value) -> None:
    keys = dotted.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def cmd_report(a) -> int:
    """One build's report.json, from key=value pairs (dotted keys) and JSON files."""
    rep: dict = {}
    for kv in a.set or []:
        k, _, v = kv.partition("=")
        _set(rep, k, {"true": True, "false": False}.get(v, v))
    for kv in a.json or []:
        k, _, path = kv.partition("=")
        _set(rep, k, _load(Path(path)))
    Path(a.out).write_text(json.dumps(rep, indent=1) + "\n")
    print(json.dumps(rep, indent=1))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--ref-name", required=True)
    p.add_argument("--mode", required=True, choices=["auto", "report", "strict"])
    p.set_defaults(func=cmd_prepare)
    p = sub.add_parser("pinned")
    p.add_argument("--platform", required=True)
    p.add_argument("--arch", required=True)
    p.add_argument("--sha", required=True)
    p.set_defaults(func=cmd_pinned)
    p = sub.add_parser("ci-manifest")
    p.add_argument("--tarball", required=True)
    p.add_argument("--version", required=True)
    p.set_defaults(func=cmd_ci_manifest)
    p = sub.add_parser("qualify")
    p.add_argument("--mode", required=True, choices=["report", "strict"])
    p.add_argument("--version", required=True)
    p.add_argument("--reports", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_qualify)
    p = sub.add_parser("report")
    p.add_argument("--out", required=True)
    p.add_argument("--set", action="append", metavar="KEY=VALUE")
    p.add_argument("--json", action="append", metavar="KEY=FILE")
    p.set_defaults(func=cmd_report)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
