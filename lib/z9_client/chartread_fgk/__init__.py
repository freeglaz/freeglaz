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

"""External measurement engine: ``chartread-fgK``.

``chartread-fgK`` is ArgyllCMS's official ``chartread`` with the freeglaz checkpoint
patch (``-K``: the .ti3 is rewritten atomically after each strip pass, resumed with
``-r``), built for USB instruments only. It is a separate program, distributed with its
own source (AGPL); freeglaz only runs it. Nothing here runs it yet: :mod:`.engine`
finds the binary, checks it without executing it and builds its command line.
"""
