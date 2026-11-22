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

"""Printer model family, from the identification a DesignJet reports.

freeglaz is validated on the DesignJet Z9. The Z9 Pro answers the same web
services but behaves differently where freeglaz cares (roll dimensions, two
rolls, embedded spectrophotometer), so it must never pass for a Z9 — and its
``ModelName`` ("HP DesignJet Z9 Pro 64in PS") does contain "DesignJet Z9".

Families:

- ``z9``        DesignJet Z9 / Z9+ (24in, 44in, dr): ``validated``;
- ``z9pro``     DesignJet Z9 Pro / Z9+ Pro: ``partial`` (known limits);
- ``designjet`` any other DesignJet: accepted, ``untested``;
- ``None``      not a DesignJet.

Matching is on the casefolded ``ModelName``, never a strict equality (widths
and the PS suffix vary). ``ModelNumber`` ("Z9 Pro 64in PS") is a second
witness for the Pro only: a Pro must not fall back to ``z9`` because one
field is short.
"""
from __future__ import annotations

import re
from typing import Mapping, Optional

FAMILY_Z9 = "z9"
FAMILY_Z9_PRO = "z9pro"
FAMILY_OTHER = "designjet"

SUPPORT_VALIDATED = "validated"
SUPPORT_PARTIAL = "partial"
SUPPORT_UNTESTED = "untested"

_SUPPORT = {
    FAMILY_Z9: SUPPORT_VALIDATED,
    FAMILY_Z9_PRO: SUPPORT_PARTIAL,
    FAMILY_OTHER: SUPPORT_UNTESTED,
}

# "z9 pro", "z9+ pro", "z9pro" — in ModelName or ModelNumber.
_PRO = re.compile(r"\bz9\+?\s*pro\b")
# "designjet z9", "designjet z9+", "designjet z9dr", "designjet z9+ dr";
# not "designjet z90…".
_Z9 = re.compile(r"\bdesignjet\s+z9\+?(?:\s*dr)?(?![\w+])")


def _norm(value) -> str:
    return " ".join(str(value or "").split()).casefold()


def classify(ident: Optional[Mapping]) -> Optional[str]:
    """Family of the printer described by ``ident`` (the ``Fields`` of the
    identification), or ``None`` when it is not a DesignJet."""
    ident = ident or {}
    name = _norm(ident.get("ModelName"))
    if "designjet" not in name:
        return None
    if _PRO.search(name) or _PRO.search(_norm(ident.get("ModelNumber"))):
        return FAMILY_Z9_PRO
    if _Z9.search(name):
        return FAMILY_Z9
    return FAMILY_OTHER


def support_level(family: Optional[str]) -> Optional[str]:
    """``validated`` / ``partial`` / ``untested`` for a family, ``None`` for
    a non-DesignJet."""
    return _SUPPORT.get(family) if family else None
