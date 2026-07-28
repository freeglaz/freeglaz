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

"""
freeglaz — ICC profile comparator.

Exposes ``compare_profiles(paths, ti3_paths=None, with_curvature=False)``
which returns a structured dictionary comparing 2+ ICC profiles:
  - ICC identity (size, version, class, color space, PCS, description,
    guessed type HP factory / custom / Argyll, ...)
  - whitepoint / blackpoint Lab D50
  - gamut volumes (perceptual / relative / saturation / absolute) via
    lcms2 + convex hull (33³), reuses ``InspectOps.compute_volumes``
  - LUT A2B0 structure (grid size, covered Lab ranges)
  - LUT curvature [EXPERIMENTAL] if ``with_curvature=True``
  - profcheck CIEDE2000 self-consistency if ``ti3_paths`` provided

────────────────────────────────────────────────────────────────────────
⚠ EPISTEMIC — the CURVATURE metric is LABELED EXPERIMENTAL: not yet
validated against an independent spectrophotometer. It measures the
SMOOTHNESS of the LUT, NOT colorimetric ACCURACY. Not to be confused
with a spectro validation. See ``profile_curvature`` module +
``Docs/Note Analyseur Courbure Profil.md``.

The factual metrics (gamut, WP/BP, ΔE profcheck) are, by contrast,
solid and usable as such.
────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import hashlib
import os
import re
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from . import profile_curvature as _curv
from .inspect import HpProprietaryDecoder, InspectOps


__all__ = [
    "compare_profiles",
    "ProfileCompareEntry",
    "CURVATURE_EXPERIMENTAL_NOTICE",
]


# Broadcast as-is by every layer (CLI, API, UI) — a single label.
CURVATURE_EXPERIMENTAL_NOTICE = (
    "EXPERIMENTAL — curvature measures the SMOOTHNESS of the LUT, NOT its "
    "colorimetric ACCURACY. Not yet validated against an independent "
    "spectrophotometer. To be interpreted as a relative qualitative "
    "indicator, never as proof of accuracy."
)


@dataclass
class ProfileCompareEntry:
    """Per-profile block in the result of :func:`compare_profiles`."""

    # Minimal identity
    path: str = ""
    file_size: int = 0

    # Colorimetric content signature
    # (md5 hex 16 chars of the concatenated A2B0 + B2A0 + wtpt tags). Two
    # profiles with the SAME signature have the same transformation LUT
    # and will necessarily yield the same metrics (gamut, WP, ranges).
    # Possible case: several nominally different "papers" actually
    # share the same factory colorimetric profile
    # (only the ICC description differs). Lets the UI
    # detect and flag colorimetric duplicates instead of
    # letting the user believe there is a comparison bug.
    # Value "" if none of the signature tags is readable.
    content_signature: str = ""

    # ICC header
    icc_version: str = ""
    device_class: str = ""
    device_class_label: str = ""
    color_space: str = ""
    pcs: str = ""
    cmm: str = ""
    description: str = ""
    profile_type_guess: str = ""

    # WP / BP in Lab D50
    whitepoint_lab: list = field(default_factory=list)
    blackpoint_lab: list = field(default_factory=list)

    # Gamut (dict as returned by InspectOps / GamutAnalyzer)
    gamut: dict = field(default_factory=dict)

    # LUT A2B0 structure (None if tag absent or type unsupported)
    lut: Optional[dict] = None

    # Curvature (None if with_curvature=False or LUT unreadable)
    # The dict always carries the key ``experimental: True`` + a ``notice``.
    curvature: Optional[dict] = None

    # profcheck CIEDE2000 (None if ti3 absent or profcheck unavailable)
    profcheck: Optional[dict] = None

    def to_dict(self):
        return asdict(self)


# ─── Internal helpers ────────────────────────────────────────────────


def _content_signature(path: Path) -> str:
    """Colorimetric signature of the profile: md5 hex (16 chars) of the
    bytes of the concatenated A2B0 + B2A0 + wtpt tags.

    Why these 3 tags: A2B0 is the device→PCS LUT (the gamut), B2A0
    the inverse PCS→device LUT, wtpt the whitepoint. If two profiles have
    these 3 tags identical byte for byte, they are strictly
    equivalent colorimetrically (same gamut, same Lab ranges,
    same WP). This is exactly the case where the comparison "compares
    nothing" without there being a bug — the UI must flag it.

    Documented edge case:
    several Z9 factory profiles named differently share the
    same A2B0/B2A0/wtpt byte for byte; only ``desc`` differs. The
    Z9 firmware serves the same colorimetric content under different
    names — the signature identifies them as duplicates.

    Returns "" if the file is unreadable or has none of the 3 tags.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if len(data) < 132 or data[36:40] != b"acsp":
        return ""
    tags = HpProprietaryDecoder.parse_icc_tags(data)
    sig_signatures = (b"A2B0", b"B2A0", b"wtpt")
    h = hashlib.md5()
    found_any = False
    for sig in sig_signatures:
        for t in tags:
            if t["sig"].encode("ascii", errors="replace") == sig:
                off = t["offset"]
                size = t["size"]
                if 0 <= off and off + size <= len(data):
                    h.update(sig)
                    h.update(data[off : off + size])
                    found_any = True
                break
    if not found_any:
        return ""
    return h.hexdigest()[:16]


