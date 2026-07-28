"""icc_color_hash — colorimetric identity, metadata-independent.

Reproduces the real bug: a file's embedded ICC and the live get_profile export
of the SAME Z9 profile differed ONLY in the ``desc`` (name) tag, all colour
tables byte-identical, yet a full-file MD5 flagged a false mismatch. The colour
hash must be EQUAL across such differences, and CHANGE when a colour tag changes.
"""
import struct

from PIL import ImageCms

from webapp.backend.services.icc_identity import icc_color_hash


def _srgb() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _tag_table(icc: bytes):
    n = struct.unpack(">I", icc[128:132])[0]
    for i in range(n):
        o = 132 + i * 12
        sig = icc[o:o + 4]
        off, sz = struct.unpack(">II", icc[o + 4:o + 12])
        yield sig, off, sz


def test_hash_is_stable_and_nonempty():
    icc = _srgb()
    h = icc_color_hash(icc)
    assert h and icc_color_hash(icc) == h            # deterministic


def test_header_creation_date_ignored():
    icc = bytearray(_srgb())
    icc[24:36] = struct.pack(">6H", 1999, 1, 1, 0, 0, 0)   # mangle creation date
    assert icc_color_hash(bytes(icc)) == icc_color_hash(_srgb())


def test_desc_tag_change_ignored():
    # Flip a byte INSIDE the desc tag's data → colour hash unchanged (the bug).
    icc = bytearray(_srgb())
    desc = next((t for t in _tag_table(bytes(icc)) if t[0] == b"desc"), None)
    if desc is None:
        return  # sRGB always has desc, but stay defensive
    _, off, sz = desc
    icc[off + sz - 2] ^= 0xFF                          # perturb desc content
    assert icc_color_hash(bytes(icc)) == icc_color_hash(_srgb())


def test_colour_tag_change_detected():
    # Flip a byte in a NON-metadata (colour) tag → colour hash MUST change.
    base = _srgb()
    icc = bytearray(base)
    meta = {b"desc", b"cprt", b"dmnd", b"dmdd", b"meta", b"text"}
    color = next((t for t in _tag_table(base) if t[0] not in meta and t[2] > 0), None)
    assert color is not None
    _, off, sz = color
    icc[off + sz // 2] ^= 0xFF
    assert icc_color_hash(bytes(icc)) != icc_color_hash(base)


def test_garbage_and_none_return_none():
    assert icc_color_hash(None) is None
    assert icc_color_hash(b"") is None
    assert icc_color_hash(b"not an icc profile at all") is None
