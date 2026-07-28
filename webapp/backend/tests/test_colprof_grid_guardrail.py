"""Guardrail against silent corruption of the colprof build (bug #1).

The faulty pattern (`$FLAGS` unquoted in zsh -> flags passed as a single argument
-> colprof ignores them -> default 17^3 build without -S, no error) produced
valid profiles with the wrong settings. The repo code already passes the args as
a LIST (immune); this guardrail additionally detects any grid-vs-q mismatch after
the fact.
"""

import struct

from lib.z9_client.profiling import (
    _a2b0_clut_grid,
    _expected_grid_for_flags,
)


def test_expected_grid_for_flags():
    assert _expected_grid_for_flags(["-v", "-qh", "-nc"]) == 33
    assert _expected_grid_for_flags(["-qm"]) == 17
    assert _expected_grid_for_flags(["-ql"]) == 9
    assert _expected_grid_for_flags(["-qu"]) == 45
    # no -q -> undetermined (no false alarm)
    assert _expected_grid_for_flags(["-v", "-S", "x.icc"]) is None
    # -q + other flags stuck together
    assert _expected_grid_for_flags(["-v", "-qh", "-r", "0.7"]) == 33


def _synthetic_icc_with_a2b0_grid(grid: int) -> bytes:
    """Build a minimal ICC with an A2B0 lut16Type 'mft2' tag of the given grid."""
    mft2_off = 160
    # mft2 : 'mft2'(4) + reserved(4) + ic(1) + oc(1) + grid(1) + reserved(1) + ...
    mft2 = b"mft2" + b"\x00" * 4 + bytes([3, 3, grid, 0]) + b"\x00" * 40
    head = bytearray(mft2_off + len(mft2))
    struct.pack_into(">I", head, 128, 1)          # 1 tag
    head[132:136] = b"A2B0"
    struct.pack_into(">I", head, 136, mft2_off)   # offset
    struct.pack_into(">I", head, 140, len(mft2))  # size
    head[mft2_off:mft2_off + len(mft2)] = mft2
    return bytes(head)


def test_a2b0_clut_grid_reads_grid(tmp_path):
    for g in (9, 17, 33, 45):
        p = tmp_path / f"prof_{g}.icc"
        p.write_bytes(_synthetic_icc_with_a2b0_grid(g))
        assert _a2b0_clut_grid(p) == g


def test_a2b0_clut_grid_robust_on_garbage(tmp_path):
    p = tmp_path / "garbage.icc"
    p.write_bytes(b"not an icc file at all")
    assert _a2b0_clut_grid(p) is None


def test_guardrail_mismatch_detectable():
    """The guardrail logic: -qh expects 33, but 17 obtained -> mismatch detected."""
    expected = _expected_grid_for_flags(["-v", "-qh", "-nc"])
    actual = 17  # what the faulty pattern produced
    assert expected == 33
    assert actual != expected  # -> the guardrail logs a warning