def _lut_summary(path: Path) -> Optional[dict]:
    """Summarize a profile's A2B0 LUT: grid_size + covered Lab ranges.

    Returns ``None`` if there is no A2B0 tag, or an unsupported type (the
    curvature parser only handles ``mft2``). Without raising — structural
    comparison must remain possible even for v4 mAB profiles that
    our curvature cannot analyze.
    """
    try:
        g, L, a, b = _curv.load_a2b_clut(path)
    except (ValueError, struct.error, OSError, IndexError):
        return None
    return {
        "grid_size": int(g),
        "lab_l_range": [float(L.min()), float(L.max())],
        "lab_a_range": [float(a.min()), float(a.max())],
        "lab_b_range": [float(b.min()), float(b.max())],
        "type": "mft2",
    }


def _curvature_summary(path: Path) -> Optional[dict]:
    """Curvature statistics for a profile (EXPERIMENTAL).

    Returns ``None`` if the LUT is not readable by the curvature module.
    Always labeled ``experimental=True`` + ``notice`` when returned,
    to avoid any silent consumption by an upper layer.
    """
    try:
        g_native, g_eff, _field, stats = _curv.analyze_single(path)
    except (ValueError, struct.error, OSError, IndexError) as e:
        return {
            "experimental": True,
            "notice": CURVATURE_EXPERIMENTAL_NOTICE,
            "status": "unavailable",
            "reason": str(e),
        }
    return {
        "experimental": True,
        "notice": CURVATURE_EXPERIMENTAL_NOTICE,
        "status": "ok",
        "grid_native": int(g_native),
        "grid_effective": int(g_eff),
        "moy": stats["moy"],
        "med": stats["med"],
        "p95": stats["p95"],
        "max": stats["mx"],
        "weight_l": 2.0,
    }


_PROFCHECK_RE = re.compile(
    r"errors\(CIEDE2000\):\s*max\.\s*=\s*([\d.]+),\s*"
    r"avg\.\s*=\s*([\d.]+),\s*RMS\s*=\s*([\d.]+)"
)

# Per-patch line of `profcheck -v2 -k -s`:
#   [ΔE₀₀] ID: R G B -> L_obtained a b should be L_target a b
# device in 0..1 (×255 = canonical 8-bit), obtained Lab (profile) vs target (measurement).
_PROFCHECK_PATCH_RE = re.compile(
    r"^\[\s*([\d.]+)\]\s+(\d+):\s+"
    r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+->\s+"
    r"(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+should be\s+"
    r"(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)",
    re.MULTILINE,
)


def _parse_profcheck_patches(output: str) -> list:
    """Extract the per-patch list (profcheck -v2 -s), sorted worst→best.

    Each patch: id, ΔE₀₀, 8-bit RGB (device ×255), target Lab (measured) and
    obtained Lab (profile output). Presentation only — no influence on the
    verdict (in-house grid) nor on the max/avg/RMS summary."""
    patches = []
    for mm in _PROFCHECK_PATCH_RE.finditer(output):
        g = mm.groups()
        de = float(g[0])
        rgb = [round(float(g[i]) * 255) for i in (2, 3, 4)]
        lab_ach = [round(float(g[i]), 3) for i in (5, 6, 7)]
        lab_tgt = [round(float(g[i]), 3) for i in (8, 9, 10)]
        patches.append({
            "id": int(g[1]),
            "de2000": round(de, 4),
            "rgb": rgb,
            "lab_target": lab_tgt,
            "lab_achieved": lab_ach,
        })
    patches.sort(key=lambda p: p["de2000"], reverse=True)
    return patches


