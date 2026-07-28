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

"""Profile refinement "Redo with Argyll" (Part B).

Assembles the existing building blocks — ``ProfilingOps.extract_cgats_from_icc``
(measurements regenerated from the base .icc, firmware CGATS.17 or Argyll CTI3)
then ``ProfilingOps.build_profile`` (colprof) — to produce a VARIANT profile
from the measurements already embedded in a base profile, without hardware.

No durable ti3 storage: the ti3 is regenerated in scratch, consumed by
colprof, then discarded with the working directory by the caller.

Two modes of colprof parameters:
  - Simple: 6 presets = {qm, qh} × {r0.5, r1.0, r2.0}, default qm-r1.0.
  - Advanced: raw flags provided by the caller (quality -q, smoothing -r, table,
    source gamut -S reserved for advanced).

The preset labels are BARE FLAGS (no marketing gloss). The descriptions of
-q and -r are the OFFICIAL Argyll colprof doc (captured as-is), not an
invented gloss.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass


# ─── Official Argyll colprof doc (captured from `colprof` usage, v3.5.0) ────
#
# Exposed as-is in the UI (tooltip on flag hover). DO NOT gloss.

ARGYLL_FLAG_DOC = {
    "-q": "Quality - Low, Medium (def), High, Ultra",
    "-r": "Average deviation of device+instrument readings as a "
          "percentage (default 0.50%)",
    "-S": "Apply gamut mapping to output profile perceptual and "
          "saturation B2A table, or expansion percentage",
}


# ─── Simple mode presets ────────────────────────────────────────────────────
#
# 6 presets = {qm, qh} × {r0.5, r1.0, r2.0}. Default: qm-r1.0 (empirical
# sweet spot established for a 464-patch photo profile). Display = bare flags.

@dataclass(frozen=True)
class RefinePreset:
    """A simple-mode colprof preset.

    :param key: stable identifier (e.g. 'qm-r1.0').
    :param quality: -q letter (l|m|h|u).
    :param avgdev: -r value (average deviation in %).
    :param is_default: True for the default preset.
    """
    key: str
    quality: str
    avgdev: str
    is_default: bool = False

    @property
    def label(self) -> str:
        return self.key

    @property
    def colprof_flags(self) -> list[str]:
        # NO -nc: we WANT colprof to embed the measurements (targ tag) in
        # the variant, so it stays self-sufficient and re-refinable.
        return ["-v", f"-q{self.quality}", f"-r{self.avgdev}"]


_PRESET_DEFS = [
    ("qm-r0.5", "m", "0.5", False),
    ("qm-r1.0", "m", "1.0", True),
    ("qm-r2.0", "m", "2.0", False),
    ("qh-r0.5", "h", "0.5", False),
    ("qh-r1.0", "h", "1.0", False),
    ("qh-r2.0", "h", "2.0", False),
]

REFINE_PRESETS: dict[str, RefinePreset] = {
    key: RefinePreset(key=key, quality=q, avgdev=r, is_default=d)
    for (key, q, r, d) in _PRESET_DEFS
}

DEFAULT_PRESET_KEY = "qm-r1.0"


def list_presets() -> list[dict]:
    """List the presets for the UI (simple mode).

    :return: list of dicts ``{key, quality, avgdev, is_default, flags}`` +
        the official Argyll doc for the -q / -r flags (for tooltip).
    """
    out = []
    for p in REFINE_PRESETS.values():
        out.append({
            "key": p.key,
            "quality": p.quality,
            "avgdev": p.avgdev,
            "is_default": p.is_default,
            "flags": p.colprof_flags,
        })
    return out


# ─── Advanced mode: free-form colprof flag input (validated) ────────────────
#
# The user (Argyll expert) types their flag line. The pipeline injects the
# input (ti3) and the output (.icc) — the user NEVER provides a path. colprof
# is run in direct exec (arg list, never shell=True). The validation is the
# security barrier: we only accept FLAG TOKENS, we refuse any shell
# metacharacter, any path/positional argument, and the flags that break
# self-sufficiency or the plumbing.

# Shell metacharacters forbidden anywhere in the raw input (defense in
# depth: we never use a shell, but their presence = malicious or erroneous
# input → clean rejection).
_SHELL_METACHARS = set(';|&<>`$(){}[]!*?~\n\r\t\\"\'')

# Accepted form of a flag token: -x, --xxx, -qh, -r0.8, -V1.0… (letter/digit,
# dot, sign). No space (tokenized), no path separator.
_FLAG_TOKEN_RE = re.compile(r'^-{1,2}[A-Za-z][A-Za-z0-9._+-]*$')
# Standalone numeric/enum value accepted as an argument of a preceding flag
# (e.g. `-r 0.8`, `-V 2.0`). No path.
_VALUE_TOKEN_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._+-]*$')

# BLOCKED flags — break self-sufficiency or the injected plumbing.
# -nc : "Don't put the input .ti3 data in the profile" → removes the
#       measurements → non-re-refinable variant (banned from the pipeline).
# -O  : output file → the output is injected by the pipeline.
_BLOCKED_FLAGS = {
    "-nc": "removes the embedded measurements (-nc) → breaks self-sufficiency "
           "(the variant would no longer be re-refinable). Forbidden flag.",
    "-O": "sets the output file (-O) → the output is managed by the "
          "pipeline. Forbidden flag.",
}


def parse_advanced_flags(raw: str) -> list[str]:
    """Validate and tokenize a free-form colprof flag line.

    Accepts ONLY flag tokens (and their numeric/enum values). Refuses any
    shell metacharacter, any path or positional argument (files), and the
    forbidden flags (self-sufficiency / injected plumbing).

    :param raw: raw line entered by the user (e.g. "-v -qh -ax -r0.8").
    :return: list of validated tokens, ready for direct exec (never shell).
    :raises ValueError: clear message if the input is invalid or forbidden.
    """
    if raw is None:
        raise ValueError("No flag provided.")
    text = raw.strip()
    if not text:
        raise ValueError("Empty flag line.")
    bad = sorted(c for c in _SHELL_METACHARS if c in text)
    if bad:
        shown = " ".join(repr(c) for c in bad)
        raise ValueError(
            f"Forbidden character(s) in the input: {shown}. "
            f"Only colprof flags are accepted (no shell metacharacter, "
            f"no file path)."
        )
    tokens = text.split()
    for tok in tokens:
        is_flag = _FLAG_TOKEN_RE.match(tok)
        is_value = _VALUE_TOKEN_RE.match(tok)
        if not (is_flag or is_value):
            raise ValueError(
                f"Invalid token: {tok!r}. Expected: a colprof flag (-x, "
                f"--xxx, -qh, -r0.8…) or a numeric value. No path "
                f"or file argument (input/output managed by the pipeline)."
            )
        # Blocked flag (exact form or glued like -ncX)?
        for blocked, why in _BLOCKED_FLAGS.items():
            if tok == blocked or (blocked == "-nc" and tok == "-nc"):
                raise ValueError(why)
    return tokens


def colprof_help(colprof_path: str = "colprof") -> str:
    """Return the real help output of the installed colprof version.

    Runs ``colprof -?`` in direct exec (never shell). Argyll writes the usage
    to stderr and exits with a non-zero code; we capture stdout+stderr and
    treat any non-empty output as a success.

    :raises FileNotFoundError: if colprof is not installed.
    :raises RuntimeError: if colprof returns no usable output.
    """
    from .argyll import resolve_argyll_binary
    _colprof = resolve_argyll_binary(colprof_path)   # raises ArgyllNotFound (actionable message)
    try:
        proc = subprocess.run(
            [_colprof, "-?"],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("colprof -? timed out.")
    out = (proc.stderr or "") + (proc.stdout or "")
    out = out.strip()
    if not out:
        raise RuntimeError("colprof -? returned no output.")
    return out
