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

"""ICC inspection module.

Parses ICC profiles with proprietary HP91 decoding (zlib raw deflate)
and delegates to Argyll iccdump/iccgamut for the standard tags.
"""
import hashlib
import logging
import re
import struct
import subprocess
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


DEVICE_CLASS_LABELS = {
    'prtr': 'Output (printer)',
    'mntr': 'Display (monitor)',
    'scnr': 'Input (scanner)',
    'spac': 'Color Space',
    'abst': 'Abstract',
    'link': 'Device Link',
    'nmcl': 'Named Color',
}


def _find_argyll_tool(name: str) -> Optional[str]:
    """Look up an Argyll binary — delegates to the central resolver (single point,
    cf. lib/z9_client/argyll: explicit override → system paths → PATH)."""
    from .argyll import find_argyll_binary
    return find_argyll_binary(name)


# ─── Return dataclass ──────────────────────────────────────────────────

@dataclass
class ProfileInspection:
    file_path: str = ""
    file_size: int = 0

    # Header
    icc_version: str = ""
    device_class: str = ""
    device_class_label: str = ""
    color_space: str = ""
    pcs: str = ""
    cmm: str = ""
    platform: str = ""
    profile_size: int = 0
    description: str = ""
    copyright: str = ""

    # Origin — general-purpose
    is_hp_ingenium: bool = False
    hp90_signature: str = ""
    hp91_cluster_md5: str = ""
    hp91_description: str = ""
    profile_type_guess: str = "non-hp"

    # Standard ICC origin tags
    manufacturer_desc: str = ""  # dmnd
    model_desc: str = ""         # dmdd
    creator_signature: str = ""  # header offset 80

    # Structure
    n_tags: int = 0
    tags: list = field(default_factory=list)
    private_tags: list = field(default_factory=list)

    # HP91 config
    hp91_config: dict = field(default_factory=dict)
    hp91_raw: str = ""

    # Layer 4 — project interpretation
    custom_mapping_active: list = field(default_factory=list)
    custom_mapping_by_hue_active: bool = False
    custom_mapping_by_hue_primaries: list = field(default_factory=list)

    # Gamut
    gamut: dict = field(default_factory=dict)

    # Whitepoint / Blackpoint
    whitepoint_xyz: list = field(default_factory=list)
    whitepoint_lab: list = field(default_factory=list)
    blackpoint_xyz: list = field(default_factory=list)
    blackpoint_lab: list = field(default_factory=list)

    # TRC + taxonomy. Classifications structured as
    # {auto, override, effective} to prepare for the user override.
    trc: dict = field(default_factory=dict)
    taxonomy: dict = field(default_factory=dict)

    # Per-tag details for the interactive frontend popover.
    tags_details: list = field(default_factory=list)

    # Structured internals block (HP91, custom_mapping_by_hue, CIED)
    # + ICC v2/v4 conformance (validate_conformance).
    internals: dict = field(default_factory=dict)
    conformance: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


# ─── Layer 2 — HP Proprietary Decoder ─────────────────────────────────

class HpProprietaryDecoder:

    @staticmethod
    def parse_icc_tags(data: bytes):
        if len(data) < 132 or data[36:40] != b'acsp':
            return []
        n_tags = struct.unpack('>I', data[128:132])[0]
        tags = []
        for i in range(n_tags):
            off = 132 + i * 12
            sig = data[off:off+4].decode('ascii', errors='replace')
            tag_off = struct.unpack('>I', data[off+4:off+8])[0]
            tag_size = struct.unpack('>I', data[off+8:off+12])[0]
            tag_type = ""
            if tag_off + 4 <= len(data):
                tag_type = data[tag_off:tag_off+4].decode('ascii', errors='replace').strip()
            tags.append({'sig': sig, 'offset': tag_off, 'size': tag_size, 'type': tag_type})
        return tags

    @staticmethod
    def extract_header(data: bytes) -> dict:
        if len(data) < 128:
            return {}
        v = data[8:12]
        creator = data[80:84].decode('ascii', errors='replace').strip().replace('\x00', '')
        return {
            'icc_version': f'{v[0]}.{(v[1]>>4)&0xF}.{v[1]&0xF}',
            'device_class': data[12:16].decode('ascii', errors='replace').strip(),
            'color_space': data[16:20].decode('ascii', errors='replace').strip(),
            'pcs': data[20:24].decode('ascii', errors='replace').strip(),
            'cmm': data[4:8].decode('ascii', errors='replace').strip(),
            'platform': data[40:44].decode('ascii', errors='replace').strip(),
            'profile_size': struct.unpack('>I', data[0:4])[0],
            'creator_signature': creator,
        }

    @staticmethod
    def extract_text_tag(data: bytes, tag_sig: bytes, tags: list) -> str:
        """Extract text from a text-like tag (mluc, text, desc). Returns empty if not found."""
        for t in tags:
            if t['sig'].encode('ascii', errors='replace') != tag_sig:
                continue
            off = t['offset']
            size = t['size']
            if off + 8 > len(data):
                return ""
            ttype = data[off:off+4]
            payload = data[off+8:off+size]
            try:
                if ttype == b'mluc':
                    # ICC v4 multi-localized Unicode
                    if len(payload) < 8:
                        return ""
                    n_records = struct.unpack('>I', payload[0:4])[0]
                    rec_size = struct.unpack('>I', payload[4:8])[0]
                    if n_records == 0 or rec_size < 12:
                        return ""
                    # First record
                    rec = payload[8:8+rec_size]
                    str_len = struct.unpack('>I', rec[4:8])[0]
                    str_off = struct.unpack('>I', rec[8:12])[0]
                    # Offset is from start of tag (off), not from payload
                    raw = data[off+str_off:off+str_off+str_len]
                    return raw.decode('utf-16-be', errors='replace').rstrip('\x00').strip()
                elif ttype == b'text':
                    return payload.rstrip(b'\x00').decode('ascii', errors='replace').strip()
                elif ttype == b'desc':
                    # ICC v2 textDescription
                    if len(payload) < 4:
                        return ""
                    ascii_len = struct.unpack('>I', payload[0:4])[0]
                    if ascii_len > 0 and 4 + ascii_len <= len(payload):
                        return payload[4:4+ascii_len].rstrip(b'\x00').decode('ascii', errors='replace').strip()
            except Exception:
                return ""
        return ""

    @staticmethod
    def extract_xyz_tag(data: bytes, tag_sig: bytes, tags: list) -> Optional[tuple]:
        """Extract a single XYZ value from a tag (wtpt, bkpt). Returns (X, Y, Z) or None."""
        for t in tags:
            if t['sig'].encode('ascii', errors='replace') == tag_sig:
                off = t['offset']
                if data[off:off+4] != b'XYZ ':
                    return None
                payload = data[off+8:off+t['size']]
                if len(payload) >= 12:
                    x = struct.unpack('>i', payload[0:4])[0] / 65536.0
                    y = struct.unpack('>i', payload[4:8])[0] / 65536.0
                    z = struct.unpack('>i', payload[8:12])[0] / 65536.0
                    return (x, y, z)
        return None

    @staticmethod
    def extract_hp90(data: bytes, off: int, size: int) -> str:
        if data[off:off+4] != b'text':
            return ""
        return data[off+8:off+size].rstrip(b'\x00').decode('ascii', errors='replace')

    @staticmethod
    def extract_hp91_zut8(data: bytes, off: int, size: int) -> str:
        if data[off:off+4] != b'zut8':
            return ""
        try:
            payload = data[off+8:off+size]
            decompressed = zlib.decompress(payload, wbits=-15)
            return decompressed.decode('utf-8', errors='replace')
        except Exception:
            return ""

    @staticmethod
    def parse_hp91_config(text: str) -> dict:
        if not text:
            return {}
        # First: rejoin backslash-continuation lines (but preserve the
        # "commented" nature — the continuation of a commented line stays
        # commented). Approach: iterate line by line and merge.
        merged_lines = []
        buffer = ""
        for raw_line in text.splitlines():
            if buffer:
                buffer = buffer + " " + raw_line.strip()
            else:
                buffer = raw_line
            stripped = buffer.rstrip()
            if stripped.endswith('\\'):
                buffer = stripped[:-1].rstrip()
                continue
            merged_lines.append(buffer)
            buffer = ""
        if buffer:
            merged_lines.append(buffer)

        config = {}
        current_section = 'global'
        for line in merged_lines:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith(';'):
                continue
            m = re.match(r'^\[(.+)]$', line)
            if m:
                current_section = m.group(1)
                if current_section not in config:
                    config[current_section] = {}
                continue
            m = re.match(r'^([^=]+)=(.*)$', line)
            if m:
                key = m.group(1).strip()
                val = m.group(2).strip()
                if val.startswith('"') and val.endswith('"') and len(val) >= 2:
                    val = val[1:-1]
                if current_section not in config:
                    config[current_section] = {}
                config[current_section][key] = val
        return config

    @staticmethod
    def detect_custom_mappings(raw_text: str) -> dict:
        """Detect the active (non-commented) custom_mappings and the by_hue in the HP91 raw.

        Rejoins the `\\` continuation lines then filters out the commented ones.
        Returns: {
          'custom_mapping_active': list[str] (primaries from non-commented custom_mapping),
          'custom_mapping_by_hue_active': bool,
          'custom_mapping_by_hue_primaries': list[str],
        }
        """
        if not raw_text:
            return {'custom_mapping_active': [], 'custom_mapping_by_hue_active': False,
                    'custom_mapping_by_hue_primaries': []}

        merged = []
        buffer = ""
        for raw_line in raw_text.splitlines():
            if buffer:
                buffer = buffer + " " + raw_line.strip()
            else:
                buffer = raw_line
            stripped = buffer.rstrip()
            if stripped.endswith('\\'):
                buffer = stripped[:-1].rstrip()
                continue
            merged.append(buffer)
            buffer = ""
        if buffer:
            merged.append(buffer)

        active_primaries = set()
        by_hue_active = False
        by_hue_primaries = set()

        for line in merged:
            stripped = line.lstrip()
            if stripped.startswith('#') or stripped.startswith(';'):
                continue
            if 'custom_mapping' not in stripped:
                continue
            for prim in re.findall(r'(?:source|target|radius|magnet)\.(\w+)', stripped):
                active_primaries.add(prim.lower())
            if 'color_mapping_by_hue' in stripped or 'mapping_by_hue' in stripped:
                by_hue_active = True
                for prim in re.findall(r'(?:source|target|radius|magnet)\.(\w+)', stripped):
                    by_hue_primaries.add(prim.lower())

        return {
            'custom_mapping_active': sorted(active_primaries),
            'custom_mapping_by_hue_active': by_hue_active,
            'custom_mapping_by_hue_primaries': sorted(by_hue_primaries),
        }

    @staticmethod
    def guess_profile_type(hp90: str, hp91_desc: str,
                            copyright: str = "", creator: str = "") -> str:
        if hp90:
            if any(p in hp91_desc.lower() for p in ['test', 'newlut', 'jaguar', 'lessink']):
                return "factory"
            if 'default profiling' in hp91_desc.lower():
                return "user-default"
            return "user-custom"
        # Non-HP: enrich if the vendor is known
        c = copyright.lower()
        cr = creator.lower()
        for keyword, label in [
            ('x-rite', 'X-Rite/i1Profiler'),
            ('xrite', 'X-Rite/i1Profiler'),
            ('basiccolor', 'basICColor'),
            ('color solutions', 'ColorSolutions'),
            ('lcms', 'lcms (Little CMS)'),
            ('argyll', 'Argyll CMS'),
            ('adobe', 'Adobe'),
            ('apple', 'Apple ColorSync'),
            ('canson', 'Canson'),
            ('hahnem', 'Hahnemühle'),
            ('epson', 'Epson'),
        ]:
            if keyword in c or keyword in cr:
                return f'non-hp ({label})'
        return "non-hp"


