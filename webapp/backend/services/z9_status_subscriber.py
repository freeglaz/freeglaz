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

"""Z9StatusSubscriber: abstraction of a Z9 event channel → backend.

Allows swapping the notification strategy without touching the rest of
the code (SSE route, frontend hooks):

- ``Z9StatusPollSubscriber`` (A1, coded): poll EventTable.xml every 2 s,
  diff on AgingStamp, TARGETED refetch only of the resources that
  changed (cf. _refetch_changed_resources). Benefit: 1 event = 1 light
  GET (~50 ms for ConsumableEvent) instead of a full device.status()
  (~250-400 ms in 5 sequential GETs).

- ``Z9StatusUDPSubscriber`` (A2 future, stub): native LEDM UDP push.
  See the class docstring for the spec.

Singleton pattern: created at lifespan, stored in app.state. The SSE
routes subscribe via subscribe(cb) → unsubscribe(cb). A single Z9 poll
serves N web clients.
"""
import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from lib.z9_client import Z9Client, Z9Error

logger = logging.getLogger(__name__)

# Signature of the subscription callbacks: (event_type, data) → None.
# event_type ∈ {"status_full", "status_diff", "z9_state"}.
ChangeCallback = Callable[[str, dict], None]


# ═══════════════════════════════════════════════════════════════════════
# Extraction helpers — produce sub-portions of the status dict in a
# format STRICTLY identical to what device.status() returns for these
# fields. Cf. test_partial_fetch_matches_full_fetch_format for the
# format guarantee (sentinel vs live fixture).
# ═══════════════════════════════════════════════════════════════════════

def _extract_inks(ink_system_raw: dict) -> dict:
    """Extract ``{ink_levels, ink_warnings}`` from raw ink_system().

    Exact replica of the ``DeviceOps.status()`` logic for these two
    fields — any lib evolution must trigger the test sentinel.
    """
    ink_levels: dict[str, float] = {}
    ink_warnings: list[dict] = []
    groups = (ink_system_raw or {}).get("InkSlotGroupCollection", {}).get("InkSlotGroup", [])
    if not isinstance(groups, list):
        groups = [groups]
    for g in groups:
        color = g.get("Color", "?")
        info = g.get("InkSlotGroupInfo", {}).get("InkSupplyGroupInfo", {})
        pct = info.get("LevelPercentage")
        state = info.get("State")
        if pct is not None:
            ink_levels[color] = float(pct)
        if state in ("Warning", "Error"):
            ink_warnings.append({
                "color": color,
                "state": state,
                "status": info.get("UserReportedStatus"),
            })
    return {"ink_levels": ink_levels, "ink_warnings": ink_warnings}


def _extract_loaded_paper(
    media_info: Optional[dict],
    paper_lookup: Optional[Callable[[str], Optional[dict]]],
) -> dict:
    """Extract ``loaded_paper_*`` from loaded_media_info().

    ``paper_lookup`` is typically ``z9.paper.get`` (callable str → dict|None).
    Passed as an argument rather than via the whole z9 for testability.
    """
    if media_info is None:
        return {
            "loaded_paper_id": None,
            "loaded_paper_name": None,
            "loaded_paper_source": None,
            "loaded_paper_source_label": None,
            "loaded_paper_width_mm": None,
            "loaded_paper_length_mm": None,
        }
    loaded_id = media_info.get("media_id")
    paper = paper_lookup(loaded_id) if (paper_lookup and loaded_id) else None
    return {
        "loaded_paper_id": loaded_id,
        "loaded_paper_name": paper["name"] if paper else None,
        "loaded_paper_source": media_info.get("source"),
        "loaded_paper_source_label": media_info.get("source_label"),
        "loaded_paper_width_mm": media_info.get("width_mm"),
        "loaded_paper_length_mm": media_info.get("length_mm"),
    }


def _extract_global_status(device_status_raw: dict) -> str:
    """Extract ``global_status`` from raw device_status()."""
    return (device_status_raw or {}).get("StatusOverview", {}).get(
        "MostRelevantStatus", "Unknown"
    )


