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

"""Is a TI3 written by ``chartread-fgK`` a state we can rely on? Pure: bytes in, verdict out.

The chart side comes from the session's copy of the ``.ti2`` (:class:`ChartPlan`): every
patch's SAMPLE_ID, SAMPLE_LOC and RGB, and how patches group into strip passes (the
letters of SAMPLE_LOC, ``A``…``Z``, ``AA``…, chartread's own labels). Padding patches
(SAMPLE_ID 0) have a place on paper but are never written by chartread.

:func:`validate` accepts a TI3 only when:

- it is complete CGATS (data format, NUMBER_OF_SETS equal to the rows, END_DATA: a
  truncated file is refused), with SAMPLE_ID, SAMPLE_LOC, RGB and XYZ columns;
- every row is a patch of the chart, once, with ITS location and RGB; no padding id;
- passes are whole: each pass is either fully there (all its useful patches) or absent;
- XYZ values are finite numbers, and the spectra follow the publication rule (at least
  two bands on one regular grid, finite values; the grid of a given model is not imposed
  before a real reading has shown it);
- the reported instrument is the session's model, and INSTRUMENT_FILTER matches the
  session's condition (M2 → UVCUT; M0, M1 and the ColorMunki leave none);
- the row count is the one chartread announced, when given;
- against the previous validated state: same instrument, calibration standard, filter
  and spectral grid; no validated pass lost; no validated pass with different values,
  except passes the caller explicitly allows (a re-read chartread announced, a correction).

Each pass gets a digest of its rows, so the comparison with the previous state does not
need the previous file.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Collection, Iterable, Optional

from .. import measurement_identity as mi
from ..measurement_publish import spectral_grid, spectral_issue

PADDING_ID = "0"
_LOC = re.compile(r"^([A-Za-z]+)(\d+)$")
RGB_TOLERANCE = 5e-4
_FILTER_OF_CONDITION = {"M2": "UVCUT", "M3": "POLARIZED", "D65": "D65"}


class CheckpointInvalid(ValueError):
    """``code`` is stable: chart_unreadable | ti3_unreadable | ti3_truncated | columns_missing |
    duplicate_id | unknown_id | padding_id | location_mismatch | rgb_mismatch | partial_pass |
    xyz_invalid | not_spectral | instrument_mismatch | filter_mismatch | count_mismatch |
    identity_changed | pass_lost | pass_changed | pass_unexpected."""

    def __init__(self, code: str, message: str, **info):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.info = info


def pass_order(label: str) -> tuple[int, str]:
    """chartread's order of pass labels for printtarg's ``A-Z, A-Z``: A…Z, AA…AZ, BA…"""
    return (len(label), label)


# ─── CGATS ──────────────────────────────────────────────────────────────────
def _parse(data: bytes, what: str, code: str) -> tuple[dict, list[str], list[list[str]]]:
    """Keywords, data format and rows of the first table, strictly."""
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        raise CheckpointInvalid(code, f"{what} is not ASCII text") from None
    kw: dict[str, str] = {}
    fmt: list[str] = []
    rows: list[list[str]] = []
    n_sets: Optional[int] = None
    state, ended = "head", False
    for raw in text.splitlines():
        line = raw.strip()
        if state == "data":
            if line == "END_DATA":
                ended = True
                break
            if line and not line.startswith("#"):
                rows.append(line.split())
            continue
        if not line or line.startswith("#"):
            continue
        if state == "fmt":
            if line == "END_DATA_FORMAT":
                state = "head"
            else:
                fmt.extend(line.split())
        elif line == "BEGIN_DATA_FORMAT":
            state = "fmt"
        elif line == "BEGIN_DATA":
            state = "data"
        elif line.startswith("NUMBER_OF_SETS"):
            try:
                n_sets = int(line.split()[1])
            except (IndexError, ValueError):
                raise CheckpointInvalid(code, f"{what}: bad NUMBER_OF_SETS") from None
        elif not line.startswith("KEYWORD "):
            key, _, rest = line.partition(" ")
            kw.setdefault(key, rest.strip().strip('"'))
    if not fmt:
        raise CheckpointInvalid(code, f"{what}: no data format")
    if n_sets is None:
        raise CheckpointInvalid(code, f"{what}: no NUMBER_OF_SETS")
    if not ended:
        raise CheckpointInvalid("ti3_truncated" if code == "ti3_unreadable" else code,
                                f"{what}: no END_DATA (truncated file)")
    if len(rows) != n_sets:
        raise CheckpointInvalid("ti3_truncated" if code == "ti3_unreadable" else code,
                                f"{what}: {len(rows)} rows for NUMBER_OF_SETS {n_sets}")
    for r in rows:
        if len(r) != len(fmt):
            raise CheckpointInvalid(code, f"{what}: a row has {len(r)} values for {len(fmt)} columns",
                                    row=" ".join(r[:2]))
    return kw, fmt, rows


def _col(fmt: list[str], name: str, what: str) -> int:
    try:
        return fmt.index(name)
    except ValueError:
        raise CheckpointInvalid("columns_missing", f"{what} has no {name} column") from None


# ─── chart ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ChartPlan:
    ti2_sha256: str
    target_instrument: Optional[str]
    passes: tuple[str, ...]                          # chartread order
    pass_ids: dict                                   # pass -> tuple of useful SAMPLE_IDs (ti2 order)
    patches: dict                                    # SAMPLE_ID -> (loc, pass, (r, g, b))
    padding: int

    @property
    def n_useful(self) -> int:
        return len(self.patches)

    def useful_in(self, passes: Iterable[str]) -> int:
        return sum(len(self.pass_ids[p]) for p in passes)

    @classmethod
    def from_ti2(cls, data: bytes) -> "ChartPlan":
        kw, fmt, rows = _parse(data, "the .ti2", "chart_unreadable")
        i_id, i_loc = _col(fmt, "SAMPLE_ID", ".ti2"), _col(fmt, "SAMPLE_LOC", ".ti2")
        i_rgb = [_col(fmt, c, ".ti2") for c in ("RGB_R", "RGB_G", "RGB_B")]
        patches, pass_ids, padding, seen_locs = {}, {}, 0, set()
        for r in rows:
            loc = r[i_loc].strip('"')
            m = _LOC.match(loc)
            if m is None or loc in seen_locs:
                raise CheckpointInvalid("chart_unreadable", f".ti2: bad or repeated SAMPLE_LOC {loc!r}")
            seen_locs.add(loc)
            label = m.group(1)
            pass_ids.setdefault(label, [])
            sid = r[i_id]
            if sid == PADDING_ID:
                padding += 1
                continue
            if sid in patches:
                raise CheckpointInvalid("chart_unreadable", f".ti2: SAMPLE_ID {sid} twice")
            try:
                rgb = tuple(float(r[k]) for k in i_rgb)
            except ValueError:
                raise CheckpointInvalid("chart_unreadable", f".ti2: bad RGB for {sid}") from None
            patches[sid] = (loc, label, rgb)
            pass_ids[label].append(sid)
        passes = tuple(sorted(pass_ids, key=pass_order))
        return cls(hashlib.sha256(data).hexdigest(), kw.get("TARGET_INSTRUMENT"), passes,
                   {p: tuple(pass_ids[p]) for p in passes}, patches, padding)


# ─── state ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Ti3Identity:
    target_instrument: Optional[str]
    devcalstd: Optional[str]
    instrument_filter: Optional[str]
    bands: tuple[float, ...]

    def to_dict(self) -> dict:
        return {"target_instrument": self.target_instrument, "devcalstd": self.devcalstd,
                "instrument_filter": self.instrument_filter, "n_bands": len(self.bands),
                "bands": [self.bands[0], self.bands[-1]] if self.bands else []}


@dataclass(frozen=True)
class CheckpointState:
    sha256: str
    passes: tuple[str, ...]                          # saved passes, chartread order
    n_patches: int                                   # rows = useful patches saved
    identity: Optional[Ti3Identity]                  # None for a TI3 with no row
    digests: dict = field(default_factory=dict)      # pass -> digest of its rows
    complete: bool = False

    def summary(self) -> dict:
        return {"sha256": self.sha256, "passes": list(self.passes), "passes_saved": len(self.passes),
                "patches_saved": self.n_patches, "complete": self.complete,
                "identity": self.identity.to_dict() if self.identity else None}


def validate(data: bytes, plan: ChartPlan, *, model: str, condition: str,
             previous: Optional[CheckpointState] = None, announced: Optional[int] = None,
             may_add: Optional[Collection[str]] = None, may_change: Collection[str] = (),
             may_remove: Collection[str] = ()) -> CheckpointState:
    """The verdict on ``data`` (see module doc). ``may_add``: passes allowed to appear
    (None: any); ``may_change`` / ``may_remove``: validated passes allowed to change or
    disappear. Raises CheckpointInvalid."""
    kw, fmt, rows = _parse(data, "the TI3", "ti3_unreadable")
    i_id, i_loc = _col(fmt, "SAMPLE_ID", "the TI3"), _col(fmt, "SAMPLE_LOC", "the TI3")
    i_rgb = [_col(fmt, c, "the TI3") for c in ("RGB_R", "RGB_G", "RGB_B")]
    i_xyz = [_col(fmt, c, "the TI3") for c in ("XYZ_X", "XYZ_Y", "XYZ_Z")]
    if announced is not None and announced != len(rows):
        raise CheckpointInvalid("count_mismatch", f"chartread announced {announced} patches, "
                                f"the file holds {len(rows)}", announced=announced, found=len(rows))
    by_pass: dict[str, list[list[str]]] = {}
    seen: set[str] = set()
    for r in rows:
        sid, loc = r[i_id], r[i_loc].strip('"')
        if sid == PADDING_ID:
            raise CheckpointInvalid("padding_id", f"padding SAMPLE_ID 0 at {loc}")
        if sid in seen:
            raise CheckpointInvalid("duplicate_id", f"SAMPLE_ID {sid} twice", sample_id=sid)
        seen.add(sid)
        p = plan.patches.get(sid)
        if p is None:
            raise CheckpointInvalid("unknown_id", f"SAMPLE_ID {sid} is not in the chart", sample_id=sid)
        if loc != p[0]:
            raise CheckpointInvalid("location_mismatch", f"SAMPLE_ID {sid} at {loc}, the chart says {p[0]}",
                                    sample_id=sid)
        try:
            rgb = [float(r[k]) for k in i_rgb]
            xyz = [float(r[k]) for k in i_xyz]
        except ValueError:
            raise CheckpointInvalid("xyz_invalid", f"non-numeric value for SAMPLE_ID {sid}") from None
        if any(abs(a - b) > RGB_TOLERANCE for a, b in zip(rgb, p[2])):
            raise CheckpointInvalid("rgb_mismatch", f"RGB of SAMPLE_ID {sid} differs from the chart",
                                    sample_id=sid)
        if not all(math.isfinite(v) for v in xyz):
            raise CheckpointInvalid("xyz_invalid", f"XYZ of SAMPLE_ID {sid} is not finite", sample_id=sid)
        by_pass.setdefault(p[1], []).append(r)
    for label, prow in by_pass.items():
        if len(prow) != len(plan.pass_ids[label]):
            raise CheckpointInvalid("partial_pass", f"pass {label} has {len(prow)} of its "
                                    f"{len(plan.pass_ids[label])} patches", pass_label=label)
    identity = None
    if rows:
        issue = spectral_issue(fmt, rows)
        if issue is not None:
            raise CheckpointInvalid("not_spectral", issue.pop("message"), **issue)
        identity = Ti3Identity(kw.get("TARGET_INSTRUMENT"), kw.get("DEVCALSTD") or None,
                               kw.get("INSTRUMENT_FILTER") or None, spectral_grid(fmt))
        m = mi.INSTRUMENT_MODELS.get(model)
        if m is None or identity.target_instrument not in m.reported_names:
            raise CheckpointInvalid("instrument_mismatch", f"the TI3 reports "
                                    f"{identity.target_instrument!r}, not a {model}",
                                    reported=identity.target_instrument)
        want = _FILTER_OF_CONDITION.get(condition)
        got = (identity.instrument_filter or "").upper() or None
        if got != want:
            raise CheckpointInvalid("filter_mismatch", f"INSTRUMENT_FILTER {got!r}, the session's "
                                    f"condition {condition} expects {want!r}")
    order = {sid: k for k, sid in enumerate(plan.patches)}
    digests = {}
    for label, prow in by_pass.items():
        h = hashlib.blake2b(digest_size=16)
        for r in sorted(prow, key=lambda r: order[r[i_id]]):
            h.update(" ".join(r).encode())
            h.update(b"\n")
        digests[label] = h.hexdigest()
    passes = tuple(sorted(by_pass, key=pass_order))
    state = CheckpointState(hashlib.sha256(data).hexdigest(), passes, len(rows), identity, digests,
                            complete=len(passes) == len(plan.passes))
    if previous is not None:
        _against(previous, state, may_add, set(may_change), set(may_remove))
    return state


def _against(prev: CheckpointState, cur: CheckpointState, may_add, may_change: set,
             may_remove: set) -> None:
    if prev.identity is not None and cur.identity is not None and prev.identity != cur.identity:
        raise CheckpointInvalid("identity_changed", "instrument, calibration standard, filter or "
                                "spectral grid differ from the validated state",
                                before=prev.identity.to_dict(), after=cur.identity.to_dict())
    lost = sorted(set(prev.passes) - set(cur.passes) - may_remove, key=pass_order)
    if lost:
        raise CheckpointInvalid("pass_lost", f"validated pass(es) missing: {', '.join(lost)}", passes=lost)
    changed = sorted((p for p in set(prev.passes) & set(cur.passes)
                      if prev.digests.get(p) != cur.digests.get(p) and p not in may_change), key=pass_order)
    if changed:
        raise CheckpointInvalid("pass_changed", f"validated pass(es) with other values: {', '.join(changed)}",
                                passes=changed)
    if may_add is not None:
        extra = sorted(set(cur.passes) - set(prev.passes) - set(may_add), key=pass_order)
        if extra:
            raise CheckpointInvalid("pass_unexpected", f"pass(es) chartread did not announce: "
                                    f"{', '.join(extra)}", passes=extra)


def without_pass(data: bytes, plan: ChartPlan, label: str) -> bytes:
    """The TI3 without the rows of pass ``label`` (an offline correction: chartread -r will
    ask for that pass again). Header and other rows unchanged; NUMBER_OF_SETS updated."""
    _, fmt, rows = _parse(data, "the TI3", "ti3_unreadable")
    i_id = _col(fmt, "SAMPLE_ID", "the TI3")
    drop = set(plan.pass_ids.get(label, ()))
    if not drop:
        raise CheckpointInvalid("pass_lost", f"no pass {label} in the chart")
    out, in_data = [], False
    for raw in data.decode("ascii").splitlines():
        s = raw.strip()
        if in_data:
            if s == "END_DATA":
                in_data = False
            elif s and not s.startswith("#") and s.split()[i_id] in drop:
                continue
        elif s == "BEGIN_DATA":
            in_data = True
        elif s.startswith("NUMBER_OF_SETS"):
            raw = f"NUMBER_OF_SETS {len(rows) - sum(1 for r in rows if r[i_id] in drop)}"
        out.append(raw)
    return ("\n".join(out) + "\n").encode("ascii")
