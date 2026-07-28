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
freeglaz multipass chart generator (MVP)

⚠️ DEPRECATED (step 3 of the consolidation, ~June 7 2026) — the shifted-cube
generator (343 cube 7³ + 121 extras, FIXED at 464) worked around the impossibility of
generating free patches (firmware chart locked at 464). The FREE CHART track (free targen
→ native SOL scan → enrichment by concat start+pass) now carries V1
(start/restore) and V2 (enrichment), validated in the field → this workaround is no longer
the default path. Code KEPT and tested (reversible); out of the UI/CLI by default.
Replaced by: `sol_chart.orchestrate_free_chart` + webapp slot → "Enrichir".

Generates .ti1 charts + JSON sidecar for passes 2/3/N of the shifted-cube
multi-pass protocol. The produced .ti1 is directly consumable by
`freeglaz chart generate --ti1 ...`.

3 cube_shift modes × 3 extras modes = 9 strategies.

Z9 firmware invariants respected (hypothesis C validated empirically):
  - first 343 patches in a regular 7³ cube (arbitrary levels, mandatory cube
    structure)
  - last 121 patches structured on the HP 13-level grid with ordered
    grammar (R≥G≥B, B≥G≥R, or R≈G≈B)

Strategy convention:
  Pass 1 = HP factory native (rgb_7cube_plus chart, no need to regenerate)
  Pass 2+ = freeglaz multipass charts via this module

Typical same-day 3-pass strategy:
  Pass 1: HP firmware (cube_shift=hp_canonical + extras=hp_skintones)
  Pass 2: freeglaz chart generate-multipass --cube-shift small_low  --extras sky_tones
  Pass 3: freeglaz chart generate-multipass --cube-shift small_high --extras gray_ramp

→ ~1000 unique measured patches (maximal coverage of the device gamut).

Module added after delivery of Tier 1 CLI ergonomics.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence


# ─── Structural constants ────────────────────────────────────────────

# HP 13-level grid — reference for the extras (validated empirically)
HP_GRID_13: tuple[int, ...] = (
    0, 21, 43, 64, 85, 106, 128, 149, 170, 191, 213, 234, 255,
)

# Grid of MID-POINTS between consecutive HP nodes (12 levels). Used for
# DENSIFICATION: these levels fall EXACTLY between the HP levels → any patch
# built on them is intercalary (never on the HP grid), so necessarily
# new vs a base measured on the HP grid (cube + factory extras). Same
# principle as gray_ramp (which leaves the 13-grid to densify the neutral axis).
MID_GRID_12: tuple[int, ...] = tuple(
    (HP_GRID_13[i] + HP_GRID_13[i + 1]) // 2 for i in range(len(HP_GRID_13) - 1)
)  # = (10, 32, 53, 74, 95, 117, 138, 159, 180, 202, 223, 244)

# Levels of the 7³ cube per shift strategy
# All start at 0 and end at 255 (anchored shift — corners preserved)
# Intermediate levels aligned on HP_GRID_13 for grammatical coherence
CUBE_LEVELS: dict[str, tuple[int, ...]] = {
    "hp_canonical": (0, 43,  85, 128, 170, 213, 255),  # HP factory reference
    "small_low":    (0, 21,  64, 106, 149, 191, 255),  # shift downward
    "small_high":   (0, 64, 106, 149, 191, 234, 255),  # shift upward
}

EXTRAS_MODES: tuple[str, ...] = ("hp_skintones", "sky_tones", "gray_ramp")

N_CUBE_PATCHES = 343        # 7³
N_EXTRAS_PATCHES = 121
N_TOTAL_PATCHES = 464

GENERATOR_VERSION = "freeglaz multipass_chart MVP v1 (2026-05-20)"


# ─── Data model ──────────────────────────────────────────────────────


@dataclass
class MultipassChartSpec:
    """
    Complete description of a generated multipass chart.

    Serialized as a JSON sidecar next to the .ti1, will be used by downstream
    commands (analysis, pass comparison, debug).
    """
    cube_shift: str
    extras: str
    pass_number: int
    cube_levels: tuple[int, ...]
    n_patches: int
    n_cube: int
    n_extras: int
    paper: str | None
    created: str
    generator: str = GENERATOR_VERSION
    notes: list[str] = field(default_factory=list)


