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
Multi-pass concatenation of ti3 measurement sets for freeglaz.

Strategic pivot of the project: combine the measurements of several
charts (HP factory + freeglaz shifted cubes + N additional freeglaz
passes) into a single unified ti3 that is passed to colprof to produce
an enriched ICC profile.

Input: native Argyll ti3 only.
For an HP factory ICC profile, extract it beforehand:
    freeglaz chart extract-cgats hp_factory.icc -o hp_factory.ti3

Consistency required for concatenation:
    - Identical spectral bands (wavelengths and count)
    - Same data_format columns (checked, otherwise automatic intersection)
    - Same physical paper, same printmode, same cartridges, same CLC

Duplicates:
    RGB patches duplicated across sources (typically the device cube
    corners: (0,0,0), (255,255,255), primaries, secondaries) are kept
    by default. Argyll uses them as multiple observations of the same
    point → reduces measurement noise via implicit averaging.

    Strategies (dedup_strategy):
        "keep"   : keep all duplicates (default, recommended)
        "first"  : keep only the first occurrence
        "last"   : keep only the last (override by later sources)
        "mean"   : average the duplicate measurements into a single patch

Architecture:
    - Pure module-level functions (load_source, merge_sources, write_ti3)
    - MultiPassOps class: public API, exposed via Z9Client
    - on_step callback for CLI tracing

Intended CLI usage:
    freeglaz chart concat-ti3 \\
        --ti3 hp_factory.ti3 \\
        --ti3 pass2_freeglaz.ti3 \\
        --ti3 pass3_freeglaz.ti3 \\
        --output multipass.ti3 \\
        --dedup keep

The produced ti3 can then be passed directly to colprof or to a future
`freeglaz chart build-multipass-profile`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional


# ─── Dataclasses ──────────────────────────────────────────────────────────


@dataclass
class TI3Source:
    """An Argyll ti3 measurement source, ready for concatenation."""
    label: str
    source_path: str
    n_patches: int
    spectral_bands: list[int]
    data_format: list[str]
    data_rows: list[list[str]]
    header_lines: list[str]
    metadata: dict = field(default_factory=dict)


@dataclass
class ConcatResult:
    output_path: Path
    n_sources: int
    n_patches_total: int
    n_patches_input: int
    n_duplicates_dropped: int
    sources: list[TI3Source]
    dedup_strategy: str
    spectral_bands: list[int]
    dropped_columns: list[str]


# ─── Pure functions ───────────────────────────────────────────────────────


def parse_ti3(content: str) -> tuple[list[str], list[str], list[list[str]]]:
    """Read an Argyll ti3. (Duplicates parse_ti3 from profiling.py for
    standalone use. Once integrated into the z9_client module, replace
    with an import.)"""
    lines = content.splitlines()
    header_lines: list[str] = []
    data_format: Optional[list[str]] = None
    data_rows: list[list[str]] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip() == 'BEGIN_DATA_FORMAT':
            data_format = lines[i + 1].split()
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
                    data_rows.append(stripped.split())
                j += 1
            break
        header_lines.append(line)
        i += 1

    if data_format is None:
        raise ValueError("No BEGIN_DATA_FORMAT section found")
    if not data_rows:
        raise ValueError("No patches found in BEGIN_DATA")

    return header_lines, data_format, data_rows


def extract_spectral_bands(data_format: list[str]) -> list[int]:
    """Extract the wavelengths from the SPEC_NNN columns (native Argyll)."""
    bands = []
    for col in data_format:
        if col.startswith('SPEC_'):
            try:
                bands.append(int(col.split('_')[1]))
            except (ValueError, IndexError):
                pass
    return sorted(bands)


def _parse_header_metadata(header_lines: list[str]) -> dict:
    """Extract the useful fields from the ti3 header."""
    meta = {}
    for line in header_lines:
        line = line.strip()
        for key in ('DESCRIPTOR', 'ORIGINATOR', 'CREATED',
                    'TARGET_INSTRUMENT', 'INSTRUMENT_TYPE_SPECTRAL',
                    'ILLUMINANT', 'OBSERVER', 'MEASUREMENT_TYPE',
                    'SPECTRAL_BANDS', 'SPECTRAL_START_NM', 'SPECTRAL_END_NM',
                    'NORMALIZED_TO_Y_100'):
            if line.startswith(key + ' ') or line == key:
                rest = line[len(key):].strip()
                if rest.startswith('"') and rest.endswith('"'):
                    meta[key] = rest[1:-1]
                else:
                    meta[key] = rest
                break
    return meta


