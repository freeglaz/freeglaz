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

"""Single source of truth for the writable runtime-data directory.

Logs, settings, the job mapping, the paper snapshot and job previews are all
written under this directory. It defaults to ``webapp/data`` next to the
source, which is writable for a source or dev install.

Overridable via the ``FREEGLAZ_DATA_DIR`` environment variable so a *read-only*
install can redirect writes to a user-writable location — a bundled macOS
``.app`` in ``/Applications`` (its Contents are read-only), a system package,
or any install where the package directory is not writable. The bundled app
sets this before importing the backend (see ``packaging/macos/entry.py``).

Note: user-facing data (custom profiles, config) already lives in the home
directory (``~/Documents/freeglaz``, ``~/.freeglazrc.toml``,
``~/Library/ColorSync/Profiles``) and is unaffected by this — only the
webapp's internal runtime state is covered here.
"""
from __future__ import annotations

import os
from pathlib import Path

# webapp/data — this file is webapp/backend/paths.py, so parent.parent is webapp/.
_DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def data_dir() -> Path:
    """Return the writable runtime-data directory (``FREEGLAZ_DATA_DIR`` or default)."""
    env = os.environ.get("FREEGLAZ_DATA_DIR")
    return Path(env).expanduser() if env else _DEFAULT_DATA_DIR