# ─── 7³ cube ─────────────────────────────────────────────────────────


def build_cube_patches(levels: Sequence[int]) -> list[tuple[int, int, int]]:
    """
    Generates the 343 patches of the 7³ cube from 7 RGB levels.

    Canonical order: R-slow / G-mid / B-fast (consistent with HP rgb_7cube_plus).

    Args:
        levels: 7 integer levels ∈ [0, 255], strictly increasing,
                with levels[0] == 0 and levels[-1] == 255 (anchored shift).

    Returns:
        List of 343 (R, G, B) triplets.
    """
    if len(levels) != 7:
        raise ValueError(f"7 levels required for a 7³ cube, got {len(levels)}")
    if levels[0] != 0:
        raise ValueError(f"levels[0] must be 0 (anchored shift), got {levels[0]}")
    if levels[-1] != 255:
        raise ValueError(f"levels[-1] must be 255 (anchored shift), got {levels[-1]}")
    if list(levels) != sorted(set(levels)):
        raise ValueError(f"levels must be strictly increasing: {levels}")

    return [
        (r, g, b)
        for r in levels
        for g in levels
        for b in levels
    ]


# ─── Extras (121 patches structured on the 13-level grid) ────────────


def build_extras_hp_skintones(
    hp_csv_path: Path | None = None,
    *,
    exclude: set[tuple[int, int, int]] | None = None,
) -> tuple[list[tuple[int, int, int]], list[str]]:
    """
    121 R≥G≥B patches (skin tones zone) — TWO behaviors depending on `hp_csv_path`.

    - CSV provided (`rgb_7cube_plus.csv`, explicit opt-in) → we read the 121 EXACT
      HP factory patches (block=skin_tones). REPRODUCTION path of the HP chart.

    - CSV absent (default case, e.g. webapp refinement) → DENSIFICATION via
      `_generate_skintones_densify`: 121 R>G>B patches on the mid-points
      grid, all INTERCALARY (between the HP nodes) → really new
      vs a base measured on the HP grid. This is what is needed to enrich and
      measure the zone finely, NOT the old approximation (which stayed on the
      13-grid → ~50% duplicates with the factory chart, 0 intercalary).

    The `exclude` parameter (cube patches) avoids any cube↔extras collision.

    Returns:
        (patches, notes) — `notes` documents the origin of the patches.
    """
    notes: list[str] = []

    if hp_csv_path is not None and Path(hp_csv_path).exists():
        patches = _load_skintones_from_csv(Path(hp_csv_path))
        notes.append(
            f"hp_skintones read from CSV: {hp_csv_path} "
            f"(121 exact HP factory patches)"
        )
        # Note: the HP CSV is designed for exclude=cube hp_canonical. For
        # other cube_shifts, there may be collisions; we report them
        # without correcting (the user explicitly chose the CSV).
        if exclude is not None:
            collisions = set(patches) & exclude
            if collisions:
                notes.append(
                    f"hp_skintones: {len(collisions)} collisions with the current "
                    f"cube (HP CSV is designed for the hp_canonical cube)"
                )
        return patches, notes

    patches = _generate_skintones_densify(exclude=exclude)
    notes.append(
        "skin_tones: DENSIFICATION (mid-points) — 121 interleaved R>G>B "
        "patches (outside the HP grid), all new vs the factory chart. "
        "To reproduce the exact HP factory patches, provide "
        "rgb_7cube_plus.csv via --hp-skintones-csv."
    )
    if exclude:
        notes.append(
            "skin_tones: 0 collision with the cube (mid-points disjoint from the "
            "cube levels)"
        )
    return patches, notes


def _load_skintones_from_csv(csv_path: Path) -> list[tuple[int, int, int]]:
    """Reads the 121 block=skin_tones patches from the HP factory CSV."""
    patches: list[tuple[int, int, int]] = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("block") == "skin_tones":
                patches.append((int(row["R"]), int(row["G"]), int(row["B"])))

    if len(patches) != 121:
        raise ValueError(
            f"CSV {csv_path}: expected 121 skin_tones patches, got {len(patches)}"
        )

    # Grammar validation: strict R > G > B.
    violations = [(r, g, b) for r, g, b in patches if not (r > g > b)]
    if violations:
        raise ValueError(
            f"CSV {csv_path} : {len(violations)} patches skin_tones violent R>G>B"
        )
    return patches