def load_source(
    path: Path,
    label: Optional[str] = None,
    on_step: Optional[Callable] = None,
) -> TI3Source:
    """Load an Argyll ti3 source.

    :raises ValueError: if .icc/.icm (extract first via extract-cgats)
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"ti3 file not found: {path}")
    if path.suffix.lower() in ('.icc', '.icm'):
        raise ValueError(
            f"{path.name} is an ICC file. "
            f"First extract the ti3:\n"
            f"  freeglaz chart extract-cgats {path.name} -o <output>.ti3"
        )

    if label is None:
        label = path.stem

    if on_step:
        on_step('load', f'Loading {label}', source=str(path))

    with open(path) as f:
        content = f.read()

    header_lines, data_format, data_rows = parse_ti3(content)
    spectral_bands = extract_spectral_bands(data_format)
    metadata = _parse_header_metadata(header_lines)

    if on_step:
        if spectral_bands:
            msg = (f'{label}: {len(data_rows)} patches, '
                   f'{len(spectral_bands)} bands '
                   f'({spectral_bands[0]}–{spectral_bands[-1]} nm)')
        else:
            msg = f'{label}: {len(data_rows)} patches (no spectra)'
        on_step('load', msg)

    return TI3Source(
        label=label,
        source_path=str(path),
        n_patches=len(data_rows),
        spectral_bands=spectral_bands,
        data_format=data_format,
        data_rows=data_rows,
        header_lines=header_lines,
        metadata=metadata,
    )


def check_sources_compatible(sources: list[TI3Source]) -> tuple[bool, list[str]]:
    """Check the compatibility of the sources for concatenation."""
    if len(sources) < 2:
        return (True, [])

    warnings = []
    ref = sources[0]

    for src in sources[1:]:
        if src.spectral_bands != ref.spectral_bands:
            return (False, [
                f"Bandes spectrales incompatibles : "
                f"{ref.label}={ref.spectral_bands} vs {src.label}={src.spectral_bands}"
            ])

        for col in ('RGB_R', 'RGB_G', 'RGB_B'):
            if col not in src.data_format or col not in ref.data_format:
                return (False, [f"Column {col} missing in {src.label}"])

        ref_ill = ref.metadata.get('ILLUMINANT')
        src_ill = src.metadata.get('ILLUMINANT')
        if ref_ill and src_ill and ref_ill != src_ill:
            warnings.append(
                f"Different illuminant: {ref.label}={ref_ill} vs {src.label}={src_ill}"
            )

        ref_obs = ref.metadata.get('OBSERVER')
        src_obs = src.metadata.get('OBSERVER')
        if ref_obs and src_obs and ref_obs != src_obs:
            warnings.append(
                f"Different observer: {ref.label}={ref_obs} vs {src.label}={src_obs}"
            )

    return (True, warnings)


def _align_data_format(
    sources: list[TI3Source],
) -> tuple[list[str], list[list[list[str]]], list[str]]:
    """Align the data_format on their INTERSECTION.

    XYZ dropped if not common (Argyll recomputes via colprof).

    :return: (common_format, [rows_aligned_per_source], dropped_columns)
    """
    if not sources:
        return [], [], []

    common = set(sources[0].data_format)
    for src in sources[1:]:
        common &= set(src.data_format)

    ordered = [col for col in sources[0].data_format if col in common]
    if not ordered:
        raise ValueError("No common column between sources")

    all_cols = set()
    for src in sources:
        all_cols |= set(src.data_format)
    dropped = sorted(all_cols - common)

    aligned_rows_per_source = []
    for src in sources:
        col_indices = [src.data_format.index(col) for col in ordered]
        aligned = [[row[i] for i in col_indices] for row in src.data_rows]
        aligned_rows_per_source.append(aligned)

    return ordered, aligned_rows_per_source, dropped


def _mean_rows(row_a: list[str], row_b: list[str], data_format: list[str]) -> list[str]:
    """Average two rows (SAMPLE_ID and RGB preserved)."""
    merged = list(row_a)
    keep_idx = {0}
    for col in ('RGB_R', 'RGB_G', 'RGB_B'):
        if col in data_format:
            keep_idx.add(data_format.index(col))

    for i in range(len(row_a)):
        if i in keep_idx:
            continue
        try:
            va = float(row_a[i])
            vb = float(row_b[i])
            merged[i] = f"{(va + vb) / 2:.10f}"
        except (ValueError, IndexError):
            pass
    return merged


def merge_sources(
    sources: list[TI3Source],
    dedup_strategy: str = "keep",
    on_step: Optional[Callable] = None,
) -> tuple[list[str], list[list[str]], int, list[str]]:
    """Merge several TI3Source.

    :return: (unified_data_format, unified_data_rows, n_dup, dropped_columns)
    """
    if not sources:
        raise ValueError("No source to concatenate")

    ref_format, aligned_per_source, dropped = _align_data_format(sources)

    if dropped and on_step:
        on_step('merge', f'⚠ Dropped columns (not common to all sources): {dropped}')

    r_idx = ref_format.index('RGB_R')
    g_idx = ref_format.index('RGB_G')
    b_idx = ref_format.index('RGB_B')

    all_rows = []
    seen_rgb = {}
    n_dup = 0
    next_sample_id = 1

    for src, aligned_rows in zip(sources, aligned_per_source):
        if on_step:
            on_step('merge', f'{src.label}: adding {len(aligned_rows)} patches')

        for row in aligned_rows:
            new_row = list(row)
            new_row[0] = str(next_sample_id)
            next_sample_id += 1

            try:
                r = round(float(new_row[r_idx]), 1)
                g = round(float(new_row[g_idx]), 1)
                b = round(float(new_row[b_idx]), 1)
                rgb_key = (r, g, b)
            except ValueError:
                rgb_key = None

            if rgb_key is not None and rgb_key in seen_rgb:
                if dedup_strategy == "keep":
                    all_rows.append(new_row)
                elif dedup_strategy == "first":
                    n_dup += 1
                    continue
                elif dedup_strategy == "last":
                    n_dup += 1
                    all_rows[seen_rgb[rgb_key]] = new_row
                    continue
                elif dedup_strategy == "mean":
                    n_dup += 1
                    existing_idx = seen_rgb[rgb_key]
                    merged = _mean_rows(all_rows[existing_idx], new_row, ref_format)
                    all_rows[existing_idx] = merged
                    continue
                else:
                    raise ValueError(f"unknown dedup_strategy: {dedup_strategy}")
            else:
                if rgb_key is not None:
                    seen_rgb[rgb_key] = len(all_rows)
                all_rows.append(new_row)

    return ref_format, all_rows, n_dup, dropped


def write_multipass_ti3(
    output_path: Path,
    data_format: list[str],
    data_rows: list[list[str]],
    descriptor: str,
    sources: list[TI3Source],
    dedup_strategy: str,
) -> None:
    """Write the multipass ti3 in native Argyll format (with SPECTRAL_* metadata)."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    header = [
        'CTI3',
        '',
        f'DESCRIPTOR "{descriptor}"',
        'ORIGINATOR "freeglaz multipass (lib/z9_client/multipass.py)"',
        f'CREATED "{datetime.now().date().isoformat()}"',
        '',
        '# Concatenated sources:',
    ]
    for i, src in enumerate(sources, 1):
        header.append(f'#   [{i}] {src.label} ({src.n_patches} patches) — {src.source_path}')
    header.append(f'# Dedup strategy: {dedup_strategy}')
    header.append('')
    header.append('KEYWORD "DEVICE_CLASS"')
    header.append('DEVICE_CLASS "OUTPUT"')
    header.append('')
    header.append('KEYWORD "COLOR_REP"')
    header.append('COLOR_REP "RGB_XYZ"')
    header.append('')

    ref_meta = sources[0].metadata
    if 'TARGET_INSTRUMENT' in ref_meta:
        header.append('KEYWORD "TARGET_INSTRUMENT"')
        header.append(f'TARGET_INSTRUMENT "{ref_meta["TARGET_INSTRUMENT"]}"')
        header.append('')
    if 'INSTRUMENT_TYPE_SPECTRAL' in ref_meta:
        header.append('KEYWORD "INSTRUMENT_TYPE_SPECTRAL"')
        header.append(f'INSTRUMENT_TYPE_SPECTRAL "{ref_meta["INSTRUMENT_TYPE_SPECTRAL"]}"')
        header.append('')
    if 'NORMALIZED_TO_Y_100' in ref_meta:
        header.append('KEYWORD "NORMALIZED_TO_Y_100"')
        if ref_meta['NORMALIZED_TO_Y_100']:
            header.append(f'NORMALIZED_TO_Y_100 "{ref_meta["NORMALIZED_TO_Y_100"]}"')
        header.append('')

    # Spectral metadata (required for Argyll to recognize SPEC_NNN)
    spec_bands = []
    for col in data_format:
        if col.startswith('SPEC_'):
            try:
                spec_bands.append(int(col.split('_')[1]))
            except (ValueError, IndexError):
                pass
    spec_bands.sort()
    if spec_bands:
        header.append(f'SPECTRAL_BANDS "{len(spec_bands)}"')
        header.append(f'SPECTRAL_START_NM "{float(spec_bands[0]):.1f}"')
        header.append(f'SPECTRAL_END_NM "{float(spec_bands[-1]):.1f}"')
        header.append('')

    header.append(f'NUMBER_OF_FIELDS {len(data_format)}')
    header.append('BEGIN_DATA_FORMAT')
    header.append(' ' + ' '.join(data_format))
    header.append('END_DATA_FORMAT')
    header.append('')
    header.append(f'NUMBER_OF_SETS {len(data_rows)}')
    header.append('BEGIN_DATA')
    for row in data_rows:
        header.append(' ' + ' '.join(row))
    header.append('END_DATA')

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(header) + '\n')


