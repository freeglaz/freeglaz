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

"""Measurement identity: what decides whether two sets of readings may be combined.

A raw scan is ``measurements/<stem>.ti3``. Two scans may be averaged (or concatenated
into one colprof input) only when they were taken by the same method, the same
instrument model, under the same measurement condition, on the same spectral grid and
calibration standard. Equal spectral bands alone prove nothing: a ColorMunki and an
i1Pro 3 Plus share 380-730 nm / 10 nm, and an M0 and an M2 reading share everything but
the filter.

Two parts, kept apart on purpose:

* the **compatibility identity** (:class:`MeasurementIdentity` fields) — compared by
  :func:`compare_identities`, the ONLY place that decides compatibility;
* the **provenance** (:class:`Provenance`) — reported instrument name, unit serial,
  executed argv, software versions, date, chart, raw files: for audit, never compared.

Where the identity comes from (always recomputed, never stored as a verdict):

* the ti3 itself: ``TARGET_INSTRUMENT``, ``INSTRUMENT_FILTER``, ``DEVCALSTD`` and the
  ``SPEC_*`` columns;
* the **measurement record** ``measurements/<stem>.json`` written by freeglaz when it
  publishes an external reading. It is bound to the ti3 by its sha256 and holds what
  the ti3 cannot: the instrument model chosen for the chart, the argv actually run
  (chartread writes no trace of ``-F n`` / ``-F 5``, so M0 and M1 are only known from
  the argv) and the provenance. The client never supplies any of it.

Historical SOL scans have no record: every ti3 the SOL pipeline ever wrote carries
``TARGET_INSTRUMENT "HP DesignJet Z9 embedded spectro"`` (``profiling.build_cti3``), so
they are recognised from that header. Their condition is ``fixed``: the embedded
spectrophotometer is driven with one non-selectable measurement mode whose ISO 13655
class is not established. ``fixed`` is never read as M0/M1/M2: it only matches the
same instrument model. A ti3 with neither that header nor a record is not identified
and is never combined with anything (it can still be used alone, as before).

Pure module: reads the files it is given, never writes.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

IDENTITY_VERSION = 1
RECORD_SCHEMA = "freeglaz.measurement_record"
RECORD_SCHEMA_VERSION = 1
RECORD_SUFFIX = ".json"           # measurements/<stem>.json, twin of <stem>.ti3

METHOD_SOL = "sol"
METHOD_EXTERNAL = "external"
METHODS = (METHOD_SOL, METHOD_EXTERNAL)

CONDITION_FIXED = "fixed"         # single, non-selectable, unclassified condition
CONDITION_UNKNOWN = "unknown"     # selectable instrument, condition not recorded
M_CONDITIONS = ("M0", "M1", "M2", "M3")
CONDITIONS = M_CONDITIONS + ("D65", CONDITION_FIXED, CONDITION_UNKNOWN)

ORIGIN_RECORD = "record"          # ti3 + measurement record
ORIGIN_SOL_HEADER = "sol_header"  # historical SOL scan, recognised by its header
ORIGIN_TI3_HEADER = "ti3_header"  # any other ti3 without record

SOL_TARGET_INSTRUMENT = "HP DesignJet Z9 embedded spectro"

# chartread -F (Argyll 3.5.0 usage text): n None (M0), 5 D50 (M1), 6 D65, u UV cut (M2),
# p polarising (M3).
CHARTREAD_FILTERS = {"n": "M0", "5": "M1", "6": "D65", "u": "M2", "p": "M3"}
# INSTRUMENT_FILTER as chartread writes it (only these three; M0 / M1 leave no trace).
TI3_INSTRUMENT_FILTERS = {"UVCUT": "M2", "POLARIZED": "M3", "D65": "D65"}
# Reading modes other than reflective: never recorded as a profiling measurement.
_NON_REFLECTIVE_FLAGS = ("-t", "-e", "-d")
# chartread options whose readings are not a spectral profiling measurement:
#   -x  values typed in by hand (-xl / -xx), not measured;   -n  spectra not saved.
_NOT_MEASURED_FLAGS = ("-x", "-n")
# Programs an external record may name in argv[0]: the official chartread and the freeglaz
# engine (chartread + checkpoint patch). Anything else (a replay helper, another engine)
# is not a chartread reading, whatever "tool" says.
EXTERNAL_PROGRAMS = ("chartread", "chartread-fgK")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _program_name(arg0: str) -> str:
    """Base name of argv[0], any path style, without a Windows ``.exe``."""
    name = re.split(r"[\\/]", arg0)[-1]
    return name[:-4] if name.lower().endswith(".exe") else name


def check_external_argv(argv: Sequence[str]) -> None:
    """The argv of an external reading must be a real chartread reading, reflective, with
    its spectra. Raises MeasurementRecordError (measurement_record_unsupported).

    chartread reads ONE option letter per argument (``argv[i][1]``, Argyll style), so the
    letter decides: ``-nv`` is ``-n``. chartread 3.5.0 has no long option at all: any
    ``--…`` comes from another program (``--replay`` of a simulation, a JSON engine…) and is
    refused without keeping a list of them. Values of options (``-F n``) do not start with
    ``-`` and are not options."""
    prog = _program_name(argv[0])
    if prog not in EXTERNAL_PROGRAMS:
        raise _unsupported(f"argv[0] {prog!r} is not chartread", program=prog)
    for tok in argv[1:]:
        if tok.startswith("--"):
            raise _unsupported(f"option {tok} is not a chartread option (simulation or foreign "
                               "engine): not a measurement", option=tok)
        if len(tok) < 2 or tok[0] != "-":
            continue
        flag = tok[:2]
        if flag in _NON_REFLECTIVE_FLAGS:
            raise _unsupported(f"non-reflective reading mode {flag} is not a profiling measurement",
                               option=tok)
        if flag in _NOT_MEASURED_FLAGS:
            raise _unsupported(f"chartread {flag} does not give a spectral measurement "
                               "(-x: typed values, -n: no spectra)", option=tok)


# ── instrument models ───────────────────────────────────────────────────────
@dataclass(frozen=True)
class InstrumentModel:
    key: str                          # canonical model key
    method: str                       # METHOD_*
    label: str                        # log / audit text, not UI text
    reported_names: tuple[str, ...]   # TARGET_INSTRUMENT values this model writes
    header_identifies: bool           # the reported name alone designates THIS model
    conditions: tuple[str, ...]       # conditions it can be read under
    filter_selectable: bool           # True: the condition comes from the argv (-F)


# External keys are the printtarg whitelist keys (lib.z9_client.printtarg_chart.INSTRUMENTS,
# checked by a test). Reported names come from Argyll's inst_name() (insttypes.c):
#  - "X-Rite ColorMunki" is that model only (also seen in a real chartread output);
#  - "X-Rite i1 Pro 3" is the whole i1Pro 3 family: the header cannot tell an i1Pro 3 from
#    an i1Pro 3 Plus, so only a record names the model. Whether a 3 Plus really reports
#    this type still needs a physical reading.
INSTRUMENT_MODELS: dict[str, InstrumentModel] = {
    "hp_z9_embedded": InstrumentModel(
        "hp_z9_embedded", METHOD_SOL, "HP DesignJet Z9 embedded spectrophotometer",
        (SOL_TARGET_INSTRUMENT,), True, (CONDITION_FIXED,), False),
    "colormunki": InstrumentModel(
        "colormunki", METHOD_EXTERNAL, "X-Rite ColorMunki",
        ("X-Rite ColorMunki",), True, (CONDITION_FIXED,), False),
    "i1pro3plus": InstrumentModel(
        "i1pro3plus", METHOD_EXTERNAL, "X-Rite i1Pro 3 Plus",
        ("X-Rite i1 Pro 3",), False, ("M0", "M1", "M2"), True),
}
SOL_MODEL = "hp_z9_embedded"


def external_models() -> tuple[str, ...]:
    return tuple(k for k, m in INSTRUMENT_MODELS.items() if m.method == METHOD_EXTERNAL)


# ── errors ──────────────────────────────────────────────────────────────────
class MeasurementRecordError(ValueError):
    """A measurement record is malformed, unsupported or contradicts its ti3.
    ``code``: measurement_record_malformed | measurement_record_unsupported |
    measurement_record_mismatch."""

    def __init__(self, code: str, message: str, **info):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.info = info


def _malformed(msg: str, **info) -> MeasurementRecordError:
    return MeasurementRecordError("measurement_record_malformed", msg, **info)


def _unsupported(msg: str, **info) -> MeasurementRecordError:
    return MeasurementRecordError("measurement_record_unsupported", msg, **info)


def _mismatch(msg: str, **info) -> MeasurementRecordError:
    return MeasurementRecordError("measurement_record_mismatch", msg, **info)


# ── ti3 header ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Ti3Header:
    keywords: Mapping[str, str]
    data_format: tuple[str, ...]


def read_ti3_header(text: str) -> Ti3Header:
    """Keywords and data format of the FIRST table of a CGATS ti3 (stops at BEGIN_DATA).
    Raises ValueError when there is no data format."""
    kw: dict[str, str] = {}
    fmt: list[str] = []
    in_fmt = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if in_fmt:
            if line == "END_DATA_FORMAT":
                in_fmt = False
                continue
            fmt.extend(line.split())
            continue
        if line == "BEGIN_DATA_FORMAT":
            in_fmt = True
            continue
        if line == "BEGIN_DATA":
            break
        if line.startswith("KEYWORD "):
            continue
        key, _, rest = line.partition(" ")
        rest = rest.strip()
        if len(rest) >= 2 and rest[0] == '"' and rest[-1] == '"':
            rest = rest[1:-1]
        kw.setdefault(key, rest)
    if not fmt:
        raise ValueError("no BEGIN_DATA_FORMAT … END_DATA_FORMAT in the ti3")
    return Ti3Header(kw, tuple(fmt))


def spectral_bands(data_format: Iterable[str]) -> tuple[float, ...]:
    """Wavelengths of the SPEC_* columns, sorted (empty when the ti3 has no spectra)."""
    out = []
    for col in data_format:
        if col.startswith("SPEC_"):
            try:
                out.append(float(col[5:]))
            except ValueError:
                raise ValueError(f"unreadable spectral column {col!r}") from None
    return tuple(sorted(out))


def _spectral_summary(bands: tuple[float, ...]) -> Optional[dict]:
    if not bands:
        return None
    steps = {round(b - a, 3) for a, b in zip(bands, bands[1:])}
    return {"n_bands": len(bands), "start_nm": bands[0], "end_nm": bands[-1],
            "step_nm": steps.pop() if len(steps) == 1 else None, "bands": list(bands)}


# ── identity ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Provenance:
    reported_instrument: Optional[str] = None     # TARGET_INSTRUMENT of the ti3
    serial: Optional[str] = None                  # instrument unit, when known
    argv: Optional[tuple[str, ...]] = None        # command actually executed
    filter_option: Optional[str] = None           # -F value found in argv (None = no -F)
    measured_at: Optional[str] = None
    software: Optional[Mapping[str, str]] = None
    chart_id: Optional[str] = None
    raw_files: tuple[Mapping[str, str], ...] = ()
    record_sha256: Optional[str] = None
    ti3_sha256: Optional[str] = None

    def to_dict(self) -> dict:
        return {"reported_instrument": self.reported_instrument, "serial": self.serial,
                "argv": list(self.argv) if self.argv is not None else None,
                "filter_option": self.filter_option, "measured_at": self.measured_at,
                "software": dict(self.software) if self.software else None,
                "chart_id": self.chart_id, "raw_files": [dict(x) for x in self.raw_files],
                "record_sha256": self.record_sha256, "ti3_sha256": self.ti3_sha256}


@dataclass(frozen=True)
class MeasurementIdentity:
    method: Optional[str]                  # METHOD_* or None (unknown)
    instrument: Optional[str]              # INSTRUMENT_MODELS key or None (unknown)
    condition: str                         # CONDITIONS
    spectral_bands: tuple[float, ...]      # () = no spectra
    calibration_standard: Optional[str]    # DEVCALSTD, None = not reported
    origin: str                            # ORIGIN_*
    provenance: Provenance = field(default_factory=Provenance, compare=False)

    @property
    def missing(self) -> tuple[str, ...]:
        """Compatibility fields that are not known."""
        out = []
        if self.method is None:
            out.append("method")
        if self.instrument is None:
            out.append("instrument")
        if self.condition == CONDITION_UNKNOWN:
            out.append("condition")
        return tuple(out)

    @property
    def complete(self) -> bool:
        return not self.missing

    def key(self) -> tuple:
        """The compared fields (two complete identities are compatible iff equal)."""
        return (self.method, self.instrument, self.condition, self.spectral_bands,
                self.calibration_standard)

    def to_dict(self) -> dict:
        return {"identity_version": IDENTITY_VERSION, "method": self.method,
                "instrument": self.instrument, "condition": self.condition,
                "spectral": _spectral_summary(self.spectral_bands),
                "calibration_standard": self.calibration_standard,
                "complete": self.complete, "missing": list(self.missing),
                "origin": self.origin, "has_record": self.origin == ORIGIN_RECORD,
                "historical": self.origin == ORIGIN_SOL_HEADER,
                "provenance": self.provenance.to_dict()}


def identity_from_dict(d: Mapping) -> MeasurementIdentity:
    """Compatibility identity back from :meth:`MeasurementIdentity.to_dict` (a build
    manifest). Provenance is not rebuilt. Raises ValueError when the dict is malformed."""
    if not isinstance(d, Mapping) or d.get("identity_version") != IDENTITY_VERSION:
        raise ValueError("not a measurement identity (identity_version)")
    method, inst, cond = d.get("method"), d.get("instrument"), d.get("condition")
    if method is not None and method not in METHODS:
        raise ValueError(f"unknown method {method!r}")
    if inst is not None and inst not in INSTRUMENT_MODELS:
        raise ValueError(f"unknown instrument model {inst!r}")
    if cond not in CONDITIONS:
        raise ValueError(f"unknown condition {cond!r}")
    spec = d.get("spectral")
    try:
        bands = tuple(float(x) for x in spec["bands"]) if spec else ()
    except (KeyError, TypeError, ValueError):
        raise ValueError("unreadable spectral bands") from None
    calstd = d.get("calibration_standard")
    if calstd is not None and not isinstance(calstd, str):
        raise ValueError("calibration_standard must be a string or null")
    origin = d.get("origin")
    if origin not in (ORIGIN_RECORD, ORIGIN_SOL_HEADER, ORIGIN_TI3_HEADER):
        raise ValueError(f"unknown origin {origin!r}")
    return MeasurementIdentity(method, inst, cond, bands, calstd, origin)


def filter_option_from_argv(argv: Sequence[str]) -> Optional[str]:
    """The chartread ``-F`` value in ``argv`` (``-F x`` or ``-Fx``), None without -F.
    Raises MeasurementRecordError when -F is repeated or has no value."""
    found: list[str] = []
    items = list(argv)
    i = 0
    while i < len(items):
        tok = items[i]
        if tok == "-F":
            if i + 1 >= len(items):
                raise _malformed("-F without a value in argv")
            found.append(items[i + 1])
            i += 2
            continue
        if tok.startswith("-F") and len(tok) > 2:
            found.append(tok[2:])
        i += 1
    if len(found) > 1:
        raise _malformed(f"-F given {len(found)} times in argv")
    return found[0] if found else None


def _header_condition(header: Ti3Header) -> Optional[str]:
    raw = header.keywords.get("INSTRUMENT_FILTER")
    if raw is None:
        return None
    cond = TI3_INSTRUMENT_FILTERS.get(raw.strip().upper())
    if cond is None:
        raise _unsupported(f"unknown INSTRUMENT_FILTER {raw!r} in the ti3")
    return cond


def identity_from_header(header: Ti3Header, *, ti3_sha256: Optional[str] = None) -> MeasurementIdentity:
    """Identity of a ti3 WITHOUT measurement record (historical SOL scans, files dropped
    by hand). Nothing is guessed: an unrecognised or ambiguous instrument stays None, an
    unrecognised INSTRUMENT_FILTER makes the condition unknown."""
    reported = header.keywords.get("TARGET_INSTRUMENT")
    bands = spectral_bands(header.data_format)
    calstd = header.keywords.get("DEVCALSTD") or None
    raw_filter = header.keywords.get("INSTRUMENT_FILTER")
    filt = TI3_INSTRUMENT_FILTERS.get(raw_filter.strip().upper()) if raw_filter is not None else None
    prov = Provenance(reported_instrument=reported, ti3_sha256=ti3_sha256)
    if reported == SOL_TARGET_INSTRUMENT and raw_filter is None:
        return MeasurementIdentity(METHOD_SOL, SOL_MODEL, CONDITION_FIXED, bands, calstd,
                                   ORIGIN_SOL_HEADER, prov)
    model = next((m for m in INSTRUMENT_MODELS.values()
                  if m.method == METHOD_EXTERNAL and reported in m.reported_names), None)
    if filt is not None:
        condition = filt
    elif raw_filter is not None or model is None or model.filter_selectable:
        condition = CONDITION_UNKNOWN        # M0 and M1 leave no trace in the ti3
    else:
        condition = CONDITION_FIXED
    if model is None:
        return MeasurementIdentity(None, None, condition, bands, calstd, ORIGIN_TI3_HEADER, prov)
    instrument = model.key if model.header_identifies else None
    return MeasurementIdentity(METHOD_EXTERNAL, instrument, condition, bands, calstd,
                               ORIGIN_TI3_HEADER, prov)


# ── measurement record ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class MeasurementRecord:
    ti3_sha256: str
    ti3_name: Optional[str]
    method: str
    instrument: str
    serial: Optional[str]
    tool: Optional[str]
    argv: Optional[tuple[str, ...]]
    measured_at: Optional[str]
    software: Optional[Mapping[str, str]]
    chart_id: Optional[str]
    raw_files: tuple[Mapping[str, str], ...]


def _opt_str(d: Mapping, key: str, where: str) -> Optional[str]:
    v = d.get(key)
    if v is not None and not isinstance(v, str):
        raise _malformed(f"{where}.{key} must be a string or null")
    return v


def parse_record(data) -> MeasurementRecord:
    """Validates a measurement record (the parsed JSON). Raises MeasurementRecordError."""
    if not isinstance(data, Mapping):
        raise _malformed("the record is not a JSON object")
    if data.get("schema") != RECORD_SCHEMA:
        raise _malformed(f"schema must be {RECORD_SCHEMA!r}")
    ver = data.get("schema_version")
    if not isinstance(ver, int) or isinstance(ver, bool):
        raise _malformed("schema_version must be an integer")
    if ver != RECORD_SCHEMA_VERSION:
        raise _unsupported(f"schema_version {ver} is not supported (expected {RECORD_SCHEMA_VERSION})")
    ti3 = data.get("ti3")
    if not isinstance(ti3, Mapping) or not isinstance(ti3.get("sha256"), str) \
            or not _SHA256.match(ti3["sha256"]):
        raise _malformed("ti3.sha256 must be a lowercase hex sha256")
    method = data.get("method")
    if method not in METHODS:
        raise _malformed(f"method must be one of {list(METHODS)}")
    inst = data.get("instrument")
    if not isinstance(inst, Mapping) or not isinstance(inst.get("model"), str):
        raise _malformed("instrument.model is required")
    model = INSTRUMENT_MODELS.get(inst["model"])
    if model is None:
        raise _unsupported(f"instrument model {inst['model']!r} is not supported")
    if model.method != method:
        raise _malformed(f"instrument {model.key!r} is not a {method!r} instrument")
    acq = data.get("acquisition") or {}
    if not isinstance(acq, Mapping):
        raise _malformed("acquisition must be an object")
    argv = acq.get("argv")
    if argv is not None:
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise _malformed("acquisition.argv must be a non-empty list of strings")
        argv = tuple(argv)
    tool = _opt_str(acq, "tool", "acquisition")
    if method == METHOD_EXTERNAL:
        if tool != "chartread":
            raise _unsupported(f"external tool {tool!r} is not supported (chartread only)")
        if argv is None:
            raise _malformed("an external record must keep the executed argv")
        check_external_argv(argv)
    software = acq.get("software")
    if software is not None and (not isinstance(software, Mapping)
                                 or not all(isinstance(v, (str, type(None))) for v in software.values())):
        raise _malformed("acquisition.software must map names to version strings")
    chart = data.get("chart") or {}
    if not isinstance(chart, Mapping):
        raise _malformed("chart must be an object")
    raw = data.get("raw_files") or []
    if not isinstance(raw, list):
        raise _malformed("raw_files must be a list")
    raw_files = []
    for r in raw:
        if not isinstance(r, Mapping) or not isinstance(r.get("path"), str) \
                or not isinstance(r.get("sha256"), str) or not _SHA256.match(r["sha256"]):
            raise _malformed("each raw_files entry needs a path and a sha256")
        raw_files.append({"path": r["path"], "sha256": r["sha256"]})
    return MeasurementRecord(
        ti3_sha256=ti3["sha256"], ti3_name=_opt_str(ti3, "name", "ti3"), method=method,
        instrument=model.key, serial=_opt_str(inst, "serial", "instrument"), tool=tool,
        argv=argv, measured_at=_opt_str(acq, "measured_at", "acquisition"),
        software=dict(software) if software else None,
        chart_id=_opt_str(chart, "chart_id", "chart"), raw_files=tuple(raw_files))


def identity_from_record(header: Ti3Header, record: MeasurementRecord, *, ti3_sha256: str,
                         record_sha256: Optional[str] = None,
                         chart_id: Optional[str] = None) -> MeasurementIdentity:
    """Identity of a ti3 published with its record. Every fact is cross-checked with the
    ti3; any contradiction raises MeasurementRecordError (measurement_record_mismatch)."""
    if record.ti3_sha256 != ti3_sha256:
        raise _mismatch("the ti3 is not the one the record was written for (sha256 differs)")
    if chart_id is not None and record.chart_id is not None and record.chart_id != chart_id:
        raise _mismatch(f"the record belongs to chart {record.chart_id}, not {chart_id}")
    model = INSTRUMENT_MODELS[record.instrument]
    reported = header.keywords.get("TARGET_INSTRUMENT")
    if reported not in model.reported_names:
        raise _mismatch(f"the ti3 reports {reported!r}, not a {model.key} instrument",
                        reported=reported)
    from_ti3 = _header_condition(header)
    opt = filter_option_from_argv(record.argv) if record.argv is not None else None
    if opt is not None:
        if not model.filter_selectable:
            raise _mismatch(f"-F {opt} given to {model.key}, which has no selectable filter")
        cond = CHARTREAD_FILTERS.get(opt)
        if cond is None:
            raise _malformed(f"unknown chartread filter -F {opt}")
        if from_ti3 is not None and from_ti3 != cond:
            raise _mismatch(f"argv says -F {opt} ({cond}) but the ti3 says {from_ti3}")
        if from_ti3 is None and cond in TI3_INSTRUMENT_FILTERS.values():
            raise _mismatch(f"-F {opt} ({cond}) but the ti3 has no INSTRUMENT_FILTER")
    elif from_ti3 is not None:
        cond = from_ti3
    elif model.filter_selectable:
        cond = CONDITION_UNKNOWN
    else:
        cond = CONDITION_FIXED
    if cond not in model.conditions and cond != CONDITION_UNKNOWN:
        raise _unsupported(f"condition {cond} is not offered by {model.key}")
    prov = Provenance(reported_instrument=reported, serial=record.serial, argv=record.argv,
                      filter_option=opt, measured_at=record.measured_at, software=record.software,
                      chart_id=record.chart_id, raw_files=record.raw_files,
                      record_sha256=record_sha256, ti3_sha256=ti3_sha256)
    return MeasurementIdentity(record.method, model.key, cond, spectral_bands(header.data_format),
                               header.keywords.get("DEVCALSTD") or None, ORIGIN_RECORD, prov)


def record_path(ti3_path: Path) -> Path:
    """``measurements/<stem>.json`` next to ``measurements/<stem>.ti3``."""
    p = Path(ti3_path)
    return p.with_name(p.stem + RECORD_SUFFIX)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity_from_bytes(ti3_bytes: bytes, record_bytes: Optional[bytes], *,
                        chart_id: Optional[str] = None) -> MeasurementIdentity:
    """Identity of a ti3 given its bytes and those of its record (None = no record).
    Raises ValueError for an unreadable ti3, MeasurementRecordError for a bad record."""
    header = read_ti3_header(ti3_bytes.decode("ascii", errors="replace"))
    ti3_sha = _sha256(ti3_bytes)
    if record_bytes is None:
        return identity_from_header(header, ti3_sha256=ti3_sha)
    try:
        data = json.loads(record_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise _malformed(f"the record is not valid JSON ({e})") from None
    return identity_from_record(header, parse_record(data), ti3_sha256=ti3_sha,
                                record_sha256=_sha256(record_bytes), chart_id=chart_id)


def _chart_id_of(ti3_path: Path) -> Optional[str]:
    p = Path(ti3_path)
    return p.parent.parent.name if p.parent.name == "measurements" else None


def read_identity(ti3_path: Path) -> MeasurementIdentity:
    """Identity of ``measurements/<stem>.ti3`` (+ its record when present)."""
    p = Path(ti3_path)
    rec = record_path(p)
    return identity_from_bytes(p.read_bytes(), rec.read_bytes() if rec.is_file() else None,
                               chart_id=_chart_id_of(p))


def describe_identity(ti3_path: Path) -> dict:
    """``identity.to_dict()`` or ``{"error": {code, message}}`` — never raises (display)."""
    try:
        return read_identity(ti3_path).to_dict()
    except MeasurementRecordError as e:
        return {"error": {"code": e.code, "message": e.message}}
    except (OSError, ValueError) as e:
        return {"error": {"code": "ti3_unreadable", "message": str(e)}}


# ── compatibility ───────────────────────────────────────────────────────────
COMPATIBLE = "compatible"
INCOMPATIBLE = "incompatible"
INSUFFICIENT = "insufficient"


@dataclass(frozen=True)
class Difference:
    code: str              # method_differs | instrument_differs | condition_differs |
    #                        spectral_differs | calibration_standard_differs |
    #                        method_unknown | instrument_unknown | condition_unknown |
    #                        calibration_standard_unknown
    field: str
    a: object
    b: object

    def to_dict(self) -> dict:
        def norm(v):
            return _spectral_summary(v) if self.field == "spectral_bands" else v
        return {"code": self.code, "field": self.field, "a": norm(self.a), "b": norm(self.b)}


@dataclass(frozen=True)
class Compatibility:
    status: str                              # COMPATIBLE | INCOMPATIBLE | INSUFFICIENT
    differences: tuple[Difference, ...] = ()

    @property
    def compatible(self) -> bool:
        return self.status == COMPATIBLE


def compare_identities(a: MeasurementIdentity, b: MeasurementIdentity) -> Compatibility:
    """THE compatibility rule. Incompatible as soon as one KNOWN field differs; otherwise
    insufficient when a field is unknown on either side; otherwise compatible. Unknown
    values are never assimilated to known ones (``unknown`` ≠ M0, a missing DEVCALSTD ≠
    XRGA)."""
    hard: list[Difference] = []
    soft: list[Difference] = []
    for fld, code in (("method", "method"), ("instrument", "instrument")):
        va, vb = getattr(a, fld), getattr(b, fld)
        if va is not None and vb is not None:
            if va != vb:
                hard.append(Difference(f"{code}_differs", fld, va, vb))
        else:
            soft.append(Difference(f"{code}_unknown", fld, va, vb))
    if CONDITION_UNKNOWN in (a.condition, b.condition):
        soft.append(Difference("condition_unknown", "condition", a.condition, b.condition))
    elif a.condition != b.condition:
        hard.append(Difference("condition_differs", "condition", a.condition, b.condition))
    if a.spectral_bands != b.spectral_bands:
        hard.append(Difference("spectral_differs", "spectral_bands", a.spectral_bands, b.spectral_bands))
    ca, cb = a.calibration_standard, b.calibration_standard
    if ca != cb:
        if ca is not None and cb is not None:
            hard.append(Difference("calibration_standard_differs", "calibration_standard", ca, cb))
        else:
            soft.append(Difference("calibration_standard_unknown", "calibration_standard", ca, cb))
    if hard:
        return Compatibility(INCOMPATIBLE, tuple(hard + soft))
    if soft:
        return Compatibility(INSUFFICIENT, tuple(soft))
    return Compatibility(COMPATIBLE)


@dataclass(frozen=True)
class CombinationDecision:
    """Whether a set of readings may be combined (averaged or concatenated)."""
    status: str
    members: tuple[tuple[str, MeasurementIdentity], ...]
    conflicts: tuple[tuple[str, str, Compatibility], ...] = ()   # (label a, label b, result)

    @property
    def combined(self) -> bool:
        return len(self.members) > 1

    @property
    def compatible(self) -> bool:
        return self.status == COMPATIBLE

    def to_dict(self) -> dict:
        return {"status": self.status, "combined": self.combined,
                "members": [{"label": lbl, "identity": ident.to_dict()} for lbl, ident in self.members],
                "conflicts": [{"a": la, "b": lb, "status": c.status,
                               "differences": [d.to_dict() for d in c.differences]}
                              for la, lb, c in self.conflicts]}


def check_combination(members: Sequence[tuple[str, MeasurementIdentity]]) -> CombinationDecision:
    """Every member compared with the first one (the rule is an equality on known
    fields). A single member is trivially compatible (nothing is combined)."""
    members = tuple(members)
    if len(members) < 2:
        return CombinationDecision(COMPATIBLE, members)
    ref_label, ref = members[0]
    conflicts = tuple((ref_label, label, c) for label, c in
                      ((label, compare_identities(ref, ident)) for label, ident in members[1:])
                      if not c.compatible)
    statuses = {c.status for _, _, c in conflicts}
    status = (INCOMPATIBLE if INCOMPATIBLE in statuses else
              INSUFFICIENT if statuses else COMPATIBLE)
    return CombinationDecision(status, members, conflicts)


# ── chart measurement contract ──────────────────────────────────────────────
LAYOUT_HONEYCOMB = "honeycomb"
LAYOUT_PRINTTARG = "printtarg"


def chart_layout(desc: Mapping) -> str:
    """``layout`` of a chart.json; absent = honeycomb (every chart before printtarg)."""
    return (desc or {}).get("layout") or LAYOUT_HONEYCOMB


def expected_measurement(desc: Mapping) -> dict:
    """How a chart is meant to be measured, from its chart.json only.

    honeycomb → the built-in scan (SOL), on the printer families
    ``printer_model.scan_supported`` allows; printtarg → chartread with the instrument
    printtarg laid it out for (v1: that model only), no printer needed. An unknown
    layout is reported as such, never guessed."""
    from .printer_model import FAMILY_OTHER, FAMILY_Z9, FAMILY_Z9_PRO, scan_supported
    desc = desc or {}
    layout = chart_layout(desc)
    geom = desc.get("geometry") or {}
    base = {"layout": layout, "roll_layout": desc.get("roll_layout"),
            "patch_count": geom.get("patch_count"), "n_strips": geom.get("total_strips")}
    if layout == LAYOUT_HONEYCOMB:
        return {**base, "method": METHOD_SOL, "instruments": [SOL_MODEL], "requires_printer": True,
                "printer_families": [f for f in (FAMILY_Z9, FAMILY_Z9_PRO, FAMILY_OTHER)
                                     if scan_supported(f)]}
    if layout == LAYOUT_PRINTTARG:
        key = (desc.get("instrument") or {}).get("key")
        model = INSTRUMENT_MODELS.get(key)
        ok = model is not None and model.method == METHOD_EXTERNAL
        return {**base, "method": METHOD_EXTERNAL, "instruments": [key] if ok else [],
                "requires_printer": False, "printer_families": None,
                "instrument_unknown": None if ok else key}
    return {**base, "method": None, "instruments": [], "requires_printer": None,
            "printer_families": None}
