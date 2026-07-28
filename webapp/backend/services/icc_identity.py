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

"""Colorimetric identity of an ICC profile (for file ↔ paper matching).

Why not a plain MD5 of the whole ICC: two exports of the SAME Z9 profile can
differ byte-for-byte in their *metadata* only — typically the ``desc`` tag (the
human-readable name), the ``cprt`` copyright, or the header creation date — while
every colour-defining tag (the A2B*/B2A* LUTs, white/black point, gamut…) is
byte-identical. A full-file MD5 then reports a *false* mismatch (verified live:
a file's embedded ICC vs the live ``get_profile`` differed ONLY in ``desc``,
494 vs 248 bytes, all colour tables identical).

``icc_color_hash`` hashes ONLY the colour-relevant tag data, ignoring the header
(creation date, profile ID, size, tag-table offsets) and the pure-metadata tags
below. So the same profile compares EQUAL regardless of name/copyright/date, and
a real change in the colour tables still yields a different hash.
"""
from __future__ import annotations

import hashlib
from typing import Optional

# Pure text / metadata tags that can legitimately differ without any colour
# change → excluded from the colorimetric identity.
_METADATA_TAGS = {b"desc", b"cprt", b"dmnd", b"dmdd", b"meta", b"text"}


def icc_color_hash(icc_bytes: Optional[bytes]) -> Optional[str]:
    """SHA-256 over the colour-defining ICC tags only, or None.

    Returns None when ``icc_bytes`` is absent or not a parseable ICC profile
    (the caller then falls back to a name comparison). Order-independent:
    tags are hashed sorted by signature.
    """
    if not icc_bytes or len(icc_bytes) < 132:
        return None
    try:
        count = int.from_bytes(icc_bytes[128:132], "big")
        if count <= 0 or count > 4096:          # sane tag-count bound
            return None
        entries = []
        for i in range(count):
            o = 132 + i * 12
            if o + 12 > len(icc_bytes):
                return None
            sig = icc_bytes[o:o + 4]
            off = int.from_bytes(icc_bytes[o + 4:o + 8], "big")
            size = int.from_bytes(icc_bytes[o + 8:o + 12], "big")
            if sig in _METADATA_TAGS:
                continue
            if size <= 0 or off < 128 or off + size > len(icc_bytes):
                return None
            entries.append((sig, icc_bytes[off:off + size]))
        if not entries:
            return None
        entries.sort(key=lambda e: e[0])
        h = hashlib.sha256()
        for sig, data in entries:
            h.update(sig)
            h.update(len(data).to_bytes(4, "big"))
            h.update(data)
        return h.hexdigest()
    except Exception:  # noqa: BLE001 — never let a malformed ICC break matching
        return None