# ─── Public API ───────────────────────────────────────────────────────────


def concat_ti3(
    source_paths: list[Path],
    output_path: Path,
    labels: Optional[list[str]] = None,
    descriptor: Optional[str] = None,
    dedup_strategy: str = "keep",
    on_step: Optional[Callable] = None,
) -> ConcatResult:
    """Main API: concatenate N Argyll ti3 sources into a single one.

    :param source_paths: list of .ti3 paths (native Argyll only)
    :param output_path: path of the merged output ti3
    :param labels: labels for traceability (default: file stems)
    :param descriptor: descriptor of the final ti3
    :param dedup_strategy: "keep" (default) | "first" | "last" | "mean"
    :param on_step: CLI progress callback
    :return: ConcatResult with metadata
    """
    source_paths = [Path(p) for p in source_paths]
    output_path = Path(output_path)

    if len(source_paths) < 1:
        raise ValueError("At least 1 source required")
    if labels is not None and len(labels) != len(source_paths):
        raise ValueError("Number of labels != number of sources")

    sources: list[TI3Source] = []
    for i, path in enumerate(source_paths):
        label = labels[i] if labels else path.stem
        src = load_source(path, label=label, on_step=on_step)
        sources.append(src)

    if on_step:
        on_step('check', 'Checking source compatibility')
    ok, warnings = check_sources_compatible(sources)
    if not ok:
        raise ValueError(f"Incompatible sources: {warnings[0]}")
    if warnings and on_step:
        for w in warnings:
            on_step('check', f'⚠ Warning: {w}')

    if on_step:
        on_step('merge', f'Merge ({dedup_strategy})')
    data_format, data_rows, n_dup, dropped = merge_sources(
        sources, dedup_strategy=dedup_strategy, on_step=on_step,
    )

    if descriptor is None:
        descriptor = f"freeglaz multipass ({len(sources)} sources)"
    if on_step:
        on_step('write', f'Writing {output_path.name} ({len(data_rows)} patches)')
    write_multipass_ti3(output_path, data_format, data_rows, descriptor, sources, dedup_strategy)

    n_input = sum(s.n_patches for s in sources)

    return ConcatResult(
        output_path=output_path,
        n_sources=len(sources),
        n_patches_total=len(data_rows),
        n_patches_input=n_input,
        n_duplicates_dropped=n_dup,
        sources=sources,
        dedup_strategy=dedup_strategy,
        spectral_bands=sources[0].spectral_bands if sources else [],
        dropped_columns=dropped,
    )


