"""Test harness for the chartread-fgK driver pieces (not a test module).

Wires what the session (a later increment) will wire: a PtyProcess running the FAKE
chartread, the Transcript, the PromptParser, the KeyGate and the ActivityMonitor, under a
hard deadline. Nothing here touches an instrument."""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from lib.z9_client.chartread_fgk.prompts import Event, KeyGate, PromptParser
from lib.z9_client.chartread_fgk.pty_process import PtyProcess
from lib.z9_client.chartread_fgk.transcript import ActivityMonitor, Transcript, Verdict

FAKE = Path(__file__).parent / "fakes" / "fake_chartread.py"
HOLDER = Path(__file__).parent / "fakes" / "spawn_and_hold.py"


def scenario(tmp: Path, steps: list, *, max_seconds: int = 20, name: str = "scenario") -> tuple[Path, Path]:
    keys = tmp / f"{name}.keys.jsonl"
    p = tmp / f"{name}.json"
    p.write_text(json.dumps({"max_seconds": max_seconds, "keys_log": str(keys), "steps": steps}))
    return p, keys


def fake_argv(scen: Path) -> list[str]:
    return [sys.executable, "-I", str(FAKE), str(scen)]


def fake_env() -> dict:
    return {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": os.environ.get("HOME", "/")}


def spawn_fake(tmp: Path, steps: list, **kw) -> tuple[PtyProcess, Path]:
    scen, keys = scenario(tmp, steps, **kw)
    return PtyProcess.spawn(fake_argv(scen), cwd=str(tmp), env=fake_env()), keys


def keys_read(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


@dataclass
class Pump:
    proc: PtyProcess
    log_path: Optional[Path] = None
    min_delay: float = 0.1
    resend_after: float = 1.5
    monitor: ActivityMonitor = field(default_factory=ActivityMonitor)
    transcript_kw: dict = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    verdicts: list[tuple[float, Verdict]] = field(default_factory=list)

    def __post_init__(self):
        self.transcript = Transcript(self.log_path, **self.transcript_kw)
        self.parser = PromptParser()
        self.gate = KeyGate(self.proc.write, min_delay=self.min_delay, resend_after=self.resend_after)
        self.exited = False

    def run(self, *, timeout: float, until: Callable[[Event], bool] = lambda e: False,
            respond: Callable[[Event, KeyGate], None] = lambda e, g: None,
            stop_on: tuple[str, ...] = (), expect_output_within: Optional[float] = None) -> Optional[Event]:
        """Pump until ``until(event)``, a verdict in ``stop_on``, the process exits, or the
        deadline (then AssertionError). Returns the event that matched ``until``."""
        start = time.monotonic()
        deadline = start + timeout
        next_cpu = 0.0
        while True:
            now = time.monotonic()
            if now > deadline:
                raise AssertionError(f"pump deadline after {timeout} s; last events: "
                                     f"{[e.kind for e in self.events[-8:]]}")
            data = self.proc.read(0.02)
            now = time.monotonic()
            if data is None:
                self.exited = True
                self.proc.wait(2.0)                         # end of file can precede the exit
                return None
            if data:
                self.gate.on_output(now)
                self.monitor.on_output(now)
                for ev in self.parser.feed(self.transcript.feed(data, now), now):
                    self.events.append(ev)
                    self.gate.on_event(ev)
                    if ev.kind != "usb_error":
                        self.monitor.on_progress(now)
                    respond(ev, self.gate)
                    if until(ev):
                        self.gate.tick(time.monotonic())
                        return ev
            self.gate.tick(now)
            if now >= next_cpu:
                self.monitor.sample_cpu(self.proc.cpu_seconds(), now)
                next_cpu = now + 0.25
            v = self.monitor.verdict(now, self.transcript.flood(now),
                                     expect_output_within=expect_output_within, since=start)
            if v.status != "ok":
                self.verdicts.append((now, v))
                if v.status in stop_on:
                    return None

    def kinds(self) -> list[str]:
        return [e.kind for e in self.events]