def _generate_skintones_approximation(
    exclude: set[tuple[int, int, int]] | None = None,
) -> list[tuple[int, int, int]]:
    """
    Generates 121 R≥G≥B patches approximating the HP skin tones distribution.

    Heuristic criterion:
      - all patches strict R > G > B on HP_GRID_13
      - filtered on the levels observed in HP:
          R ∈ {43..255}, G ∈ {21..234}, B ∈ {0..213}
      - exclusion of patches already in the cube (if `exclude` provided)
      - uniform subsampling

    HP factory avoids by construction the cube patches in its 121 skin
    tones (0 collision). We reproduce this property via `exclude`.
    """
    exclude = exclude or set()
    candidates: list[tuple[int, int, int]] = []
    for r in HP_GRID_13:
        if r < 43:
            continue
        for g in HP_GRID_13:
            if g >= r or g < 21 or g > 234:
                continue
            for b in HP_GRID_13:
                if b >= g or b > 213:
                    continue
                if (r, g, b) in exclude:
                    continue
                candidates.append((r, g, b))

    if len(candidates) < 121:
        raise RuntimeError(
            f"skin_tones approximation: only {len(candidates)} "
            f"candidates (< 121) after excluding the cube. "
            f"Levels too restrictive."
        )

    candidates.sort()  # canonical order for reproducibility
    step = len(candidates) / 121
    return [candidates[int(i * step)] for i in range(121)]


def _generate_skintones_densify(
    exclude: set[tuple[int, int, int]] | None = None,
) -> list[tuple[int, int, int]]:
    """121 R>G>B patches on the MID-POINTS grid (DENSIFICATION).

    Unlike ``_generate_skintones_approximation`` (which mimics the HP
    distribution on the 13-grid → ~50% duplicates with the factory chart,
    0 intercalary), here all patches fall BETWEEN the HP nodes:
      - built on ``MID_GRID_12`` (mid-points of consecutive HP pairs);
      - strict R>G>B grammar (warm zone / skin tones);
      - C(12,3) = 220 candidates, all off the HP grid → 0 collision with a base
        measured on the HP grid (factory cube + factory skin), whatever the
        cube_shift (the mid levels are disjoint from all CUBE_LEVELS);
      - uniform subsampling to 121.

    Goal: enrich the skin tones zone with 121 REALLY NEW and
    intercalary points, measurable to refine the profile (not a re-measure).

    The ``exclude`` (cube of the pass) is applied for safety; in practice it
    removes nothing because mid-points ∩ cube levels = ∅.
    """
    exclude = exclude or set()
    levels = MID_GRID_12
    candidates = [
        (r, g, b)
        for r in levels
        for g in levels if g < r
        for b in levels if b < g
        if (r, g, b) not in exclude
    ]
    if len(candidates) < 121:
        raise RuntimeError(
            f"skin densify: only {len(candidates)} R>G>B candidates "
            f"on the mid-points grid (< 121)."
        )
    candidates.sort()  # canonical order for reproducibility
    step = len(candidates) / 121
    patches = [candidates[int(i * step)] for i in range(121)]
    if len(set(patches)) != 121:
        raise RuntimeError(
            f"skin densify : doublons internes ({len(set(patches))}/121 uniques)"
        )
    return patches


