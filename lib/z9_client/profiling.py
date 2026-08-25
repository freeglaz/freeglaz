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
freeglaz profiling operations (post-scan).

Full pipeline:
    1. Z9 scan_only -> firmware ICC with CIED tag (embedded CGATS.17)
    2. ProfilingOps.extract_cgats_from_icc(icc) -> Argyll .ti3 file
    3. ProfilingOps.remap_ti3_from_sidecar(ti3, sidecar.json) -> remapped .ti3
       (substitutes HP nominal RGB with the exact freeglaz sidecar RGB)
    4. ProfilingOps.build_profile(ti3_basename, ...) -> final .icc via colprof
    5. ProfilingOps.run_pipeline(...) orchestrates the 3 steps in one call

Architecture:
    - Pure module-level functions (parse_icc_tags, parse_cgats_data, etc.)
    - ProfilingOps class: public API, methods exposed via Z9Client
    - Dataclasses for typed returns
    - `on_step` callback for CLI tracing

Why a remap_ti3?
    The Z9 firmware returns in its CIED tag the nominal RGB of the HP
    template `rgb_7cube_plus` (levels 0/43/85/128/170/213/255), NOT the RGB
    we actually sent (freeglaz levels 0/42/85/127/170/212/255 + linear gray
    ramp of 121 levels instead of HP skin-tone "plus" patches).

    The remap substitutes the nominal RGB with the exact RGB sent (read from
    the JSON sidecar produced by ChartOps), preserving all XYZ measurements +
    spectra. Essential so colprof sees the correct RGB->measured-color
    mapping.