def _quality_label(avg: float, peak: float) -> str:
    """Same grid as cmd_profile_check for consistent interpretation."""
    if avg < 0.5 and peak < 3:
        return "Excellent"
    if avg < 1.0 and peak < 5:
        return "Good"
    if avg < 2.0:
        return "Fair"
    return "Poor"


def _profcheck_run(icc_path: Path, ti3_path: Path,
                   timeout: int = 120, *, detailed: bool = False) -> Optional[dict]:
    """Run Argyll profcheck and parse the CIEDE2000 ΔE.

    Reproduces the logic of ``cmd_profile_check`` (freeglaz line 3406+)
    so that a given (.icc, .ti3) pair yields the same verdict on the CLI
    side and on the ``profile compare`` side.

    ``detailed=True`` adds ``-v2 -s`` → per-patch list (key ``patches``,
    sorted worst→best) in addition to the summary. The summary and the verdict
    (in-house grid) are identical in both modes (presentation only extra).
    """
    from .argyll import find_argyll_binary
    _profcheck = find_argyll_binary("profcheck")
    if _profcheck is None:
        return {"status": "unavailable", "reason": "profcheck not found (Argyll CMS)"}
    if not ti3_path.exists():
        return {"status": "unavailable", "reason": f"ti3 not found: {ti3_path}"}
    cmd = [_profcheck, "-k"]
    if detailed:
        cmd += ["-v2", "-s"]
    cmd += [str(ti3_path), str(icc_path)]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "reason": f">{timeout}s"}
    except Exception as e:
        return {"status": "error", "reason": str(e)}

    output = (r.stdout or "") + (r.stderr or "")
    m = _PROFCHECK_RE.search(output)
    if not m:
        return {
            "status": "parse_failed",
            "reason": "unexpected profcheck output",
            "raw_head": output[:200],
        }
    peak = float(m.group(1))
    avg = float(m.group(2))
    rms = float(m.group(3))
    result = {
        "status": "ok",
        "ti3_path": str(ti3_path),
        "peak_de2000": peak,
        "avg_de2000": avg,
        "rms_de2000": rms,
        "quality": _quality_label(avg, peak),
    }
    if detailed:
        patches = _parse_profcheck_patches(output)
        result["patches"] = patches
        result["n_patches"] = len(patches)
    return result


def _white_point_lab(icc_path: Path) -> Optional[dict]:
    """Lab of the white (wtpt tag) — catch-all anti-bug XYZ ("yellow-warm shift" of
    05/20). Reference points taken from cmd_profile_check: for a quality photo paper,
    a*≈0±2 and b*≈+1±3 → outside [a∈[-2,2], b∈[-2,4]] = suspect."""
    from .profiling import parse_icc_tags
    try:
        data = icc_path.read_bytes()
    except OSError:
        return None
    for sig, off, _sz in parse_icc_tags(data):
        if sig == "wtpt" and off + 20 <= len(data) and data[off:off + 4] == b"XYZ ":
            X = struct.unpack(">i", data[off + 8:off + 12])[0] / 65536.0
            Y = struct.unpack(">i", data[off + 12:off + 16])[0] / 65536.0
            Z = struct.unpack(">i", data[off + 16:off + 20])[0] / 65536.0

            def _f(t):
                return t ** (1 / 3) if t > (6 / 29) ** 3 else t / (3 * (6 / 29) ** 2) + 4 / 29
            Xn, Yn, Zn = 0.9642, 1.0, 0.8249   # D50
            fy = _f(Y / Yn)
            L, a, b = 116 * fy - 16, 500 * (_f(X / Xn) - fy), 200 * (fy - _f(Z / Zn))
            return {"L": round(L, 2), "a": round(a, 2), "b": round(b, 2),
                    "suspect": not (-2.0 <= a <= 2.0 and -2.0 <= b <= 4.0)}
    return None


