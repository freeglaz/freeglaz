# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the CxF3 reader (lib.z9_client.cxf).

Synthetic CxF3 — no large binary fixture. Verifies: zlib/ZXML unwrap, Target<->
Measurement pairing by Location (objects deliberately out of order), the emitted
CGATS.17, and that it flows into the existing parse_cgats_data -> build_cti3.
"""
import zlib

import pytest

from lib.z9_client import cxf
from lib.z9_client.profiling import parse_cgats_data, build_cti3

# Measurements listed BEFORE targets on purpose -> pairing must use Location,
# not document order. T1@(col0) = RGB(102,231,23); T2@(col1) = RGB(255,0,128).
SYNTH_CXF = """<?xml version="1.0" encoding="UTF-8"?>
<cc:CxF xmlns:cc="http://colorexchangeformat.com/CxF3-core">
 <cc:Resources>
  <cc:ObjectCollection>
   <cc:Object ObjectType="M0_Measurement" Name="M0_1" Id="c1">
    <cc:ColorValues>
     <cc:ReflectanceSpectrum StartWL="380" ColorSpecification="M0">0.10 0.20 0.30 0.40</cc:ReflectanceSpectrum>
    </cc:ColorValues>
    <cc:TagCollection Name="Location">
     <cc:Tag Name="Column" Value="0"/><cc:Tag Name="Page" Value="1"/><cc:Tag Name="Row" Value="0"/>
    </cc:TagCollection>
   </cc:Object>
   <cc:Object ObjectType="M0_Measurement" Name="M0_2" Id="c2">
    <cc:ColorValues>
     <cc:ReflectanceSpectrum StartWL="380" ColorSpecification="M0">0.50 0.60 0.70 0.80</cc:ReflectanceSpectrum>
    </cc:ColorValues>
    <cc:TagCollection Name="Location">
     <cc:Tag Name="Column" Value="1"/><cc:Tag Name="Page" Value="1"/><cc:Tag Name="Row" Value="0"/>
    </cc:TagCollection>
   </cc:Object>
   <cc:Object ObjectType="Target" Name="T1" Id="c3">
    <cc:DeviceColorValues><cc:ColorRGB><cc:R>102</cc:R><cc:G>231</cc:G><cc:B>23</cc:B></cc:ColorRGB></cc:DeviceColorValues>
    <cc:TagCollection Name="Location">
     <cc:Tag Name="Column" Value="0"/><cc:Tag Name="Page" Value="1"/><cc:Tag Name="Row" Value="0"/>
    </cc:TagCollection>
   </cc:Object>
   <cc:Object ObjectType="Target" Name="T2" Id="c4">
    <cc:DeviceColorValues><cc:ColorRGB><cc:R>255</cc:R><cc:G>0</cc:G><cc:B>128</cc:B></cc:ColorRGB></cc:DeviceColorValues>
    <cc:TagCollection Name="Location">
     <cc:Tag Name="Column" Value="1"/><cc:Tag Name="Page" Value="1"/><cc:Tag Name="Row" Value="0"/>
    </cc:TagCollection>
   </cc:Object>
  </cc:ObjectCollection>
  <cc:MeasurementSpec><cc:WavelengthRange StartWL="380" Increment="10"/></cc:MeasurementSpec>
 </cc:Resources>
</cc:CxF>
"""


def _zxml_tag(xml_text: str) -> bytes:
    """Wrap CxF3 XML as an ICC 'CxF ' tag payload (ZXML + 4 reserved + zlib)."""
    return b"ZXML" + b"\x00\x00\x00\x00" + zlib.compress(xml_text.encode("utf-8"))


# ── parse_cxf ───────────────────────────────────────────────────────────

def test_parse_pairs_by_location_not_order():
    patches, wl = cxf.parse_cxf(SYNTH_CXF, measurement="M0")
    assert wl == [380, 390, 400, 410]
    assert len(patches) == 2
    # Target document order preserved; each paired with its own spectrum by Location
    assert patches[0]["rgb"] == (102.0, 231.0, 23.0)
    assert patches[0]["spectrum"] == [0.10, 0.20, 0.30, 0.40]
    assert patches[1]["rgb"] == (255.0, 0.0, 128.0)
    assert patches[1]["spectrum"] == [0.50, 0.60, 0.70, 0.80]


def test_parse_unknown_measurement_raises():
    with pytest.raises(ValueError):
        cxf.parse_cxf(SYNTH_CXF, measurement="M9")


def test_parse_missing_condition_raises():
    # No M2_Measurement objects present
    with pytest.raises(ValueError):
        cxf.parse_cxf(SYNTH_CXF, measurement="M2")


# ── read_cxf_tag ────────────────────────────────────────────────────────

def test_read_tag_rejects_non_zxml():
    with pytest.raises(ValueError):
        cxf.read_cxf_tag(b"NOPE" + b"\x00" * 8, 0, 12)


def test_read_tag_roundtrip():
    tag = _zxml_tag(SYNTH_CXF)
    assert cxf.read_cxf_tag(tag, 0, len(tag)).startswith("<?xml")


# ── cxf_to_cgats17 + downstream (parse_cgats_data -> build_cti3) ─────────

def test_cgats_and_build_cti3():
    tag = _zxml_tag(SYNTH_CXF)
    cgats = cxf.cxf_to_cgats17(tag, 0, len(tag), measurement="M0")

    # header + fields
    assert cgats.startswith("CGATS.17")
    assert "NUMBER_OF_SETS 2" in cgats
    assert "SPECTRAL_380" in cgats and "SPECTRAL_410" in cgats

    parsed = parse_cgats_data(cgats)
    assert parsed["format"][:4] == ["SAMPLE_ID", "RGB_R", "RGB_G", "RGB_B"]
    assert len(parsed["data"]) == 2

    cti3 = build_cti3(parsed)
    assert cti3.startswith("CTI3")
    assert "SPEC_380" in cti3
    # RGB rescaled 0-255 -> 0-100 by build_cti3 (102 -> 40.0)
    assert "40.0000" in cti3