def _extract_activity(device_status_raw: dict) -> dict:
    """Extract the current physical activity from raw ``device_status()``.

    The Z9 firmware exposes in ``/DeviceStatus.json``:

      ``ActivitiesOverview > MostRelevantActivity > Name``

    observed values: ``NoActivity``, ``PreparingToPrint``,
    ``Processing``, ``Drying``, ``TerminatingPrint``, ``CancellingPrint``,
    ``DeviceMaintenance``, ``DeviceOptimizing``, etc.

    The ``progress_pct`` is attempted on a few candidate fields
    (PercentComplete, ActivityCompletion). In their absence we return
    ``None`` — the UI will then show an indeterminate bar rather than a
    fabricated value.

    Always returns 2 keys (strictly formed snapshot):
      - ``activity_name``: str (never None — default "NoActivity")
      - ``activity_progress_pct``: float | None
    """
    overview = (device_status_raw or {}).get("ActivitiesOverview", {}) or {}
    activity = overview.get("MostRelevantActivity", {}) or {}
    name = (activity.get("Name") or "NoActivity").strip() or "NoActivity"
    progress: Optional[float] = None
    # Several candidates depending on firmware; accept the first numeric one.
    for key in ("PercentComplete", "ActivityCompletion", "Percent"):
        if key in activity and activity[key] is not None:
            try:
                progress = float(activity[key])
            except (TypeError, ValueError):
                continue
            break
    return {"activity_name": name, "activity_progress_pct": progress}


# ═══════════════════════════════════════════════════════════════════════
# Abstract interface
# ═══════════════════════════════════════════════════════════════════════

class Z9StatusSubscriber(ABC):
    """Emits Z9 change events. Stateless on the API side: the SSE routes
    subscribe via subscribe(cb). The subscriber is created at lifespan
    and started once."""

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    def subscribe(self, cb: ChangeCallback) -> None: ...

    @abstractmethod
    def unsubscribe(self, cb: ChangeCallback) -> None: ...

    @abstractmethod
    def current_snapshot(self) -> dict: ...


# ═══════════════════════════════════════════════════════════════════════
# Z9StatusPollSubscriber — A1 implementation (poll EventTable + targeted fetch)
# ═══════════════════════════════════════════════════════════════════════