def verify_profile_self(icc_path, ti3_path=None, *, timeout: int = 120) -> dict:
    """SELF-CONSISTENCY — profcheck of a profile against ITS creation ti3,
    WITHOUT reprinting. Reuses `_profcheck_run` (profcheck -k CIEDE2000) +
    `_quality_label` (IN-HOUSE grid) + white Lab test (anti-bug XYZ).

    ⚠️ BEST CASE / optimistic: on its OWN patches, profcheck mainly detects
    measurement anomalies (misreads), not the actual predictive quality (= INDEPENDENT
    validation). The ``scope='self'`` + ``caveat`` fields carry this
    warning → the UI must display it (no false confidence).

    ti3 auto-sourced if not provided: measurements embedded in the ICC (targ/CIED tag via
    extract_cgats_from_icc). ``status='no_measurements'`` if the ICC carries none
    (profile built with -nc, or without measurements) → steer toward independent
    validation.
    """
    icc_path = Path(icc_path)
    if not icc_path.exists():
        return {"status": "error", "reason": f"ICC not found: {icc_path}"}
    wp = _white_point_lab(icc_path)
    tmp = None
    try:
        if ti3_path is None:
            from .profiling import ProfilingOps
            fd, name = tempfile.mkstemp(suffix=".ti3", prefix="freeglaz_verify_")
            os.close(fd)
            tmp = Path(name)
            try:
                ProfilingOps().extract_cgats_from_icc(str(icc_path), output_path=str(tmp))
            except (ValueError, FileNotFoundError):
                return {"status": "no_measurements", "scope": "self",
                        "reason": "the ICC carries no embedded measurements (targ/CIED) — "
                                  "self-consistency check impossible; use the "
                                  "independent validation",
                        "white_point_lab": wp}
            ti3_path = tmp
        pc = _profcheck_run(icc_path, Path(ti3_path), timeout=timeout, detailed=True)
        return {"status": "ok", "scope": "self", "caveat": "auto-coherence",
                "profcheck": pc, "white_point_lab": wp}
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass


def _device_rgb_set(ti3_or_ti1_path) -> set:
    """Set of 8-bit device RGB (r,g,b) of a ti1/ti3 (reuses read_ti1,
    which reads RGB_R/G/B 0-100 → 8-bit on both formats)."""
    from .chart import read_ti1
    return {(r, g, b) for (_sid, r, g, b) in read_ti1(ti3_or_ti1_path)}


def _independence_vs_creation(validation_ti3: Path, profile_icc: Path) -> dict:
    """Checks the NON-OVERLAP of the validation patches vs those of the profile's
    CREATION (sourced from the embedded measurements). Independence is VERIFIABLE, not assumed:
    targen OFPS is deterministic → a validation targen could overlap the creation.

    ``creation_known=False`` if the profile does not carry its measurements (no reference to
    compare against; independence can then only be assumed from the -q distribution)."""
    try:
        val_set = _device_rgb_set(validation_ti3)
    except (ValueError, FileNotFoundError):
        return {"creation_known": None, "reason": "validation ti3 unreadable"}
    n_val = len(val_set)
    from .profiling import ProfilingOps
    fd, name = tempfile.mkstemp(suffix=".ti3", prefix="freeglaz_creation_")
    os.close(fd)
    cre = Path(name)
    try:
        try:
            ProfilingOps().extract_cgats_from_icc(str(profile_icc), output_path=str(cre))
        except (ValueError, FileNotFoundError):
            return {"creation_known": False, "n_validation": n_val,
                    "note": "profile without embedded measurements — overlap not verifiable"}
        cre_set = _device_rgb_set(cre)
    finally:
        try:
            cre.unlink()
        except OSError:
            pass
    n_overlap = len(val_set & cre_set)
    pct = round(100.0 * n_overlap / n_val, 1) if n_val else 0.0
    return {"creation_known": True, "n_validation": n_val,
            "n_creation": len(cre_set), "n_overlap": n_overlap,
            "pct_overlap": pct,
            # safeguard: beyond this threshold, (b) slides toward a disguised (a)
            "compromised": pct > 20.0}


def verify_profile_independent(icc_path, ti3_path, *, timeout: int = 120) -> dict:
    """INDEPENDENT VALIDATION (Mech 1 forward A2B) — profcheck of a profile
    against an INDEPENDENT chart (≠ creation patches), reprinted as raw device and
    re-scanned. This is the REAL predictive accuracy (vs the optimistic self-consistency of (a)).

    ``ti3_path`` = measurements of the validation chart (already printed+scanned via the pipeline).
    Reuses `_profcheck_run(detailed=True)` (parse c.1) → presentation c.2 on the UI side.

    The ``independence`` block cross-checks the non-overlap vs the creation patches
    (sourced from the profile): independence is VERIFIED, not assumed. ``scope='independent'``.

    ⚠️ HONESTY (b) — not a limitation, a FEATURE: (b) reflects the CURRENT PHYSICAL
    state (paper/GE/CLC). If the calibration has changed since profiling, (b) measures
    the current state → detects the DRIFT since the profile creation.
    """
    icc_path = Path(icc_path)
    ti3_path = Path(ti3_path)
    if not icc_path.exists():
        return {"status": "error", "reason": f"ICC not found: {icc_path}"}
    if not ti3_path.exists():
        return {"status": "error", "reason": f"validation ti3 not found: {ti3_path}"}
    pc = _profcheck_run(icc_path, ti3_path, timeout=timeout, detailed=True)
    return {"status": "ok", "scope": "independent", "caveat": "independent-validation",
            "profcheck": pc, "white_point_lab": _white_point_lab(icc_path),
            "independence": _independence_vs_creation(ti3_path, icc_path)}