"""

from __future__ import annotations

import json
import logging
import os
import struct
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# Explicit coupling: application version already resolved by printing.py
# (read from the package metadata). Reused to timestamp the provenance of
# profiles (name change freeglaz -> freeglaz). No duplicated logic.
from .printing import _FREEGLAZ_VERSION as _FREEGLAZ_VERSION

logger = logging.getLogger(__name__)

# Expected CLUT grid resolution per colprof -q (Argyll).
# Empirically verified on Z9: -qm/default -> 17^3, -qh -> 33^3.
_QUALITY_EXPECTED_GRID = {"l": 9, "m": 17, "h": 33, "u": 45}


def _a2b0_clut_grid(icc_path: str | Path) -> Optional[int]:
    """Read the number of CLUT grid points from the A2B0 tag (lut16Type 'mft2').

    :return: the grid (e.g. 33 for 33^3) or None if unreadable / unexpected type.
    """
    try:
        d = Path(icc_path).read_bytes()
        n = struct.unpack(">I", d[128:132])[0]
        off = 132
        for _ in range(n):
            sig = d[off:off + 4].decode("latin1", "replace")
            if sig == "A2B0":
                o = struct.unpack(">I", d[off + 4:off + 8])[0]
                if d[o:o + 4] == b"mft2":
                    return d[o + 10]
                return None
            off += 12
    except Exception:
        return None
    return None


def _expected_grid_for_flags(flags: list[str]) -> Optional[int]:
    """Expected CLUT grid based on the -q flag present in the colprof flags."""
    for f in flags:
        if f.startswith("-q") and len(f) >= 3:
            return _QUALITY_EXPECTED_GRID.get(f[2])
    return None


# --- CIE tables - for spectra -> XYZ conversion ---------------------------


# Compiled CIE 1931 2 deg x D50 illuminant table, sampled at 20 nm steps.
# For each wavelength: (x_bar x S x d-lambda, y_bar x S x d-lambda, z_bar x S x d-lambda).
# Allows computing XYZ relative to perfect white Y=100 from 16 spectral bands.
_CIE_D50_20NM = {
    400: (0.0068460, 0.0001915, 0.0324820),
    420: (1.0233200, 0.0304560, 4.9159000),
    440: (3.4351000, 0.2267900, 17.231000),
    460: (3.3568000, 0.6925800, 19.265000),
    480: (1.1542000, 1.6772000, 9.8083000),
    500: (0.0612000, 4.0327000, 3.3964000),
    520: (0.8108000, 9.1011000, 1.0024000),
    540: (3.7720000, 12.394000, 0.2637000),
    560: (8.0530000, 13.477000, 0.0528100),
    580: (12.984000, 12.327000, 0.0233700),
    600: (15.402000, 9.1496000, 0.0116000),
    620: (12.713000, 5.6694000, 0.0029760),
    640: (6.4805000, 2.6294000, 0.0003005),
    660: (2.4767000, 0.9163400, 0.0000000),
    680: (0.6906700, 0.2510700, 0.0000000),
    700: (0.1660700, 0.0599810, 0.0000000),
}


# --- ICC + CGATS parsing --------------------------------------------------


def parse_icc_tags(icc_bytes: bytes) -> list[tuple[str, int, int]]:
    """Read the tag table of an ICC profile.

    :param icc_bytes: full binary content of the ICC file
    :return: list of tuples (signature, offset, size)
    :raises ValueError: if the file is too short or the ICC signature is absent
    """
    if len(icc_bytes) < 132:
        raise ValueError(
            f"File too short to be a valid ICC: {len(icc_bytes)} bytes"
        )

    sig = icc_bytes[36:40]
    if sig != b'acsp':
        raise ValueError(
            f"Invalid ICC signature at offset 36: {sig!r} (expected 'acsp')"
        )

    num_tags = struct.unpack('>I', icc_bytes[128:132])[0]
    tags = []
    for i in range(num_tags):
        offset = 132 + i * 12
        tag_sig = icc_bytes[offset:offset+4].decode('ascii', errors='replace')
        tag_offset = struct.unpack('>I', icc_bytes[offset+4:offset+8])[0]
        tag_size = struct.unpack('>I', icc_bytes[offset+8:offset+12])[0]
        tags.append((tag_sig, tag_offset, tag_size))
    return tags


def extract_cied_text(icc_bytes: bytes, cied_offset: int, cied_size: int) -> str:
    """Extract the CGATS/CTI3 text embedded in a measurements tag.

    The tag starts with 8 bytes of ICC header (type 'text' + reserved), then
    ASCII text terminated by nulls. The body carries the signature
    'CGATS.17' (HP/Z9 firmware profiles) or 'CTI3' (Argyll/freeglaz profiles,
    both in the CIED tag and the targ tag). Both signatures are accepted,
    which allows regenerating the ti3 of a profile already built by colprof.

    :raises ValueError: if no CGATS/CTI3 signature is found
    """
    cied = icc_bytes[cied_offset:cied_offset + cied_size]

    positions = [p for p in (cied.find(b'CGATS'), cied.find(b'CTI3')) if p >= 0]
    if not positions:
        raise ValueError(
            f"No CGATS/CTI3 signature in the tag. "
            f"Hex preview: {cied[:32].hex()}"
        )
    sig_start = min(positions)

    text = cied[sig_start:].decode('ascii', errors='replace')
    text = text.split('\x00')[0].rstrip()
    return text


def parse_cgats_data(cgats_text: str) -> dict:
    """Parse a usable CGATS.17 file (HP firmware) or CTI3 file (Argyll).

    The two dialects differ on the placement of NUMBER_OF_SETS: HP puts it
    in the header (before BEGIN_DATA_FORMAT), Argyll inserts it between
    END_DATA_FORMAT and BEGIN_DATA. The 'await_data' state absorbs these
    intermediate keywords without overwriting the already-captured columns
    line.

    :return: dict {
        'header': {key: value}      -- the header KEYWORDs
        'format': [field names]     -- column order
        'data':   [[str values], ...]
    }
    """
    lines = cgats_text.split('\n')
    header = {}
    fmt: list[str] = []
    data: list[list[str]] = []

    state = 'header'
    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        if state == 'header':
            if line == 'BEGIN_DATA_FORMAT':
                state = 'format'
                continue
            if line == 'BEGIN_DATA':
                state = 'data'
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                key = parts[0]
                val = parts[1].strip().strip('"')
                header[key] = val

        elif state == 'format':
            if line == 'END_DATA_FORMAT':
                state = 'await_data'
                continue
            if line == 'BEGIN_DATA':
                state = 'data'
                continue
            fmt = line.split()

        elif state == 'await_data':
            if line == 'BEGIN_DATA':
                state = 'data'
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                header[parts[0]] = parts[1].strip().strip('"')

        elif state == 'data':
            if line == 'END_DATA':
                break
            tokens = line.split()
            if tokens:
                data.append(tokens)

    # Safeguard: the declared NUMBER_OF_SETS must match the actual number of
    # lines read. A mismatch = truncated/corrupted CGATS (e.g. incomplete
    # scan) - we refuse rather than silently propagate partial measurements.
    declared = header.get('NUMBER_OF_SETS')
    if declared is not None:
        try:
            declared_n = int(declared)
        except ValueError:
            declared_n = None
        if declared_n is not None and declared_n != len(data):
            raise ValueError(
                f"Inconsistent CGATS: NUMBER_OF_SETS declared = {declared_n} "
                f"but {len(data)} data lines read "
                f"(truncated/corrupted scan?)."
            )

    return {'header': header, 'format': fmt, 'data': data}


def spectra_to_xyz_d50(
    spectra_values: list[float],
    wavelengths: list[int],
) -> tuple[float, float, float]:
    """Compute XYZ relative to D50 / CIE 1931 2 deg observer.

    WARNING: FALLBACK ONLY. The `_CIE_D50_20NM` table produces XYZ with a
    systematic bias (white paper Lab(96, +5.8, +5.7) instead of
    (96, 0, +1.4)). Use `apply_spec2cie_xyz_correction()` whenever
    possible (uses the official Argyll tables via spec2cie).

    Y is normalized to 100 for the perfect white diffuser (refl=1 everywhere).

    :param spectra_values: reflectances at the wavelengths
    :param wavelengths: corresponding wavelengths (nm)
    :return: (X, Y, Z) with bias ~dE 6 on a*, dE 4 on b* (to be corrected)
    """
    sum_X = sum_Y = sum_Z = 0.0
    norm_Y = 0.0
    for wl, refl in zip(wavelengths, spectra_values):
        if wl not in _CIE_D50_20NM:
            continue
        xS, yS, zS = _CIE_D50_20NM[wl]
        sum_X += refl * xS
        sum_Y += refl * yS
        sum_Z += refl * zS
        norm_Y += yS

    if norm_Y == 0:
        return (0.0, 0.0, 0.0)

    k = 100.0 / norm_Y
    return (sum_X * k, sum_Y * k, sum_Z * k)


# Perfect white (perfect diffuser) XYZ x100, 2 deg observer, per illuminant - to
# recompute Lab from the 0-100 XYZ.
_ILLUMINANT_WHITE = {
    "D50": (96.422, 100.0, 82.521),
    "D65": (95.047, 100.0, 108.883),
}


def _xyz_to_lab(X: float, Y: float, Z: float, white: tuple) -> tuple:
    """XYZ (0-100) -> CIELAB, white-point ``white`` (0-100). Standard CIE formula."""
    Xn, Yn, Zn = white

    def f(t):
        return t ** (1.0 / 3.0) if t > 0.008856 else 7.787 * t + 16.0 / 116.0

    fx, fy, fz = f(X / Xn), f(Y / Yn), f(Z / Zn)
    return (116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz))


def apply_spec2cie_xyz_correction(
    ti3_path: Path,
    output_path: Path | None = None,
    illuminant: str = "D50",
    observer: str = "1931_2",
) -> Path:
    """Recompute the XYZ of a ti3 via Argyll `spec2cie` (official CIE tables).

    Fixes the bias of the local `_CIE_D50_20NM` table (cf. doc of
    `spectra_to_xyz_d50`). Two-step pipeline:
      1. `spec2cie -i D50 -o 1931_2 input.ti3 tmp.ti3`
      2. Multiply XYZ x 100 (spec2cie outputs in 0-1, Argyll standard expects 0-100)

    The input ti3 must have SPEC_NNN spectra. Existing XYZ will be
    overwritten. Output: ti3 with correct XYZ (bit-perfect Argyll table).

    :param ti3_path: input ti3 (must have spectra)
    :param output_path: output ti3. Default: same path (in-place).
    :param illuminant: spec2cie -i. Default: D50.
    :param observer: spec2cie -o. Default: 1931_2.
    :return: path of the corrected ti3
    :raises FileNotFoundError: if spec2cie absent or ti3 not found
    :raises RuntimeError: if spec2cie fails
    """

    ti3_path = Path(ti3_path)
    if not ti3_path.exists():
        raise FileNotFoundError(f"ti3 not found: {ti3_path}")

    from .argyll import find_argyll_binary
    _spec2cie = find_argyll_binary("spec2cie")
    if _spec2cie is None:
        raise FileNotFoundError(
            "spec2cie not found (Argyll CMS not installed / not resolved). "
            "Argyll-CMS must be installed for the XYZ correction. "
            "Without correction, ICC profiles will have a Lab bias "
            "Δa*≈+6 Δb*≈+4 on the paper white (warm-yellow shift)."
        )

    if output_path is None:
        output_path = ti3_path
    output_path = Path(output_path)

    # Step 1: spec2cie to a temporary file
    tmp_path = output_path.with_suffix('.spec2cie_tmp.ti3')
    try:
        subprocess.run(
            [_spec2cie, "-i", illuminant, "-o", observer,
             str(ti3_path), str(tmp_path)],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"spec2cie failed (exit {e.returncode}): {e.stderr}"
        ) from e

    if not tmp_path.exists():
        raise RuntimeError(f"spec2cie did not produce {tmp_path}")

    # Step 2: multiply XYZ x 100 (0-1 -> 0-100)
    with open(tmp_path) as f:
        content = f.read()

    lines = content.splitlines()
    in_format = False
    data_format = None
    for line in lines:
        if line.strip() == 'BEGIN_DATA_FORMAT':
            in_format = True
            continue
        if line.strip() == 'END_DATA_FORMAT':
            break
        if in_format and line.strip():
            data_format = line.split()
            break

    if data_format is None:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"Invalid format in spec2cie output")

    try:
        xi = data_format.index('XYZ_X')
        yi = data_format.index('XYZ_Y')
        zi = data_format.index('XYZ_Z')
    except ValueError:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"XYZ_X/Y/Z columns missing in spec2cie output")

    # spec2cie also adds LAB_L/A/B, but computed on its 0-1 XYZ -> WRONG after
    # the x100 (white L ~ 7.7 instead of ~ 94). We recompute them from the x100 XYZ
    # (white-point = illuminant). Columns ignored by colprof (PCS=XYZ declared),
    # so purely cosmetic/profcheck - but we make them correct. If absent
    # (e.g. ti3 without Lab), we touch nothing.
    li = data_format.index('LAB_L') if 'LAB_L' in data_format else -1
    ai = data_format.index('LAB_A') if 'LAB_A' in data_format else -1
    bi = data_format.index('LAB_B') if 'LAB_B' in data_format else -1
    has_lab = li >= 0 and ai >= 0 and bi >= 0
    white = _ILLUMINANT_WHITE.get(illuminant.upper(), _ILLUMINANT_WHITE["D50"])

    out_lines = []
    in_data = False
    for line in lines:
        if line.strip() == 'BEGIN_DATA':
            out_lines.append(line)
            in_data = True
            continue
        if line.strip() == 'END_DATA':
            out_lines.append(line)
            in_data = False
            continue
        if in_data and line.strip():
            toks = line.split()
            if len(toks) >= len(data_format):
                X = float(toks[xi]) * 100
                Y = float(toks[yi]) * 100
                Z = float(toks[zi]) * 100
                toks[xi] = f"{X:.4f}"
                toks[yi] = f"{Y:.4f}"
                toks[zi] = f"{Z:.4f}"
                if has_lab:
                    L, a, b = _xyz_to_lab(X, Y, Z, white)
                    toks[li] = f"{L:.4f}"
                    toks[ai] = f"{a:.4f}"
                    toks[bi] = f"{b:.4f}"
                out_lines.append(' ' + ' '.join(toks))
        else:
            out_lines.append(line)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out_lines) + '\n')

    tmp_path.unlink(missing_ok=True)
    return output_path


# --- Building the Argyll CTI3 from HP CGATS --------------------------------


def build_cti3(
    parsed_cgats: dict,
    descriptor: str = f"freeglaz {_FREEGLAZ_VERSION} from ICC CIED",
) -> str:
    """Rebuild an Argyll CTI3 from a parsed HP CGATS.17.

    Key differences HP CGATS -> Argyll CTI3:
      - "CTI3" signature instead of "CGATS.17"
      - SPEC_400..SPEC_700 fields instead of SPECTRAL_400..SPECTRAL_700
      - SAMPLE_ID numbered from 1 (instead of 0)
      - RGB in percent (0..100) instead of uint8 (0..255)
      - mandatory Argyll metadata (DEVICE_CLASS, COLOR_REP, etc.)
      - numbers as explicit float (no bare integers in the spectra)

    XYZ is computed from the spectra with the CIE 1931 2 deg x D50 table, Y
    normalized to 100 for perfect white. colprof will recompute XYZ/Lab from
    the spectra anyway with its high-precision tables.

    :raises ValueError: if RGB fields or spectral bands are absent
    """
    fmt_in = parsed_cgats['format']
    data_in = parsed_cgats['data']

    def idx(name):
        return fmt_in.index(name) if name in fmt_in else -1

    i_r = idx('RGB_R')
    i_g = idx('RGB_G')
    i_b = idx('RGB_B')

    if -1 in (i_r, i_g, i_b):
        raise ValueError(
            f"RGB_R/G/B fields missing in the CGATS: {fmt_in}"
        )

    spec_indices = []
    spec_wavelengths = []
    for i, name in enumerate(fmt_in):
        if name.startswith('SPECTRAL_'):
            try:
                wl = int(name.split('_')[1])
                spec_indices.append(i)
                spec_wavelengths.append(wl)
            except (ValueError, IndexError):
                pass

    if not spec_indices:
        raise ValueError(
            f"No SPECTRAL_* spectral band found in: {fmt_in}"
        )

    n_bands = len(spec_indices)
    wl_min = min(spec_wavelengths)
    wl_max = max(spec_wavelengths)

    cti3_lines = []
    for new_id, row in enumerate(data_in, start=1):
        try:
            r = float(row[i_r]) * 100.0 / 255.0
            g = float(row[i_g]) * 100.0 / 255.0
            b = float(row[i_b]) * 100.0 / 255.0
        except (ValueError, IndexError):
            continue

        spectra = []
        for si in spec_indices:
            try:
                v = float(row[si])
                spectra.append(v)
            except (ValueError, IndexError):
                spectra.append(0.0)

        X, Y, Z = spectra_to_xyz_d50(spectra, spec_wavelengths)
        spectra_str = " ".join(f"{v:.10f}" for v in spectra)
        line = (
            f"{new_id} "
            f"{r:.4f} {g:.4f} {b:.4f} "
            f"{X:.4f} {Y:.4f} {Z:.4f} "
            + spectra_str
        )
        cti3_lines.append(line)

    fields = ['SAMPLE_ID', 'RGB_R', 'RGB_G', 'RGB_B', 'XYZ_X', 'XYZ_Y', 'XYZ_Z']
    fields += [f"SPEC_{wl}" for wl in spec_wavelengths]
    n_fields = len(fields)
    n_sets = len(cti3_lines)

    orig_descr = parsed_cgats['header'].get('DESCRIPTOR', descriptor)
    orig_created = parsed_cgats['header'].get('CREATED', 'unknown')

    cti3 = []
    cti3.append("CTI3")
    cti3.append("")
    cti3.append(f'DESCRIPTOR "{descriptor}"')
    cti3.append(f'ORIGINATOR "freeglaz {_FREEGLAZ_VERSION} (lib/z9_client/profiling.py)"')
    cti3.append(f'CREATED "{orig_created}"')
    cti3.append(f'ORIGINAL_DESCRIPTOR "{orig_descr}"')
    cti3.append("")
    cti3.append('KEYWORD "DEVICE_CLASS"')
    cti3.append('DEVICE_CLASS "OUTPUT"')
    cti3.append("")
    cti3.append('KEYWORD "COLOR_REP"')
    cti3.append('COLOR_REP "RGB_XYZ"')
    cti3.append("")
    cti3.append('KEYWORD "TARGET_INSTRUMENT"')
    cti3.append('TARGET_INSTRUMENT "HP DesignJet Z9 embedded spectro"')
    cti3.append("")
    cti3.append('KEYWORD "INSTRUMENT_TYPE_SPECTRAL"')
    cti3.append('INSTRUMENT_TYPE_SPECTRAL "YES"')
    cti3.append("")
    cti3.append('KEYWORD "NORMALIZED_TO_Y_100"')
    cti3.append('NORMALIZED_TO_Y_100 "YES"')
    cti3.append("")
    cti3.append('ILLUMINANT "D50"')
    cti3.append('OBSERVER "CIE_1931_2"')
    cti3.append('MEASUREMENT_TYPE 8')
    cti3.append("")
    cti3.append(f'SPECTRAL_BANDS "{n_bands}"')
    cti3.append(f'SPECTRAL_START_NM "{wl_min}.0"')
    cti3.append(f'SPECTRAL_END_NM "{wl_max}.0"')
    cti3.append("")
    cti3.append(f"NUMBER_OF_FIELDS {n_fields}")
    cti3.append("BEGIN_DATA_FORMAT")
    cti3.append(" ".join(fields))
    cti3.append("END_DATA_FORMAT")
    cti3.append("")
    cti3.append(f"NUMBER_OF_SETS {n_sets}")
    cti3.append("BEGIN_DATA")
    cti3.extend(cti3_lines)
    cti3.append("END_DATA")
    cti3.append("")

    return '\n'.join(cti3)


# --- ti3 parsing/writing (Argyll) -----------------------------------------


def parse_ti3(content: str) -> tuple[list[str], list[str], list[list[str]]]:
    """Read an Argyll ti3.

    :return: (header_lines, data_format, data_lines)
      - header_lines: preamble up to (excluding) BEGIN_DATA_FORMAT
      - data_format: list of column names
      - data_lines: list of value tuples (str) per patch
    """
    lines = content.splitlines()
    header_lines: list[str] = []
    data_format: Optional[list[str]] = None
    data_lines: list[list[str]] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip() == 'BEGIN_DATA_FORMAT':
            data_format = lines[i+1].split()
            j = i + 2
            while lines[j].strip() != 'END_DATA_FORMAT':
                j += 1
            i = j + 1
            continue
        if line.strip() == 'BEGIN_DATA':
            j = i + 1
            while lines[j].strip() != 'END_DATA':
                stripped = lines[j].strip()
                if stripped and not stripped.startswith('#'):
                    data_lines.append(stripped.split())
                j += 1
            break
        header_lines.append(line)
        i += 1

    if data_format is None:
        raise ValueError("No BEGIN_DATA_FORMAT section found")
    if not data_lines:
        raise ValueError("No patches found")

    return header_lines, data_format, data_lines


def write_ti3(
    header_lines: list[str],
    data_format: list[str],
    data_rows: list[list[str]],
    output_path: Path,
    descriptor_override: Optional[str] = None,
    originator_override: Optional[str] = f"freeglaz {_FREEGLAZ_VERSION} (lib/z9_client/profiling.py)",
) -> None:
    """Write an Argyll ti3.

    Can replace the ORIGINATOR / CREATED / DESCRIPTOR fields in the header
    for traceability.
    """
    out_lines = []
    for line in header_lines:
        stripped = line.strip()
        if stripped.startswith('ORIGINATOR') and originator_override:
            out_lines.append(f'ORIGINATOR "{originator_override}"')
        elif stripped.startswith('CREATED'):
            out_lines.append(f'CREATED "{datetime.now().date().isoformat()}"')
        elif stripped.startswith('DESCRIPTOR') and descriptor_override:
            out_lines.append(f'DESCRIPTOR "{descriptor_override}"')
        else:
            out_lines.append(line)

    out_lines.append('BEGIN_DATA_FORMAT')
    out_lines.append(' ' + ' '.join(data_format))
    out_lines.append('END_DATA_FORMAT')
    out_lines.append('')
    out_lines.append(f'NUMBER_OF_SETS {len(data_rows)}')
    out_lines.append('BEGIN_DATA')
    for row in data_rows:
        out_lines.append(' ' + ' '.join(row))
    out_lines.append('END_DATA')

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out_lines) + '\n')


# --- JSON sidecar ---------------------------------------------------------


def load_sidecar_json(path: Path) -> tuple[dict, dict, dict]:
    """Read a JSON sidecar produced by ChartOps.

    :return: (rgb_by_index, rgb_by_sample_id, layout_dict)
        rgb_by_index     : dict {logical_index (int): (r8, g8, b8)}
        rgb_by_sample_id : dict {sample_id (int): (r8, g8, b8)} - empty if the
                           sidecar carries no usable sample_id
        layout_dict      : dict {num_cols, nrows, width, height, ...}

    :raises ValueError: if unexpected format
    """
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if 'patches_in_layout_order' not in data:
        raise ValueError(
            f"Unexpected sidecar format: no 'patches_in_layout_order' "
            f"in {path}"
        )

    rgb_by_index = {}
    rgb_by_sample_id = {}
    for item in data['patches_in_layout_order']:
        idx = item['index']
        rgb = tuple(item['rgb'])
        rgb_by_index[idx] = rgb
        # sample_id may be int or str ("1") depending on the .ti1 source -> coerce.
        sid_raw = item.get('sample_id')
        if sid_raw is not None:
            try:
                rgb_by_sample_id[int(sid_raw)] = rgb
            except (ValueError, TypeError):
                pass

    return rgb_by_index, rgb_by_sample_id, data.get('layout', {})


# --- Return dataclasses ---------------------------------------------------


@dataclass
class ExtractCgatsResult:
    """Result of ProfilingOps.extract_cgats_from_icc()."""
    icc_path: str
    output_path: str
    n_patches: int
    n_spectral_bands: int
    spectral_range_nm: tuple[int, int]
    original_descriptor: str
    original_created: str
    icc_size_bytes: int
    ti3_size_bytes: int


@dataclass
class RemapTi3Result:
    """Result of ProfilingOps.remap_ti3_from_sidecar()."""
    ti3_path: str
    sidecar_path: str
    output_path: str
    n_patches_ti3: int
    n_patches_sidecar: int
    n_modified: int
    n_unchanged: int
    n_missing_in_sidecar: int


@dataclass
class BuildProfileResult:
    """Result of ProfilingOps.build_profile()."""
    ti3_path: str
    output_icc_path: str
    descriptor: str
    colprof_command: list[str]
    colprof_stdout: str
    colprof_stderr: str
    colprof_returncode: int
    output_icc_size_bytes: int


@dataclass
class PipelineResult:
    """Result of ProfilingOps.run_pipeline() (full orchestration)."""
    extract: ExtractCgatsResult
    remap: RemapTi3Result
    build: BuildProfileResult
    output_icc_path: str
    n_patches: int


# --- Main class -----------------------------------------------------------


class ProfilingOps:
    """
    freeglaz profiling operations (post-scan).

    Three main steps + an orchestrator:
      - extract_cgats_from_icc : Z9 firmware ICC (CIED tag) -> Argyll ti3
      - remap_ti3_from_sidecar : substitutes the nominal RGB with the sidecar's
      - build_profile          : Argyll colprof wrapper -> final ICC
      - run_pipeline           : chains the 3 steps

    Usable directly (without Z9Client) for purely local operations.
    """

    def __init__(self, z9_client=None):
        """
        :param z9_client: Z9Client instance (optional). Not used for
                          now - all operations are local.
        """
        self._client = z9_client

    # --- Step 1: CIED extraction -> ti3 ----------------------------------

    def extract_cgats_from_icc(
        self,
        icc_path: str | Path,
        output_path: Optional[str | Path] = None,
        *,
        descriptor: Optional[str] = None,
        xyz_correction: bool = True,
        cxf_measurement: str = "M0",
        on_step=None,
    ) -> ExtractCgatsResult:
        """Extract the CGATS measurements embedded in a Z9 firmware ICC
        profile (CIED tag) and convert them to an Argyll CTI3.

        Also reads i1Profiler / X-Rite V4 profiles that store their spectral
        measurements in the ``CxF `` tag (CxF3) instead of CIED/targ — enabling
        an external-i1 open profiling path (e.g. on a Z9 Pro whose embedded
        spectro is locked). ``cxf_measurement`` picks the M0/M1/M2 condition.

        :param icc_path: path to the ICC file (typically Z9 firmware)
        :param output_path: output .ti3 path. If None: <basename>.ti3
                            in the current directory
        :param descriptor: CTI3 descriptor (default: "freeglaz from <basename>")
        :param xyz_correction: if True (default), corrects the XYZ via Argyll
                               spec2cie to remove the bias of the local CIE
                               table (yellow shift dE ~6 on white). Makes the
                               final ICC profile bit-perfect with the HP factory
                               on white paper. Disable only if Argyll
                               is not installed (fallback to local table).
        :param on_step: callback(step, total, label, **details) for tracing

        :return: ExtractCgatsResult with stats and path
        :raises FileNotFoundError: if icc_path absent
        :raises ValueError: if no CIED tag, or invalid ICC
        """
        def _step(n, total, label, **details):
            if on_step:
                on_step(n, total, label, **details)

        icc_path = Path(icc_path)
        if not icc_path.exists():
            raise FileNotFoundError(f"ICC file not found: {icc_path}")

        if output_path is None:
            output_path = Path.cwd() / (icc_path.stem + ".ti3")
        else:
            output_path = Path(output_path)

        if descriptor is None:
            descriptor = f"freeglaz {_FREEGLAZ_VERSION} from {icc_path.stem}"

        # Total number of steps: 4 without correction, 5 with
        n_total_steps = 5 if xyz_correction else 4

        # 1. Read the ICC and parse the tags
        _step(1, n_total_steps, 'read-icc', icc_path=str(icc_path))
        with open(icc_path, 'rb') as f:
            icc_bytes = f.read()
        tags = parse_icc_tags(icc_bytes)

        # 2. Find the measurements tag: CIED (HP/Z9 firmware + freeglaz pipeline)
        #    in priority, otherwise targ (raw Argyll colprof profile - a refined
        #    variant only has targ until it has been passed back through
        #    freeglaz).
        cied = None
        cxf = None
        measure_tag = None
        for sig, off, sz in tags:
            if sig == 'CIED':
                cied = (off, sz)
                measure_tag = 'CIED'
                break
        if cied is None:
            for sig, off, sz in tags:
                if sig == 'targ':
                    cied = (off, sz)
                    measure_tag = 'targ'
                    break
        # CxF fallback: i1Profiler / X-Rite store the measurements in the 'CxF '
        # tag (CxF3 zlib XML), not CIED/targ. Read that too so an external-i1
        # profile (e.g. from a Z9 Pro with a locked embedded spectro) is usable.
        if cied is None:
            for sig, off, sz in tags:
                if sig.rstrip() == 'CxF':
                    cxf = (off, sz)
                    measure_tag = 'CxF'
                    break

        if cied is None and cxf is None:
            raise ValueError(
                f"Measurement tag (CIED, targ or CxF) not found in {icc_path.name}. "
                f"This profile contains no embedded CGATS/CTI3/CxF data. "
                f"Tags present: {[t[0] for t in tags]}"
            )

        src = cied if cied is not None else cxf
        _step(2, n_total_steps, 'cied-found',
              cied_offset=src[0], cied_size=src[1],
              measure_tag=measure_tag,
              n_tags=len(tags))

        # 3. Extract and parse the measurements text (HP CGATS.17 / Argyll CTI3 /
        #    i1Profiler CxF3 -> synthesized HP-style CGATS.17)
        if measure_tag == 'CxF':
            from .cxf import cxf_to_cgats17
            cgats_text = cxf_to_cgats17(
                icc_bytes, cxf[0], cxf[1],
                measurement=cxf_measurement, descriptor=descriptor or "freeglaz from CxF")
        else:
            cgats_text = extract_cied_text(icc_bytes, cied[0], cied[1])
        parsed = parse_cgats_data(cgats_text)
        is_native_cti3 = cgats_text.lstrip().startswith('CTI3')

        n_patches = len(parsed['data'])
        if n_patches == 0:
            raise ValueError(
                f"CIED tag present but no parsable patch in {icc_path.name}"
            )

        # Spectral band detection: HP names them SPECTRAL_<nm>, Argyll SPEC_<nm>
        spec_wavelengths = []
        for name in parsed['format']:
            if name.startswith(('SPECTRAL_', 'SPEC_')):
                try:
                    spec_wavelengths.append(int(name.split('_')[1]))
                except (ValueError, IndexError):
                    pass

        _step(3, n_total_steps, 'cgats-parsed',
              n_patches=n_patches,
              n_spectral_bands=len(spec_wavelengths),
              spectral_range=(min(spec_wavelengths), max(spec_wavelengths))
                              if spec_wavelengths else (0, 0),
              source_format='CTI3' if is_native_cti3 else 'CGATS.17',
              fields=parsed['format'])

        # 4. Write the ti3
        #    Argyll/freeglaz profile: the embedded text IS already a valid CTI3
        #    (RGB 0-100, SPEC_* fields, correct XYZ), we write it verbatim
        #    without loss. HP firmware profile: we rebuild the CTI3 from the
        #    CGATS.17.
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if is_native_cti3:
            cti3 = cgats_text if cgats_text.endswith('\n') else cgats_text + '\n'
        else:
            cti3 = build_cti3(parsed, descriptor=descriptor)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(cti3)

        _step(4, n_total_steps, 'ti3-written',
              output_path=str(output_path),
              verbatim=is_native_cti3,
              ti3_size_bytes=os.path.getsize(output_path))

        # 5. (Optional) Correct the XYZ via Argyll spec2cie
        #    Only for the rebuilt HP CGATS.17: the local _CIE_D50_20NM table
        #    has a bias (cf. function doc) that shifts white toward
        #    warm (da*~+6, db*~+4). A native CTI3 already has correct XYZ.
        if xyz_correction and not is_native_cti3:
            try:
                apply_spec2cie_xyz_correction(output_path)
                _step(5, n_total_steps, 'xyz-corrected',
                      output_path=str(output_path),
                      method='Argyll spec2cie -i D50 -o 1931_2',
                      ti3_size_bytes=os.path.getsize(output_path))
            except FileNotFoundError as e:
                # spec2cie not available: explicit warning, we continue
                # (the ti3 stays usable but with XYZ bias)
                _step(5, n_total_steps, 'xyz-correction-skipped',
                      reason=str(e),
                      output_path=str(output_path))
        elif xyz_correction and is_native_cti3:
            _step(5, n_total_steps, 'xyz-correction-skipped',
                  reason='Native CTI3: XYZ measurements already correct',
                  output_path=str(output_path))

        return ExtractCgatsResult(
            icc_path=str(icc_path),
            output_path=str(output_path),
            n_patches=n_patches,
            n_spectral_bands=len(spec_wavelengths),
            spectral_range_nm=(min(spec_wavelengths), max(spec_wavelengths))
                              if spec_wavelengths else (0, 0),
            original_descriptor=parsed['header'].get('DESCRIPTOR', ''),
            original_created=parsed['header'].get('CREATED', ''),
            icc_size_bytes=len(icc_bytes),
            ti3_size_bytes=os.path.getsize(output_path),
        )

    # --- Step 2: RGB remap from sidecar ----------------------------------

    def remap_ti3_from_sidecar(
        self,
        ti3_path: str | Path,
        sidecar_path: str | Path,
        output_path: str | Path,
        *,
        descriptor_override: Optional[str] = "freeglaz remapped from sidecar",
        on_step=None,
    ) -> RemapTi3Result:
        """Substitute the RGB of a firmware ti3 with those of the JSON sidecar.

        The Z9 firmware returns the nominal RGB of the HP template (HP levels),
        NOT the RGB we actually sent. This operation substitutes
        the values using the sidecar's logical index for the mapping
        (row-major order of patch generation).

        :param ti3_path: ti3 produced by extract_cgats_from_icc
        :param sidecar_path: JSON sidecar produced by ChartOps.generate
        :param output_path: path of the remapped output ti3
        :param descriptor_override: new DESCRIPTOR (None to keep)
        :param on_step: callback for tracing

        :return: RemapTi3Result with substitution stats
        :raises FileNotFoundError: if ti3 or sidecar absent
        :raises ValueError: if invalid format
        """
        def _step(n, total, label, **details):
            if on_step:
                on_step(n, total, label, **details)

        ti3_path = Path(ti3_path)
        sidecar_path = Path(sidecar_path)
        output_path = Path(output_path)

        if not ti3_path.exists():
            raise FileNotFoundError(f"ti3 not found: {ti3_path}")
        if not sidecar_path.exists():
            raise FileNotFoundError(f"sidecar not found: {sidecar_path}")

        # 1. Parse the ti3
        _step(1, 4, 'read-ti3', ti3_path=str(ti3_path))
        with open(ti3_path, 'r', encoding='utf-8') as f:
            content = f.read()
        header, fmt, data = parse_ti3(content)

        try:
            idx_r = fmt.index('RGB_R')
            idx_g = fmt.index('RGB_G')
            idx_b = fmt.index('RGB_B')
        except ValueError:
            raise ValueError(
                f"Colonnes RGB_R/RGB_G/RGB_B absentes du ti3 : {fmt}"
            )
        # Usable SAMPLE_ID? (the Z9 firmware ti3 and the freeglaz ti1 both
        # carry one - 1st column). We use it to match by patch identity
        # rather than by rank: a missing patch no longer shifts the others.
        idx_sid = fmt.index('SAMPLE_ID') if 'SAMPLE_ID' in fmt else None

        # 2. Load the sidecar
        _step(2, 4, 'read-sidecar', sidecar_path=str(sidecar_path))
        rgb_by_index, rgb_by_sample_id, layout = load_sidecar_json(sidecar_path)

        n_expected = len(rgb_by_index)
        by_sample_id = idx_sid is not None and len(rgb_by_sample_id) == n_expected

        # -- COUNT consistency SAFEGUARD ----------------------------------
        # Refuse a truncated/disordered scan BEFORE producing a wrong profile.
        if by_sample_id:
            ti3_ids = []
            for row in data:
                try:
                    ti3_ids.append(int(row[idx_sid]))
                except (ValueError, IndexError):
                    raise ValueError(
                        f"Unreadable SAMPLE_ID in the ti3: line {row[:1]}"
                    )
            ti3_set = set(ti3_ids)
            expected_set = set(rgb_by_sample_id)
            if len(ti3_ids) != n_expected or ti3_set != expected_set:
                missing = sorted(expected_set - ti3_set)
                extra = sorted(ti3_set - expected_set)
                dups = len(ti3_ids) - len(ti3_set)
                raise ValueError(
                    f"Remap refused: {len(ti3_ids)} measurements read, "
                    f"{n_expected} expected (sidecar). "
                    f"SAMPLE_ID missing={missing[:10]} "
                    f"extra={extra[:10]} duplicates={dups}. "
                    f"Incomplete/defective scan — profile NOT generated."
                )
        else:
            # No reliable SAMPLE_ID: positional remap + strict count net.
            if len(data) != n_expected:
                raise ValueError(
                    f"Remap refused: {len(data)} measurements read, "
                    f"{n_expected} expected (sidecar), and no usable SAMPLE_ID "
                    f"to re-pair. Incomplete scan — "
                    f"profile NOT generated."
                )

        # 3. Substitution
        _step(3, 4, 'remap',
              n_patches_ti3=len(data),
              n_patches_sidecar=n_expected,
              mode='sample_id' if by_sample_id else 'positional')

        n_modified = 0
        n_same = 0
        n_missing = 0
        for i, row in enumerate(data):
            if by_sample_id:
                sid = int(row[idx_sid])
                rgb = rgb_by_sample_id.get(sid)
            else:
                rgb = rgb_by_index.get(i)
            if rgb is None:
                # Impossible after the asserts above, but we stay defensive.
                n_missing += 1
                continue

            r8, g8, b8 = rgb
            r_pct = r8 * 100.0 / 255.0
            g_pct = g8 * 100.0 / 255.0
            b_pct = b8 * 100.0 / 255.0

            r_old = float(row[idx_r])
            g_old = float(row[idx_g])
            b_old = float(row[idx_b])

            if (abs(r_pct - r_old) < 0.01 and
                abs(g_pct - g_old) < 0.01 and
                abs(b_pct - b_old) < 0.01):
                n_same += 1
            else:
                n_modified += 1

            row[idx_r] = f"{r_pct:.4f}"
            row[idx_g] = f"{g_pct:.4f}"
            row[idx_b] = f"{b_pct:.4f}"

        # 4. Write the remapped ti3
        write_ti3(
            header, fmt, data, output_path,
            descriptor_override=descriptor_override,
        )

        _step(4, 4, 'written',
              output_path=str(output_path),
              n_modified=n_modified,
              n_same=n_same,
              n_missing=n_missing)

        return RemapTi3Result(
            ti3_path=str(ti3_path),
            sidecar_path=str(sidecar_path),
            output_path=str(output_path),
            n_patches_ti3=len(data),
            n_patches_sidecar=n_expected,
            n_modified=n_modified,
            n_unchanged=n_same,
            n_missing_in_sidecar=n_missing,
        )

    # --- Step 3: build_profile via Argyll colprof ------------------------

    def build_profile(
        self,
        ti3_basename: str | Path,
        *,
        descriptor: str,
        output_icc_path: Optional[str | Path] = None,
        manufacturer: str = "HP",
        model: str = "HP DesignJet Z9",
        copyright_str: str = "No copyright, use freely",
        colprof_flags: Optional[list[str]] = None,
        colprof_path: str = "colprof",
        on_step=None,
    ) -> BuildProfileResult:
        """Run Argyll colprof to generate an ICC profile from a ti3.

        Argyll colprof expects the ti3 path WITHOUT the .ti3 extension as
        argument (it adds .ti3 itself). The ICC profile is created in the same
        directory as <basename>.icc, unless overridden via output_icc_path.

        :param ti3_basename: path to the ti3 WITHOUT extension (Argyll convention)
                            If .ti3 is provided, it is stripped automatically.
        :param descriptor: profile description (-D)
        :param output_icc_path: if provided, the .icc produced by colprof is
                                moved/copied to this path after creation.
                                If None: stays at <basename>.icc
        :param manufacturer: -A (default: "HP" - real HP printer)
        :param model: -M (default: "HP DesignJet Z9")
        :param copyright_str: -C (default: "No copyright, use freely" - the
                              computation comes from Argyll colprof, we don't
                              appropriate the output)
        :param colprof_flags: list of extra flags (default: ["-v",
                              "-qh"]). NO -nc: we embed the .ti3 (targ tag)
                              -> self-sufficient profile. Will be merged with
                              the flags above.
        :param colprof_path: name of the colprof executable (override for tests)
        :param on_step: callback for tracing

        :return: BuildProfileResult
        :raises FileNotFoundError: if the ti3 does not exist
        :raises RuntimeError: if colprof fails (return code != 0)
        """
        def _step(n, total, label, **details):
            if on_step:
                on_step(n, total, label, **details)

        # Normalize the basename (strip .ti3 if provided)
        ti3_basename = Path(ti3_basename)
        if ti3_basename.suffix.lower() == '.ti3':
            ti3_basename = ti3_basename.with_suffix('')

        ti3_path = ti3_basename.with_suffix('.ti3')
        if not ti3_path.exists():
            raise FileNotFoundError(f"ti3 not found: {ti3_path}")

        # Build the colprof command
        if colprof_flags is None:
            colprof_flags = ["-v", "-qh"]

        # DURABLE resolution of the binary (absolute path) - see lib/z9_client/argyll:
        # works even from a GUI app with a minimal PATH (pywebview / .app).
        from .argyll import resolve_argyll_binary
        _colprof = resolve_argyll_binary(colprof_path)   # raises ArgyllNotFound if absent
        cmd = [_colprof] + list(colprof_flags) + [
            "-A", manufacturer,
            "-M", model,
            "-C", copyright_str,
            "-D", descriptor,
            str(ti3_basename),
        ]

        _step(1, 2, 'colprof-launch',
              command=cmd, ti3_path=str(ti3_path))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,  # 10 minutes max
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"colprof exceeded the timeout (600s). Command: {cmd}"
            )

        if result.returncode != 0:
            raise RuntimeError(
                f"colprof failed (returncode={result.returncode}).\n"
                f"Command: {' '.join(cmd)}\n"
                f"stderr:\n{result.stderr}"
            )

        # colprof produces <basename>.icc
        produced_icc = ti3_basename.with_suffix('.icc')
        if not produced_icc.exists():
            raise RuntimeError(
                f"colprof OK but the expected .icc file is missing: {produced_icc}"
            )

        # Move if output_icc_path provided
        if output_icc_path is not None:
            output_icc_path = Path(output_icc_path)
            output_icc_path.parent.mkdir(parents=True, exist_ok=True)
            if produced_icc.resolve() != output_icc_path.resolve():
                # Read/write to avoid cross-fs issues
                output_icc_path.write_bytes(produced_icc.read_bytes())
                produced_icc.unlink()
            final_path = output_icc_path
        else:
            final_path = produced_icc

        # Anti-silent-corruption safeguard: the produced CLUT grid must
        # match the requested -q. A mismatch (e.g. -qh expected 33^3 but 17^3
        # obtained) signals that the flags were not applied (cf. historical
        # trap: flags reassembled into a shell string -> ignored by colprof).
        expected_grid = _expected_grid_for_flags(list(colprof_flags))
        actual_grid = _a2b0_clut_grid(final_path)
        if expected_grid is not None and actual_grid is not None \
                and actual_grid != expected_grid:
            logger.warning(
                "colprof: CLUT grid %d³ obtained != %d³ expected for the requested "
                "-q (flags=%s). Were the flags actually applied? "
                "Profile: %s",
                actual_grid, expected_grid, colprof_flags, final_path,
            )

        _step(2, 2, 'colprof-done',
              output_icc_path=str(final_path),
              output_icc_size_bytes=os.path.getsize(final_path),
              clut_grid=actual_grid,
              expected_grid=expected_grid,
              returncode=result.returncode)

        return BuildProfileResult(
            ti3_path=str(ti3_path),
            output_icc_path=str(final_path),
            descriptor=descriptor,
            colprof_command=cmd,
            colprof_stdout=result.stdout,
            colprof_stderr=result.stderr,
            colprof_returncode=result.returncode,
            output_icc_size_bytes=os.path.getsize(final_path),
        )

    # --- Orchestrator: full pipeline -------------------------------------

    def run_pipeline(
        self,
        *,
        firmware_icc_path: str | Path,
        sidecar_path: str | Path,
        output_icc_path: str | Path,
        descriptor: str,
        ti3_intermediate_path: Optional[str | Path] = None,
        ti3_remapped_path: Optional[str | Path] = None,
        manufacturer: str = "HP",
        model: str = "HP DesignJet Z9",
        copyright_str: str = "No copyright, use freely",
        colprof_flags: Optional[list[str]] = None,
        colprof_path: str = "colprof",
        on_step=None,
    ) -> PipelineResult:
        """Orchestrate extract -> remap -> build_profile in a single pass.

        Pipeline:
          1. extract_cgats_from_icc(firmware_icc) -> intermediate ti3
          2. remap_ti3_from_sidecar(ti3, sidecar) -> remapped ti3
          3. build_profile(ti3_remapped) -> final ICC

        If ti3_intermediate_path / ti3_remapped_path are None, temporary
        paths are computed from output_icc_path.

        :raises: like the called methods
        """
        firmware_icc_path = Path(firmware_icc_path)
        sidecar_path = Path(sidecar_path)
        output_icc_path = Path(output_icc_path)

        base = output_icc_path.with_suffix('')
        if ti3_intermediate_path is None:
            ti3_intermediate_path = base.with_name(base.name + '_raw').with_suffix('.ti3')
        else:
            ti3_intermediate_path = Path(ti3_intermediate_path)
        if ti3_remapped_path is None:
            ti3_remapped_path = base.with_suffix('.ti3')
        else:
            ti3_remapped_path = Path(ti3_remapped_path)

        def _sub_step(stage):
            def wrapped(n, total, label, **details):
                if on_step:
                    on_step(stage, label, n=n, total=total, **details)
            return wrapped

        # 1. Extract
        extract_res = self.extract_cgats_from_icc(
            firmware_icc_path,
            output_path=ti3_intermediate_path,
            descriptor=f"freeglaz raw from {firmware_icc_path.stem}",
            on_step=_sub_step('extract'),
        )

        # 2. Remap (raises a clean ValueError if scan incomplete/misaligned)
        remap_res = self.remap_ti3_from_sidecar(
            ti3_intermediate_path,
            sidecar_path,
            ti3_remapped_path,
            descriptor_override=f"freeglaz remapped — {descriptor}",
            on_step=_sub_step('remap'),
        )

        # Defensive cross-check: the number of extracted measurements must
        # equal the number expected by the sidecar (the remap already enforces
        # this, this documents and locks the invariant at the orchestrator
        # level).
        if extract_res.n_patches != remap_res.n_patches_sidecar:
            raise ValueError(
                f"Inconsistent pipeline: {extract_res.n_patches} patches extracted "
                f"!= {remap_res.n_patches_sidecar} expected (sidecar). "
                f"Profile NOT generated."
            )

        # 3. Build
        build_res = self.build_profile(
            ti3_remapped_path,
            descriptor=descriptor,
            output_icc_path=output_icc_path,
            manufacturer=manufacturer,
            model=model,
            copyright_str=copyright_str,
            colprof_flags=colprof_flags,
            colprof_path=colprof_path,
            on_step=_sub_step('build'),
        )

        return PipelineResult(
            extract=extract_res,
            remap=remap_res,
            build=build_res,
            output_icc_path=str(output_icc_path),
            n_patches=extract_res.n_patches,
        )
