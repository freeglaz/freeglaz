"""Tests — CLI ``freeglaz check`` (Argyll availability diagnostic).

Smoke tests via subprocess. Deterministic regardless of whether the runner has
Argyll installed: we assert the JSON shape + the exit-code invariant (0 iff ok),
not a specific availability outcome.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI = REPO_ROOT / "freeglaz"


def _run(*args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    return subprocess.run(
        [sys.executable, str(CLI), "check", *args],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT,
    )


def test_check_json_shape_and_exit_invariant():
    res = _run("--json")
    payload = json.loads(res.stdout)
    for key in ("ok", "bin_ok", "bin_dir", "binaries", "ref_ok", "ref_dir",
                "missing", "install_hint"):
        assert key in payload
    assert set(payload["binaries"]) == {"targen", "colprof", "spec2cie", "profcheck"}
    # exit code 0 iff fully available
    assert (res.returncode == 0) == payload["ok"]
    # when incomplete, 'missing' is non-empty and the hint is actionable
    if not payload["ok"]:
        assert payload["missing"]
        assert "Argyll" in payload["install_hint"]


def test_check_human_output_runs():
    res = _run()
    assert "Argyll CMS" in res.stdout
    assert res.returncode in (0, 1)


def test_check_help_mentions_argyll():
    res = subprocess.run(
        [sys.executable, str(CLI), "check", "--help"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert res.returncode == 0
    assert "Argyll" in res.stdout
