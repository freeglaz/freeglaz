"""Z9 Pro second roll (ROLL2): reported, never printed on.

Neither the PJL token nor the paper/profile chain has been observed on ROLL2,
and how the Pro handles two loaded rolls is unknown. freeglaz prints on the
first roll (ROLL) — chosen explicitly, not because it comes first — and
refuses a paper sitting on ROLL2, with no fallback to another source.
Synthetic MediaSystem payloads shaped like the Z9 and Z9 Pro answers.
"""
import json
from types import SimpleNamespace

import pytest

from lib.z9_client.client import DeviceOps
from webapp.backend.models import LoadedPaper, PrintParams
from webapp.backend.routes.status import build_loaded_paper
from webapp.backend.services import print_geometry as pg

P1 = "11111111111111111111111111111111"
P2 = "22222222222222222222222222222222"


def _roll(id_for_printing, id_for_ui, media_id=None, width_in=24.0):
    loaded = None
    if media_id:
        loaded = {"LoadedMedia": {"MediaTypeID": media_id,
                                  "Width": {"Unit": "in", "Value": width_in},
                                  "RemainingLength": {"Unit": "in", "Value": 0.0}}}
    return {"IdForPrinting": id_for_printing, "IdForUI": id_for_ui,
            "LoadedMediaCollection": loaded}


def _pro(roll1=None, roll2=None, *, drawer2_first=False):
    """Two drawers, one Roll source each (the Z9 Pro layout)."""
    d1 = {"Id": 1, "InputDeviceCollection": {"Roll": _roll("ROLL", "Roll", roll1)}}
    d2 = {"Id": 2, "InputDeviceCollection": {"Roll": _roll("ROLL2", "Roll2", roll2, 44.0)}}
    drawers = [d2, d1] if drawer2_first else [d1, d2]
    return {"MediaSystem": {"DrawerCollection": {"Drawer": drawers}}}


def _info(payload):
    rest = SimpleNamespace(get=lambda endpoint: json.dumps(payload))
    return DeviceOps(SimpleNamespace(rest=rest)).loaded_media_info()


# ─── which source freeglaz works with ────────────────────────────────


def test_roll1_only():
    info = _info(_pro(roll1=P1))
    assert info["source"] == "ROLL" and info["media_id"] == P1
    assert info["other_loaded_sources"] == []


def test_roll2_only_is_reported_not_replaced():
    info = _info(_pro(roll2=P2))
    assert info["source"] == "ROLL2" and info["media_id"] == P2
    assert info["other_loaded_sources"] == []


@pytest.mark.parametrize("drawer2_first", [False, True])
def test_both_rolls_roll1_is_chosen_explicitly(drawer2_first):
    # The printer's order must not decide: roll 1 is the printable one.
    info = _info(_pro(roll1=P1, roll2=P2, drawer2_first=drawer2_first))
    assert info["source"] == "ROLL" and info["media_id"] == P1
    assert info["width_mm"] == pytest.approx(609.6)       # roll 1's own width
    assert info["other_loaded_sources"] == ["ROLL2"]


def test_nothing_loaded():
    assert _info(_pro()) is None


def test_z9_roll_and_manual_feed_keep_the_printer_order():
    # Unchanged on the Z9: every source is printable → the first loaded one.
    payload = {"MediaSystem": {"DrawerCollection": {"Drawer": [{
        "Id": 1, "InputDeviceCollection": {
            "Roll": _roll("ROLL", "Roll", P1),
            "Sheet": _roll("MANUAL_FEED", "ManualSheet", P2, 8.27),
        }}]}}}
    info = _info(payload)
    assert info["source"] == "ROLL"
    assert info["other_loaded_sources"] == ["MANUAL_FEED"]


# ─── status: the paper on ROLL2 is not presented as a sheet ──────────


def _z9_for_status():
    paper = SimpleNamespace(get=lambda _id: {"name": "Paper", "category_id": "PHOTO",
                                             "is_factory": False},
                            capabilities=lambda _id: {})
    return SimpleNamespace(paper=paper)


def test_status_roll2_has_no_dimensions():
    lp = build_loaded_paper(_z9_for_status(), {
        "loaded_paper_id": P2, "loaded_paper_source": "ROLL2",
        "loaded_paper_width_mm": 1117.6, "loaded_paper_length_mm": 0.0,
    }, {})
    assert lp.media_source == "ROLL2"
    assert lp.sheet_width_mm is None and lp.sheet_height_mm is None
    assert lp.roll_width_mm is None


def test_status_reports_the_other_loaded_sources():
    lp = build_loaded_paper(_z9_for_status(), {
        "loaded_paper_id": P1, "loaded_paper_source": "ROLL",
        "loaded_paper_width_mm": 609.6, "loaded_paper_length_mm": 0.0,
        "loaded_paper_other_sources": ["ROLL2"],
    }, {})
    assert lp.roll_width_mm == pytest.approx(609.6)
    assert lp.other_loaded_sources == ["ROLL2"]


# ─── geometry: no print path can go on with ROLL2 ────────────────────


def _loaded(source, others=()):
    return LoadedPaper(id=P1, name="Paper", category="PHOTO", is_factory=False,
                       media_source=source, roll_width_mm=609.6 if source == "ROLL" else None,
                       other_loaded_sources=list(others))


@pytest.mark.parametrize("override", [None, "AUTO", "ROLL", "MANUALFEED"])
def test_compute_geometry_refuses_roll2_whatever_the_override(override):
    params = PrintParams(media_source_override=override)
    with pytest.raises(pg.UnsupportedMediaSource, match="ROLL2"):
        pg.compute_geometry(_loaded("ROLL2"), params, 200.0, 300.0)


@pytest.mark.parametrize("source", ["?", "", "TRAY"])
def test_unknown_source_is_refused_not_mapped_to_manual_feed(source):
    with pytest.raises(pg.UnsupportedMediaSource):
        pg.compute_geometry(_loaded(source), PrintParams(), 200.0, 300.0)


def test_roll1_with_roll2_loaded_prints_on_roll1_with_a_notice():
    lp = _loaded("ROLL", others=["ROLL2"])
    g = pg.compute_geometry(lp, PrintParams(), 200.0, 300.0)
    assert g.media_source == "ROLL"
    assert "ROLL2" in pg.other_sources_warning(lp)


def test_no_notice_for_printable_other_sources():
    # Z9: paper on the roll AND the manual feed — nothing new to say.
    assert pg.other_sources_warning(_loaded("ROLL", others=["MANUAL_FEED"])) is None