class Z9StatusPollSubscriber(Z9StatusSubscriber):
    """Poll of /EventMgmt/EventTable.xml every ``poll_interval`` s.

    Strategy:
      1. At start: 1 full fetch of device.status() to seed the internal
         snapshot and the reference aging_stamps.
      2. Loop: event_table() → diff aging_stamps → TARGETED refetch
         (cf. _refetch_changed_resources) → broadcast to subscribers.
      3. On Z9Error: broadcast a z9_state=error event, exponential
         backoff × 2 up to BACKOFF_MAX. Reset to poll_interval as soon as
         a fetch succeeds.
    """

    POLL_INTERVAL_DEFAULT = 2.0
    # Backoff ceiling (anti-hammer): do not poke a down/errored Z9 every
    # 30 s indefinitely → let it breathe (auto recovery <=2 min when it comes back).
    BACKOFF_MAX = 120.0
    # Min interval between 2 explicit polls of loaded_media_info. The Z9
    # firmware fires NO LEDM event on physical paper change (EventTable
    # capture 22/05/2026 over 3 min of changes → aging_stamps unchanged).
    # Without this polling, the snapshot stays frozen.
    # 5 s = tradeoff of network load (~12 GET/min) vs detection latency.
    PAPER_POLL_INTERVAL = 5.0
    # Interval between explicit polls of device_status() to track
    # MostRelevantActivity. The polling runs **unconditionally**
    # (fix B11) at a variable frequency:
    #
    # - ``ACTIVE`` (3 s): as long as a job is in progress on the firmware
    #   side (Processing / Drying / etc.). Responsive UI on internal
    #   transitions (Drying → TerminatingPrint → NoActivity), ~20 GET/min.
    # - ``IDLE`` (10 s): when activity == NoActivity. Tradeoff of job
    #   start detection latency (~5 s average) vs network noise
    #   (~6 GET/min at idle).
    #
    # The previous fix inc 11.1 polled ONLY if activity != NoActivity,
    # creating a silent chicken-and-egg: without a poll we never got out
    # of NoActivity (bug B11). JobEvent in EventTable is a hint but does
    # not fire reliably on this Z9 (pattern B5).
    ACTIVITY_POLL_INTERVAL_ACTIVE = 3.0
    ACTIVITY_POLL_INTERVAL_IDLE = 10.0

    def __init__(self, z9: Z9Client, poll_interval: float = POLL_INTERVAL_DEFAULT):
        self._z9 = z9
        self._poll_interval = poll_interval
        self._subscribers: set[ChangeCallback] = set()
        self._current_snapshot: dict[str, Any] = {}
        self._last_aging_stamps: dict[str, str] = {}
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None  # lazy init
        self._last_paper_poll: float = 0.0     # monotonic timestamp
        self._last_activity_poll: float = 0.0  # monotonic timestamp

    # ─── lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        """Full initial fetch (BLOCKING) then launch the poll loop in the
        background. Must have finished before the 1st HTTP request is
        accepted (cf. lifespan order)."""
        self._stop_event = asyncio.Event()
        await self._refetch_full_snapshot()
        # `device.status()` covers loaded_paper_* + global_status, but
        # NOT the activity (which lives in raw device_status()).
        # Additional fetch to have activity_name from the 1st snapshot on.
        try:
            dev_raw = await asyncio.to_thread(self._z9.device.device_status)
            self._current_snapshot.update(_extract_activity(dev_raw))
        except Z9Error as e:
            logger.info("Initial activity fetch failed: %s", e)
            self._current_snapshot.setdefault("activity_name", "NoActivity")
            self._current_snapshot.setdefault("activity_progress_pct", None)
        now = time.monotonic()
        self._last_paper_poll = now
        self._last_activity_poll = now
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "Z9StatusPollSubscriber started (interval=%.1fs, %d events in snapshot)",
            self._poll_interval, len(self._last_aging_stamps),
        )

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Z9StatusPollSubscriber stopped")

    # ─── pub/sub ──────────────────────────────────────────────────

    def subscribe(self, cb: ChangeCallback) -> None:
        """Add a callback + immediately push the current snapshot as
        status_full (the new subscriber does not have to wait for the
        next tick)."""
        self._subscribers.add(cb)
        try:
            cb("status_full", dict(self._current_snapshot))
        except Exception:  # noqa: BLE001
            logger.exception("Subscriber initial callback error")

    def unsubscribe(self, cb: ChangeCallback) -> None:
        self._subscribers.discard(cb)

    def current_snapshot(self) -> dict:
        return dict(self._current_snapshot)

    # ─── internal loop ────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        assert self._stop_event is not None
        backoff = self._poll_interval
        while not self._stop_event.is_set():
            try:
                events = await asyncio.to_thread(self._z9.events.event_table)
                changed = self._diff_events(events)
                if changed:
                    await self._refetch_changed_resources(changed)
                paper_changed = await self._poll_paper_if_due()
                # JobEvent in the diff forces an activity refetch even if
                # the snapshot was NoActivity (otherwise we miss the
                # NoActivity → Processing transition at a job start).
                activity_changed = await self._poll_activity_if_due(
                    force="JobEvent" in changed,
                )
                if changed or paper_changed or activity_changed:
                    self._broadcast("status_diff", dict(self._current_snapshot))
                backoff = self._poll_interval  # reset after success
            except Z9Error as e:
                logger.warning("Z9 poll error: %s (backoff %.1fs)", e, backoff)
                self._broadcast("z9_state", {"state": "error", "reason": str(e)})
                backoff = min(backoff * 2, self.BACKOFF_MAX)
            except Exception:  # noqa: BLE001
                logger.exception("Unexpected error in Z9 poll loop")
                backoff = min(backoff * 2, self.BACKOFF_MAX)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass

    def _diff_events(self, events: list[dict]) -> list[str]:
        """Return the list of event_category whose aging_stamp changed
        since the last call. Updates ``_last_aging_stamps`` in place."""
        changed: list[str] = []
        for evt in events:
            cat = evt["category"]
            stamp = evt["aging_stamp"]
            if self._last_aging_stamps.get(cat) != stamp:
                changed.append(cat)
                self._last_aging_stamps[cat] = stamp
        return changed

    async def _refetch_changed_resources(self, changed: list[str]) -> None:
        """Targeted fetch depending on the event categories. Updates
        ``_current_snapshot`` in place with the concerned sub-portions.

        Coverage strategy (cf. B5, 22/05/2026):
        - ``ConsumableEvent`` → refetch ink only (most frequent case in
          continuous operation, we want to keep it light)
        - ``PowerUp/PoweringDown`` → full refetch (radical state)
        - **any other non-ink event** → refetch media + global_status.
          The Z9 firmware fires ``AlertTableChanged`` on most paper
          changes, but not all (depending on firmware we have also
          observed ``MediaSubunitStatusChanged``, ``DeviceCapabilitiesChanged``,
          even bursts of correlated events). Rather than maintaining an
          exhaustive and fragile whitelist, we treat **by default** any
          non-ink event as a media refetch signal. Cost ~100-150 ms per
          triggered cycle — negligible compared to the snapshot
          consistency gain.
        """
        snap = self._current_snapshot

        if "ConsumableEvent" in changed:
            # 1 GET — /InkSystem.json
            ink_raw = await asyncio.to_thread(self._z9.device.ink_system)
            snap.update(_extract_inks(ink_raw))

        if any(c in changed for c in ("PowerUpEvent", "PoweringDownEvent")):
            # Full refetch — Z9 state changed radically. The full status
            # already covers media + global_status, so we exit without an
            # additional refetch (except the JobEvent hint below).
            full = await asyncio.to_thread(self._z9.device.status)
            snap.update(full)
        elif any(c != "ConsumableEvent" for c in changed):
            # 2 GET — /MediaSystem.json + /DeviceStatus.json. Covers
            # AlertTableChanged AND any other non-ink event.
            media = await asyncio.to_thread(self._z9.device.loaded_media_info)
            dev = await asyncio.to_thread(self._z9.device.device_status)
            snap.update(_extract_loaded_paper(media, self._z9.paper.get))
            snap["global_status"] = _extract_global_status(dev)

        if "JobEvent" in changed:
            # UI hint only — the job detail comes from
            # /api/print/{id}/events (existing SSE inc 5b).
            snap["z9_busy"] = True

    async def _poll_paper_if_due(self) -> bool:
        """Explicit and unconditional polling of ``loaded_media_info()``.

        The Z9 firmware generates **no** LEDM event on a physical paper
        change (verified empirically 22/05/2026 via EventTable.xml
        capture over ~3 min of repeated changes: the 3 event categories
        present — ConsumableEvent, PowerUpEvent, PoweringDownEvent — keep
        their ``aging_stamp`` strictly stable). The event_table diff
        therefore never triggers a media refetch after a paper change;
        without this explicit polling, the snapshot stays frozen on the
        initial paper → silent fail pattern like B1
        (cf. ``Docs/freeglaz_Webapp_Roadmap.md`` § B5).

        Returns ``True`` if the snapshot was updated (id/name/source
        differs from the previous value), to signal to the caller that it
        must broadcast a ``status_diff``.

        Cost: 1 GET ``MediaSystem.json`` every ``PAPER_POLL_INTERVAL``
        seconds (5 s by default), ~50-100 ms, i.e. ~12 GET/min —
        negligible compared to the consistency gain. On Z9Error, we log
        and return False without propagating (the event_table diff covers
        the retry).
        """
        now = time.monotonic()
        if now - self._last_paper_poll < self.PAPER_POLL_INTERVAL:
            return False
        self._last_paper_poll = now
        try:
            media = await asyncio.to_thread(self._z9.device.loaded_media_info)
            new_paper = _extract_loaded_paper(media, self._z9.paper.get)
        except Z9Error as e:
            logger.info("paper poll loaded_media_info failed: %s", e)
            return False

        snap = self._current_snapshot
        # Comparison on the identity fields — not on the dimensions which
        # can fluctuate slightly (sensor measurement, economical ROLL).
        changed = any(
            snap.get(k) != new_paper.get(k)
            for k in ("loaded_paper_id", "loaded_paper_name", "loaded_paper_source")
        )
        if not changed:
            return False

        old_name = snap.get("loaded_paper_name")
        snap.update(new_paper)
        # A paper change can move global_status
        # (Ready ↔ WithAlerts ↔ OutOfPaper). We refetch for consistency.
        try:
            dev = await asyncio.to_thread(self._z9.device.device_status)
            snap["global_status"] = _extract_global_status(dev)
        except Z9Error as e:
            logger.info("paper poll device_status failed: %s", e)
        logger.info(
            "Paper change detected via explicit poll: %r → %r",
            old_name, new_paper.get("loaded_paper_name"),
        )
        return True

    async def _poll_activity_if_due(self, *, force: bool = False) -> bool:
        """Explicit and **unconditional** polling of the Z9 physical
        activity.

        Fix B11: we ALWAYS poll, at a variable frequency depending on the
        state:

        - ``activity != NoActivity`` → ``ACTIVE`` interval (3 s, responsive
          during a job in progress).
        - ``activity == NoActivity`` → ``IDLE`` interval (10 s, network
          savings at idle).

        The previous fix inc 11.1 conditioned the polling on
        ``activity != NoActivity``, which introduced a silent
        chicken-and-egg: without a poll we never got out of NoActivity.
        The catch-up planned via ``JobEvent`` in the EventTable diff was
        not reliable (pattern B5 — the Z9 does not fire an LEDM event for
        critical transitions).

        ``force=True`` bypasses the interval for an immediate refetch —
        useful when a ``JobEvent`` does appear in the diff (opportunistic
        signal, not guaranteed).

        Returns ``True`` if the snapshot was updated, to signal to the
        caller that it must broadcast a ``status_diff``. On Z9Error, we
        log and return False without propagating.
        """
        now = time.monotonic()
        snap = self._current_snapshot
        current_activity = snap.get("activity_name", "NoActivity")
        is_active = current_activity != "NoActivity"
        interval = (
            self.ACTIVITY_POLL_INTERVAL_ACTIVE if is_active
            else self.ACTIVITY_POLL_INTERVAL_IDLE
        )
        if not force and now - self._last_activity_poll < interval:
            return False
        self._last_activity_poll = now

        try:
            dev_raw = await asyncio.to_thread(self._z9.device.device_status)
        except Z9Error as e:
            logger.info("activity poll device_status failed: %s", e)
            return False
        new_activity = _extract_activity(dev_raw)

        changed = any(
            snap.get(k) != new_activity.get(k)
            for k in ("activity_name", "activity_progress_pct")
        )
        if not changed:
            return False
        old_name = snap.get("activity_name")
        snap.update(new_activity)
        logger.info(
            "Z9 activity changed: %r → %r (pct=%s)",
            old_name, new_activity["activity_name"],
            new_activity["activity_progress_pct"],
        )
        return True

    async def _refetch_full_snapshot(self) -> None:
        """Full initial fetch (at start) + seeding of the aging_stamps."""
        try:
            full = await asyncio.to_thread(self._z9.device.status)
            self._current_snapshot.update(full)
            events = await asyncio.to_thread(self._z9.events.event_table)
            for evt in events:
                self._last_aging_stamps[evt["category"]] = evt["aging_stamp"]
        except Z9Error as e:
            logger.warning("Initial Z9 snapshot failed: %s", e)
            self._current_snapshot["error"] = str(e)

    def _broadcast(self, event_type: str, data: dict) -> None:
        for cb in list(self._subscribers):
            try:
                cb(event_type, data)
            except Exception:  # noqa: BLE001
                logger.exception("Subscriber callback error (event=%s)", event_type)


# ═══════════════════════════════════════════════════════════════════════
# Z9StatusUDPSubscriber — stub for future A2
# ═══════════════════════════════════════════════════════════════════════

class Z9StatusUDPSubscriber(Z9StatusSubscriber):
    """A2: native LEDM UDP push (NOT IMPLEMENTED — stub with spec).

    Future implementation spec:
      1. Backend opens a UDP socket (configurable port ≥ 1024).
      2. POST /EventMgmt/SubscriptionList.xml with XML body declaring:
           <Subscription>
             <EventList>
               <UnqualifiedEventCategory>AlertTableChanged</UnqualifiedEventCategory>
               <UnqualifiedEventCategory>ConsumableEvent</UnqualifiedEventCategory>
               <UnqualifiedEventCategory>JobEvent</UnqualifiedEventCategory>
             </EventList>
             <ClientAccessData>
               <ProtocolType>UDP</ProtocolType>
               <NetworkInterface>
                 <ClientPort>{port}</ClientPort>
                 <ClientRelativeURL>/EventMgmt/EventNotification</ClientRelativeURL>
                 <IPAddress>{backend_ip}</IPAddress>
               </NetworkInterface>
             </ClientAccessData>
           </Subscription>
      3. UDP reception loop in the background: for each kick → GET the
         mentioned ResourceURI → diff → broadcast.
      4. At shutdown: DELETE /EventMgmt/SubscriptionList/{id}.
      5. Re-subscription at boot (MaxPersistentSubscriptions=0 firmware).

    Considerations:
      - Backend must be reachable from the Z9 (LAN OK, NAT/proxy = KO)
      - Lost UDP packets → keep a sanity poll ~30 s in parallel
      - Reconnect after Z9 reboot (loss of the subscription)

    To implement when we have a real need for sub-second latency (live
    job progress in particular). For now, A1 polling 2 s is enough.
    """

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "Z9StatusUDPSubscriber : voir docstring pour la spec. "
            "Utiliser Z9StatusPollSubscriber pour le moment."
        )

    async def start(self) -> None: raise NotImplementedError
    async def stop(self) -> None: raise NotImplementedError
    def subscribe(self, cb: ChangeCallback) -> None: raise NotImplementedError
    def unsubscribe(self, cb: ChangeCallback) -> None: raise NotImplementedError
    def current_snapshot(self) -> dict: raise NotImplementedError