def build_extras_sky_tones(
    exclude: set[tuple[int, int, int]] | None = None,
) -> tuple[list[tuple[int, int, int]], list[str]]:
    """
    121 B≥G≥R patches (colorimetric mirror of the HP skin tones).

    Built by R↔B swap on the skin tones approximation, excluding
    the patches that would collide with the current cube. This is exactly
    the empirically validated strategy.

    Returns:
        (patches, notes)
    """
    # To apply exclude correctly on the mirror, we invert the exclude:
    # a patch (b, g, r) in skin_tones becomes (r, g, b) here → the exclusion
    # is done on (b, g, r) on the skin_tones side.
    skin_exclude = None
    if exclude:
        skin_exclude = {(b, g, r) for (r, g, b) in exclude}
    skin = _generate_skintones_approximation(exclude=skin_exclude)
    sky = [(b, g, r) for (r, g, b) in skin]
    notes = [
        "sky_tones: B↔R mirror of the HP skin_tones approximation "
        "(cold sky zone, validated empirically).",
    ]
    if exclude:
        notes.append("sky_tones: 0 collision with the cube (post-filter exclude)")
    return sky, notes


def build_extras_gray_ramp(
    exclude: set[tuple[int, int, int]] | None = None,
) -> tuple[list[tuple[int, int, int]], list[str]]:
    """
    121 R=G=B patches (true fine gray ramp).

    Intentionally leaves the HP 13-level grid to generate a pure gray ramp
    on a fine grid [0, 255], spacing ~2 RGB units. Densifies the
    gray diagonal for higher-quality profiling in B&W / neutrals.

    Empirical validation: an sRGB2014 chart patched with R=G=B
    grammar was accepted by the Z9 firmware → the R=G=B grammar
    on a fine grid is compatible. Hypothesis C confirmed: the firmware
    does NOT require the HP 13-grid, only a regular structure.

    Historical note: MVP v1 used an "R≥G≥B 13-grid
    with R-B ≤ 4 steps" logic, which produced earth tones / warm browns instead
    of grays (1 step = 21 RGB units → R-B up to 85 = saturated brown).
    Version v2: true R=G=B gray ramp on a fine grid.

    Returns:
        (patches, notes)
    """
    exclude = exclude or set()

    # Generation on a fine grid: all R=G=B on [0, 255]
    candidates = [(v, v, v) for v in range(256) if (v, v, v) not in exclude]

    if len(candidates) < 121:
        raise RuntimeError(
            f"gray_ramp: only {len(candidates)} R=G=B candidates "
            f"after excluding the cube (< 121). Atypical cube?"
        )

    # Uniform subsampling: one patch every ~2 RGB units
    step = len(candidates) / 121
    patches = [candidates[int(i * step)] for i in range(121)]

    # Sanity check: 121 unique R=G=B patches
    if len(set(patches)) != 121:
        raise RuntimeError(
            f"gray_ramp : doublons internes ({len(set(patches))}/121 uniques)"
        )

    notes = [
        "gray_ramp v2: 121 R=G=B patches fine grid [0,255], "
        "pure gray ramp (spacing ~2 RGB units).",
        "R=G=B grammar validated empirically.",
    ]
    if exclude:
        notes.append(
            f"gray_ramp: 0 collision with the cube "
            f"(excluding {sum(1 for v in range(256) if (v,v,v) in exclude)} "
            f"pure grays of the cube)"
        )
    return patches, notes


# ─── Structural validation (basic strict) ────────────────────────────


