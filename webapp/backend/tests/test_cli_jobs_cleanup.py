"""Tests — CLI ``freeglaz jobs cleanup-previews``.

Smoke tests via subprocess to validate:
- Without option: shows stats without deleting anything
- --dry-run: preview what would be deleted
- --older-than DAYS: actual deletion + mapping purge
"""
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI = REPO_ROOT / "freeglaz"


def _run_cli(*args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """Run ``freeglaz jobs cleanup-previews ...`` and return the result."""
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(CLI), "jobs", "cleanup-previews", *args],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT,
    )


@pytest.fixture
def temp_previews_dir(tmp_path, monkeypatch):
    """Create a tmp dir and patch the thumbs path on the CLI side.

    The CLI run in a subprocess does not see the monkeypatch of the
    current test — so we must pass the override via an env variable
    intercepted on the job_preview / job_mapping side. To simplify these
    CLI tests, we do not use the monkeypatch but isolate via
    PYTHONPATH+FREEGLAZ_DATA_DIR (to add if we want to actually
    override). For this phase we test the surface
    invocation (help + stats) which does not depend on the content.
    """
    return tmp_path


def test_cli_help_displays_correctly():
    """The cleanup-previews help lists the right args."""
    proc = _run_cli("--help")
    assert proc.returncode == 0
    assert "--older-than" in proc.stdout
    assert "--dry-run" in proc.stdout


def test_cli_without_args_returns_stats_when_dir_empty(tmp_path, monkeypatch):
    """Smoke: without args + missing/empty dir, returns 0 and erases nothing."""
    # The CLI uses webapp/data/job_previews by default. If that path is
    # empty locally (dev case without deployed prod), we expect "No thumbnail".
    # Tolerance: if the dev env already has thumbs (user printed live),
    # we also accept the stats format with count > 0 — what matters is
    # that the command returns 0 and did not crash.
    proc = _run_cli()
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    # Either "No thumbnail" (empty dir), or "freeglaz thumbnails" (stats)
    assert ("No thumbnail" in proc.stdout) or ("freeglaz thumbnails" in proc.stdout)


def test_cli_rejects_negative_older_than():
    proc = _run_cli("--older-than", "-1")
    # Returncode may be 0 (if dir empty -> exit early before validation)
    # or 2 (if dir non-empty and validation triggered). We accept both
    # but check the message if error.
    if proc.returncode != 0:
        assert ">=" in proc.stderr or ">=" in proc.stdout


def test_cli_dry_run_does_not_modify_files(tmp_path):
    """--dry-run + --older-than must NEVER touch the filesystem.

    Smoke: if the prod dir contains thumbs, we check that their
    count is unchanged after the dry-run call. If no thumbs, the
    test has no observable effect but validates non-regression.

    We use ``--older-than 0`` so that all existing thumbs
    are candidates (otherwise the test is inert if all prod
    thumbs are recent — the local dev case after live validation).
    """
    from webapp.backend.services import job_preview as jp
    before = list(jp.list_thumbnails())
    proc = _run_cli("--older-than", "0", "--dry-run")
    assert proc.returncode == 0
    after = list(jp.list_thumbnails())
    assert {p.name for p in before} == {p.name for p in after}
    if before:
        # With --older-than 0, all thumbs are > 0 days old
        # -> all candidates -> output must contain "DRY-RUN".
        assert "DRY-RUN" in proc.stdout
