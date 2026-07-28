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

"""3D gamut extraction from an ICC profile for the 3D Gamut view.

Single method: ``device_surface_grid``.

Walks the 6 faces of the profile's RGB device cube (17×17 sampling
per face = 1734 points) and converts each point to Lab via the
A2B LUT under the chosen intent. Direct triangulation of the
Cartesian product grid (3072 triangles), regular geometry, without an
arbitrary threshold nor occupancy to interpret — direct read of the profile.

The wireframe combines the 12 edges of the device cube projected into Lab
(RGB-interpolated polylines with their own vertices) and a
dense internal grid (1 line out of 4 in the 17×17 grid → 3
internal × 2 directions × 6 faces = 36 additional polylines
reusing the surface vertices).

Limitation: RGB only. CMYK profiles raise ``ValueError`` (out of
Z9 scope — no CMYK printer handled by freeglaz).

The gamt tag is handled separately by ``extract_gamt_mesh`` (boundary
declared by the profiler, marching cubes on the 1-channel CLUT).

Colorization: each Lab vertex is converted to sRGB (relative colorimetric
intent toward sRGB IEC 61966-2-1, clamp [0,1] without specific damping —
gamut mapping delegated to lcms2).

Disk cache: ``webapp/data/gamut/<md5>_device_<intent>.json`` +
references under ``webapp/data/gamut/_refs/<name>_<intent>.json``.
"""
import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Default grid resolution for the gamt tag (legacy, kept for
# backward compat of calls to extract_gamt_mesh). device_surface_grid
# uses its own internal resolution (17, hard-coded).
DEFAULT_GRID_RESOLUTION = 33

# Lab ranges covered by the axes used by the marching cubes
# of the gamt tag.
LAB_RANGES = {
    "L": (0.0, 100.0),
    "a": (-128.0, 127.0),
    "b": (-128.0, 127.0),
}


# Reference spaces of the 3D overlays / 2D slices.
# Elle Stone ICC bundle (assets/, PORTABLE, CC-BY-SA — cf REFERENCE_PROFILES_LICENSE.md)
# FIRST candidate; macOS ColorSync kept as a bonus fallback (never a
# hard dependency → works identically on Linux/Windows/macOS). DCI-P3 removed (video-oriented, out of use).
def _ref_profile_paths() -> dict:
    """ICC paths of the standard references: Elle Stone bundle first, ColorSync
    as a bonus fallback (macOS). Several candidates absorb path differences."""
    assets = Path(__file__).parent / "assets"
    return {
        "sRGB": [
            str(assets / "sRGB-elle-V2-srgbtrc.icc"),
            "/System/Library/ColorSync/Profiles/sRGB Profile.icc",
        ],
        "AdobeRGB": [
            str(assets / "ClayRGB-elle-V2-g22.icc"),
            "/System/Library/ColorSync/Profiles/AdobeRGB1998.icc",
        ],
        "Rec.2020": [
            str(assets / "Rec2020-elle-V2-rec709.icc"),
            "/System/Library/ColorSync/Profiles/ITU-2020.icc",
        ],
        "ProPhoto": [
            str(assets / "LargeRGB-elle-V2-g18.icc"),
            "/System/Library/ColorSync/Profiles/ROMM RGB.icc",
        ],
        "Rec.709": [
            str(assets / "Rec709-elle-V2-rec709.icc"),
            "/System/Library/ColorSync/Profiles/ITU-709.icc",
        ],
    }


def _cache_dir() -> Path:
    p = Path(__file__).resolve().parent.parent.parent / "webapp" / "data" / "gamut"
    p.mkdir(parents=True, exist_ok=True)
    (p / "_refs").mkdir(parents=True, exist_ok=True)
    return p


def _profile_md5(profile_bytes: bytes) -> str:
    return hashlib.md5(profile_bytes).hexdigest()


# ─── Lab sampling + roundtrip ──────────────────────────────────


def _lab_to_pil_bytes(L, a, b):
    """Encode float Lab arrays into 8-bit Pillow Lab bytes.

    Pillow LAB mode convention:
    - L : uint8 0-255 → L*=0-100 (linear scale ×2.55)
    - a, b : int8 bit-pattern (byte 0 = 0, byte 128 = -128, byte 127 = +127)

    Verified empirically on sRGB gray (128,128,128) → Lab raw=(137,0,0)
    i.e. L*=53.7, a*=0, b*=0.
    """
    import numpy as np
    L_8 = np.clip(L * 2.55, 0, 255).astype(np.uint8)
    a_8 = np.clip(a, -128, 127).astype(np.int8).view(np.uint8)
    b_8 = np.clip(b, -128, 127).astype(np.int8).view(np.uint8)
    return np.stack([L_8, a_8, b_8], axis=1).tobytes()


def _pil_lab_bytes_to_float(raw_bytes, n):
    """Decode 8-bit Pillow Lab bytes into float arrays."""
    import numpy as np
    arr = np.frombuffer(raw_bytes, dtype=np.uint8).reshape(n, 3)
    L = arr[:, 0].astype(np.float64) * 100.0 / 255.0
    # int8 bit-pattern: view, not cast (preserve the binary pattern)
    a = arr[:, 1].view(np.int8).astype(np.float64)
    b = arr[:, 2].view(np.int8).astype(np.float64)
    return L, a, b


_INTENT_PILLOW = {
    "perceptual": "PERCEPTUAL",
    "relative":   "RELATIVE_COLORIMETRIC",
    "saturation": "SATURATION",
    "absolute":   "ABSOLUTE_COLORIMETRIC",
}


def _intent_value(intent: str):
    """Resolve an intent string → Pillow ImageCms.Intent enum."""
    from PIL import ImageCms
    name = _INTENT_PILLOW.get(intent, "RELATIVE_COLORIMETRIC")
    return getattr(ImageCms.Intent, name)


# ─── Lab → sRGB conversion for vertex colorization ───────────────────


def _colorize_vertices_srgb(vertices_lab):
    """Convert the Lab vertices into sRGB colors [0..1].

    Out of sRGB gamut → clamp [0,1] with slight saturation damping
    to avoid flat banding (naive gamut mapping).
    """
    import numpy as np
    from PIL import Image, ImageCms

    n = vertices_lab.shape[0]
    raw_in = _lab_to_pil_bytes(
        vertices_lab[:, 0], vertices_lab[:, 1], vertices_lab[:, 2],
    )
    src_img = Image.frombytes("LAB", (n, 1), raw_in)
    lab_prof = ImageCms.createProfile("LAB", colorTemp=5000)
    srgb_prof = ImageCms.createProfile("sRGB")
    t = ImageCms.buildTransform(
        lab_prof, srgb_prof, "LAB", "RGB",
        renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
    )
    rgb_img = ImageCms.applyTransform(src_img, t)
    arr = np.frombuffer(rgb_img.tobytes(), dtype=np.uint8).reshape(n, 3)
    return (arr.astype(np.float32) / 255.0)


# ─── Main extraction (orchestrates marching cubes + colors) ──────


def _run_marching_cubes(occupancy, axes):
    """Run skimage.marching_cubes and return (vertices_lab, indices).

    The vertices are returned in Lab coordinates (not voxel index)
    thanks to the spacing argument + offset translation.
    """
    import numpy as np
    from skimage.measure import marching_cubes

    L_axis, a_axis, b_axis = axes
    # Spacing between voxels in each dimension
    dL = L_axis[1] - L_axis[0]
    da = a_axis[1] - a_axis[0]
    db = b_axis[1] - b_axis[0]
    spacing = (float(dL), float(da), float(db))

    try:
        verts, faces, _normals, _values = marching_cubes(
            occupancy, level=0.5, spacing=spacing,
        )
    except (ValueError, RuntimeError) as e:
        logger.warning("marching_cubes failed: %s", e)
        return np.zeros((0, 3), dtype=np.float32), np.zeros((0, 3), dtype=np.int32)

    # Translate vertices: skimage returns in (0,0,0) coordinates
    # → we align on the axis minimums.
    verts[:, 0] += LAB_RANGES["L"][0]
    verts[:, 1] += LAB_RANGES["a"][0]
    verts[:, 2] += LAB_RANGES["b"][0]
    return verts.astype(np.float32), faces.astype(np.int32)


def _bounds_from_vertices(verts):
    import numpy as np
    if verts.size == 0:
        return {"L": [0, 0], "a": [0, 0], "b": [0, 0]}
    return {
        "L": [float(verts[:, 0].min()), float(verts[:, 0].max())],
        "a": [float(verts[:, 1].min()), float(verts[:, 1].max())],
        "b": [float(verts[:, 2].min()), float(verts[:, 2].max())],
    }


def _volume_from_mesh(verts, faces):
    """Signed Lab volume of the mesh (divergence theorem)."""
    import numpy as np
    if faces.size == 0:
        return 0.0
    v0 = verts[faces[:, 0]]
    v1 = verts[faces[:, 1]]
    v2 = verts[faces[:, 2]]
    cross = np.cross(v1 - v0, v2 - v0)
    vols = np.einsum("ij,ij->i", v0, cross) / 6.0
    return float(abs(vols.sum()))


def extract_gamut_mesh(
    profile_path: str,
    intent: str = "relative",
) -> dict:
    """Extract the effective gamut mesh of an RGB profile.

    Single method ``device_surface_grid`` (6 RGB faces 17² + A2B).
    Raises ``ValueError`` for CMYK profiles (out of Z9 scope).

    Args:
        profile_path : absolute path of the .icc
        intent       : "perceptual" | "relative" | "saturation"

    Returns a dict with ``vertices``, ``indices``, ``colors_srgb``,
    ``wireframe_edges``, ``wireframe_style = "cage_device_grid"``,
    ``bounds_lab``, ``volume_lab``, ``n_vertices``, ``n_triangles``,
    ``extraction_method``.
    """
    path = Path(profile_path)
    if not path.exists():
        raise FileNotFoundError(f"ICC profile not found: {profile_path}")
    return _extract_device_surface_grid(profile_path, intent=intent)


def _cache_file_for(md5: str, intent: str) -> Path:
    """Cache file name (single method device_surface_grid)."""
    return _cache_dir() / f"{md5}_device_{intent}.json"


def extract_gamut_mesh_cached(
    profile_path: str,
    intent: str = "relative",
) -> dict:
    """Cached variant of extract_gamut_mesh. Key: md5(content) + intent."""
    path = Path(profile_path)
    if not path.exists():
        raise FileNotFoundError(f"ICC profile not found: {profile_path}")
    data = path.read_bytes()
    md5 = _profile_md5(data)
    cache_file = _cache_file_for(md5, intent)
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Gamut cache illisible (%s), recalcul", e)

    mesh = extract_gamut_mesh(profile_path, intent=intent)
    try:
        cache_file.write_text(json.dumps(mesh), encoding="utf-8")
    except OSError as e:
        logger.warning("Gamut cache not written (%s)", e)
    return mesh


# ─── References ─────────────────────────────────────────────────────


def _find_reference_icc(name: str) -> Optional[str]:
    """Look up the ICC path of a reference among the known candidates."""
    for candidate in _ref_profile_paths().get(name, []):
        if Path(candidate).exists():
            return candidate
    return None


def extract_reference_mesh(
    name: str,
    intent: str = "relative",
) -> dict:
    """Extract the mesh of a standard reference (sRGB, AdobeRGB, …).

    All references are RGB → device_surface_grid applies
    uniformly. Permanent cache under
    ``webapp/data/gamut/_refs/<name>_<intent>.json``.
    """
    cache_file = _cache_dir() / "_refs" / f"{name}_{intent}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    icc_path = _find_reference_icc(name)
    if not icc_path:
        raise FileNotFoundError(f"Reference profile not found: {name}")

    mesh = extract_gamut_mesh(icc_path, intent=intent)
    mesh["reference_name"] = name
    try:
        cache_file.write_text(json.dumps(mesh), encoding="utf-8")
    except OSError as e:
        logger.warning("Reference cache not written (%s)", e)
    return mesh


REFERENCE_NAMES = list(_ref_profile_paths().keys())


# ─── Block B — CLUT scatter for LUT popovers ───────────────


# [DECISION] Rather than parse the binary CLUT payload (complex,
# fragile depending on mft1/mft2/mAB/mBA), we generate the scatter by sampling
# the device space N³ via ImageCms. Visually equivalent result +
# unified code + sRGB colors already computed backend-side.

# Intent mapping per LUT tag (A2B/B2A) — decided semantically.
_INTENT_BY_LUT_TAG = {
    "A2B0": "perceptual",
    "A2B1": "relative",
    "A2B2": "saturation",
    "B2A0": "perceptual",
    "B2A1": "relative",
    "B2A2": "saturation",
}

_PILLOW_INTENT = {
    "perceptual": 0,
    "relative":   1,
    "saturation": 2,
    "absolute":   3,
}


def extract_clut_scatter(
    profile_path: str,
    intent: str = "perceptual",
    resolution: int = 9,
) -> dict:
    """Generate a 3D scatter sampling the device space of an RGB
    profile and its Lab rendering with associated sRGB color.

    Returns:
        {
            "scatter": [[L, a, b, r_srgb, g_srgb, b_srgb], ...],
            "n_points": int,
            "intent": str,
            "resolution": int,
        }

    Non-RGB (CMYK input): empty scatter. The frontend then hides the
    mini-render.
    """
    import numpy as np
    from PIL import Image, ImageCms

    if resolution not in (9, 17):
        resolution = 9

    dev_prof = ImageCms.getOpenProfile(profile_path)
    # Probe to check that the profile is RGB → supportable PCS
    try:
        t_dev_to_lab = ImageCms.buildTransform(
            dev_prof, ImageCms.createProfile("LAB", colorTemp=5000),
            "RGB", "LAB",
            renderingIntent=_PILLOW_INTENT.get(intent, 0),
        )
    except ImageCms.PyCMSError:
        return {"scatter": [], "n_points": 0, "intent": intent,
                "resolution": resolution, "skipped_reason": "non_rgb_input"}

    # Device RGB grid
    vals = np.linspace(0, 255, resolution).round().astype(np.uint8)
    rr, gg, bb = np.meshgrid(vals, vals, vals, indexing="ij")
    n = resolution ** 3
    rgb = np.stack([rr.ravel(), gg.ravel(), bb.ravel()], axis=1).astype(np.uint8)
    src = Image.frombytes("RGB", (n, 1), rgb.tobytes())
    lab_img = ImageCms.applyTransform(src, t_dev_to_lab)
    L, a, b = _pil_lab_bytes_to_float(lab_img.tobytes(), n)

    # sRGB color of each point — we can either (a) take the original
    # device RGB color if the profile IS sRGB, or (b) re-convert
    # Lab → sRGB for universal rendering. We choose (b) for consistency
    # with the main mesh (color = sRGB projection of each Lab point).
    colors = _colorize_vertices_srgb(np.stack([L, a, b], axis=1))

    scatter = []
    for i in range(n):
        scatter.append([
            round(float(L[i]), 3), round(float(a[i]), 3), round(float(b[i]), 3),
            round(float(colors[i, 0]), 4),
            round(float(colors[i, 1]), 4),
            round(float(colors[i, 2]), 4),
        ])
    return {
        "scatter": scatter,
        "n_points": n,
        "intent": intent,
        "resolution": resolution,
    }


def intent_for_lut_tag(tag_signature: str) -> str:
    """Return the semantic ICC intent associated with a LUT tag."""
    return _INTENT_BY_LUT_TAG.get(tag_signature, "perceptual")


# ─── gamt tag: iso-surface from the 17³ × 1-channel LUT ──


def _find_tag(data: bytes, signature: bytes) -> Optional[tuple]:
    """Return (offset, size) of the ``signature`` tag or None."""
    import struct
    if len(data) < 132:
        return None
    n_tags = struct.unpack(">I", data[128:132])[0]
    for i in range(n_tags):
        off = 132 + i * 12
        sig = data[off:off+4]
        if sig == signature:
            tag_off = struct.unpack(">I", data[off+4:off+8])[0]
            tag_size = struct.unpack(">I", data[off+8:off+12])[0]
            return (tag_off, tag_size)
    return None


def _read_gamt_clut(data: bytes, tag_off: int, tag_size: int) -> Optional[dict]:
    """Read the CLUT of a gamt tag (mft1 or mft2 with 1-channel output).

    Returns {"clut": ndarray (grid,grid,grid), "grid": int, "bits": int}
    or None if unexpected format.
    """
    import numpy as np
    import struct
    if tag_off + 12 > len(data):
        return None
    sub_type = data[tag_off:tag_off+4]
    if sub_type not in (b"mft1", b"mft2"):
        return None
    input_ch = data[tag_off + 8]
    output_ch = data[tag_off + 9]
    grid = data[tag_off + 10]
    if input_ch != 3 or output_ch != 1 or grid < 2:
        return None

    bits = 8 if sub_type == b"mft1" else 16
    byte_per = bits // 8

    if sub_type == b"mft1":
        n_in = 256
        n_out = 256
        cur = tag_off + 48
    else:
        if tag_off + 52 > len(data):
            return None
        n_in = struct.unpack(">H", data[tag_off + 48:tag_off + 50])[0]
        n_out = struct.unpack(">H", data[tag_off + 50:tag_off + 52])[0]
        cur = tag_off + 52

    # Skip the input curves (input_ch × n_in × byte_per)
    cur += input_ch * n_in * byte_per
    # Read the CLUT: grid^input_ch × output_ch × byte_per
    n_clut = grid ** input_ch * output_ch
    needed = n_clut * byte_per
    if cur + needed > len(data):
        return None
    if bits == 8:
        clut_flat = np.frombuffer(data[cur:cur + needed], dtype=np.uint8)
    else:
        clut_flat = np.frombuffer(data[cur:cur + needed], dtype=">u2")
    # Layout (L major, a middle, b minor) for input PCS Lab
    clut = clut_flat.reshape((grid, grid, grid))
    return {"clut": clut, "grid": int(grid), "bits": bits}


def _detect_gamt_convention(clut, bits: int) -> tuple:
    """Determine which value represents in-gamut in the gamt CLUT.

    ICC v4 spec §9.2.20: "0 = in-gamut, non-zero = out-of-gamut, the larger
    the value, the farther the point is from the gamut". But some
    profilers (HP, sometimes X-Rite) invert the convention in mft2: the
    max value represents in-gamut.

    Heuristic: for a typical print profile, we expect 5-60 %
    of the PCS Lab space to be inside the gamut. We look at which value (0 or
    max) falls in this range and infer the convention.

    Returns (in_gamut_predicate, convention_label).
    """
    import numpy as np
    max_val = 255 if bits == 8 else 65535
    n_total = clut.size

    frac_zero = (clut == 0).sum() / n_total
    frac_max = (clut == max_val).sum() / n_total

    # Standard case: 0 = in-gamut
    if 0.02 <= frac_zero <= 0.70:
        return (clut == 0), "standard (0=in)"
    # Inverted case: max = in-gamut
    if 0.02 <= frac_max <= 0.70:
        return (clut == max_val), "inverted (max=in)"
    # Ambiguous case: few strict saturations — use a
    # relative threshold (in-gamut = below the median)
    median = float(np.median(clut))
    if median > 0:
        return (clut < median), f"thresholded (<{median:.0f})"
    return (clut == 0), "standard (0=in, fallback)"


def extract_gamt_mesh(profile_path: str) -> dict:
    """Decode the gamt tag and extract the in-gamut iso-surface.

    Automatic detection of the convention (0=in standard, max=in inverted).

    Raises:
        FileNotFoundError : profile absent
        ValueError        : profile without gamt tag or unexpected format
    """
    import numpy as np

    path = Path(profile_path)
    if not path.exists():
        raise FileNotFoundError(f"ICC profile not found: {profile_path}")
    data = path.read_bytes()
    located = _find_tag(data, b"gamt")
    if not located:
        raise ValueError("Profile has no 'gamt' tag")

    tag_off, tag_size = located
    parsed = _read_gamt_clut(data, tag_off, tag_size)
    if not parsed:
        raise ValueError("Unable to decode 'gamt' tag layout")

    clut = parsed["clut"]
    grid = parsed["grid"]
    bits = parsed["bits"]

    in_gamut_mask, convention = _detect_gamt_convention(clut, bits)
    occupancy = in_gamut_mask.astype(np.float32)

    in_gamut_count = int(in_gamut_mask.sum())
    in_gamut_fraction = in_gamut_count / clut.size if clut.size else 0.0

    logger.info(
        "gamt: %s grid=%d bits=%d  convention=%s  in_gamut=%d/%d (%.1f%%)  "
        "clut min/max/median=%d/%d/%d",
        Path(profile_path).name, grid, bits, convention,
        in_gamut_count, int(clut.size), 100 * in_gamut_fraction,
        int(clut.min()), int(clut.max()), int(np.median(clut)),
    )

    L_axis = np.linspace(LAB_RANGES["L"][0], LAB_RANGES["L"][1], grid)
    a_axis = np.linspace(LAB_RANGES["a"][0], LAB_RANGES["a"][1], grid)
    b_axis = np.linspace(LAB_RANGES["b"][0], LAB_RANGES["b"][1], grid)

    verts, faces = _run_marching_cubes(occupancy, (L_axis, a_axis, b_axis))
    colors = _colorize_vertices_srgb(verts) if verts.size else np.zeros((0, 3), dtype=np.float32)

    return {
        "vertices": verts.tolist(),
        "indices": faces.tolist(),
        "colors_srgb": colors.tolist(),
        "bounds_lab": _bounds_from_vertices(verts),
        "n_vertices": int(verts.shape[0]),
        "n_triangles": int(faces.shape[0]),
        "volume_lab": _volume_from_mesh(verts, faces),
        "extraction_method": "gamt_isosurface",
        "grid_resolution": grid,
        "precision_bits": bits,
        "convention": convention,
        "in_gamut_fraction": round(in_gamut_fraction, 4),
        "source": "gamt_tag",
        "wireframe_edges": None,
        "wireframe_style": "triangular",
    }


# ─── Single method device_surface_grid ──────────────────


def _extract_device_surface_grid(
    profile_path: str, intent: str = "relative", resolution: int = 17,
    wireframe_step: int = 4,
) -> dict:
    """Device surface method: samples the 6 faces of the printer's RGB
    cube (n×n per face) and converts to Lab via A2B under the given
    intent. Triangulation derived from the natural grid.

    Wireframe: 12 edges of the device cube + internal grid,
    1 line out of ``wireframe_step`` on each face. With
    resolution=17 and wireframe_step=4: lines at indices 0, 4, 8, 12,
    16 in each direction → 3 internal per direction + 2 borders per
    direction × 6 faces × 2 directions = 36 internal + 12 cube edges
    (borders repeated between faces, deduplicated).

    The internal grid vertices **reuse** those of the
    surface (no additional vertices). Only the 12 cube edges
    add their own vertices to preserve the Lab fidelity
    along the RGB edges (device interpolation continuity).

    RGB only — raises ValueError on a CMYK or other profile.
    """
    import numpy as np
    from PIL import Image, ImageCms

    dev_prof = ImageCms.getOpenProfile(profile_path)
    lab_prof = ImageCms.createProfile("LAB", colorTemp=5000)
    intent_enum = _intent_value(intent)

    try:
        t_dev_to_lab = ImageCms.buildTransform(
            dev_prof, lab_prof, "RGB", "LAB",
            renderingIntent=intent_enum,
        )
    except ImageCms.PyCMSError as e:
        raise ValueError(f"device_surface_grid requires an RGB profile ({e})")

    n = resolution
    faces_def = [
        ("R", 0), ("R", 255),
        ("G", 0), ("G", 255),
        ("B", 0), ("B", 255),
    ]
    all_rgb = []
    for (axis, val) in faces_def:
        for i in range(n):
            for j in range(n):
                u = int(round(i / (n - 1) * 255))
                v = int(round(j / (n - 1) * 255))
                if axis == "R":
                    rgb = (val, u, v)
                elif axis == "G":
                    rgb = (u, val, v)
                else:
                    rgb = (u, v, val)
                all_rgb.append(rgb)
    rgb_arr = np.array(all_rgb, dtype=np.uint8)
    n_points = len(all_rgb)

    src_img = Image.frombytes("RGB", (n_points, 1), rgb_arr.tobytes())
    lab_img = ImageCms.applyTransform(src_img, t_dev_to_lab)
    L_arr, a_arr, b_arr = _pil_lab_bytes_to_float(lab_img.tobytes(), n_points)
    vertices_lab = np.stack([L_arr, a_arr, b_arr], axis=1).astype(np.float32)

    # Grid triangulation — each 1×1 square produces 2 triangles.
    # Winding oriented for **outward** normal on each face
    # of the device cube. Without this, 3 faces out of 6 produce an inverted cross
    # → signed volume partially compensated → volume_lab falsified.
    #
    # Computation of the natural cross on the triangulation (i,j) → linear index:
    #   - face R : edges = (+B, +G+B), cross = -R
    #   - face G : edges = (+B, +R+B), cross = +G
    #   - face B : edges = (+G, +R+G), cross = -B
    # Outward = -axis for val=0, +axis for val=255. Hence the need to
    # flip the winding for: (R,255), (G,0), (B,255).
    flip_winding = {(axis, val) for (axis, val) in [
        ("R", 255), ("G", 0), ("B", 255),
    ]}
    faces_idx = []
    pp = n * n
    for f, (axis, val) in enumerate(faces_def):
        base = f * pp
        flip = (axis, val) in flip_winding
        for i in range(n - 1):
            for j in range(n - 1):
                v00 = base + i * n + j
                v01 = base + i * n + j + 1
                v10 = base + (i + 1) * n + j
                v11 = base + (i + 1) * n + j + 1
                if flip:
                    faces_idx.append([v00, v11, v01])
                    faces_idx.append([v00, v10, v11])
                else:
                    faces_idx.append([v00, v01, v11])
                    faces_idx.append([v00, v11, v10])
    faces_arr = np.array(faces_idx, dtype=np.int32)
    colors = _colorize_vertices_srgb(vertices_lab)

    # Internal grid wireframe — reuses the
    # surface vertices. For each face f, for each
    # strictly internal intermediate line k (0 < k < n-1, k % step == 0):
    #   - line i=k constant: segments (f*pp + k*n + j, f*pp + k*n + j+1) for j=0..n-2
    #   - line j=k constant: segments (f*pp + i*n + k, f*pp + (i+1)*n + k) for i=0..n-2
    edge_pairs: list = []
    for f in range(6):
        base = f * pp
        for k in range(wireframe_step, n - 1, wireframe_step):
            # Horizontal line (i=k, j varies)
            for j in range(n - 1):
                edge_pairs.append([base + k * n + j, base + k * n + j + 1])
            # Vertical line (j=k, i varies)
            for i in range(n - 1):
                edge_pairs.append([base + i * n + k, base + (i + 1) * n + k])

    # 12 edges of the device cube: we compute them separately (additional
    # vertices) to get a faithful Lab polyline, independently
    # of the interpolation errors between grid faces.
    cube_corners = [
        (0, 0, 0), (255, 0, 0), (0, 255, 0), (255, 255, 0),
        (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
    ]
    cube_edges_def = [
        (0, 1), (0, 2), (0, 4),
        (1, 3), (1, 5),
        (2, 3), (2, 6),
        (3, 7), (4, 5), (4, 6),
        (5, 7), (6, 7),
    ]
    edge_vertices = []
    base_idx = len(vertices_lab)
    for (c1, c2) in cube_edges_def:
        rgb1 = np.array(cube_corners[c1], dtype=np.float64)
        rgb2 = np.array(cube_corners[c2], dtype=np.float64)
        edge_rgb = []
        for k in range(n):
            t_ = k / (n - 1)
            rgb = (rgb1 * (1 - t_) + rgb2 * t_).astype(np.uint8)
            edge_rgb.append(rgb)
        edge_rgb_arr = np.array(edge_rgb, dtype=np.uint8)
        src_edge = Image.frombytes("RGB", (n, 1), edge_rgb_arr.tobytes())
        lab_edge = ImageCms.applyTransform(src_edge, t_dev_to_lab)
        L_e, a_e, b_e = _pil_lab_bytes_to_float(lab_edge.tobytes(), n)
        for k in range(n):
            edge_vertices.append([float(L_e[k]), float(a_e[k]), float(b_e[k])])
        for k in range(n - 1):
            edge_pairs.append([base_idx + k, base_idx + k + 1])
        base_idx += n

    if edge_vertices:
        edge_arr = np.array(edge_vertices, dtype=np.float32)
        vertices_lab = np.concatenate([vertices_lab, edge_arr], axis=0)
        edge_colors = _colorize_vertices_srgb(edge_arr)
        colors = np.concatenate([colors, edge_colors], axis=0)

    return {
        "vertices": vertices_lab.tolist(),
        "indices": faces_arr.tolist(),
        "colors_srgb": colors.tolist(),
        "bounds_lab": _bounds_from_vertices(vertices_lab),
        "n_vertices": int(vertices_lab.shape[0]),
        "n_triangles": int(faces_arr.shape[0]),
        "volume_lab": _volume_from_mesh(vertices_lab, faces_arr),
        "extraction_method": f"device_surface_grid_{intent}",
        "wireframe_edges": edge_pairs,
        "wireframe_style": "cage_device_grid",
    }


def extract_gamt_mesh_cached(profile_path: str) -> dict:
    """Cached variant of extract_gamt_mesh. Key: md5(content)."""
    path = Path(profile_path)
    if not path.exists():
        raise FileNotFoundError(f"ICC profile not found: {profile_path}")
    data = path.read_bytes()
    md5 = _profile_md5(data)
    cache_file = _cache_dir() / f"{md5}_gamt.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    mesh = extract_gamt_mesh(profile_path)
    try:
        cache_file.write_text(json.dumps(mesh), encoding="utf-8")
    except OSError as e:
        logger.warning("Gamt cache not written (%s)", e)
    return mesh