def validate_chart_structure(
    patches: list[tuple[int, int, int]],
    cube_levels: Sequence[int],
    extras_mode: str = "",
) -> None:
    """
    Checks the Z9 firmware invariants on a complete chart.

    Basic strict (cf. Q4 of the Tier 1 design): abort if invariants violated,
    no detection of overlap with the previous pass.

    Invariants checked:
      - 343 patches in the first patches: regular 7³ cube with cube_levels
      - 121 extras patches unique among themselves
      - The extras MUST respect an ordered grammar (R≥G≥B, B≥G≥R, or
        R=G=B). But they may leave the HP 13-level grid for
        some modes (notably gray_ramp v2 which uses a fine grid).

    Raises ValueError if a violation is detected.
    """
    if len(patches) != N_TOTAL_PATCHES:
        raise ValueError(
            f"Expected {N_TOTAL_PATCHES} patches, got {len(patches)}"
        )

    levels_set = set(cube_levels)

    # 1. The first 343 form a regular 7³ cube with cube_levels
    cube_seen: set[tuple[int, int, int]] = set()
    for i in range(N_CUBE_PATCHES):
        r, g, b = patches[i]
        if r not in levels_set or g not in levels_set or b not in levels_set:
            raise ValueError(
                f"Patch cube #{i+1} = ({r},{g},{b}) hors levels cube_shift "
                f"{tuple(cube_levels)}"
            )
        cube_seen.add((r, g, b))
    if len(cube_seen) != N_CUBE_PATCHES:
        raise ValueError(
            f"7³ cube contains {len(cube_seen)}/{N_CUBE_PATCHES} unique "
            f"patches (duplicates detected)"
        )

    # 2. The 121 extras are unique and respect an ordered grammar
    grid_set = set(HP_GRID_13)
    extras_seen: set[tuple[int, int, int]] = set()
    n_off_grid = 0
    for i in range(N_CUBE_PATCHES, N_TOTAL_PATCHES):
        r, g, b = patches[i]
        # Ordered grammar: R≥G≥B, or B≥G≥R, or R=G=B (special case of
        # R≥G≥B). The firmware refuses patches without order (validated: OFPS
        # crashes CALCULATING 44%).
        if not (r >= g >= b or b >= g >= r):
            raise ValueError(
                f"Extra patch #{i+1} = ({r},{g},{b}) respects no "
                f"ordered grammar (R>=G>=B or B>=G>=R)"
            )
        # 13-grid check (informational — some modes like
        # gray_ramp v2 intentionally leave this grid)
        if r not in grid_set or g not in grid_set or b not in grid_set:
            n_off_grid += 1
        extras_seen.add((r, g, b))
    if len(extras_seen) != N_EXTRAS_PATCHES:
        raise ValueError(
            f"Extras contains {len(extras_seen)}/{N_EXTRAS_PATCHES} unique "
            f"patches (duplicates detected)"
        )

    # If many patches leave the 13-grid, this is expected for
    # gray_ramp v2 but unusual for the other modes — not an error.


# ─── Complete chart generation ───────────────────────────────────────


def build_ti1_multipass(
    *,
    cube_shift: str,
    extras: str,
    pass_number: int = 2,
    paper: str | None = None,
    hp_skintones_csv: Path | None = None,
) -> tuple[list[tuple[int, int, int]], MultipassChartSpec]:
    """
    Generates 464 RGB patches + descriptive spec of a multipass chart.

    Args:
        cube_shift: 'hp_canonical' | 'small_low' | 'small_high'
        extras:     'hp_skintones' | 'sky_tones' | 'gray_ramp'
        pass_number: pass number for the sidecar (does not affect the
                     generation, default 2 since pass 1 = HP factory native)
        paper:      paper name (sidecar info)
        hp_skintones_csv: path to rgb_7cube_plus.csv (optional)

    Returns:
        (patches, spec)
    """
    if cube_shift not in CUBE_LEVELS:
        raise ValueError(
            f"invalid cube_shift: {cube_shift!r}. "
            f"Options: {list(CUBE_LEVELS.keys())}"
        )
    if extras not in EXTRAS_MODES:
        raise ValueError(
            f"invalid extras: {extras!r}. Options: {list(EXTRAS_MODES)}"
        )

    levels = CUBE_LEVELS[cube_shift]
    cube_patches = build_cube_patches(levels)
    cube_set = set(cube_patches)

    notes: list[str] = []
    if extras == "hp_skintones":
        extras_patches, extras_notes = build_extras_hp_skintones(
            hp_skintones_csv, exclude=cube_set,
        )
    elif extras == "sky_tones":
        extras_patches, extras_notes = build_extras_sky_tones(exclude=cube_set)
    elif extras == "gray_ramp":
        extras_patches, extras_notes = build_extras_gray_ramp(exclude=cube_set)
    else:
        raise RuntimeError(f"Unsupported extras mode: {extras}")
    notes.extend(extras_notes)

    if len(extras_patches) != N_EXTRAS_PATCHES:
        raise RuntimeError(
            f"extras mode={extras} generated {len(extras_patches)} patches "
            f"instead of {N_EXTRAS_PATCHES}"
        )

    all_patches = cube_patches + extras_patches

    # Basic strict: validation of the firmware invariants
    validate_chart_structure(all_patches, levels, extras_mode=extras)

    spec = MultipassChartSpec(
        cube_shift=cube_shift,
        extras=extras,
        pass_number=pass_number,
        cube_levels=tuple(levels),
        n_patches=len(all_patches),
        n_cube=N_CUBE_PATCHES,
        n_extras=N_EXTRAS_PATCHES,
        paper=paper,
        created=datetime.now().isoformat(timespec="seconds"),
        notes=notes,
    )

    return all_patches, spec


