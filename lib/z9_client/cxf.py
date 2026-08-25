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

"""Read i1Profiler / X-Rite CxF3 measurements embedded in an ICC ``CxF `` tag.

i1Profiler (X-Rite) stores the full spectral measurements of a printer
characterization inside the ICC ``CxF `` tag — type ``ZXML`` = zlib-compressed
CxF3 XML — NOT in the ``CIED`` / ``targ`` tags used by the Z9 firmware or Argyll.
So ``extract_cgats_from_icc`` reports "no embedded measurements" on an i1Profiler
V4 profile even though the spectral data is fully present.

This module reads that ``CxF `` tag and emits an HP-style **CGATS.17** text
(``RGB_R/G/B`` in 0-255, ``SPECTRAL_<nm>`` reflectance in 0-1), which the existing
pipeline consumes unchanged (``parse_cgats_data`` -> ``build_cti3`` -> spec2cie).

CxF3 structure observed (i1Profiler, verified on a 1600-patch Z9 Pro profile):
  - one ``Object ObjectType="Target"`` per patch:
    ``DeviceColorValues/ColorRGB`` with ``<R>/<G>/<B>`` in 0-255,
  - one ``Object ObjectType="<M>_Measurement"`` per patch per condition
    (M0/M1/M2): ``ColorValues/ReflectanceSpectrum`` with ``StartWL`` and
    space-separated reflectances in 0-1,
  - both carry a ``TagCollection Name="Location"`` (Column/Page/Row) — the key
    used to pair a device value with its measurement,
  - ``WavelengthRange`` gives the nm increment between bands.

Enabling this = an OPEN profiling path for machines whose embedded spectro is
locked (e.g. the Z9 Pro): measure with an external i1 -> CxF3 ICC -> freeglaz
reads it -> Argyll builds the profile.
"""

import xml.etree.ElementTree as ET
import zlib

CXF_NS = "http://colorexchangeformat.com/CxF3-core"
_Q = "{%s}" % CXF_NS   # Clark-notation namespace prefix for ElementTree

# Measurement conditions exposed by i1Profiler (ISO 13655 illumination modes).
ALLOWED_MEASUREMENTS = ("M0", "M1", "M2")


def read_cxf_tag(icc_bytes: bytes, off: int, size: int) -> str:
    """Return the decompressed CxF3 XML text from an ICC ``CxF `` tag.

    The tag payload is ``ZXML`` (4-byte type) + 4 reserved bytes + a zlib stream.

    :raises ValueError: if the tag is not a ``ZXML`` type.
    """
    tag = icc_bytes[off:off + size]
    if tag[:4] != b"ZXML":
        raise ValueError(f"'CxF ' tag is not ZXML (got {tag[:4]!r})")
    xml = zlib.decompress(tag[8:]).decode("utf-8", "replace")
    # ICC tags are 4-byte aligned: the CxF3 payload can carry trailing NUL
    # padding after </cc:CxF>, which a strict XML parser rejects. Trim it.
    return xml.rstrip("\x00 \t\r\n")


def _location(obj):
    """Return ``(page, row, col)`` from an Object's ``Location`` TagCollection.

    Used as the pairing key between a Target (device RGB) and its Measurement
    (spectrum). Returns ``None`` if the Location is absent/unparsable.
    """
    for tc in obj.findall(f"{_Q}TagCollection"):
        if tc.get("Name") != "Location":
            continue
        vals = {t.get("Name"): t.get("Value") for t in tc.findall(f"{_Q}Tag")}
        try:
            return (int(vals.get("Page", 0)), int(vals.get("Row", 0)),
                    int(vals.get("Column", 0)))
        except (TypeError, ValueError):
            return None
    return None