# ─── API class for Z9Client integration ───────────────────────────────────


class MultiPassOps:
    """Multi-pass operations for freeglaz.

    Exposed via Z9Client as `client.multipass.concat_ti3(...)`.
    Future extension: `build_multipass_profile()` chaining concat + colprof.
    """

    def __init__(self, z9_client=None):
        self.z9_client = z9_client

    def concat_ti3(self, **kwargs) -> ConcatResult:
        return concat_ti3(**kwargs)


# ─── Standalone entry point (test) ────────────────────────────────────────


def main():
    import argparse
    import sys

    p = argparse.ArgumentParser(
        description="Concatenate multi-pass ti3 files (native Argyll only)",
        epilog="For an HP factory ICC: first extract via "
               "'freeglaz chart extract-cgats hp.icc -o hp.ti3'"
    )
    p.add_argument('--ti3', action='append', required=True,
                   help='Path of an Argyll ti3 source (repeatable)')
    p.add_argument('--label', action='append', default=None,
                   help='Source label (repeatable, same order as --ti3)')
    p.add_argument('--output', required=True, help='output multipass ti3')
    p.add_argument('--descriptor', default=None, help='Descriptor of the final ti3')
    p.add_argument('--dedup', choices=['keep', 'first', 'last', 'mean'],
                   default='keep', help='Dedup strategy (default: keep)')
    p.add_argument('-v', '--verbose', action='store_true')
    args = p.parse_args()

    def on_step(stage, label, n=0, total=0, **kw):
        icon = {'load': '📥', 'check': '🔍', 'merge': '🔀', 'write': '💾'}.get(stage, '•')
        if n and total:
            print(f"  {icon} [{n}/{total}] {label}")
        else:
            print(f"  {icon} {label}")

    print(f"🔀 freeglaz multi-pass concatenation")
    print(f"  Sources : {len(args.ti3)}")
    for i, path in enumerate(args.ti3, 1):
        print(f"    [{i}] {path}")
    print(f"  Output  : {args.output}")
    print(f"  Dedup   : {args.dedup}")
    print()

    try:
        result = concat_ti3(
            source_paths=args.ti3,
            output_path=args.output,
            labels=args.label,
            descriptor=args.descriptor,
            dedup_strategy=args.dedup,
            on_step=on_step if args.verbose else None,
        )
    except Exception as e:
        print(f"❌ {type(e).__name__}: {e}")
        sys.exit(1)

    print()
    print(f"✅ Concatenation succeeded")
    print(f"  Patches input    : {result.n_patches_input}")
    print(f"  Patches output   : {result.n_patches_total}")
    print(f"  Duplicates dropped: {result.n_duplicates_dropped} (strategy: {result.dedup_strategy})")
    if result.spectral_bands:
        print(f"  Spectral bands   : {len(result.spectral_bands)} "
              f"({result.spectral_bands[0]}–{result.spectral_bands[-1]} nm)")
    if result.dropped_columns:
        print(f"  Dropped columns  : {result.dropped_columns}")
        print(f"                     (Argyll will recompute them from the spectra)")
    print(f"  Output file      : {result.output_path}")
    print()
    print(f"💡 Next step:")
    base = str(result.output_path).rsplit('.', 1)[0]
    print(f"   colprof -v -qh \\")
    print(f"     -cmt -dpp \\")
    print(f"     -A \"HP\" -M \"DesignJet Z9\" -C \"No copyright, use freely\" \\")
    print(f"     -D \"freeglaz multipass\" \\")
    print(f"     {base}")


if __name__ == '__main__':
    main()