# ─── Public entry point ─────────────────────────────────────────────────


def compare_profiles(paths, ti3_paths=None, with_curvature: bool = False) -> dict:
    """Compare 2+ ICC profiles structurally (and qualitatively if requested).

    :param paths: iterable of ``Path`` or ``str`` paths to the .icc files.
        At least 2 profiles are expected on the UX side (the code does not enforce it).
    :param ti3_paths: optional, iterable of ``.ti3`` paths aligned with
        ``paths`` (one per profile, ``None``/``""`` to omit). If provided,
        ``profcheck`` is run for each available (icc, ti3) pair.
    :param with_curvature: if True, compute the curvature stats of the
        A2B0 LUT for each profile. The result is ALWAYS labeled
        ``experimental: True`` + ``notice`` — NEVER present it
        as a validated metric.

    :return: dict ::

        {
            "profiles": [entry_dict, ...],
            "meta": {
                "n_profiles": int,
                "with_curvature": bool,
                "with_profcheck": bool,
            },
            "curvature_notice": str (present iff with_curvature),
        }
    """
    paths = [Path(p) for p in paths]
    if ti3_paths is None:
        ti3_paths = [None] * len(paths)
    else:
        ti3_paths = list(ti3_paths)
        # Align with paths (pad with None or truncate)
        if len(ti3_paths) < len(paths):
            ti3_paths += [None] * (len(paths) - len(ti3_paths))
        else:
            ti3_paths = ti3_paths[:len(paths)]

    ops = InspectOps()
    entries: list[dict] = []
    profcheck_used = False

    for icc_path, ti3 in zip(paths, ti3_paths):
        insp = ops.inspect_icc_file(icc_path)
        entry = ProfileCompareEntry(
            path=str(icc_path),
            file_size=insp.file_size,
            content_signature=_content_signature(icc_path),
            icc_version=insp.icc_version,
            device_class=insp.device_class,
            device_class_label=insp.device_class_label,
            color_space=insp.color_space,
            pcs=insp.pcs,
            cmm=insp.cmm,
            description=insp.description,
            profile_type_guess=insp.profile_type_guess,
            whitepoint_lab=list(insp.whitepoint_lab),
            blackpoint_lab=list(insp.blackpoint_lab),
            gamut=dict(insp.gamut),
            lut=_lut_summary(icc_path),
        )

        if with_curvature:
            entry.curvature = _curvature_summary(icc_path)

        if ti3 not in (None, ""):
            ti3_path = Path(ti3)
            entry.profcheck = _profcheck_run(icc_path, ti3_path)
            profcheck_used = True

        entries.append(entry.to_dict())

    # Colorimetric duplicate detection: indices of the profiles
    # sharing the same content_signature. Pre-chews the work for
    # the UI, which can display a banner "these profiles have the same
    # colorimetric LUT" instead of letting the user believe the
    # comparison is buggy when all the columns are identical.
    duplicate_groups: list[dict] = []
    sig_to_indices: dict[str, list[int]] = {}
    for i, e in enumerate(entries):
        sig = e.get("content_signature") or ""
        if not sig:
            continue
        sig_to_indices.setdefault(sig, []).append(i)
    for sig, idxs in sig_to_indices.items():
        if len(idxs) >= 2:
            duplicate_groups.append({
                "signature": sig,
                "profile_indices": idxs,
                "n_profiles": len(idxs),
            })

    out = {
        "profiles": entries,
        "meta": {
            "n_profiles": len(entries),
            "with_curvature": bool(with_curvature),
            "with_profcheck": profcheck_used,
            "duplicate_signature_groups": duplicate_groups,
            "has_duplicates": bool(duplicate_groups),
        },
    }
    if with_curvature:
        out["curvature_notice"] = CURVATURE_EXPERIMENTAL_NOTICE
    return out
