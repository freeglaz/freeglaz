"""PIWS dimensions are read with their Unit (parsers.dimension_mm).

The unit is not fixed: inches for a plain request on the Z9, millimetres when
the Z9 Pro's web interface asks ``?units=metric``. Read as inches, a 609.6 mm
roll would be 15.5 m wide. Synthetic payloads.
"""
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.z9_client import parsers
from lib.z9_client.client import DeviceOps
from lib.z9_client.jobqueue import JobQueueOps

_JOBS_XML_SAMPLE = (Path(__file__).parent / "fixtures" / "jobs_all_sample.xml").read_text(
    encoding="utf-8")


# ─── parsers.dimension_mm ────────────────────────────────────────────


@pytest.mark.parametrize("value, unit, expected", [
    (24.0, "in", 609.6),
    ("24.000000", "in", 609.6),
    (609.6, "mm", 609.6),
    ("609.6", " MM ", 609.6),
    (61.0, "cm", 610.0),
    (24.0, None, 609.6),     # no unit: inches, the historic assumption
    (24.0, "", 609.6),
])
def test_dimension_mm_honours_the_unit(value, unit, expected):
    assert parsers.dimension_mm(value, unit) == pytest.approx(expected)


@pytest.mark.parametrize("value", [None, "", "n/a"])
def test_dimension_mm_without_a_number(value):
    assert parsers.dimension_mm(value, "mm") is None


def test_dimension_mm_unknown_unit_is_none_and_logged_once(caplog):
    parsers._unknown_units_logged.discard("furlong")
    with caplog.at_level(logging.WARNING, logger="lib.z9_client.parsers"):
        assert parsers.dimension_mm(3.0, "furlong") is None
        assert parsers.dimension_mm(4.0, "furlong") is None
    assert sum("furlong" in r.getMessage() for r in caplog.records) == 1


# ─── DeviceOps.loaded_media_info (MediaSystem.json) ──────────────────


def _media_system(width, remaining):
    """One drawer, one roll loaded (the shape both printers answer)."""
    return json.dumps({"MediaSystem": {"DrawerCollection": {"Drawer": [{
        "Id": 1,
        "InputDeviceCollection": {"Roll": {
            "IdForPrinting": "ROLL", "IdForUI": "Roll",
            "LoadedMediaCollection": {"LoadedMedia": {
                "MediaTypeID": "0123456789ABCDEF0123456789ABCDEF",
                "Width": width, "RemainingLength": remaining,
            }},
        }},
    }]}}})


def _device(payload):
    rest = SimpleNamespace(get=lambda endpoint: payload)
    return DeviceOps(SimpleNamespace(rest=rest))


def test_z9_roll_in_inches():
    info = _device(_media_system({"Unit": "in", "Value": 24.0},
                                 {"Unit": "in", "Value": 0.0})).loaded_media_info()
    assert info["source"] == "ROLL"
    assert info["width_mm"] == pytest.approx(609.6)
    assert info["width_in"] == pytest.approx(24.0)
    assert info["length_mm"] == 0.0


def test_roll_in_millimetres():
    info = _device(_media_system({"Unit": "mm", "Value": 609.6},
                                 {"Unit": "mm", "Value": 1500.0})).loaded_media_info()
    assert info["width_mm"] == pytest.approx(609.6)      # not 15 484 mm
    assert info["width_in"] == pytest.approx(24.0)
    assert info["length_mm"] == pytest.approx(1500.0)


def test_unknown_unit_gives_no_width_rather_than_a_wrong_one():
    info = _device(_media_system({"Unit": "furlong", "Value": 3.0}, None)).loaded_media_info()
    assert info["media_id"]                               # the paper is still reported
    assert info["width_mm"] is None and info["width_in"] is None
    assert info["length_mm"] is None


# ─── JobQueueOps page size ───────────────────────────────────────────


def test_job_page_size_in_millimetres():
    xml = _JOBS_XML_SAMPLE.replace(
        "<Value>5.368056</Value>\n                  <Unit>in</Unit>",
        "<Value>136.35</Value>\n                  <Unit>mm</Unit>", 1)
    assert "<Value>136.35</Value>" in xml
    j0 = JobQueueOps._parse_jobs_xml(xml)["jobs"][0]
    assert j0["page_size_mm"]["width"] == pytest.approx(136.35)
    assert j0["page_size_mm"]["height"] == pytest.approx(10.086111 * 25.4, abs=0.01)
