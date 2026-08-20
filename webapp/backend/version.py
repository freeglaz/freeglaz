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

"""Single runtime source of truth for the application version.

``pyproject.toml`` is the canonical version for humans and the release process,
but it is NOT bundled in the packaged apps (the PyInstaller .app and the Flatpak
ship only the code, not pyproject). This constant travels with the code, so it is
the value surfaced at runtime (``/api/health`` → Settings "About").

Kept in sync with pyproject by ``tests/test_version.py`` — a forgotten bump fails
the suite. Bump both on each release.
"""

__version__ = "0.1.8-dev"
