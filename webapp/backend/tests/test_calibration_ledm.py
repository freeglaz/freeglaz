"""Tests — LEDM parser /Calibration/Calibration.xml."""
from lib.z9_client.calibration_ledm import parse_color_linearization


_SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<cb:Status xmlns:cb="http://www.hp.com/schemas/imaging/con/ledm/calibration/2013/02/25">
  <cb:LightSensorPhysical>
    <cb:CalibrationStatus>completed</cb:CalibrationStatus>
  </cb:LightSensorPhysical>
  <cb:PrintheadAlignment>
    <cb:CalibrationStatus>pending</cb:CalibrationStatus>
  </cb:PrintheadAlignment>
  <cb:Media>
    <cb:MediaCalibration>
      <cb:CalibrationStatus>completed</cb:CalibrationStatus>
      <cb:Type>colorLinearization</cb:Type>
      <cb:MediaKey>9E489F02AE027F9DD93191D872728C1D</cb:MediaKey>
      <cb:ImageType>text</cb:ImageType>
      <cb:TimeStamp>2026-05-25T10:57:18Z</cb:TimeStamp>
    </cb:MediaCalibration>
    <cb:MediaCalibration>
      <cb:CalibrationStatus>pending</cb:CalibrationStatus>
      <cb:Type>colorLinearization</cb:Type>
      <cb:MediaKey>5D1C573685FB47A8F2A2D553B398A1C6</cb:MediaKey>
    </cb:MediaCalibration>
    <cb:MediaCalibration>
      <cb:CalibrationStatus>obsoleted</cb:CalibrationStatus>
      <cb:Type>colorLinearization</cb:Type>
      <cb:MediaKey>2C786AC69B053030696739D5EAC34E34</cb:MediaKey>
      <cb:TimeStamp>2020-09-26T08:00:00Z</cb:TimeStamp>
    </cb:MediaCalibration>
    <cb:MediaCalibration>
      <cb:CalibrationStatus>pending</cb:CalibrationStatus>
      <cb:Type>paperAdvance</cb:Type>
      <cb:MediaKey>9E489F02AE027F9DD93191D872728C1D</cb:MediaKey>
    </cb:MediaCalibration>
  </cb:Media>
</cb:Status>
"""


def test_parse_returns_color_linearization_entries():
    """3 colorLinearization entries parsed, paperAdvance ignored."""
    out = parse_color_linearization(_SAMPLE_XML)
    assert set(out.keys()) == {
        "9E489F02AE027F9DD93191D872728C1D",
        "5D1C573685FB47A8F2A2D553B398A1C6",
        "2C786AC69B053030696739D5EAC34E34",
    }


def test_parse_completed_with_timestamp():
    out = parse_color_linearization(_SAMPLE_XML)
    canson = out["9E489F02AE027F9DD93191D872728C1D"]
    assert canson["status"] == "completed"
    assert canson["timestamp"] == "2026-05-25T10:57:18Z"


def test_parse_pending_without_timestamp():
    out = parse_color_linearization(_SAMPLE_XML)
    duplicate = out["5D1C573685FB47A8F2A2D553B398A1C6"]
    assert duplicate["status"] == "pending"
    assert duplicate["timestamp"] is None


def test_parse_obsoleted_keeps_timestamp():
    out = parse_color_linearization(_SAMPLE_XML)
    hahn = out["2C786AC69B053030696739D5EAC34E34"]
    assert hahn["status"] == "obsoleted"
    assert hahn["timestamp"] == "2020-09-26T08:00:00Z"


def test_parse_ignores_paper_advance_type():
    """paperAdvance entries must be ignored (distinct mechanical
    calibration, out of scope for V1)."""
    out = parse_color_linearization(_SAMPLE_XML)
    # Canson has 1 colorLinearization entry + 1 paperAdvance in the XML
    # → only colorLinearization is surfaced
    assert out["9E489F02AE027F9DD93191D872728C1D"]["status"] == "completed"
    # paperAdvance never appears (no duplicate, no "pending" here)


def test_parse_handles_empty_media_block():
    xml = '''<?xml version="1.0"?>
    <cb:Status xmlns:cb="http://www.hp.com/schemas/imaging/con/ledm/calibration/2013/02/25">
      <cb:LightSensorPhysical>
        <cb:CalibrationStatus>completed</cb:CalibrationStatus>
      </cb:LightSensorPhysical>
    </cb:Status>'''
    assert parse_color_linearization(xml) == {}


def test_parse_handles_malformed_xml():
    """broken XML → empty dict, no exception."""
    assert parse_color_linearization("<not valid xml") == {}
    assert parse_color_linearization("") == {}
