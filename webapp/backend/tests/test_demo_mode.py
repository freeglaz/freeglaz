"""Offline demo mode: MockZ9Client canned data + status mapping.

The hot-swap endpoint (POST /api/printers/demo) starts real background
subscribers against the mock; that plumbing is validated live, not here. These
tests cover the DATA: the mock serves a coherent loaded roll + inks, and the
real status mapper turns it into a Print-ready Status carrying the demo flag.
"""
from webapp.backend.routes.status import _dashboard_to_status
from webapp.backend.services.mock_z9 import MockZ9Client


def test_mock_client_serves_demo_roll_and_ten_healthy_inks():
    z9 = MockZ9Client()
    dash = z9.device.status()
    assert dash["loaded_paper_id"]
    assert dash["loaded_paper_source"] == "ROLL"
    assert dash["loaded_paper_width_mm"] == 610.0
    assert dash["loaded_paper_length_mm"] is None       # a roll has no fixed length
    assert len(dash["ink_levels"]) == 10
    assert dash["ink_warnings"] == []                    # healthy → no ink alerts
    assert dash["global_status"] == "Ready"


def test_mock_paper_lookup_and_capabilities():
    z9 = MockZ9Client()
    pid = z9.device.status()["loaded_paper_id"]
    paper = z9.paper.get(pid)
    assert paper and paper["name"]
    caps = z9.paper.capabilities(pid)
    assert caps["supports_gloss_enhancer"] is True
    # A paper op that fetches an ICC is unavailable in the demo (graceful).
    from lib.z9_client.exceptions import Z9Error
    import pytest
    with pytest.raises(Z9Error):
        z9.paper.export_icc(ref=pid, output_path="/tmp/x.icc")


def test_raw_shapes_feed_the_subscriber_extractors():
    """The SSE subscriber parses raw device dicts — the mock's shapes must fit."""
    from webapp.backend.services.z9_status_subscriber import (
        _extract_inks, _extract_loaded_paper, _extract_global_status,
    )
    z9 = MockZ9Client()
    inks = _extract_inks(z9.device.ink_system())
    assert len(inks["ink_levels"]) == 10 and inks["ink_warnings"] == []
    assert _extract_global_status(z9.device.device_status()) == "Ready"
    loaded = _extract_loaded_paper(z9.device.loaded_media_info(), z9.paper.get)
    assert loaded["loaded_paper_width_mm"] == 610.0


def test_status_mapping_produces_print_ready_status_with_demo_flag():
    z9 = MockZ9Client()
    status = _dashboard_to_status(z9.device.status(), z9, caps_cache={}, demo=True)
    assert status.demo is True
    assert status.ready is True
    assert status.loaded_paper is not None
    assert status.loaded_paper.media_source == "ROLL"
    assert status.loaded_paper.roll_width_mm == 610.0
    assert status.loaded_paper.gloss_enhancer_supported is True
    assert len(status.inks) == 10
