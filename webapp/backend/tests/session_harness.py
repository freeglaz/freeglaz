"""Test harness for chartread-fgK sessions (not a test module).

- a synthetic printtarg chart (chart.json + .ti2, the layout printtarg writes: passes A…Z,
  AA…, patches placed at random, padding patches SAMPLE_ID 0) and the "measured" values a
  fake instrument returns for each location (SYNTHETIC, mechanism tests only);
- TI3 bytes in chartread's format, for the pure checkpoint tests;
- an engine for a Session: the Python fake chartread in "measure" mode, or the lab's real
  chartread v3 linked to its fake instrument when FREEGLAZ_TEST_CHARTREAD_FAKEINST names it;
- ``drive()``: the user's side of a run (calibrate when asked, press the instrument's
  button at each strip prompt). The button is simulated by writing a trigger key straight
  to the terminal: exactly what the instrument switch does, and never through the driver's
  KeyGate, which has no command that starts a reading.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from webapp.backend.tests.chartread_harness import FAKE

SERIAL = "CN0TEST0001"
TARGET_TI2 = {"colormunki": "X-Rite ColorMunki", "i1pro3plus": "GretagMacbeth i1 Pro"}
TARGET_TI3 = {"colormunki": "X-Rite ColorMunki", "i1pro3plus": "X-Rite i1 Pro 3"}
NB = 36


def pass_label(i: int) -> str:
    return chr(65 + i) if i < 26 else chr(64 + i // 26) + chr(65 + i % 26)


@dataclass
class Chart:
    dir: Path
    chart_id: str
    ti2: Path
    values: dict           # location -> [X, Y, Z, s1..s36]
    labels: list
    model: str
    steps: int

    @property
    def values_path(self) -> Path:
        return self.dir.parent / f"{self.chart_id}.values.json"


def _xyz(r, g, b):
    def lin(c):
        c /= 100.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    rl, gl, bl = lin(r), lin(g), lin(b)
    return (100 * (0.4124 * rl + 0.3576 * gl + 0.1805 * bl), 100 * (0.2126 * rl + 0.7152 * gl + 0.0722 * bl),
            100 * (0.0193 * rl + 0.1192 * gl + 0.9505 * bl))


def _spectrum(r, g, b, salt):
    out = []
    for i in range(NB):
        wl = 380 + 10 * i
        v = (r * math.exp(-((wl - 610) / 45) ** 2) + g * math.exp(-((wl - 540) / 40) ** 2)
             + b * math.exp(-((wl - 455) / 35) ** 2))
        out.append(round(min(max(1.5 + 0.92 * v + 0.05 * math.sin(salt * 0.37 + i * 0.5), 0.2), 99.0), 6))
    return out


def make_chart(root: Path, *, n_useful: int = 2000, steps: int = 26, model: str = "colormunki",
               seed: int = 7, chart_id: str = "CHT-20261124-1015-AB") -> Chart:
    """A printtarg-shaped chart in ``root/charts/<serial>/<chart_id>/``."""
    rnd = random.Random(seed)
    n_pass = -(-n_useful // steps)
    labels = [pass_label(i) for i in range(n_pass)]
    locs = [f"{lab}{k}" for lab in labels for k in range(1, steps + 1)]
    order = list(range(len(locs)))
    rnd.shuffle(order)
    ids = {}
    for sid, slot in enumerate(order, start=1):
        ids[locs[slot]] = str(sid) if sid <= n_useful else "0"
    rows, values = [], {}
    for loc in sorted(locs, key=lambda l: (ids[l] == "0", int(ids[l]) if ids[l] != "0" else 0, l)):
        sid = ids[loc]
        k = int(sid) if sid != "0" else 0
        rgb = (100.0, 100.0, 100.0) if sid == "0" else \
            (round(100 * ((k * 37) % 11) / 10, 4), round(100 * ((k * 17) % 13) / 12, 4), round(100 * ((k * 7) % 9) / 8, 4))
        x, y, z = _xyz(*rgb)
        rows.append(f'{sid} "{loc}" {rgb[0]:.4f} {rgb[1]:.4f} {rgb[2]:.4f} {x:.5f} {y:.5f} {z:.5f}')
        salt = sum(ord(c) * (j + 1) for j, c in enumerate(loc))
        e = 0.004 * math.sin(salt * 1.7)
        values[loc] = [round(x * (1 + e), 6), round(y * (1 + e), 6), round(z * (1 + e), 6)] + _spectrum(*rgb, salt)
    strips = [str(min(26, n_pass - i)) for i in range(0, n_pass, 26)]
    ti2 = "\n".join([
        "CTI2   ", "", 'DESCRIPTOR "Argyll Calibration Target chart information 2"', 'ORIGINATOR "Argyll printtarg"',
        'CREATED "Tue Nov 24 10:15:00 2026"', f'TARGET_INSTRUMENT "{TARGET_TI2[model]}"',
        'ACCURATE_EXPECTED_VALUES "true"', 'APPROX_WHITE_POINT "95.048343 99.996667 108.916410"',
        'COLOR_REP "iRGB"', 'PAPER_SIZE "745.0x450.0"', f'RANDOM_START "{seed}"', f'STEPS_IN_PASS "{steps}"',
        f'PASSES_IN_STRIPS2 "{",".join(strips)}"', 'STRIP_INDEX_PATTERN "A-Z, A-Z"',
        'PATCH_INDEX_PATTERN "0-9,@-9,@-9;1-999"', 'INDEX_ORDER "STRIP_THEN_PATCH"', "",
        "NUMBER_OF_FIELDS 8", "BEGIN_DATA_FORMAT", "SAMPLE_ID SAMPLE_LOC RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z ",
        "END_DATA_FORMAT", "", f"NUMBER_OF_SETS {len(rows)}", "BEGIN_DATA", *rows, "END_DATA", ""]).encode()
    d = Path(root) / "charts" / SERIAL / chart_id
    (d / "printtarg").mkdir(parents=True)
    p = d / "printtarg" / f"{chart_id}.ti2"
    p.write_bytes(ti2)
    (d / "chart.json").write_text(json.dumps({
        "schema_version": 1, "chart_id": chart_id, "layout": "printtarg", "roll_layout": "transverse",
        "instrument": {"key": model}, "geometry": {"patch_count": n_useful, "total_strips": n_pass,
                                                   "steps_in_pass": steps},
        "files": {"ti2": f"printtarg/{chart_id}.ti2"},
        "provenance": {"ti2_sha256": hashlib.sha256(ti2).hexdigest()}}))
    c = Chart(d, chart_id, p, values, labels, model, steps)
    c.values_path.write_text(json.dumps(values))
    return c


def ti3_bytes(chart: Chart, passes, *, target: Optional[str] = None, devcalstd: str = "XRGA",
              instrument_filter: Optional[str] = None, scale: dict = None, nb: int = NB,
              start_nm: int = 380, step_nm: int = 10) -> bytes:
    """What chartread writes once ``passes`` are read (rows in .ti2 order, no padding)."""
    scale = scale or {}
    passes = set(passes)
    body = []
    for line in chart.ti2.read_text().split("BEGIN_DATA\n", 1)[1].split("\nEND_DATA")[0].splitlines():
        t = line.split()
        loc = t[1].strip('"')
        lab = loc.rstrip("0123456789")
        if t[0] == "0" or lab not in passes:
            continue
        v = chart.values[loc]
        f = scale.get(lab, 1.0)
        body.append(" ".join(t[:5] + [f"{x * f:.6f}" for x in v[:3 + nb]]))
    head = ["CTI3   ", "", 'DESCRIPTOR "Argyll Calibration Target chart information 3"',
            'ORIGINATOR "Argyll chartread"', 'CREATED "Tue Nov 24 10:20:00 2026"',
            f'TARGET_INSTRUMENT "{target or TARGET_TI3[chart.model]}"']
    if instrument_filter:
        head.append(f'INSTRUMENT_FILTER "{instrument_filter}"')
    head.append(f'DEVCALSTD "{devcalstd}"')
    cols = ["SAMPLE_ID", "SAMPLE_LOC", "RGB_R", "RGB_G", "RGB_B", "XYZ_X", "XYZ_Y", "XYZ_Z"] + \
        [f"SPEC_{start_nm + step_nm * j}" for j in range(nb)]
    return "\n".join(head + ["", f"NUMBER_OF_FIELDS {len(cols)}", "BEGIN_DATA_FORMAT", " ".join(cols),
                             "END_DATA_FORMAT", "", f"NUMBER_OF_SETS {len(body)}", "BEGIN_DATA", *body,
                             "END_DATA", ""]).encode()


def data_rows(ti3: bytes) -> list[str]:
    """The data lines of a TI3 (what must be identical between two sessions)."""
    return ti3.decode().split("BEGIN_DATA\n", 1)[1].split("\nEND_DATA")[0].splitlines()


# ─── engines ────────────────────────────────────────────────────────────────
def python_engine(tmp: Path, chart: Chart, name: str = "engine", **measure) -> tuple[Path, dict]:
    """An executable that runs the Python fake in measure mode with this scenario."""
    scen = tmp / f"{name}.json"
    scen.write_text(json.dumps({"max_seconds": measure.pop("max_seconds", 120),
                                "steps": [{"measure": {"values": str(chart.values_path), **measure}}]}))
    exe = tmp / f"{name}.sh"
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{FAKE}" "{scen}" "$@"\n')
    exe.chmod(0o755)
    return exe, {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": str(tmp)}


FAKEINST = os.environ.get("FREEGLAZ_TEST_CHARTREAD_FAKEINST")


def fakeinst_engine(tmp: Path, chart: Chart, *, itype: str = "colormunki", actions: str = "",
                    extra: Optional[dict] = None) -> tuple[Path, dict]:
    """The real chartread v3 linked to the lab's fake instrument (no USB code inside)."""
    data = [f"FGFAKE 1", f"TYPE {itype}", f"BANDS {NB} 380 730"]
    for lab in chart.labels:
        data.append(f"PASS {lab} {chart.steps}")
        for k in range(1, chart.steps + 1):
            data.append(" ".join(f"{x:.6f}" for x in chart.values[f"{lab}{k}"]))
    script = tmp / "fakeinst.data"
    script.write_text("\n".join(data) + "\n")
    env = {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": str(tmp), "FG_FAKEINST_SCRIPT": str(script),
           "FG_FAKEINST_STATE": str(tmp / "fakeinst.state"), "FG_FAKEINST_ACTIONS": actions, **(extra or {})}
    return Path(FAKEINST), env


# ─── the user's side ────────────────────────────────────────────────────────
def drive(run, *, timeout: float = 120.0, press: Optional[Callable[[dict], bool]] = None,
          on_notice: Optional[Callable] = None, until: Optional[Callable[[dict], bool]] = None) -> list[dict]:
    """Poll the run, calibrate when asked, press the button at each strip prompt that
    ``press(notice)`` accepts (default: every unread one), until it ends."""
    notices, deadline = [], time.monotonic() + timeout
    while run.ended is None:
        if time.monotonic() > deadline:
            run.stop_now("test_deadline")
            raise AssertionError(f"run did not end in {timeout} s; last: {[n['kind'] for n in notices[-6:]]}")
        for n in run.poll(0.02):
            notices.append(n)
            if on_notice:
                on_notice(n, run)
            if n["kind"] == "calibration_prompt" and not run.stopping:
                run.command("calibrate")
            elif n["kind"] == "ready" and not n["all_read"] and not run.stopping and (press or (lambda x: True))(n):
                run.proc.write(b" ")                        # the instrument's button
            if until and until(n):
                return notices
    return notices


def kinds(notices) -> list[str]:
    return [n["kind"] for n in notices]