# ─── Layer 3 — Gamut Analyzer ─────────────────────────────────────────

class GamutAnalyzer:
    """Gamut volume via lcms2 (ImageCms) + scipy ConvexHull.

    Migration iccgamut (Argyll) → lcms2. Argyll only reads
    the v2 LUTs (mft1/mft2). lcms2 also reads the v4 ones (mAB/mBA) → support
    for recent X-Rite/i1Profiler profiles (Hahnemühle, etc.).

    Volumes are not comparable to the old iccgamut numbers (different engine),
    but the perceptual/relative discrimination is preserved.
    """

    GRID = 33
    INTENTS = {"absolute": 3, "relative": 1, "perceptual": 0, "saturation": 2}

    @classmethod
    def compute_volumes(cls, icc_path: Path, tags: Optional[list] = None) -> dict:
        try:
            from PIL import Image, ImageCms
            import numpy as np
            from scipy.spatial import ConvexHull
        except ImportError as e:
            return {
                'status': 'deps_missing',
                'reason': f'Missing dependency: {e}',
            }

        volumes = {}
        errors = []
        for name, ival in cls.INTENTS.items():
            try:
                volumes[name] = cls._volume(str(icc_path), ival, cls.GRID,
                                             Image, ImageCms, np, ConvexHull)
            except Exception as e:
                volumes[name] = None
                errors.append(f'{name}: {e}')

        if not any(v for k, v in volumes.items() if k in cls.INTENTS):
            return {
                'status': 'failed',
                'reason': 'All intents failed (profile may be unreadable)',
                '_errors': errors,
                'method': 'lcms2 convex-hull (grid 33)',
            }

        # Flag degenerate absolute (rel*0.1 minimum expected)
        if (volumes.get('absolute') is not None and volumes.get('relative')
                and volumes['absolute'] < 0.1 * volumes['relative']):
            volumes['absolute_degenerate'] = True

        if volumes.get('perceptual') and volumes.get('relative'):
            rel = volumes['relative']
            per = volumes['perceptual']
            delta = (per - rel) / rel * 100 if rel > 0 else 0
            volumes['delta_per_rel_pct'] = round(delta, 2)
            abs_delta = abs(delta)
            if abs_delta <= 5:
                volumes['interpretation'] = 'no_perceptual_mapping'
            elif abs_delta <= 20:
                volumes['interpretation'] = 'light_perceptual_mapping'
            else:
                volumes['interpretation'] = 'strong_perceptual_mapping'

        volumes['method'] = 'lcms2 convex-hull (grid 33)'
        volumes['status'] = 'ok'
        if errors:
            volumes['_errors'] = errors
        return volumes

    @staticmethod
    def _volume(icc_path, intent_val, grid, Image, ImageCms, np, ConvexHull):
        dev_profile = ImageCms.getOpenProfile(icc_path)
        lab_profile = ImageCms.createProfile("LAB")
        transform = ImageCms.buildTransform(
            dev_profile, lab_profile, "RGB", "LAB",
            renderingIntent=intent_val,
        )
        vals = np.linspace(0, 255, grid).astype(np.uint8)
        rr, gg, bb = np.meshgrid(vals, vals, vals, indexing='ij')
        rgb = np.stack([rr.ravel(), gg.ravel(), bb.ravel()], axis=1).astype(np.uint8)
        n = rgb.shape[0]
        src = Image.frombytes('RGB', (n, 1), rgb.tobytes())
        out = ImageCms.applyTransform(src, transform)
        raw = np.frombuffer(out.tobytes(), dtype=np.uint8).reshape(n, 3)
        # PITFALL 1: cast to float BEFORE multiplication (uint8 overflow)
        L = raw[:, 0].astype(np.float64) * 100.0 / 255.0
        # PITFALL 2: int8 on the ARRAY (correct wrap), not on a Python scalar
        a = raw[:, 1].astype(np.int8).astype(np.float64)
        b = raw[:, 2].astype(np.int8).astype(np.float64)
        lab = np.stack([L, a, b], axis=1)
        return float(ConvexHull(lab).volume)


# ─── Layer 1 — IccDump Parser ─────────────────────────────────────────