# ─── .ti1 writing (Argyll CTI1 format) ───────────────────────────────


def write_ti1(
    patches: list[tuple[int, int, int]],
    spec: MultipassChartSpec,
    output_path: Path,
) -> None:
    """
    Writes a standard Argyll .ti1 file.

    Minimal CTI1 format compatible with `chart.py`:
      - SAMPLE_ID (1-indexed)
      - RGB_R, RGB_G, RGB_B in percent (0-100)

    No SAMPLE_LOC: the patch position is assigned by the downstream
    raster generator (chart.py / ChartOps).
    """
    lines: list[str] = [
        "CTI1   ",
        "",
        f'DESCRIPTOR "freeglaz multipass chart '
        f'(cube_shift={spec.cube_shift}, extras={spec.extras}, '
        f'pass={spec.pass_number})"',
        f'ORIGINATOR "{spec.generator}"',
        f'CREATED "{spec.created}"',
        "",
        'KEYWORD "DEVICE_CLASS"',
        'DEVICE_CLASS "OUTPUT"',
        'COLOR_REP "RGB"',
        "",
        "NUMBER_OF_FIELDS 4",
        "BEGIN_DATA_FORMAT",
        "SAMPLE_ID RGB_R RGB_G RGB_B",
        "END_DATA_FORMAT",
        "",
        f"NUMBER_OF_SETS {len(patches)}",
        "BEGIN_DATA",
    ]
    for i, (r, g, b) in enumerate(patches, start=1):
        rp = r * 100.0 / 255.0
        gp = g * 100.0 / 255.0
        bp = b * 100.0 / 255.0
        lines.append(f"{i:4d} {rp:.6f} {gp:.6f} {bp:.6f}")
    lines.append("END_DATA")
    lines.append("")

    output_path.write_text("\n".join(lines))


def write_sidecar(spec: MultipassChartSpec, output_path: Path) -> None:
    """
    Writes the JSON sidecar describing the chart.

    Used by downstream commands (debug, pass comparison,
    post-profiling diagnostic).
    """
    data = asdict(spec)
    data["cube_levels"] = list(data["cube_levels"])  # tuple → list for JSON
    output_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    )


# ─── High-level API (used by the CLI) ────────────────────────────────


class MultipassChartOps:
    """
    High-level facade used by the `freeglaz chart generate-multipass` command.

    No internal state — can be instantiated at any time.
    """

    def generate(
        self,
        *,
        cube_shift: str,
        extras: str,
        pass_number: int,
        output_ti1: Path,
        output_sidecar: Path | None = None,
        paper: str | None = None,
        hp_skintones_csv: Path | None = None,
    ) -> dict:
        """
        Generates .ti1 + JSON sidecar.

        Returns:
            dict with output_ti1_path, output_sidecar_path, n_patches,
            cube_shift, extras, pass_number, notes
        """
        patches, spec = build_ti1_multipass(
            cube_shift=cube_shift,
            extras=extras,
            pass_number=pass_number,
            paper=paper,
            hp_skintones_csv=hp_skintones_csv,
        )

        output_ti1 = Path(output_ti1).resolve()
        write_ti1(patches, spec, output_ti1)

        if output_sidecar is None:
            output_sidecar = output_ti1.with_suffix(".json")
        else:
            output_sidecar = Path(output_sidecar).resolve()
        write_sidecar(spec, output_sidecar)

        return {
            "output_ti1_path": output_ti1,
            "output_sidecar_path": output_sidecar,
            "n_patches": len(patches),
            "n_cube": N_CUBE_PATCHES,
            "n_extras": N_EXTRAS_PATCHES,
            "cube_shift": cube_shift,
            "extras": extras,
            "pass_number": pass_number,
            "cube_levels": list(spec.cube_levels),
            "notes": spec.notes,
        }
