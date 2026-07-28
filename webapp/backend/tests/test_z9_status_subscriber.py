"""Tests of Z9StatusPollSubscriber + format-consistency sentinel."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from lib.z9_client.exceptions import Z9ConnectionError
from webapp.backend.services.z9_status_subscriber import (
    Z9StatusPollSubscriber,
    _extract_activity,
    _extract_global_status,
    _extract_inks,
    _extract_loaded_paper,
)


# ─── File fixture (live Z9 capture, saved for reproducibility) ──

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "device_status_reference.json"


@pytest.fixture
def reference_fixture():
    return json.loads(_FIXTURE_PATH.read_text())


# ─── Fake Z9 helpers (no real Z9 needed) ──────────────────────

def _make_fake_z9(*, events=None, device_status=None, ink_system=None,
                  media_info=None, device_status_raw=None, paper_dict=None,
                  events_raises=False):
    """Build a Z9Client-like object exposing the methods used by
    Z9StatusPollSubscriber. All responses are controllable.

    If events_raises=True, z9.events.event_table() raises Z9ConnectionError.
    """
    events_obj = SimpleNamespace()
    if events_raises:
        events_obj.event_table = MagicMock(side_effect=Z9ConnectionError("timeout"))
    else:
        events_obj.event_table = MagicMock(return_value=events or [])

    device_obj = SimpleNamespace()
    device_obj.status            = MagicMock(return_value=device_status or {})
    device_obj.ink_system        = MagicMock(return_value=ink_system or {})
    device_obj.loaded_media_info = MagicMock(return_value=media_info)
    device_obj.device_status     = MagicMock(return_value=device_status_raw or {})

    paper_obj = SimpleNamespace()
    paper_obj.get = MagicMock(return_value=paper_dict)

    return SimpleNamespace(events=events_obj, device=device_obj, paper=paper_obj)


# ═══════════════════════════════════════════════════════════════════════
# Test 1 — diff aging_stamps
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_diff_events_detects_aging_stamp_change():
    sub = Z9StatusPollSubscriber(_make_fake_z9())
    sub._last_aging_stamps = {"ConsumableEvent": "226-171", "PowerUpEvent": "226-173"}

    # ConsumableEvent changes, PowerUpEvent stable
    events = [
        {"category": "ConsumableEvent", "aging_stamp": "226-200",  # changed
         "resource_uri": "/...", "resource_type": "..."},
        {"category": "PowerUpEvent",    "aging_stamp": "226-173",  # stable
         "resource_uri": "/...", "resource_type": "..."},
        {"category": "AlertTableChanged", "aging_stamp": "226-999",  # new
         "resource_uri": "/...", "resource_type": "..."},
    ]
    changed = sub._diff_events(events)
    assert sorted(changed) == ["AlertTableChanged", "ConsumableEvent"]
    # _last_aging_stamps updated in place
    assert sub._last_aging_stamps["ConsumableEvent"] == "226-200"
    assert sub._last_aging_stamps["AlertTableChanged"] == "226-999"


# ═══════════════════════════════════════════════════════════════════════
# Test 2 — targeted fetch by category
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_refetch_changed_resources_for_alerts():
    """AlertTableChanged → MediaSystem + DeviceStatus only (not InkSystem,
    not full device.status())."""
    z9 = _make_fake_z9(
        media_info={
            "media_id": "P1", "source": "MANUAL_FEED", "source_label": "ManualSheet",
            "width_mm": 210.0, "length_mm": 293.0,
            "width_in": 8.27, "length_in": 11.54, "drawer_id": 1,
        },
        device_status_raw={"StatusOverview": {"MostRelevantStatus": "WithAlerts"}},
        paper_dict={"name": "FakePaper"},
    )
    sub = Z9StatusPollSubscriber(z9)
    await sub._refetch_changed_resources(["AlertTableChanged"])

    # The correct targeted methods were called
    z9.device.loaded_media_info.assert_called_once()
    z9.device.device_status.assert_called_once()
    # And ESPECIALLY not the full status nor ink_system
    z9.device.status.assert_not_called()
    z9.device.ink_system.assert_not_called()

    # Snapshot updated with the extracted sub-portions
    snap = sub._current_snapshot
    assert snap["loaded_paper_id"] == "P1"
    assert snap["loaded_paper_name"] == "FakePaper"
    assert snap["loaded_paper_source"] == "MANUAL_FEED"
    assert snap["global_status"] == "WithAlerts"


@pytest.mark.asyncio
async def test_refetch_changed_resources_updates_media_on_any_non_ink_event():
    """Bug (22/05/2026): the Z9 firmware fires various events on
    paper change (not only AlertTableChanged — depending on firmware
    we have also observed MediaSubunitStatusChanged or DeviceCapabilitiesChanged).
    If the subscriber whitelist hardcoded AlertTableChanged, the snapshot
    stayed frozen on the old paper — a silent-fail pattern like B1.

    The current contract: any non-ConsumableEvent event must trigger a
    refetch of loaded_media_info() + global_status. Light cost,
    guaranteed consistency."""
    # Pre-condition the snapshot with an old paper
    z9 = _make_fake_z9(
        media_info={
            "media_id": "NEW_PAPER_ID", "source": "MANUAL_FEED",
            "source_label": "ManualSheet", "width_mm": 210.0, "length_mm": 297.0,
            "width_in": 8.27, "length_in": 11.69, "drawer_id": 1,
        },
        device_status_raw={"StatusOverview": {"MostRelevantStatus": "Ready"}},
        paper_dict={"name": "Canson Photolustre RC 2026"},
    )
    sub = Z9StatusPollSubscriber(z9)
    sub._current_snapshot.update({
        "loaded_paper_id": "OLD_PAPER_ID",
        "loaded_paper_name": "HP Premium Instant-dry Gloss Photo Paper",
        "loaded_paper_source": "MANUAL_FEED",
        "loaded_paper_source_label": "ManualSheet",
        "loaded_paper_width_mm": 210.0,
        "loaded_paper_length_mm": 297.0,
        "global_status": "Ready",
    })

    # Simulate an event that is NOT AlertTableChanged (alternative category
    # the historical whitelist missed).
    await sub._refetch_changed_resources(["MediaSubunitStatusChanged"])

    # The snapshot must reflect the NEW paper
    assert sub._current_snapshot["loaded_paper_id"] == "NEW_PAPER_ID"
    assert sub._current_snapshot["loaded_paper_name"] == "Canson Photolustre RC 2026"
    z9.device.loaded_media_info.assert_called_once()
    z9.device.device_status.assert_called_once()
    # And ESPECIALLY not ink_system (the test keeps the ink/media separation)
    z9.device.ink_system.assert_not_called()


@pytest.mark.asyncio
async def test_refetch_consumable_only_does_not_refetch_media():
    """Regression of the tradeoff: ConsumableEvent ALONE must NOT trigger
    a media refetch. Preserves the light ink optimization (an ink event
    costs only one GET /InkSystem.json, not an extra GET MediaSystem)."""
    z9 = _make_fake_z9(ink_system={
        "InkSlotGroupCollection": {
            "InkSlotGroup": [{
                "Color": "magenta",
                "InkSlotGroupInfo": {"InkSupplyGroupInfo": {"LevelPercentage": 33.0, "State": "OK"}},
            }]
        }
    })
    sub = Z9StatusPollSubscriber(z9)
    await sub._refetch_changed_resources(["ConsumableEvent"])

    z9.device.ink_system.assert_called_once()
    # No media/status fetch when ONLY ConsumableEvent changes
    z9.device.loaded_media_info.assert_not_called()
    z9.device.device_status.assert_not_called()


@pytest.mark.asyncio
async def test_refetch_changed_resources_for_consumable():
    """ConsumableEvent → InkSystem only."""
    z9 = _make_fake_z9(ink_system={
        "InkSlotGroupCollection": {
            "InkSlotGroup": [{
                "Color": "yellow",
                "InkSlotGroupInfo": {"InkSupplyGroupInfo": {"LevelPercentage": 72.0, "State": "OK"}},
            }]
        }
    })
    sub = Z9StatusPollSubscriber(z9)
    await sub._refetch_changed_resources(["ConsumableEvent"])

    z9.device.ink_system.assert_called_once()
    z9.device.loaded_media_info.assert_not_called()
    z9.device.device_status.assert_not_called()
    z9.device.status.assert_not_called()

    assert sub._current_snapshot["ink_levels"] == {"yellow": 72.0}


# ═══════════════════════════════════════════════════════════════════════
# Test 3 — push snapshot on subscribe
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_subscriber_pushes_full_snapshot_on_subscribe():
    z9 = _make_fake_z9(
        device_status={"identification": {"ModelName": "Z9"}, "global_status": "Ready",
                       "ink_levels": {}, "ink_warnings": []},
        events=[{"category": "PowerUpEvent", "aging_stamp": "1-1",
                 "resource_uri": "/", "resource_type": ""}],
    )
    sub = Z9StatusPollSubscriber(z9)
    await sub._refetch_full_snapshot()

    received = []
    def cb(event_type, data):
        received.append((event_type, data))

    sub.subscribe(cb)

    assert len(received) == 1
    event_type, data = received[0]
    assert event_type == "status_full"
    assert data["global_status"] == "Ready"
    assert data["identification"]["ModelName"] == "Z9"


# ═══════════════════════════════════════════════════════════════════════
# Test 4 — exponential backoff on Z9 unreachable
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_z9_unreachable_triggers_backoff_and_recovery():
    """At first z9.events.event_table raises Z9ConnectionError → backoff
    doubles × 3. Then we switch to success → backoff resets to poll_interval."""
    z9 = _make_fake_z9(events_raises=True)
    sub = Z9StatusPollSubscriber(z9, poll_interval=0.01)
    sub._stop_event = asyncio.Event()

    received: list[tuple[str, dict]] = []
    sub.subscribe(lambda e, d: received.append((e, d)))

    # Iterate 3 cycles manually (without the infinite loop) to observe
    # the backoff sequence.
    observed_backoffs = []

    backoff = sub._poll_interval
    for _ in range(3):
        try:
            events = await asyncio.to_thread(z9.events.event_table)
            sub._diff_events(events)  # will not be reached
            backoff = sub._poll_interval
        except Z9ConnectionError as e:
            sub._broadcast("z9_state", {"state": "error", "reason": str(e)})
            backoff = min(backoff * 2, sub.BACKOFF_MAX)
        observed_backoffs.append(backoff)

    # 0.01 → 0.02 → 0.04 → 0.08
    assert observed_backoffs[0] == pytest.approx(0.02)
    assert observed_backoffs[1] == pytest.approx(0.04)
    assert observed_backoffs[2] == pytest.approx(0.08)

    # ≥1 z9_state error event broadcast (4 including the initial subscribe
    # status_full + 3 errors)
    error_events = [e for e in received if e[0] == "z9_state"]
    assert len(error_events) == 3
    assert all(e[1]["state"] == "error" for e in error_events)

    # Switch to success → reset
    z9.events.event_table.side_effect = None
    z9.events.event_table.return_value = []
    # Recovery cycle
    try:
        events = await asyncio.to_thread(z9.events.event_table)
        sub._diff_events(events)
        backoff = sub._poll_interval
    except Z9ConnectionError:
        backoff = min(backoff * 2, sub.BACKOFF_MAX)
    assert backoff == sub._poll_interval  # reset confirmed


# ═══════════════════════════════════════════════════════════════════════
# Test 5 — SENTINEL: local helpers produce the same format as
# device.status() for the corresponding sub-portions. If device.status()
# evolves in the lib, this test breaks and forces updating the helpers.
# ═══════════════════════════════════════════════════════════════════════

def test_partial_fetch_matches_full_fetch_format(reference_fixture):
    """Sentinel: verifies that the local helpers produce a format
    strictly identical to what device.status() returns for the
    corresponding sub-portions. The fixture is a live Z9 capture
    (192.168.1.50) saved for reproducibility."""
    full_status = reference_fixture["full_status"]

    # ─── _extract_inks ────────────────────────────────────────────
    raw_ink = reference_fixture["raw_ink_system"]
    extracted_inks = _extract_inks(raw_ink)

    assert extracted_inks["ink_levels"] == full_status["ink_levels"], (
        "_extract_inks().ink_levels doit matcher device.status()['ink_levels'] "
        "à l'identique — sinon le snapshot SSE oscille selon la source"
    )
    assert extracted_inks["ink_warnings"] == full_status["ink_warnings"], (
        "_extract_inks().ink_warnings doit matcher device.status()['ink_warnings']"
    )

    # ─── _extract_loaded_paper ────────────────────────────────────
    raw_media = reference_fixture["raw_loaded_media_info"]
    expected_paper_name = full_status["loaded_paper_name"]
    paper_lookup = MagicMock(return_value={"name": expected_paper_name})
    extracted_paper = _extract_loaded_paper(raw_media, paper_lookup)
    expected_paper_sub = {
        k: v for k, v in full_status.items() if k.startswith("loaded_paper_")
    }
    assert extracted_paper == expected_paper_sub, (
        "_extract_loaded_paper doit produire les 6 clés loaded_paper_* "
        "au format strictement identique à device.status()"
    )

    # ─── _extract_global_status ───────────────────────────────────
    raw_device = reference_fixture["raw_device_status"]
    assert _extract_global_status(raw_device) == full_status["global_status"]


def test_extract_loaded_paper_handles_no_paper():
    """media_info=None (no paper loaded) → 6 keys set to None."""
    out = _extract_loaded_paper(None, lambda _: None)
    assert all(v is None for v in out.values())
    assert set(out.keys()) == {
        "loaded_paper_id", "loaded_paper_name",
        "loaded_paper_source", "loaded_paper_source_label",
        "loaded_paper_width_mm", "loaded_paper_length_mm",
    }


# ═══════════════════════════════════════════════════════════════════════
# Test 6 — Explicit polling of loaded_media_info (B5 follow-up, 22/05/2026)
# ═══════════════════════════════════════════════════════════════════════
#
# Capturing EventTable.xml over 3 min of physical paper changes showed
# that no LEDM event changes its aging_stamp. The previous fix
# (b82bea9, refetch media on any non-ink event) is therefore ineffective
# for this scenario — an explicit polling independent of the
# event_table diff is needed.


@pytest.mark.asyncio
async def test_subscriber_detects_paper_change_via_explicit_polling():
    """The snapshot must reflect a new paper after a physical change,
    even if the Z9 firmware fires no corresponding event.
    """
    paper_metadata = {
        "A": {"name": "HP Premium Instant-dry Gloss", "category_id": "PHOTO", "is_factory": True},
        "B": {"name": "Canson Photolustre RC 2026",   "category_id": "CUSTOM", "is_factory": False},
    }
    media_A = {
        "media_id": "A", "source": "MANUAL_FEED", "source_label": "ManualSheet",
        "width_mm": 210.0, "length_mm": 297.0,
        "width_in": 8.27, "length_in": 11.69, "drawer_id": 1,
    }
    media_B = {
        "media_id": "B", "source": "MANUAL_FEED", "source_label": "ManualSheet",
        "width_mm": 215.0, "length_mm": 305.0,
        "width_in": 8.46, "length_in": 12.0, "drawer_id": 1,
    }

    z9 = _make_fake_z9(
        media_info=media_A,
        device_status_raw={"StatusOverview": {"MostRelevantStatus": "Ready"}},
    )
    z9.paper.get = MagicMock(side_effect=lambda pid: paper_metadata.get(pid))

    sub = Z9StatusPollSubscriber(z9, poll_interval=0.01)
    sub.PAPER_POLL_INTERVAL = 0  # disable the pause between polls for the test
    # Initialize the snapshot with paper A (simulates _refetch_full_snapshot
    # which already fetched device.status() at start)
    sub._current_snapshot.update(_extract_loaded_paper(media_A, z9.paper.get))

    # First poll: no change (same paper as at snapshot init) →
    # the helper returns False, snapshot unchanged.
    changed_1 = await sub._poll_paper_if_due()
    assert changed_1 is False
    assert sub._current_snapshot["loaded_paper_id"] == "A"

    # user physically changes the paper on the Z9.
    z9.device.loaded_media_info.return_value = media_B

    # Second poll: change detected, snapshot updated.
    changed_2 = await sub._poll_paper_if_due()
    assert changed_2 is True
    assert sub._current_snapshot["loaded_paper_id"] == "B"
    assert sub._current_snapshot["loaded_paper_name"] == "Canson Photolustre RC 2026"
    # global_status also refetched for consistency (Ready/WithAlerts/etc.)
    assert sub._current_snapshot["global_status"] == "Ready"


# ═══════════════════════════════════════════════════════════════════════
# Tests 7-9 — Explicit polling of activity
# ═══════════════════════════════════════════════════════════════════════
#
# The Z9 firmware does not report via EventTable the internal transitions
# of a job (Processing → Drying → TerminatingPrint → NoActivity). Explicit
# polling is mandatory — symmetric to the paper polling (B5).


def test_extract_activity_default_to_noactivity_when_field_missing():
    """device_status() raw empty → activity_name='NoActivity' (never None)."""
    out = _extract_activity({})
    assert out["activity_name"] == "NoActivity"
    assert out["activity_progress_pct"] is None


def test_extract_activity_reads_name_and_optional_progress():
    raw = {
        "ActivitiesOverview": {
            "MostRelevantActivity": {
                "Name": "Drying",
                "PercentComplete": "62",  # str → float
            }
        }
    }
    out = _extract_activity(raw)
    assert out["activity_name"] == "Drying"
    assert out["activity_progress_pct"] == 62.0


@pytest.mark.asyncio
async def test_activity_polled_when_active_within_interval():
    """activity != NoActivity → poll device_status() on each tick (once the interval has passed)."""
    z9 = _make_fake_z9(device_status_raw={
        "ActivitiesOverview": {"MostRelevantActivity": {"Name": "Drying"}},
    })
    sub = Z9StatusPollSubscriber(z9, poll_interval=0.01)
    sub.ACTIVITY_POLL_INTERVAL_ACTIVE = 0  # disable the pause
    sub._current_snapshot["activity_name"] = "Processing"

    changed = await sub._poll_activity_if_due()
    assert changed is True
    assert sub._current_snapshot["activity_name"] == "Drying"
    z9.device.device_status.assert_called_once()


@pytest.mark.asyncio
async def test_subscriber_polls_activity_even_when_idle():
    """Bug B11 (22/05/2026 evening): activity polling runs **always**,
    just at a variable frequency (IDLE 10s / ACTIVE 3s). The previous
    fix conditioned it on activity != NoActivity → chicken-and-egg:
    without a poll we never left NoActivity → UI never in E_PRINTING.

    This test encodes the regression: we must poll even when idle as soon as
    the IDLE interval has elapsed, without needing force=True."""
    z9 = _make_fake_z9(device_status_raw={
        "ActivitiesOverview": {"MostRelevantActivity": {"Name": "NoActivity"}},
    })
    sub = Z9StatusPollSubscriber(z9, poll_interval=0.01)
    sub.ACTIVITY_POLL_INTERVAL_IDLE = 0  # disable the idle pause
    sub._current_snapshot["activity_name"] = "NoActivity"

    # force=False and snapshot=NoActivity → BEFORE B11 fix: silent skip.
    # AFTER fix: poll executed (the IDLE interval has elapsed).
    await sub._poll_activity_if_due(force=False)
    z9.device.device_status.assert_called_once()


@pytest.mark.asyncio
async def test_subscriber_detects_idle_to_processing_transition():
    """Bug B11: the NoActivity → Processing transition at the start of a
    print must be detected by the explicit polling, without depending
    on a possible JobEvent in EventTable (which does not fire
    reliably on this Z9, cf. pattern B5).

    Snapshot initially NoActivity. The firmware moves to PreparingToPrint
    following the PRN send. On the next poll, the snapshot must reflect
    the transition and the helper return True (status_diff broadcast
    signal on the _poll_loop caller side)."""
    z9 = _make_fake_z9(device_status_raw={
        "ActivitiesOverview": {"MostRelevantActivity": {"Name": "PreparingToPrint"}},
    })
    sub = Z9StatusPollSubscriber(z9, poll_interval=0.01)
    sub.ACTIVITY_POLL_INTERVAL_IDLE = 0  # immediate poll
    sub._current_snapshot["activity_name"] = "NoActivity"
    sub._current_snapshot["activity_progress_pct"] = None

    changed = await sub._poll_activity_if_due(force=False)
    assert changed is True, "transition idle → active doit signaler un broadcast"
    assert sub._current_snapshot["activity_name"] == "PreparingToPrint"


@pytest.mark.asyncio
async def test_activity_force_poll_catches_transition_from_idle():
    """JobEvent in the diff → force=True → immediate fetch even if
    the interval has not elapsed. Still useful as an opportunistic signal
    after the B11 fix, to instantly catch up on a job start
    without waiting the 10 s IDLE."""
    z9 = _make_fake_z9(device_status_raw={
        "ActivitiesOverview": {"MostRelevantActivity": {"Name": "PreparingToPrint"}},
    })
    sub = Z9StatusPollSubscriber(z9, poll_interval=0.01)
    # Guarantees that without force, the IDLE interval blocks the fetch
    sub.ACTIVITY_POLL_INTERVAL_IDLE = 999
    sub._current_snapshot["activity_name"] = "NoActivity"
    import time as _time
    sub._last_activity_poll = _time.monotonic()  # right now

    changed = await sub._poll_activity_if_due(force=True)
    assert changed is True
    assert sub._current_snapshot["activity_name"] == "PreparingToPrint"
    z9.device.device_status.assert_called_once()


@pytest.mark.asyncio
async def test_paper_polling_respects_interval():
    """The explicit polling must not trigger more frequently
    than ``PAPER_POLL_INTERVAL`` — otherwise we lose the optimization and each
    tick at 2s makes a useless GET MediaSystem."""
    z9 = _make_fake_z9(
        media_info={"media_id": "A", "source": "MANUAL_FEED", "source_label": "ManualSheet",
                    "width_mm": 210.0, "length_mm": 297.0,
                    "width_in": 8.27, "length_in": 11.69, "drawer_id": 1},
        device_status_raw={"StatusOverview": {"MostRelevantStatus": "Ready"}},
        paper_dict={"name": "P"},
    )
    sub = Z9StatusPollSubscriber(z9, poll_interval=0.01)
    sub.PAPER_POLL_INTERVAL = 999  # guarantees we are under the interval
    import time as _time
    sub._last_paper_poll = _time.monotonic()  # right now

    result = await sub._poll_paper_if_due()
    assert result is False
    # No fetch was triggered (the interval has not elapsed)
    z9.device.loaded_media_info.assert_not_called()
