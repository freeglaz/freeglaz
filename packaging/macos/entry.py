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

"""macOS .app entry point for freeglaz (PyInstaller bundle).

Launches the native desktop window (uvicorn + WKWebView) with an EMPTY argv,
so macOS's ``-psn_x_yyyy`` launch argument — which Finder passes to a bundled
app — never reaches ``argparse`` and crashes startup.

When frozen, the bundle's Contents are read-only (an .app in /Applications),
so the webapp's runtime data (logs, settings, job mapping, previews) is
redirected to a user-writable directory via ``FREEGLAZ_DATA_DIR`` — set here
*before* the backend is imported (the data paths are resolved at import time,
see ``webapp/backend/paths.py``). User-facing data (custom profiles, config)
already lives in the home directory and is untouched.

ArgyllCMS is NOT bundled: it stays an external dependency, auto-detected at
runtime (Homebrew paths / ``FREEGLAZ_ARGYLL_ROOT``). The printing path works
without it; only the open profiling path requires it.
"""
import os
import sys
from pathlib import Path


def _prepare_frozen_env() -> None:
    """Redirect writable state out of the read-only bundle when frozen."""
    if not getattr(sys, "frozen", False):
        return
    data = Path.home() / "Library" / "Application Support" / "freeglaz"
    os.environ.setdefault("FREEGLAZ_DATA_DIR", str(data))


def main() -> int:
    _prepare_frozen_env()
    # Imported here, after the env is prepared, so backend modules resolve
    # their data directory against FREEGLAZ_DATA_DIR.
    from webapp.desktop import main as desktop_main
    return desktop_main([])


if __name__ == "__main__":
    import multiprocessing
    # PyInstaller + any library that spawns a child interpreter (e.g. via
    # multiprocessing) needs this guard to avoid re-running the bundle.
    multiprocessing.freeze_support()
    raise SystemExit(main())