def parse_cxf(xml_text: str, measurement: str = "M0"):
    """Parse CxF3 XML into paired device values + spectra.

    :param measurement: ``"M0"`` | ``"M1"`` | ``"M2"`` (default M0, no filter).
    :return: ``(patches, wavelengths)`` where ``patches`` is a list of
             ``{"rgb": (r, g, b), "spectrum": [refl, ...]}`` in Target order, and
             ``wavelengths`` is ``[380, 390, ...]``.
    :raises ValueError: unknown measurement, no Targets, no matching spectra, or
                        no Target could be paired with a measurement.
    """
    if measurement not in ALLOWED_MEASUREMENTS:
        raise ValueError(
            f"unknown measurement {measurement!r}; expected {ALLOWED_MEASUREMENTS}"
        )

    root = ET.fromstring(xml_text)
    meas_type = f"{measurement}_Measurement"

    increment = None
    for wr in root.iter(f"{_Q}WavelengthRange"):
        try:
            increment = float(wr.get("Increment"))
        except (TypeError, ValueError):
            pass
    if increment is None:
        increment = 10.0   # i1Profiler default (380-730 nm @ 10 nm)

    targets = []       # [(location, (r, g, b))] in document order
    meas_by_loc = {}   # location -> (start_wl, [reflectances])

    for obj in root.iter(f"{_Q}Object"):
        otype = obj.get("ObjectType", "")
        loc = _location(obj)
        if otype == "Target":
            rgb = obj.find(f"{_Q}DeviceColorValues/{_Q}ColorRGB")
            if rgb is None:
                continue
            try:
                r = float(rgb.findtext(f"{_Q}R"))
                g = float(rgb.findtext(f"{_Q}G"))
                b = float(rgb.findtext(f"{_Q}B"))
            except (TypeError, ValueError):
                continue
            targets.append((loc, (r, g, b)))
        elif otype == meas_type:
            spec = obj.find(f"{_Q}ColorValues/{_Q}ReflectanceSpectrum")
            if spec is None or not (spec.text or "").strip():
                continue
            try:
                start_wl = float(spec.get("StartWL"))
                refl = [float(v) for v in spec.text.split()]
            except (TypeError, ValueError):
                continue
            if loc is not None and refl:
                meas_by_loc[loc] = (start_wl, refl)

    if not targets:
        raise ValueError("CxF: no 'Target' object (device values) found")
    if not meas_by_loc:
        raise ValueError(f"CxF: no '{meas_type}' spectrum found")

    patches = []
    wavelengths = None
    for loc, rgb in targets:
        m = meas_by_loc.get(loc)
        if m is None:
            continue
        start_wl, refl = m
        wl = [int(round(start_wl + i * increment)) for i in range(len(refl))]
        if wavelengths is None:
            wavelengths = wl
        patches.append({"rgb": rgb, "spectrum": refl})

    if not patches:
        raise ValueError(
            "CxF: no Target could be paired with a measurement (Location mismatch)"
        )
    return patches, wavelengths


def cxf_to_cgats17(icc_bytes: bytes, off: int, size: int,
                   measurement: str = "M0",
                   descriptor: str = "freeglaz from CxF") -> str:
    """Extract an ICC ``CxF `` tag's i1Profiler measurements as HP-style CGATS.17.

    The output (``SAMPLE_ID RGB_R RGB_G RGB_B SPECTRAL_<nm>``, RGB 0-255,
    reflectance 0-1) is byte-format-compatible with ``parse_cgats_data`` +
    ``build_cti3``, exactly like a Z9 firmware CGATS.

    :raises ValueError: not a ZXML tag, or unusable CxF3 content.
    """
    xml_text = read_cxf_tag(icc_bytes, off, size)
    patches, wavelengths = parse_cxf(xml_text, measurement=measurement)

    fields = ["SAMPLE_ID", "RGB_R", "RGB_G", "RGB_B"]
    fields += [f"SPECTRAL_{wl}" for wl in wavelengths]

    out = [
        "CGATS.17",
        f'DESCRIPTOR "{descriptor} ({measurement})"',
        'ORIGINATOR "freeglaz (lib/z9_client/cxf.py)"',
        f"NUMBER_OF_FIELDS {len(fields)}",
        f"NUMBER_OF_SETS {len(patches)}",
        "BEGIN_DATA_FORMAT",
        " ".join(fields),
        "END_DATA_FORMAT",
        "BEGIN_DATA",
    ]
    for i, p in enumerate(patches, start=1):
        r, g, b = p["rgb"]
        row = [str(i), f"{r:g}", f"{g:g}", f"{b:g}"]
        row += [f"{v:.6f}" for v in p["spectrum"]]
        out.append(" ".join(row))
    out.append("END_DATA")
    return "\n".join(out) + "\n"
