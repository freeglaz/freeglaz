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

"""Link 7 — native CGATS bridge (SOL channel) -> Argyll ti3.

`sol_native.measure()` returns a native CGATS.17: columns `PATCH_ROW PATCH_COL
SPECTRAL_400…700`, **WITHOUT RGB**. `colprof` (via `build_cti3`) requires `RGB_R/G/B`.
This module **injects the RGB** from the chart descriptor (`patches_in_layout_order`)
into the native CGATS — **matched by (row, col)** — then delegates to `build_cti3`.
Parametric glue: N patches / N columns whatsoever, no 464 assumption.

CRITICAL point — the matching (silent corruption otherwise):
  - the native CGATS is in row-major order (PATCH_ROW/PATCH_COL, **1-based**);
  - the descriptor carries row/col (**0-based**), index, rgb (layout order).
  We match **explicitly by (row, col)** (deterministic key), NOT by simple
  order of appearance — a shift would produce a wrong ti3 WITHOUT an error. The bijection
  is verified (count + set of (row,col)) before any write, in the spirit
  of the guards in `remap_ti3_from_sidecar`.

Reused building blocks (imported from `profiling`, a module we control):
`parse_cgats_data`, `build_cti3`, `apply_spec2cie_xyz_correction`.
XYZ via Argyll `spec2cie -i D50 -o 1931_2` (official CIE table, cf XYZ bug).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .profiling import (
    parse_cgats_data,
    build_cti3,
    apply_spec2cie_xyz_correction,
)

logger = logging.getLogger(__name__)


@dataclass
class NativeTi3Result:
    """Result of build_ti3_from_native_cgats()."""
    output_path: str
    n_patches: int
    n_spectral_bands: int
    spectral_range_nm: tuple[int, int]
    xyz_method: str           # 'spec2cie D50/1931_2' or 'fallback local table'
    rowmajor_consistent: bool  # (row,col) matching == order of appearance?


def _load_patches(descriptor) -> list[dict]:
    """Return patches_in_layout_order (list of {row,col,index,sample_id,rgb}).

    `descriptor`: JSON path (sidecar/chart.json) OR already-loaded dict."""
    if isinstance(descriptor, (str, Path)):
        with open(descriptor, "r", encoding="utf-8") as f:
            descriptor = json.load(f)
    patches = descriptor.get("patches_in_layout_order")
    if not patches:
        raise ValueError(
            "Descripteur sans 'patches_in_layout_order' — RGB introuvables."
        )
    return patches


def build_ti3_from_native_cgats(
    cgats,
    descriptor,
    output_path: str | Path,
    *,
    apply_xyz_correction: bool = True,
    illuminant: str = "D50",
    observer: str = "1931_2",
    descriptor_label: str = "freeglaz from native SOL scan",
    on_step=None,
) -> NativeTi3Result:
    """Native CGATS (SOL) + RGB descriptor -> Argyll ti3 ready for colprof.

    :param cgats: native CGATS text OR path to the .cgats.
    :param descriptor: JSON path (chart.json/sidecar) OR dict, carrying
                       `patches_in_layout_order` (row/col/index/rgb).
    :param output_path: path of the produced ti3.
    :param apply_xyz_correction: recompute the XYZ via spec2cie (recommended).
    :raises ValueError: unexpected CGATS format, or non-bijective matching
                        (truncated/reordered scan) -> ti3 NOT produced.
    """
    def _step(n, total, label, **d):
        if on_step:
            on_step(n, total, label, **d)

    output_path = Path(output_path)

    # 1. Parse the native CGATS
    _step(1, 5, "parse-cgats")
    if isinstance(cgats, (str, Path)) and "\n" not in str(cgats) and Path(cgats).exists():
        cgats_text = Path(cgats).read_text(encoding="utf-8")
    else:
        cgats_text = cgats
    parsed = parse_cgats_data(cgats_text)   # NUMBER_OF_SETS guard included
    fmt, data = parsed["format"], parsed["data"]

    try:
        i_row = fmt.index("PATCH_ROW")
        i_col = fmt.index("PATCH_COL")
    except ValueError:
        raise ValueError(
            f"Native CGATS expected (PATCH_ROW/PATCH_COL missing): {fmt[:4]}"
        )
    spec_cols = [(i, name) for i, name in enumerate(fmt)
                 if name.startswith("SPECTRAL_")]
    if not spec_cols:
        raise ValueError(f"No SPECTRAL_* band in the CGATS: {fmt}")

    # 2. Load the descriptor RGB, indexed by (row, col) AND by order
    _step(2, 5, "load-descriptor")
    patches = _load_patches(descriptor)
    n_patches = len(patches)
    rgb_by_rowcol: dict[tuple[int, int], tuple] = {}
    for it in patches:
        rgb_by_rowcol[(int(it["row"]), int(it["col"]))] = tuple(it["rgb"])
    # order of appearance (= descriptor index order, row-major) for the check
    rgb_by_appearance = [tuple(it["rgb"]) for it in patches]

    # 3. BIJECTION guard (count + coverage of (row,col)) — reject a
    #    truncated/reordered scan BEFORE writing a wrong ti3.
    _step(3, 5, "check-bijection",
          n_cgats=len(data), n_descriptor=n_patches)
    if len(data) != n_patches:
        raise ValueError(
            f"Pairing refused: {len(data)} measured patches (CGATS) != "
            f"{n_patches} expected (descriptor). Incomplete scan — ti3 NOT produced."
        )
    cgats_keys = []
    for r in data:
        # CGATS 1-based -> descriptor 0-based
        cgats_keys.append((int(r[i_row]) - 1, int(r[i_col]) - 1))
    desc_keys = set(rgb_by_rowcol)
    missing = sorted(desc_keys - set(cgats_keys))      # expected patch not measured
    extra = sorted(set(cgats_keys) - desc_keys)        # (row,col) measured but unknown
    dups = len(cgats_keys) - len(set(cgats_keys))
    if missing or extra or dups:
        raise ValueError(
            f"Non-bijective (row,col) pairing: missing={missing[:8]} "
            f"extra={extra[:8]} duplicates={dups}. Disordered/incomplete scan — "
            f"ti3 NOT produced."
        )

    # 4. Inject the RGB by (row, col) + row-major consistency check
    _step(4, 5, "inject-rgb")
    rowmajor_consistent = True
    inj_data = []
    for i, r in enumerate(data):
        rgb = rgb_by_rowcol[cgats_keys[i]]              # AUTHORITATIVE matching
        if i < len(rgb_by_appearance) and rgb != rgb_by_appearance[i]:
            rowmajor_consistent = False                # CGATS order != row-major
        spectra = [r[ci] for ci, _ in spec_cols]
        inj_data.append([str(rgb[0]), str(rgb[1]), str(rgb[2])] + spectra)
    if not rowmajor_consistent:
        logger.warning(
            "CGATS not in row-major order: (row,col) matching applied "
            "(authoritative), order divergence detected — ti3 remains correct."
        )

    parsed_inj = {
        "header": parsed["header"],
        "format": ["RGB_R", "RGB_G", "RGB_B"] + [name for _, name in spec_cols],
        "data": inj_data,
    }
    cti3_text = build_cti3(parsed_inj, descriptor=descriptor_label)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(cti3_text, encoding="utf-8")

    # 5. XYZ via spec2cie (official CIE table) — otherwise fallback local table
    _step(5, 5, "xyz-correction")
    xyz_method = "local fallback table (build_cti3)"
    if apply_xyz_correction:
        try:
            apply_spec2cie_xyz_correction(output_path, illuminant=illuminant,
                                          observer=observer)
            xyz_method = f"spec2cie {illuminant}/{observer}"
        except FileNotFoundError:
            logger.warning(
                "spec2cie unavailable: fallback XYZ kept (local table bias). "
                "Install Argyll for bit-perfect XYZ."
            )

    wl = [int(name.split("_")[1]) for _, name in spec_cols]
    return NativeTi3Result(
        output_path=str(output_path),
        n_patches=len(inj_data),
        n_spectral_bands=len(spec_cols),
        spectral_range_nm=(min(wl), max(wl)),
        xyz_method=xyz_method,
        rowmajor_consistent=rowmajor_consistent,
    )