class IccDumpParser:

    @staticmethod
    def _extract_quoted_text(stdout: str) -> str:
        """Extract first quoted text from iccdump output."""
        for line in stdout.splitlines():
            m = re.search(r':\s+"([^"]+)"', line)
            if m:
                return m.group(1)
            m = re.search(r'"([^"]+)"', line)
            if m:
                return m.group(1)
        return ""

    @classmethod
    def parse_tag_text(cls, icc_path: Path, tag: str) -> str:
        """Parse iccdump output for a specific text-like tag (desc, cprt, dmnd, dmdd)."""
        iccdump = _find_argyll_tool('iccdump')
        if not iccdump:
            return ""
        try:
            result = subprocess.run(
                [iccdump, '-t', tag, str(icc_path)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return ""
            return cls._extract_quoted_text(result.stdout)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""

    @classmethod
    def parse_description(cls, icc_path: Path) -> str:
        return cls.parse_tag_text(icc_path, 'desc')


# ─── Layer 5 — TRC analyzer ───────────────────────────────────────────

# Reference set: standard TRC curves sampled at 32 evenly spaced points
# over the input 0→1. Classification is done by RMS between the curve read
# from the rTRC/gTRC/bTRC tag and each reference. Default tolerance:
# RMS < 0.005 to match.

_TRC_SAMPLE_COUNT = 32


def _srgb_decode(x: float) -> float:
    return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4


def _rec709_decode(x: float) -> float:
    # Inverse curve of the Rec.709 OETF (encoded → linear)
    return x / 4.5 if x < 0.081 else ((x + 0.099) / 1.099) ** (1.0 / 0.45)


def _lstar_decode(x: float) -> float:
    # x is interpreted as L*/100. The inverse L* (CIE Lab) function:
    # Y/Yn = ((L*+16)/116)^3 if L* > 8, else L*/903.3
    L_star = x * 100.0
    if L_star <= 8.0:
        return L_star / 903.3
    return ((L_star + 16.0) / 116.0) ** 3.0


def _gamma_curve(g: float):
    return [(i / (_TRC_SAMPLE_COUNT - 1)) ** g if i > 0 else 0.0
            for i in range(_TRC_SAMPLE_COUNT)]


def _ref_curve(fn):
    return [fn(i / (_TRC_SAMPLE_COUNT - 1)) for i in range(_TRC_SAMPLE_COUNT)]


_REFERENCE_TRC_CURVES = {
    "linear":    _gamma_curve(1.0),
    "gamma_1.8": _gamma_curve(1.8),
    "gamma_2.2": _gamma_curve(2.2),
    "gamma_2.4": _gamma_curve(2.4),
    "srgb":      _ref_curve(_srgb_decode),
    "lstar":     _ref_curve(_lstar_decode),
    "rec709":    _ref_curve(_rec709_decode),
}


_TRC_FAMILY_LABEL = {
    "linear":         "Linear",
    "gamma_1.0":      "Linear",
    "gamma_1.8":      "Gamma 1.8",
    "gamma_2.2":      "Gamma 2.2",
    "gamma_2.4":      "Gamma 2.4",
    "srgb":           "sRGB",
    "lstar":          "L*",
    "rec709":         "Rec.709",
    "unknown_custom": "Custom",
}


def _eval_para(function_type: int, params: list, x: float) -> float:
    """Evaluate a parametric ICC curve at the point x ∈ [0, 1]."""
    if function_type == 0:
        g = params[0]
        return x ** g if x > 0 else 0.0
    if function_type == 1:
        g, a, b = params
        v = a * x + b
        return v ** g if v > 0 else 0.0
    if function_type == 2:
        g, a, b, c = params
        v = a * x + b
        return ((v ** g) if v > 0 else 0.0) + c
    if function_type == 3:
        g, a, b, c, d = params
        if x >= d:
            v = a * x + b
            return v ** g if v > 0 else 0.0
        return c * x
    if function_type == 4:
        g, a, b, c, d, e, f = params
        if x >= d:
            v = a * x + b
            return ((v ** g) if v > 0 else 0.0) + e
        return c * x + f
    return x


def _read_trc_curve(data: bytes, tag_off: int, tag_size: int) -> dict:
    """Read a curv or para tag and return its sampled form.

    Returns: {"type": ..., "method": ..., "n_entries"?, "gamma_single"?,
              "function_type"?, "params"?, "samples": list|None}
    """
    if tag_off + 12 > len(data) or tag_size < 12:
        return {"type": "unknown", "method": "unknown", "samples": None}
    tag_type = data[tag_off:tag_off+4]

    if tag_type == b'curv':
        n = struct.unpack('>I', data[tag_off+8:tag_off+12])[0]
        if n == 0:
            return {
                "type": "curv", "n_entries": 0, "method": "curv_n0",
                "samples": [i / (_TRC_SAMPLE_COUNT - 1) for i in range(_TRC_SAMPLE_COUNT)],
            }
        if n == 1:
            if tag_off + 14 > len(data):
                return {"type": "curv", "n_entries": 1, "method": "curv_n1", "samples": None}
            raw = struct.unpack('>H', data[tag_off+12:tag_off+14])[0]
            gamma = raw / 256.0  # u8.8 fixed point
            samples = [(i / (_TRC_SAMPLE_COUNT - 1)) ** gamma if i > 0 else 0.0
                       for i in range(_TRC_SAMPLE_COUNT)]
            return {"type": "curv", "n_entries": 1, "gamma_single": round(gamma, 4),
                    "method": "curv_n1", "samples": samples}
        # Table n > 1
        bytes_needed = 12 + n * 2
        if tag_off + bytes_needed > len(data):
            return {"type": "curv", "n_entries": n, "method": f"curv_table_{n}", "samples": None}
        table_bytes = data[tag_off+12:tag_off+12+n*2]
        table = struct.unpack(f'>{n}H', table_bytes)
        samples = []
        for i in range(_TRC_SAMPLE_COUNT):
            x = i / (_TRC_SAMPLE_COUNT - 1)
            idx_f = x * (n - 1)
            idx_lo = int(idx_f)
            idx_hi = min(idx_lo + 1, n - 1)
            frac = idx_f - idx_lo
            v = (1.0 - frac) * table[idx_lo] + frac * table[idx_hi]
            samples.append(v / 65535.0)
        return {"type": "curv", "n_entries": n,
                "method": f"curv_table_{n}", "samples": samples}

    if tag_type == b'para':
        if tag_off + 12 > len(data):
            return {"type": "para", "method": "para_unknown", "samples": None}
        function_type = struct.unpack('>H', data[tag_off+8:tag_off+10])[0]
        n_params_by_func = {0: 1, 1: 3, 2: 4, 3: 5, 4: 7}
        n_params = n_params_by_func.get(function_type, 0)
        if tag_off + 12 + n_params * 4 > len(data) or n_params == 0:
            return {"type": "para", "function_type": function_type,
                    "method": f"para_type{function_type}", "samples": None}
        params = []
        for i in range(n_params):
            raw = struct.unpack('>i', data[tag_off+12+i*4:tag_off+12+(i+1)*4])[0]
            params.append(raw / 65536.0)  # s15.16 fixed point
        samples = [_eval_para(function_type, params, i / (_TRC_SAMPLE_COUNT - 1))
                   for i in range(_TRC_SAMPLE_COUNT)]
        return {"type": "para", "function_type": function_type, "params": params,
                "method": f"para_type{function_type}", "samples": samples}

    return {"type": "unknown", "method": "unknown", "samples": None}


def _rms(a: list, b: list) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return float('inf')
    s = sum((a[i] - b[i]) ** 2 for i in range(n))
    return (s / n) ** 0.5


def _classify_trc_samples(samples: Optional[list]) -> tuple:
    """Return (family, gamma_estimate). family ∈ keys of _REFERENCE_TRC_CURVES
    or 'unknown_custom'; gamma_estimate ∈ float or None."""
    if not samples or len(samples) < 4:
        return ("unknown_custom", None)

    # Gamma estimate at the midpoint (input ~0.5)
    import math
    mid_idx = _TRC_SAMPLE_COUNT // 2
    mid_in = mid_idx / (_TRC_SAMPLE_COUNT - 1)
    mid_out = samples[mid_idx]
    gamma_est = None
    if 0 < mid_in < 1 and 0 < mid_out < 1:
        try:
            gamma_est = math.log(mid_out) / math.log(mid_in)
        except (ValueError, ZeroDivisionError):
            gamma_est = None

    # Find the best RMS match against each reference
    best_family = "unknown_custom"
    best_rms = float('inf')
    for name, ref in _REFERENCE_TRC_CURVES.items():
        r = _rms(samples, ref)
        if r < best_rms:
            best_rms = r
            best_family = name

    # RMS tolerance to validate the match
    if best_rms < 0.005:
        if best_family.startswith("gamma_"):
            try:
                gamma_est = float(best_family.split("_")[1])
            except (IndexError, ValueError):
                pass
        return (best_family, gamma_est)

    # No strict match. Snap to a standard gamma if gamma_est is consistent.
    if gamma_est is not None and 0.5 < gamma_est < 4.0:
        for ref_g, fam in [(1.0, "linear"), (1.8, "gamma_1.8"),
                           (2.2, "gamma_2.2"), (2.4, "gamma_2.4")]:
            if abs(gamma_est - ref_g) < 0.05:
                return (fam, gamma_est)

    return ("unknown_custom", gamma_est)


def _classified(auto_value):
    """Wrap a detected classification in the {auto, override, effective} pattern.

    The user override will be added later. For now, override=None
    and effective=auto. The logic for computing effective (override ?? auto)
    is centralized here."""
    return {"auto": auto_value, "override": None, "effective": auto_value}


def analyze_trc(data: bytes, tags: list) -> dict:
    """Analyze the TRC tags (rTRC/gTRC/bTRC/kTRC) and classify the family.

    The ``family`` field follows the {auto, override, effective} pattern to
    leave room for a user override later.

    Returns: {
        "family": {"auto": str, "override": None, "effective": str},
        "per_channel": {"r"|"g"|"b"|"k": {family, gamma_estimate, method}},
        "consistent_across_channels": bool,
        "primary_family_label": str (default FR label, frontend i18n),
    }
    """
    channels = {}
    for tag_sig, key in [(b'rTRC', 'r'), (b'gTRC', 'g'),
                          (b'bTRC', 'b'), (b'kTRC', 'k')]:
        for t in tags:
            if t['sig'].encode('ascii', errors='replace') != tag_sig:
                continue
            curve = _read_trc_curve(data, t['offset'], t['size'])
            fam, gamma_est = _classify_trc_samples(curve.get('samples'))
            channels[key] = {
                "family": fam,
                "gamma_estimate": round(gamma_est, 3) if gamma_est is not None else None,
                "method": curve.get('method', 'unknown'),
            }
            break

    if not channels:
        return {
            "family": _classified("unknown_custom"),
            "per_channel": {},
            "consistent_across_channels": True,
            "primary_family_label": _TRC_FAMILY_LABEL["unknown_custom"],
        }

    # Consistency: same family for all channels AND gamma within ±0.05
    families = {c["family"] for c in channels.values()}
    consistent = len(families) == 1
    if consistent:
        primary = next(iter(families))
        gammas = [c["gamma_estimate"] for c in channels.values()
                  if c.get("gamma_estimate") is not None]
        if len(gammas) > 1 and (max(gammas) - min(gammas)) > 0.05:
            consistent = False
    else:
        # Dominant family in case of divergence
        from collections import Counter
        primary = Counter(c["family"] for c in channels.values()).most_common(1)[0][0]

    return {
        "family": _classified(primary),
        "per_channel": channels,
        "consistent_across_channels": consistent,
        "primary_family_label": _TRC_FAMILY_LABEL.get(primary, primary),
    }


# ─── Layer 5 — Taxonomy ───────────────────────────────────────────────

# Reference xy primaries. Several variants per space to absorb the
# ICC v2 PCS=D50 convention: an sRGB profile stores its primaries
# Bradford-adapted to D50, distinct from the spec's nominal D65 values.
# We compare against all variants and keep the best match. Combined
# tolerance over 3 primaries < 0.012.
_REFERENCE_PRIMARIES_XY = {
    "sRGB": [
        # Nominal D65 (spec)
        {"r": (0.640, 0.330),   "g": (0.300, 0.600),   "b": (0.150, 0.060)},
        # D50-adapted (Bradford, as stored in most ICC v2 profiles)
        {"r": (0.6485, 0.3309), "g": (0.3212, 0.5978), "b": (0.1559, 0.0660)},
    ],
    "AdobeRGB": [
        {"r": (0.640, 0.330),   "g": (0.210, 0.710),   "b": (0.150, 0.060)},
        {"r": (0.6485, 0.3309), "g": (0.2302, 0.7016), "b": (0.1559, 0.0660)},
    ],
    "ProPhoto/ROMM": [
        # ProPhoto is already natively D50 — no adapted variant needed
        {"r": (0.7347, 0.2653), "g": (0.1596, 0.8404), "b": (0.0366, 0.0001)},
    ],
    "DCI-P3": [
        # Display P3 (D65) and DCI-P3 (D63) — nominal + D50-adapted variants
        {"r": (0.680, 0.320),   "g": (0.265, 0.690),   "b": (0.150, 0.060)},
        {"r": (0.6867, 0.3217), "g": (0.2657, 0.6904), "b": (0.1574, 0.0668)},
    ],
    "Rec.2020": [
        {"r": (0.708, 0.292),   "g": (0.170, 0.797),   "b": (0.131, 0.046)},
        {"r": (0.7170, 0.2939), "g": (0.1719, 0.7905), "b": (0.1338, 0.0521)},
    ],
    "Wide Gamut RGB": [
        # Wide Gamut RGB (Adobe) — already native D50
        {"r": (0.7350, 0.2650), "g": (0.1150, 0.8260), "b": (0.1570, 0.0180)},
    ],
}


# Reference xy whitepoints (standard CIE illuminants)
_REFERENCE_WHITEPOINTS_XY = {
    "D50": (0.3457, 0.3585),
    "D55": (0.3324, 0.3474),
    "D65": (0.3127, 0.3290),
    "D75": (0.2990, 0.3149),
}


# Thresholds on the lcms2 gamut volume (convex-hull grid 33 units).
# Empirically calibrated on the macOS ColorSync profiles:
#   sRGB         ≈ 920 000
#   AdobeRGB1998 ≈ 1 300 000
#   Display P3   ≈ 1 350 000
#   ITU-2020     ≈ 1 970 000
#   ROMM RGB     ≈ 2 830 000
# Thresholds placed halfway between tiers, preferring wider spaces in case
# of doubt (DCI-P3 vs AdobeRGB false positives preferred over under-classing).
# To be refined with user feedback.
_GAMUT_CLASS_THRESHOLDS = [
    (700_000,   "under-sRGB"),
    (1_150_000, "sRGB"),
    (1_330_000, "AdobeRGB"),
    (1_700_000, "DCI-P3"),
    (2_400_000, "Rec.2020"),
    (3_200_000, "ProPhoto"),
    (float('inf'), "over-gamut"),
]


def _xyz_to_xy(xyz):
    x, y, z = xyz
    s = x + y + z
    if s == 0:
        return (0.0, 0.0)
    return (x / s, y / s)


def analyze_taxonomy(data: bytes, tags: list, gamut_info: dict) -> dict:
    """Classify the profile by whitepoint, primary family, gamut class.

    All classifications follow {auto, override, effective}.
    The raw measurements (whitepoint_xy) are exposed as-is.

    Returns: {
        "gamut_class":   {auto, override, effective},
        "whitepoint":    {auto, override, effective},
        "whitepoint_xy": [x, y] | None,
        "primary_family":{auto, override, effective},
    }
    """
    # Whitepoint
    wp = HpProprietaryDecoder.extract_xyz_tag(data, b'wtpt', tags)
    wp_xy = _xyz_to_xy(wp) if wp else None
    wp_label = "custom"
    if wp_xy:
        for name, ref in _REFERENCE_WHITEPOINTS_XY.items():
            d = ((wp_xy[0] - ref[0]) ** 2 + (wp_xy[1] - ref[1]) ** 2) ** 0.5
            if d < 0.002:
                wp_label = name
                break

    # Primaries (RGB only). For each candidate space, compare against all
    # its variants (nominal + D50-adapted where applicable) and keep the
    # best overall distance.
    primary_family = "custom"
    r_xyz = HpProprietaryDecoder.extract_xyz_tag(data, b'rXYZ', tags)
    g_xyz = HpProprietaryDecoder.extract_xyz_tag(data, b'gXYZ', tags)
    b_xyz = HpProprietaryDecoder.extract_xyz_tag(data, b'bXYZ', tags)
    if r_xyz and g_xyz and b_xyz:
        r_xy = _xyz_to_xy(r_xyz)
        g_xy = _xyz_to_xy(g_xyz)
        b_xy = _xyz_to_xy(b_xyz)
        best_dist = float('inf')
        best_name = "custom"
        for name, variants in _REFERENCE_PRIMARIES_XY.items():
            for refs in variants:
                dist = (
                    (r_xy[0] - refs["r"][0]) ** 2 + (r_xy[1] - refs["r"][1]) ** 2 +
                    (g_xy[0] - refs["g"][0]) ** 2 + (g_xy[1] - refs["g"][1]) ** 2 +
                    (b_xy[0] - refs["b"][0]) ** 2 + (b_xy[1] - refs["b"][1]) ** 2
                ) ** 0.5
                if dist < best_dist:
                    best_dist = dist
                    best_name = name
        if best_dist < 0.012:
            primary_family = best_name

    # Gamut class from the relative volume
    gamut_class = "custom"
    rel = (gamut_info or {}).get("relative")
    if rel is not None:
        for threshold, label in _GAMUT_CLASS_THRESHOLDS:
            if rel < threshold:
                gamut_class = label
                break

    return {
        "gamut_class": _classified(gamut_class),
        "whitepoint": _classified(wp_label),
        "whitepoint_xy": [round(wp_xy[0], 4), round(wp_xy[1], 4)] if wp_xy else None,
        "primary_family": _classified(primary_family),
    }


# ─── Layer 6 — Per-tag details for popover ────────────────────────────

# Dictionary of standard ICC tags + proprietary HP tags.
# Format: signature → English name. Tags not listed are displayed by
# signature alone in the frontend.
_TAG_NAMES = {
    # Description / text
    "desc": "Profile description",
    "cprt": "Copyright",
    "dmnd": "Device manufacturer description",
    "dmdd": "Device model description",
    "targ": "Characterization target (CGATS)",
    "vued": "Viewing conditions description",
    # White / black points
    "wtpt": "Profile connection space whitepoint",
    "bkpt": "Profile blackpoint",
    # RGB primaries (ICC matrices)
    "rXYZ": "Red primary XYZ",
    "gXYZ": "Green primary XYZ",
    "bXYZ": "Blue primary XYZ",
    # TRC (tone reproduction curves)
    "rTRC": "Red tone reproduction curve",
    "gTRC": "Green tone reproduction curve",
    "bTRC": "Blue tone reproduction curve",
    "kTRC": "Gray tone reproduction curve",
    # LUTs (Device ↔ PCS)
    "A2B0": "Device → PCS, perceptual intent",
    "A2B1": "Device → PCS, relative colorimetric",
    "A2B2": "Device → PCS, saturation intent",
    "B2A0": "PCS → Device, perceptual intent",
    "B2A1": "PCS → Device, relative colorimetric",
    "B2A2": "PCS → Device, saturation intent",
    "gamt": "Out-of-gamut indicator",
    # Adaptations
    "chad": "Chromatic adaptation matrix",
    "chrm": "Chromaticity",
    # Measurements
    "meas": "Measurement type",
    "tech": "Technology signature",
    "view": "Viewing conditions",
    # Named colors and preview
    "ncl2": "Named colors v2",
    "pre0": "Preview, perceptual",
    "pre1": "Preview, relative",
    "pre2": "Preview, saturation",
    # PostScript
    "ps2s": "PostScript 2 CRD",
    "ps2i": "PostScript 2 intent",
    "psd0": "PostScript 2 CRD0 perceptual",
    "psd1": "PostScript 2 CRD1 relative",
    "psd2": "PostScript 2 CRD2 saturation",
    "psd3": "PostScript 2 CRD3 absolute",
    # HP proprietary (Ingenium)
    "HP90": "HP Ingenium signature",
    "HP91": "HP Ingenium config (zut8 compressed)",
    "HP92": "HP Ingenium reserved",
    "HP93": "HP Ingenium reserved",
    "HPgt": "HP gamut tag",
}


def _decode_xyz_tag(data: bytes, tag: dict) -> dict:
    """Decode an XYZ tag: returns X/Y/Z + xy chromaticities."""
    off = tag["offset"]
    if off + 8 > len(data):
        return {}
    if data[off:off+4] != b'XYZ ':
        return {}
    payload = data[off+8:off+tag["size"]]
    if len(payload) < 12:
        return {}
    X = struct.unpack('>i', payload[0:4])[0] / 65536.0
    Y = struct.unpack('>i', payload[4:8])[0] / 65536.0
    Z = struct.unpack('>i', payload[8:12])[0] / 65536.0
    s = X + Y + Z
    xy = [round(X / s, 4), round(Y / s, 4)] if s > 0 else None
    return {
        "kind": "xyz",
        "X": round(X, 6), "Y": round(Y, 6), "Z": round(Z, 6),
        "xy": xy,
    }


def _decode_trc_tag(data: bytes, tag: dict) -> dict:
    """Decode a TRC tag: type curv n=0/1/table or para function_type N."""
    curve = _read_trc_curve(data, tag["offset"], tag["size"])
    fam, gamma_est = _classify_trc_samples(curve.get("samples"))
    out = {
        "kind": "trc",
        "type": curve.get("type", "unknown"),
        "method": curve.get("method", "unknown"),
        "family": fam,
        "gamma_estimate": round(gamma_est, 3) if gamma_est is not None else None,
    }
    if curve.get("n_entries") is not None:
        out["n_entries"] = curve["n_entries"]
    if curve.get("function_type") is not None:
        out["function_type"] = curve["function_type"]
    if curve.get("gamma_single") is not None:
        out["gamma_single"] = curve["gamma_single"]
    return out


# Curve sampling limit for the popover (UI sparkline). Beyond this size,
# we downsample (the frontend SVG renders no better at 4096 points than at
# 64 and the network payload explodes).
_LUT_CURVE_MAX_SAMPLES = 64


def _downsample_curve(samples: list, target: int = _LUT_CURVE_MAX_SAMPLES) -> list:
    """Downsample a curve to ``target`` evenly spaced points.
    Returns the list as-is if already ≤ target."""
    n = len(samples)
    if n <= target:
        return [round(float(v), 6) for v in samples]
    out = []
    for i in range(target):
        idx = round(i * (n - 1) / (target - 1))
        out.append(round(float(samples[idx]), 6))
    return out


def _is_curve_linear(samples: list, eps: float = 0.005) -> bool:
    """True if the curve is ≈ y = x within eps over 16 points."""
    if not samples or len(samples) < 2:
        return False
    n = len(samples)
    for i in range(min(16, n)):
        idx = round(i * (n - 1) / 15)
        x = idx / (n - 1) if n > 1 else 0
        if abs(samples[idx] - x) > eps:
            return False
    return True


def _read_mft_curve(data: bytes, off: int, n_entries: int, bits: int) -> list:
    """Read an input/output curve from an mft1 (8-bit) or mft2 (16-bit).
    Returns a float list [0..1] of size n_entries."""
    if bits == 8:
        end = off + n_entries
        if end > len(data):
            return []
        return [b / 255.0 for b in data[off:end]]
    end = off + n_entries * 2
    if end > len(data):
        return []
    return [v / 65535.0 for v in struct.unpack(f'>{n_entries}H', data[off:end])]


def _read_mft1_mft2(data: bytes, off: int, size: int, lut_type: str) -> dict:
    """Decode an mft1 (v2 8-bit) or mft2 (v2 16-bit) tag.

    Layout (ICC spec):
      offset 0-3 : type signature
      offset 4-7 : reserved
      offset 8   : input channels
      offset 9   : output channels
      offset 10  : CLUT grid points (n)
      offset 11  : padding
      offset 12-47 : E-matrix 3×3 (9 × s15.16)
      mft1 : input curves (input_ch × 256 × 1 byte) → CLUT → output curves (output_ch × 256 × 1 byte)
      mft2 : offset 48-49 = n_input_entries, 50-51 = n_output_entries
             then input curves (input_ch × n_in × 2) → CLUT → output curves (output_ch × n_out × 2)
    """
    if off + 12 > len(data):
        return {}
    input_ch = data[off + 8]
    output_ch = data[off + 9]
    grid = data[off + 10]

    out: dict = {
        "lut_type": lut_type,
        "input_channels": int(input_ch),
        "output_channels": int(output_ch),
        "precision_bits": 8 if lut_type == "mft1" else 16,
    }

    # E-matrix 3×3 — always present, fixed position
    if off + 48 <= len(data):
        matrix = []
        for r in range(3):
            row = []
            for c in range(3):
                raw = struct.unpack(
                    '>i',
                    data[off + 12 + (r * 3 + c) * 4:off + 16 + (r * 3 + c) * 4],
                )[0]
                row.append(round(raw / 65536.0, 6))
            matrix.append(row)
        # Expose the matrix only if it is not the trivial identity
        # (typical case: LUT-only without a pre-rotation matrix)
        if not _is_identity_matrix(matrix):
            out["matrix_3x3"] = matrix
            out["has_m_matrix"] = True
        else:
            out["has_m_matrix"] = False

    # Offsets and entry counts depend on the type
    if lut_type == "mft1":
        n_in = 256
        n_out = 256
        cur_off = off + 48
    else:
        if off + 52 > len(data):
            return out
        n_in = struct.unpack('>H', data[off + 48:off + 50])[0]
        n_out = struct.unpack('>H', data[off + 50:off + 52])[0]
        cur_off = off + 52

    bits = 8 if lut_type == "mft1" else 16
    byte_per = bits // 8

    # Input curves
    input_curves = []
    channel_labels_in = _channel_labels(input_ch)
    for ch in range(input_ch):
        samples = _read_mft_curve(data, cur_off, n_in, bits)
        cur_off += n_in * byte_per
        if not samples:
            continue
        input_curves.append({
            "channel": channel_labels_in[ch] if ch < len(channel_labels_in) else f"c{ch}",
            "n_samples": n_in,
            "samples": _downsample_curve(samples),
            "linear": _is_curve_linear(samples),
        })
    if input_curves:
        out["input_curves"] = input_curves
        out["has_a_curves"] = True
    else:
        out["has_a_curves"] = False

    # CLUT: we return ONLY the dimensions (the raw payload is too big for
    # the popover; the 3D rendering will use it later)
    if grid > 0 and input_ch > 0:
        out["clut_dimensions"] = [int(grid)] * int(input_ch)
        clut_size_bytes = (grid ** input_ch) * output_ch * byte_per
        cur_off += clut_size_bytes

    # Output curves
    output_curves = []
    channel_labels_out = _channel_labels(output_ch, pcs=True)
    for ch in range(output_ch):
        samples = _read_mft_curve(data, cur_off, n_out, bits)
        cur_off += n_out * byte_per
        if not samples:
            continue
        output_curves.append({
            "channel": channel_labels_out[ch] if ch < len(channel_labels_out) else f"c{ch}",
            "n_samples": n_out,
            "samples": _downsample_curve(samples),
            "linear": _is_curve_linear(samples),
        })
    if output_curves:
        out["output_curves"] = output_curves
        out["has_b_curves"] = True
    else:
        out["has_b_curves"] = False

    return out


def _is_identity_matrix(m: list) -> bool:
    """3×3 matrix ≈ identity within 1e-4."""
    if not m or len(m) != 3:
        return False
    expected = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    for r in range(3):
        for c in range(3):
            if abs(m[r][c] - expected[r][c]) > 1e-4:
                return False
    return True


def _channel_labels(n: int, pcs: bool = False) -> list:
    """Channel labels based on the count. `pcs=True` → XYZ/Lab for the
    output curves that project into PCS."""
    if pcs:
        if n == 3:
            return ["X", "Y", "Z"]
        if n == 4:
            return ["C", "M", "Y", "K"]
    if n == 3:
        return ["R", "G", "B"]
    if n == 4:
        return ["C", "M", "Y", "K"]
    if n == 1:
        return ["K"]
    return [f"c{i}" for i in range(n)]


def _read_mab_curve_at(data: bytes, off: int) -> dict:
    """Read a v4 curve (curv or para) at the given offset. Returns the
    structure from _read_trc_curve (samples + metadata)."""
    if off + 8 > len(data):
        return {}
    # The tag starts with 'curv' or 'para'; the length is not explicit
    # in the sub-header — we compute it from the content.
    ttype = data[off:off+4]
    if ttype == b'curv':
        if off + 12 > len(data):
            return {}
        n = struct.unpack('>I', data[off+8:off+12])[0]
        size = 12 + max(0, n) * 2
        return _read_trc_curve(data, off, size)
    if ttype == b'para':
        if off + 12 > len(data):
            return {}
        function_type = struct.unpack('>H', data[off+8:off+10])[0]
        n_params = {0: 1, 1: 3, 2: 4, 3: 5, 4: 7}.get(function_type, 0)
        size = 12 + n_params * 4
        return _read_trc_curve(data, off, size)
    return {}


def _read_mab_mba(data: bytes, off: int, size: int, lut_type: str) -> dict:
    """Decode a v4 mAB (LutAToBType) or mBA (LutBToAType) tag.

    Layout:
      0-3   : type signature
      4-7   : reserved
      8     : input channels
      9     : output channels
      10-11 : reserved
      12-15 : offset B curves (uint32, relative to tag start, 0 if absent)
      16-19 : offset matrix (uint32, 0 if absent)
      20-23 : offset M curves (uint32, 0 if absent)
      24-27 : offset CLUT (uint32, 0 if absent)
      28-31 : offset A curves (uint32, 0 if absent)
    """
    if off + 32 > len(data):
        return {"lut_type": lut_type.strip()}

    input_ch = data[off + 8]
    output_ch = data[off + 9]
    out: dict = {
        "lut_type": lut_type.strip(),
        "input_channels": int(input_ch),
        "output_channels": int(output_ch),
        "precision_bits": 16,
    }
    off_b = struct.unpack('>I', data[off+12:off+16])[0]
    off_mat = struct.unpack('>I', data[off+16:off+20])[0]
    off_m = struct.unpack('>I', data[off+20:off+24])[0]
    off_clut = struct.unpack('>I', data[off+24:off+28])[0]
    off_a = struct.unpack('>I', data[off+28:off+32])[0]

    out["has_a_curves"] = off_a > 0
    out["has_m_matrix"] = off_mat > 0
    out["has_b_curves"] = off_b > 0
    out["has_clut"] = off_clut > 0

    # 3×4 matrix (3×3 + translation offset) if present
    if off_mat > 0 and off + off_mat + 48 <= len(data):
        mat_base = off + off_mat
        matrix = []
        for r in range(3):
            row = []
            for c in range(3):
                raw = struct.unpack(
                    '>i',
                    data[mat_base + (r * 3 + c) * 4:mat_base + 4 + (r * 3 + c) * 4],
                )[0]
                row.append(round(raw / 65536.0, 6))
            matrix.append(row)
        out["matrix_3x3"] = matrix

    # A curves: input_channels curves (the count = input_channels for mAB,
    # output_channels for mBA — but in the semantics of the mBA tag, "A" is
    # always the Device side)
    n_a = input_ch if lut_type.strip() == "mAB" else output_ch
    if off_a > 0:
        input_curves = []
        cur = off + off_a
        labels = _channel_labels(n_a)
        for ch in range(n_a):
            curve = _read_mab_curve_at(data, cur)
            if not curve or not curve.get("samples"):
                break
            input_curves.append({
                "channel": labels[ch] if ch < len(labels) else f"c{ch}",
                "n_samples": len(curve["samples"]),
                "samples": _downsample_curve(curve["samples"]),
                "linear": _is_curve_linear(curve["samples"]),
            })
            # Advance past one curve: we don't know its exact size without
            # parsing. Recompute via the type.
            cur = _advance_past_curve(data, cur)
            if cur is None:
                break
        if input_curves:
            out["input_curves"] = input_curves

    # CLUT: dimensions only (16 bytes of v4 CLUT header)
    if off_clut > 0 and off + off_clut + 20 <= len(data):
        # 16 bytes: grid points per channel (up to 16 channels)
        clut_base = off + off_clut
        grid_per_channel = list(data[clut_base:clut_base + 16])
        out["clut_dimensions"] = [int(g) for g in grid_per_channel[:input_ch] if g > 0]

    # B curves: output_channels curves for mAB, input_channels for mBA
    n_b = output_ch if lut_type.strip() == "mAB" else input_ch
    if off_b > 0:
        output_curves = []
        cur = off + off_b
        labels = _channel_labels(n_b, pcs=(lut_type.strip() == "mAB"))
        for ch in range(n_b):
            curve = _read_mab_curve_at(data, cur)
            if not curve or not curve.get("samples"):
                break
            output_curves.append({
                "channel": labels[ch] if ch < len(labels) else f"c{ch}",
                "n_samples": len(curve["samples"]),
                "samples": _downsample_curve(curve["samples"]),
                "linear": _is_curve_linear(curve["samples"]),
            })
            cur = _advance_past_curve(data, cur)
            if cur is None:
                break
        if output_curves:
            out["output_curves"] = output_curves

    return out


def _advance_past_curve(data: bytes, off: int) -> Optional[int]:
    """Compute the offset after the end of a v4 curve (curv or para),
    with 4-byte padding. Returns None if parsing fails."""
    if off + 8 > len(data):
        return None
    ttype = data[off:off+4]
    if ttype == b'curv':
        n = struct.unpack('>I', data[off+8:off+12])[0]
        size = 12 + max(0, n) * 2
    elif ttype == b'para':
        function_type = struct.unpack('>H', data[off+8:off+10])[0]
        n_params = {0: 1, 1: 3, 2: 4, 3: 5, 4: 7}.get(function_type, 0)
        size = 12 + n_params * 4
    else:
        return None
    # Padding to 4 bytes
    return off + ((size + 3) // 4) * 4


def _decode_gamt_tag(data: bytes, tag: dict) -> dict:
    """gamt tag: in-gamut/out-of-gamut indicator.
    Structure: mft1 or mft2 type with 1 output channel. We return only
    the metadata (no RGB sparklines, which make no sense for this
    signalling tag)."""
    out = {"kind": "lut", "lut_type": "gamut", "is_gamut_check": True}
    if tag["offset"] + 12 > len(data):
        return out
    sub_type = data[tag["offset"]:tag["offset"]+4].decode("ascii", errors="replace").strip()
    out["underlying_type"] = sub_type
    if sub_type in ("mft1", "mft2"):
        out["input_channels"] = int(data[tag["offset"] + 8])
        out["output_channels"] = int(data[tag["offset"] + 9])  # ≈ 1
        out["grid_points"] = int(data[tag["offset"] + 10])
        out["precision_bits"] = 8 if sub_type == "mft1" else 16
    return out


def _decode_lut_tag(data: bytes, tag: dict) -> dict:
    """Decode a LUT tag — enriched version.

    For mft1/mft2/mAB/mBA: matrix + input/output curves + CLUT dimensions.
    For gamt: 1D output LUT sub-case (no RGB sparklines).
    """
    if tag.get("sig") == "gamt":
        return _decode_gamt_tag(data, tag)

    off = tag["offset"]
    size = tag["size"]
    if off + 12 > len(data):
        return {"kind": "lut"}
    sub_type = data[off:off+4].decode("ascii", errors="replace").strip()
    if sub_type == "mft1":
        out = _read_mft1_mft2(data, off, size, "mft1")
    elif sub_type == "mft2":
        out = _read_mft1_mft2(data, off, size, "mft2")
    elif sub_type == "mAB":
        out = _read_mab_mba(data, off, size, "mAB ")
    elif sub_type == "mBA":
        out = _read_mab_mba(data, off, size, "mBA ")
    else:
        out = {"lut_type": sub_type}
    out["kind"] = "lut"
    return out


def _decode_text_tag(data: bytes, tag: dict) -> dict:
    """Decode a text tag (desc/cprt/dmnd/dmdd/targ/vued)."""
    text = HpProprietaryDecoder.extract_text_tag(
        data, tag["sig"].encode("ascii", errors="replace"),
        [tag],
    )
    if not text:
        return {}
    # Truncate very long texts (e.g. targ CGATS which can be several KB)
    if len(text) > 800:
        return {"kind": "text", "text": text[:800], "truncated": True,
                "full_length": len(text)}
    return {"kind": "text", "text": text}


def _decode_hp91(data: bytes, tag: dict) -> dict:
    """HP91 tag — note (full decoding is future work)."""
    raw = HpProprietaryDecoder.extract_hp91_zut8(data, tag["offset"], tag["size"])
    return {
        "kind": "hp91",
        "decompressed_bytes": len(raw) if raw else 0,
        "deferred_to": "16.2",
    }


def _decode_hp90(data: bytes, tag: dict) -> dict:
    """HP90 tag — HP Ingenium text signature."""
    txt = HpProprietaryDecoder.extract_hp90(data, tag["offset"], tag["size"])
    return {"kind": "text", "text": txt} if txt else {}


def _build_tags_details(data: bytes, tags: list) -> list:
    """Build the enriched per-tag details list for the popover."""
    out = []
    for t in tags:
        sig = t["sig"]
        entry = {
            "signature": sig,
            "name": _TAG_NAMES.get(sig, ""),
            "type": t.get("type", ""),
            "size": t.get("size", 0),
            "decoded": None,
        }
        ttype = (t.get("type") or "").strip()
        try:
            if sig == "HP91":
                entry["decoded"] = _decode_hp91(data, t)
            elif sig == "HP90":
                entry["decoded"] = _decode_hp90(data, t)
            elif sig in ("rTRC", "gTRC", "bTRC", "kTRC") and ttype in ("curv", "para"):
                entry["decoded"] = _decode_trc_tag(data, t)
            elif sig in ("wtpt", "bkpt", "rXYZ", "gXYZ", "bXYZ") and ttype == "XYZ":
                entry["decoded"] = _decode_xyz_tag(data, t)
            elif sig in ("A2B0", "A2B1", "A2B2", "B2A0", "B2A1", "B2A2", "gamt"):
                entry["decoded"] = _decode_lut_tag(data, t)
            elif sig in ("desc", "cprt", "dmnd", "dmdd", "targ", "vued") and \
                 ttype in ("mluc", "text", "desc"):
                entry["decoded"] = _decode_text_tag(data, t)
        except Exception as e:  # noqa: BLE001
            logger.debug("Decode failed for tag %s: %s", sig, e)
        out.append(entry)
    return out


# ─── Layer 7 — ICC conformance ────────────────────────────────────────

# Required tags per device_class (ICC v2/v4). Source: ISO 15076-1 spec.
# desc/cprt/wtpt are required for ALL profiles.
_BASE_REQUIRED_TAGS = ("desc", "cprt", "wtpt")

_REQUIRED_TAGS_BY_CLASS = {
    "prtr": {
        # Printer profile: LUT-based required
        "any_of": [("A2B0", "B2A0")],
    },
    "mntr": {
        # Display profile: matrix-based OR LUT-based
        "any_of": [
            ("rXYZ", "gXYZ", "bXYZ", "rTRC", "gTRC", "bTRC"),
            ("A2B0", "B2A0"),
        ],
    },
    "scnr": {
        # Scanner profile: matrix-based OR LUT-based
        "any_of": [
            ("rXYZ", "gXYZ", "bXYZ", "rTRC", "gTRC", "bTRC"),
            ("A2B0",),
        ],
    },
    "cmra": {
        "any_of": [
            ("rXYZ", "gXYZ", "bXYZ", "rTRC", "gTRC", "bTRC"),
            ("A2B0",),
        ],
    },
    "spac": {
        "any_of": [
            ("rXYZ", "gXYZ", "bXYZ", "rTRC", "gTRC", "bTRC"),
            ("A2B0", "B2A0"),
        ],
    },
    "link": {"any_of": [("A2B0",)]},
    "abst": {"any_of": [("A2B0",)]},
    "nmcl": {"any_of": [("ncl2",)]},
}


_ICC_VERSION_MAJOR_KNOWN = {2, 4}

_LUT_TYPES_BY_VERSION = {
    2: {"mft1", "mft2"},
    4: {"mft1", "mft2", "mAB ", "mBA "},  # v4 also accepts the v2 ones
}


def _make_issue(severity: str, code: str, message: str,
                 detail: str = "") -> dict:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "detail": detail,
    }


def validate_conformance(data: bytes, tags: list, header: dict) -> dict:
    """Check the ICC v2/v4 conformance of the profile.

    Returns {compliant, level, issues, summary}. issues is a list of
    dicts {severity, code, message, detail}.

    Severity: "error" → non_compliant; "warning" → warnings;
    "info" → compliant. The overall level is the worst of the three.
    """
    issues: list = []

    # 1. acsp signature
    if len(data) < 132:
        issues.append(_make_issue(
            "error", "FILE_TOO_SHORT",
            "File too short to be a valid ICC profile (< 132 bytes)",
        ))
        return _finalize_conformance(issues)

    if data[36:40] != b'acsp':
        issues.append(_make_issue(
            "error", "MISSING_ACSP_SIGNATURE",
            "'acsp' signature missing at offset 36 — file is not a valid ICC profile",
        ))
        return _finalize_conformance(issues)

    # 2. Header version
    icc_version = header.get("icc_version", "")
    major = int(icc_version.split(".")[0]) if icc_version and icc_version[0].isdigit() else None
    if major is not None and major not in _ICC_VERSION_MAJOR_KNOWN:
        issues.append(_make_issue(
            "warning", "UNKNOWN_ICC_VERSION",
            f"Unexpected ICC version ({icc_version}) — v2 or v4 expected",
        ))

    # 3. Consistent tag table
    file_size = len(data)
    profile_size = header.get("profile_size", file_size)
    if profile_size > file_size:
        issues.append(_make_issue(
            "error", "PROFILE_SIZE_MISMATCH",
            f"Declared profile size ({profile_size}) > actual file size ({file_size})",
        ))

    tag_sigs_present = {t["sig"] for t in tags}
    # Offset/size checking + overlap detection
    # (tags sharing exactly the same offset+size are OK:
    #  this is the standard TRC deduplication).
    seen_ranges: dict = {}  # (offset, size) → sig — for OK dedup
    for t in tags:
        off = t["offset"]
        sz = t["size"]
        if off + sz > file_size:
            issues.append(_make_issue(
                "error", "TAG_OUT_OF_BOUNDS",
                f"Tag '{t['sig']}': offset {off} + size {sz} exceeds file size ({file_size})",
            ))
        key = (off, sz)
        if key in seen_ranges:
            # Legitimate case: tags sharing the same offset+size (identical
            # deduplicated TRCs). We do not flag it.
            continue
        seen_ranges[key] = t["sig"]

    # Detection of partial overlaps (without an exact match)
    sorted_tags = sorted(tags, key=lambda x: x["offset"])
    for i in range(len(sorted_tags) - 1):
        a = sorted_tags[i]
        b = sorted_tags[i + 1]
        a_end = a["offset"] + a["size"]
        if a_end > b["offset"] and (a["offset"], a["size"]) != (b["offset"], b["size"]):
            # Non-dedup overlap
            issues.append(_make_issue(
                "error", "TAG_TABLE_OVERLAP",
                f"Partial overlap between tags '{a['sig']}' and '{b['sig']}'",
            ))

    # 4. Required tags per device_class
    device_class = header.get("device_class", "").strip()
    for required in _BASE_REQUIRED_TAGS:
        if required not in tag_sigs_present:
            issues.append(_make_issue(
                "error", "MISSING_REQUIRED_TAG",
                f"Required tag '{required}' missing",
                detail=f"Required for all ICC profiles (device_class={device_class})",
            ))

    rules = _REQUIRED_TAGS_BY_CLASS.get(device_class)
    if rules:
        any_of = rules.get("any_of", [])
        # At least one of the alternatives must be present in full
        satisfied = any(
            all(tag in tag_sigs_present for tag in alt)
            for alt in any_of
        )
        if not satisfied:
            alternatives_str = " | ".join(
                "(" + ", ".join(alt) + ")" for alt in any_of
            )
            issues.append(_make_issue(
                "error", "MISSING_REQUIRED_TAG_SET",
                f"None of the required tag sets for device_class '{device_class}' "
                f"is satisfied: {alternatives_str}",
            ))

    # 5. Whitepoint: the ICC v4 spec requires D50 (PCS-aligned). In practice,
    # many v2 display profiles store the media whitepoint (often D65) —
    # historically tolerated usage. So we only flag cases where the whitepoint
    # matches NO standard illuminant (D50/D55/D65/D75) — a symptom of a
    # corrupted or exotic profile.
    wp = HpProprietaryDecoder.extract_xyz_tag(data, b'wtpt', tags)
    if wp:
        x, y = _xyz_to_xy(wp)
        min_d = float('inf')
        nearest = "?"
        for name, ref in _REFERENCE_WHITEPOINTS_XY.items():
            d = ((x - ref[0]) ** 2 + (y - ref[1]) ** 2) ** 0.5
            if d < min_d:
                min_d = d
                nearest = name
        if min_d > 0.005:
            issues.append(_make_issue(
                "warning", "INVALID_WHITEPOINT",
                f"PCS whitepoint x={x:.4f} y={y:.4f} matches no standard "
                f"illuminant (nearest: {nearest} at Δxy={min_d:.4f})",
            ))
        if abs(wp[1] - 1.0) > 0.05:
            # Y should be ≈ 1.0 for a normalized whitepoint. A deviation
            # > 5% signals a missing normalization or an unnormalized
            # absolute-measurement profile.
            issues.append(_make_issue(
                "warning", "WHITEPOINT_Y_NOT_UNIT",
                f"Whitepoint Y={wp[1]:.4f} significantly differs from 1.0 "
                f"(profile possibly not normalized)",
            ))

    # 6. Consistency of header version vs LUT types present
    if major in _LUT_TYPES_BY_VERSION:
        accepted = _LUT_TYPES_BY_VERSION[major]
        for t in tags:
            if t.get("type") in ("mft1", "mft2", "mAB", "mBA"):
                ttype = t["type"]
                # Align mAB/mBA with a trailing space to compare
                ttype_norm = ttype + " " if ttype in ("mAB", "mBA") else ttype
                if ttype_norm not in accepted and ttype not in accepted:
                    issues.append(_make_issue(
                        "warning", "LUT_TYPE_VERSION_MISMATCH",
                        f"Tag '{t['sig']}' uses LUT type '{ttype}' "
                        f"not standard for ICC v{major}",
                    ))

    return _finalize_conformance(issues)


def _finalize_conformance(issues: list) -> dict:
    """Compute level + summary from the list of issues."""
    n_err = sum(1 for i in issues if i["severity"] == "error")
    n_warn = sum(1 for i in issues if i["severity"] == "warning")
    n_info = sum(1 for i in issues if i["severity"] == "info")
    if n_err > 0:
        level = "non_compliant"
        compliant = False
    elif n_warn > 0:
        level = "warnings"
        compliant = True
    else:
        level = "compliant"
        compliant = True
    return {
        "compliant": compliant,
        "level": level,
        "issues": issues,
        "summary": {
            "errors": n_err,
            "warnings": n_warn,
            "infos": n_info,
        },
    }


# ─── Layer 8 — Structured internals ───────────────────────────────────

def _build_custom_mapping_rotations(hp91_config: dict, hp91_raw: str) -> list:
    """Extract the hue rotations from the HP91 config.

    [HYPOTHESIS not formally verified] Looks for the color_mapping_by_hue.*
    sections with hue_in/hue_out pairs or similar. Returns a list of
    {hue_in_deg, hue_out_deg, delta} sorted by hue_in_deg. If nothing usable,
    returns [].
    """
    rotations: list = []
    if not hp91_config:
        return rotations
    # Look first in hp91_config (parsed). The by_hue sections may be
    # nested: color_mapping_by_hue.R, ...G, ...B
    for section_name, section in hp91_config.items():
        if not isinstance(section, dict):
            continue
        sn = section_name.lower()
        if "color_mapping_by_hue" not in sn and "mapping_by_hue" not in sn:
            continue
        # Assumes hue_in/hue_out pairs
        in_key = next((k for k in section if "hue_in" in k.lower()), None)
        out_key = next((k for k in section if "hue_out" in k.lower()), None)
        if in_key and out_key:
            try:
                h_in = float(section[in_key])
                h_out = float(section[out_key])
                rotations.append({
                    "hue_in_deg": round(h_in, 2),
                    "hue_out_deg": round(h_out, 2),
                    "delta": round(h_out - h_in, 2),
                    "primary": section_name,
                })
            except (TypeError, ValueError):
                pass

    # Fallback: regex on the raw text to spot the pairs
    if not rotations and hp91_raw:
        # source.X = ... target.X = ... where X is R/G/B/C/M/Y
        for prim in ["red", "green", "blue", "cyan", "magenta", "yellow"]:
            src = re.search(rf'source\.{prim}\s*=\s*([\d\.\-]+)', hp91_raw, re.IGNORECASE)
            tgt = re.search(rf'target\.{prim}\s*=\s*([\d\.\-]+)', hp91_raw, re.IGNORECASE)
            if src and tgt:
                try:
                    h_in = float(src.group(1))
                    h_out = float(tgt.group(1))
                    rotations.append({
                        "hue_in_deg": round(h_in, 2),
                        "hue_out_deg": round(h_out, 2),
                        "delta": round(h_out - h_in, 2),
                        "primary": prim,
                    })
                except ValueError:
                    pass

    rotations.sort(key=lambda r: r["hue_in_deg"])
    return rotations


def _build_internals(insp: "ProfileInspection") -> dict:
    """Build the internals block consumed by the frontend Internals view."""
    # HP91 — extraction of recognizable business fields
    hp91_present = bool(insp.is_hp_ingenium) or bool(insp.hp91_config)
    hp91_raw_fields: dict = {}
    if isinstance(insp.hp91_config, dict):
        for section, kv in insp.hp91_config.items():
            if isinstance(kv, dict):
                for k, v in kv.items():
                    # defensive cap (bounds pathological values) without truncating the real
                    # HP91: color_mapping is ~535 chars, the whole tag ~2.2 KB (cf. the 200-char
                    # display bug that cut color_mapping at "space_target.cyan").
                    hp91_raw_fields[f"{section}.{k}"] = str(v)[:2000]

    def _find(*needles) -> str:
        for k, v in hp91_raw_fields.items():
            kl = k.lower()
            for needle in needles:
                if needle in kl:
                    return v
        return ""

    hp91_block = {
        "present": hp91_present,
        "signature": insp.hp90_signature,
        "description": insp.hp91_description,
        "cluster_md5": insp.hp91_cluster_md5,
        "decoded": {
            "paper":     _find("paper", "media") if hp91_present else "",
            "ink":       _find("ink") if hp91_present else "",
            "printmode": _find("printmode", "print_mode") if hp91_present else "",
            "clc_state": _find("clc", "linearization") if hp91_present else "",
            "media_id":  _find("media_id", "mediaid") if hp91_present else "",
        } if hp91_present else None,
        "raw_fields": hp91_raw_fields if hp91_present else {},
    }

    # Custom mapping by hue
    rotations = _build_custom_mapping_rotations(
        insp.hp91_config or {},
        insp.hp91_raw or "",
    )
    mapping_block = {
        "present": bool(insp.custom_mapping_by_hue_active) or bool(rotations),
        "primaries": list(insp.custom_mapping_by_hue_primaries or []),
        "rotations": rotations,
    }

    # CIED — embedded spectral data (looked up among the private tags)
    cied_present = any(t["sig"] == "CIED" or t["sig"] == "cied"
                       for t in insp.tags)
    cied_block = {
        "present": cied_present,
        "n_patches": None,  # requires CGATS decoding — future
        "summary": None,
    }

    return {
        "hp91": hp91_block,
        "custom_mapping_by_hue": mapping_block,
        "cied": cied_block,
    }


# ─── Helpers XYZ → Lab (D50) ──────────────────────────────────────────

def _xyz_to_lab_d50(xyz: tuple) -> tuple:
    """XYZ → Lab conversion with the D50 illuminant."""
    xn, yn, zn = 0.96422, 1.00000, 0.82521  # normalized D50
    x, y, z = xyz[0] / xn, xyz[1] / yn, xyz[2] / zn

    def f(t):
        return t ** (1.0/3.0) if t > (6/29)**3 else (7.787 * t + 16/116)

    fx, fy, fz = f(x), f(y), f(z)
    L = 116 * fy - 16
    a = 500 * (fx - fy)
    b = 200 * (fy - fz)
    return (round(L, 2), round(a, 2), round(b, 2))


# ─── Layer 4 — InspectOps orchestrator ────────────────────────────────

class InspectOps:

    def inspect_icc_file(self, path: Path) -> ProfileInspection:
        data = path.read_bytes()
        insp = ProfileInspection(file_path=str(path), file_size=len(data))

        # Binary header
        header = HpProprietaryDecoder.extract_header(data)
        insp.icc_version = header.get('icc_version', '')
        insp.device_class = header.get('device_class', '')
        insp.device_class_label = DEVICE_CLASS_LABELS.get(insp.device_class, insp.device_class)
        insp.color_space = header.get('color_space', '')
        insp.pcs = header.get('pcs', '')
        insp.cmm = header.get('cmm', '')
        insp.platform = header.get('platform', '')
        insp.profile_size = header.get('profile_size', len(data))
        insp.creator_signature = header.get('creator_signature', '')

        # Tags
        tags = HpProprietaryDecoder.parse_icc_tags(data)
        insp.n_tags = len(tags)
        insp.tags = tags
        insp.private_tags = [t for t in tags if t['sig'].startswith('HP')]

        # Description + other origin tags: iccdump first, fallback to binary mluc/desc/text parsing
        insp.description = IccDumpParser.parse_description(path)
        if not insp.description:
            insp.description = HpProprietaryDecoder.extract_text_tag(data, b'desc', tags)
        insp.copyright = IccDumpParser.parse_tag_text(path, 'cprt')
        if not insp.copyright:
            insp.copyright = HpProprietaryDecoder.extract_text_tag(data, b'cprt', tags)
        insp.manufacturer_desc = IccDumpParser.parse_tag_text(path, 'dmnd')
        if not insp.manufacturer_desc:
            insp.manufacturer_desc = HpProprietaryDecoder.extract_text_tag(data, b'dmnd', tags)
        insp.model_desc = IccDumpParser.parse_tag_text(path, 'dmdd')
        if not insp.model_desc:
            insp.model_desc = HpProprietaryDecoder.extract_text_tag(data, b'dmdd', tags)

        # Whitepoint / Blackpoint
        wp = HpProprietaryDecoder.extract_xyz_tag(data, b'wtpt', tags)
        if wp:
            insp.whitepoint_xyz = list(wp)
            insp.whitepoint_lab = list(_xyz_to_lab_d50(wp))
        bp = HpProprietaryDecoder.extract_xyz_tag(data, b'bkpt', tags)
        if bp:
            insp.blackpoint_xyz = list(bp)
            insp.blackpoint_lab = list(_xyz_to_lab_d50(bp))

        # HP90 / HP91
        for t in tags:
            raw_sig = t['sig'].encode('ascii', errors='replace')
            if raw_sig == b'HP90':
                insp.hp90_signature = HpProprietaryDecoder.extract_hp90(data, t['offset'], t['size'])
                insp.is_hp_ingenium = True
            elif raw_sig == b'HP91':
                raw_text = HpProprietaryDecoder.extract_hp91_zut8(data, t['offset'], t['size'])
                insp.hp91_raw = raw_text
                insp.hp91_config = HpProprietaryDecoder.parse_hp91_config(raw_text)
                if raw_text:
                    insp.hp91_cluster_md5 = hashlib.md5(raw_text.encode()).hexdigest()[:16]
                    meta = insp.hp91_config.get('metadata', {})
                    desc = meta.get('description', '')
                    # Strip outer quotes if present
                    if desc.startswith('"') and desc.endswith('"') and len(desc) >= 2:
                        desc = desc[1:-1]
                    insp.hp91_description = desc

                # Layer 4 — custom mapping detection from the raw
                cm = HpProprietaryDecoder.detect_custom_mappings(raw_text)
                insp.custom_mapping_active = cm['custom_mapping_active']
                insp.custom_mapping_by_hue_active = cm['custom_mapping_by_hue_active']
                insp.custom_mapping_by_hue_primaries = cm['custom_mapping_by_hue_primaries']

        insp.profile_type_guess = HpProprietaryDecoder.guess_profile_type(
            insp.hp90_signature, insp.hp91_description,
            copyright=insp.copyright, creator=insp.creator_signature)

        # Gamut volumes (pass the tags for the v4 LUT-type cross-check)
        insp.gamut = GamutAnalyzer.compute_volumes(path, tags=tags)

        # TRC layers + taxonomy
        insp.trc = analyze_trc(data, tags)
        insp.taxonomy = analyze_taxonomy(data, tags, insp.gamut)

        # Per-tag details for the interactive popover
        insp.tags_details = _build_tags_details(data, tags)

        # Structured internals + ICC conformance
        insp.internals = _build_internals(insp)
        insp.conformance = validate_conformance(data, tags, header)

        return insp

    def to_dict(self, insp: ProfileInspection) -> dict:
        return insp.to_dict()

    def format_console(self, insp: ProfileInspection, short: bool = False) -> str:
        lines = []
        sep = '═' * 72
        lines.append(sep)
        lines.append(f'  {Path(insp.file_path).name}')
        lines.append(sep)
        lines.append('')

        # HEADER
        lines.append('HEADER')
        lines.append(f'  Version ICC          : {insp.icc_version}')
        dc_str = insp.device_class_label
        if insp.color_space:
            dc_str += f' {insp.color_space}'
        lines.append(f'  Device class         : {dc_str}')
        lines.append(f'  PCS                  : {insp.pcs}')
        lines.append(f'  Profile size         : {insp.profile_size:,} bytes')
        if insp.description:
            lines.append(f'  Description          : "{insp.description}"')
        lines.append('')

        # ORIGIN
        lines.append('ORIGIN')
        if insp.is_hp_ingenium:
            lines.append(f'  Generator            : HP Ingenium ({insp.hp90_signature})')
            if insp.hp91_cluster_md5:
                lines.append(f'  Cluster HP91         : {insp.hp91_cluster_md5}')
            if insp.hp91_description:
                lines.append(f'  Internal description : "{insp.hp91_description}"')
        else:
            # Non-HP profile: read the standard ICC tags
            if insp.creator_signature:
                lines.append(f'  CMM / Creator        : {insp.creator_signature}')
            elif insp.cmm:
                lines.append(f'  CMM                  : {insp.cmm}')
            if insp.copyright:
                lines.append(f'  Copyright            : {insp.copyright}')
            if insp.manufacturer_desc:
                lines.append(f'  Manufacturer         : {insp.manufacturer_desc}')
            if insp.model_desc:
                lines.append(f'  Model                : {insp.model_desc}')
        lines.append(f'  Estimated type       : {insp.profile_type_guess}')
        lines.append('')

        # STRUCTURE
        lines.append('STRUCTURE')
        lines.append(f'  Total tags           : {insp.n_tags}')
        hp_sigs = [t["sig"] for t in insp.private_tags]
        if hp_sigs:
            lines.append(f'  HP private tags      : {", ".join(hp_sigs)}')
        lines.append('')

        # CUSTOM MAPPING (layer 4)
        if insp.is_hp_ingenium:
            lines.append('CUSTOM MAPPING (HP Ingenium)')
            if insp.custom_mapping_active:
                lines.append(f'  Active primaries     : {", ".join(insp.custom_mapping_active)}')
            else:
                lines.append(f'  Active primaries     : (none)')
            if insp.custom_mapping_by_hue_active:
                primaries = ", ".join(insp.custom_mapping_by_hue_primaries) or "?"
                lines.append(f'  by_hue active        : YES ⚠  ({primaries})')
                lines.append(f'  ⓘ This profile produces a "spike" in 3D visualization (hue shift)')
            else:
                lines.append(f'  by_hue active        : no')
            lines.append('')

        # CONFIGURATION HP91 (verbose)
        if insp.hp91_config and not short:
            lines.append('CONFIGURATION HP91')
            for section, kv in insp.hp91_config.items():
                if section == 'metadata':
                    continue
                lines.append(f'  [{section}]')
                if isinstance(kv, dict):
                    for k, v in kv.items():
                        v_short = v if len(str(v)) <= 100 else str(v)[:97] + '...'
                        lines.append(f'    {k:30s} = {v_short}')
            lines.append('')

        # GAMUT
        if insp.gamut:
            lines.append('GAMUT (Lab volumes)')
            status = insp.gamut.get('status', '')
            if status == 'deps_missing':
                lines.append(f'  (missing dependency: {insp.gamut.get("reason", "")})')
            elif status == 'failed':
                lines.append('  (computation failed — see --json for details)')
            else:
                for intent in ['absolute', 'relative', 'perceptual', 'saturation']:
                    v = insp.gamut.get(intent)
                    if v is not None:
                        suffix = ''
                        if intent == 'absolute' and insp.gamut.get('absolute_degenerate'):
                            suffix = ' (degenerate)'
                        elif intent == 'perceptual' and 'delta_per_rel_pct' in insp.gamut:
                            suffix = f' ({insp.gamut["delta_per_rel_pct"]:+.2f}% vs relative)'
                        lines.append(f'  {intent:20s} : {v:,.0f}{suffix}')
                    elif v is None and intent in insp.gamut:
                        lines.append(f'  {intent:20s} : (computation failed)')
                interp_map = {
                    'no_perceptual_mapping': 'No significant perceptual mapping (perceptual ≈ relative)',
                    'light_perceptual_mapping': 'Moderate perceptual mapping',
                    'strong_perceptual_mapping': 'Marked perceptual mapping (perceptual clearly extends the gamut vs relative)',
                }
                interp = insp.gamut.get('interpretation', '')
                if interp:
                    lines.append(f'  Interpretation       : {interp_map.get(interp, interp)}')
                # HP mention ONLY if HP Ingenium profile AND by_hue active
                if insp.is_hp_ingenium and insp.custom_mapping_by_hue_active:
                    lines.append(f'  ⓘ HP Ingenium profile : custom_mapping_by_hue active (probable cause of the "spike" in 3D viz)')
                method = insp.gamut.get('method', '')
                if method:
                    lines.append(f'  Method               : {method}')
            lines.append('')

        # WHITE / BLACK POINT
        if insp.whitepoint_lab or insp.blackpoint_lab:
            lines.append('WHITE / BLACK POINT')
            if insp.whitepoint_lab:
                L, a, b = insp.whitepoint_lab
                lines.append(f'  Paper white Lab      : ({L:6.2f}, {a:+6.2f}, {b:+6.2f})')
            if insp.blackpoint_lab:
                L, a, b = insp.blackpoint_lab
                lines.append(f'  Paper black Lab      : ({L:6.2f}, {a:+6.2f}, {b:+6.2f})')
            lines.append('')

        lines.append(sep)
        return '\n'.join(lines)
