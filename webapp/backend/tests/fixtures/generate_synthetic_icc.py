"""Generator for synthetic NON-HP printer ICC test fixtures (Option A).

Builds a minimal but raw-parseable ICC v4 printer profile (prtr/RGB/Lab) with:
  - A2B0 'mft2' LUT (grid g, decodable by profile_curvature.load_a2b_clut)
  - 'wtpt' XYZ tag (paper-like whitepoint; L>80, |a|<15, |b|<15)
  - 'desc' mluc tag (neutral non-HP name; exercises mluc parsing)
No HP data. Extends the byte layout of `_synth_mft2` (test_profile_compare.py)
to full grid + wtpt + mluc desc. NOT meant to be lcms2-valid (gamut degrades
gracefully to status='failed'); the Pillow-based mluc test uses a real bundled
non-HP mluc profile instead.
"""
import struct, math

def _s15f16(x): return struct.pack(">i", round(x * 65536))

def _mft2(g, a_scale, b_scale, l_gamma):
    ic = oc = 3
    n_in = n_out = 2
    body = bytearray()
    body += b"mft2" + b"\x00\x00\x00\x00"
    body += bytes([ic, oc, g, 0])
    for v in (65536,0,0, 0,65536,0, 0,0,65536):   # 3x3 identity e_matrix
        body += struct.pack(">i", v)
    body += struct.pack(">H", n_in) + struct.pack(">H", n_out)
    for _ in range(ic):                            # input curves (trivial 0..65535)
        body += struct.pack(">HH", 0, 65535)
    # CLUT g^3 nodes, Lab-encoded: L=v/65535*100, a=v/65535*255-128
    gm1 = g - 1
    for i in range(g):
        for j in range(g):
            for k in range(g):
                t = (i + j + k) / (3 * gm1)
                L = 100.0 * (t ** l_gamma)
                a = (i - k) / gm1 * a_scale
                b = (j - (i + k) / 2.0) / gm1 * b_scale
                vL = max(0, min(65535, round(L / 100.0 * 65535)))
                vA = max(0, min(65535, round((a + 128.0) / 255.0 * 65535)))
                vB = max(0, min(65535, round((b + 128.0) / 255.0 * 65535)))
                body += struct.pack(">HHH", vL, vA, vB)
    for _ in range(oc):                            # output curves
        body += struct.pack(">HH", 0, 65535)
    return bytes(body)

def _wtpt(X, Y, Z):
    return b"XYZ " + b"\x00\x00\x00\x00" + _s15f16(X) + _s15f16(Y) + _s15f16(Z)

def _desc_mluc(name):
    u = name.encode("utf-16-be")
    # 'mluc' rsv count(1) recsize(12) | rec: lang('en') country('US') len off(=28)
    rec = b"enUS" + struct.pack(">II", len(u), 28)
    return b"mluc" + b"\x00\x00\x00\x00" + struct.pack(">II", 1, 12) + rec + u

def build(name, g, a_scale, b_scale, l_gamma, wp):
    a2b0 = _mft2(g, a_scale, b_scale, l_gamma)
    wtpt = _wtpt(*wp)
    desc = _desc_mluc(name)
    tags = [(b"A2B0", a2b0), (b"desc", desc), (b"wtpt", wtpt)]
    header = bytearray(128)
    header[8:10] = b"\x04\x20"          # v4.2.0
    header[12:16] = b"prtr"
    header[16:20] = b"RGB "
    header[20:24] = b"Lab "
    header[36:40] = b"acsp"
    n = len(tags)
    table_size = 4 + n * 12
    data_off = 128 + table_size
    # 4-byte align each tag
    entries = []
    blobs = bytearray()
    cur = data_off
    for sig, blob in tags:
        if cur % 4: 
            pad = 4 - (cur % 4); blobs += b"\x00"*pad; cur += pad
        entries.append((sig, cur, len(blob)))
        blobs += blob; cur += len(blob)
    table = struct.pack(">I", n)
    for sig, off, sz in entries:
        table += sig + struct.pack(">II", off, sz)
    out = bytes(header) + table + bytes(blobs)
    out = struct.pack(">I", len(out)) + out[4:]   # header bytes 0-4 = profile size
    return out

if __name__ == "__main__":
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    A = build("freeglaz Test Resident A (synthetic)", 9,  64.0, 64.0, 1.04, (0.8680, 0.9000, 0.7276))
    B = build("freeglaz Test Resident B (synthetic)", 11, 48.0, 80.0, 1.10, (0.8556, 0.8900, 0.7609))
    with open(os.path.join(here, "synthetic_test_resident_A.icc"), "wb") as f:
        f.write(A)
    with open(os.path.join(here, "synthetic_test_resident_B.icc"), "wb") as f:
        f.write(B)
    print(f"A: {len(A)} bytes   B: {len(B)} bytes   (distinct file_size: {len(A) != len(B)})")
