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

"""
JobQueueOps — Z9 print job queue management via REST PIWS.

Phase 1. Mirror the Z9 EWS on the lib side to offer granular control
without depending on HP's proprietary EWS:

- List / snapshot of jobs (in progress, waiting, completed, deleted)
- Cancel and remove individual jobs
- Global pause/resume of the queue
- Job thumbnail preview (admin auth required on the firmware side)

REST endpoints used:

  GET  /LFPWebServices/PI/JQ/JobQueueCollection
  GET  /LFPWebServices/PI/JQ/JobQueue/<queue-uuid>/jobs/all
  GET  /LFPWebServices/PI/JQ/JobQueue/<queue-uuid>/jobs/all?fromModificationNumber=N
  PUT  /LFPWebServices/PI/JQ/JobQueue/<queue-uuid>/control/pause
  PUT  /LFPWebServices/PI/JQ/JobQueue/<queue-uuid>/control/resume
  PUT  /LFPWebServices/PI/JQ/JobQueue/<queue-uuid>/control/removeJob?UUID=...
  PUT  /LFPWebServices/PI/JQ/JobQueue/<queue-uuid>/Job/<job-uuid>/control/cancel
  GET  /LFPWebServices/PI/JQ/JobQueue/<queue-uuid>/Job/<job-uuid>/Page/<n>/resources/preview

Auth: GET and control operations WITHOUT auth, except the preview which
requires HTTP Basic admin.

Content negotiation: the ``/JQ/JobQueueCollection`` endpoint does
content negotiation based on the ``Accept`` header (XML by default, JSON
with ``Accept: application/json``). We force JSON for parser simplicity.
All the other endpoints ``jobs/all``, ``control/*`` etc. return raw XML,
which we parse with lxml.

Firmware quirks:

- The queue UUID changes on EVERY Z9 reboot. The firmware answers 404
  ``Unkownw QueueID`` (sic — HP typo) if a stale UUID is used.
  ``JobQueueOps`` automatically re-discovers on 404.
- 502 on ``cancel`` or ``removeJob`` when the job is already in
  ``Deleted`` (silent no-op on the firmware side). We treat this as a
  success (returns False to signal "nothing to do").
- 500 on ``preview`` when the job is in ``Deleted`` (propagated as a
  Z9RESTError to the caller).
- A minimum 1-2 s delay between queue operations is recommended on the
  client side to avoid blocking the firmware. ``JobQueueOps`` does not
  enforce this delay (leaves the responsibility to the caller — a
  subscriber that polls every 3 s or a UI that debounces clicks).
"""
import json as _json
import logging
from typing import Optional

from lxml import etree

from .exceptions import Z9Error, Z9RESTError

logger = logging.getLogger(__name__)


# PIWS v2.0 XML namespace — used for all queue payloads
PIWS_NS = "http://www.hp.com/schemas/piws/v2_0"
_NS = {"piws": PIWS_NS}

# Inches → millimeters conversion for Page dimensions (the XML exposes
# them as ``Unit="in"`` but the rest of freeglaz reasons in mm).
INCH_TO_MM = 25.4


class JobQueueOps:
    """Operations on the Z9 print job queue.

    Attached to ``Z9Client.jobs``. The class maintains a cache of the
    ``queue_uuid`` (volatile across Z9 reboots) and re-runs discovery
    automatically on 404 ``Unkownw QueueID``.
    """

    def __init__(self, client):
        """:param client: Z9Client instance (to access ``client.rest``)."""
        self._client = client
        self._rest = client.rest
        self._queue_uuid: Optional[str] = None
        # Last global ``ModificationNumber`` seen — debug info only.
        # No longer used for the query since the bug:
        # the ``/jobs/all?fromModificationNumber=N`` endpoint is a DELTA
        # endpoint (only sends changes since N, with a minimal XML
        # without Settings/Status when nothing changed). The client now
        # forces ``fromModificationNumber=0`` on every poll.
        self._last_mod_number: int = 0

    # ─── Discovery ──────────────────────────────────────────────────────

    def discover_queue_uuid(self, force: bool = False) -> str:
        """Fetch the current queue UUID via ``JobQueueCollection``.

        The UUID changes on every Z9 reboot. We cache it; ``force=True``
        invalidates the cache and redoes the call. Auto-called by
        ``get_jobs_snapshot`` on 404 ``Unkownw QueueID``.

        Returns the UUID extracted from the ``URI`` field (last segment).
        """
        if self._queue_uuid is not None and not force:
            return self._queue_uuid

        # Content negotiation on the Z9 side: without an Accept header, the
        # firmware answers XML by default; with ``Accept: application/json``,
        # it answers JSON. We force JSON to simplify parsing (consistent
        # with the format documented in the roadmap).
        raw = self._rest.get(
            "/JQ/JobQueueCollection",
            extra_headers={"Accept": "application/json"},
        )
        data = _json.loads(raw)

        # Observed format:
        #   {"JobQueueCollection": {"Queue": {"Name": "PRINT",
        #     "URI": "/LFPWebServices/PI/JQ/JobQueue/<uuid>", ...}}}
        # If several Queue in the future, take the first one of type
        # Print (PRINT is the standard name on this Z9).
        try:
            queue_obj = data["JobQueueCollection"]["Queue"]
            if isinstance(queue_obj, list):
                # Several queues — take the first one of type Print
                queue_obj = next(
                    (q for q in queue_obj if q.get("Type", "").lower() == "print"),
                    queue_obj[0],
                )
            uri = queue_obj["URI"]
        except (KeyError, TypeError, IndexError, StopIteration) as e:
            raise Z9Error(
                f"Format JobQueueCollection inattendu : {data!r} ({e})"
            )

        new_uuid = uri.rstrip("/").rsplit("/", 1)[-1]
        if not new_uuid:
            raise Z9Error(f"Empty UUID extracted from the URI: {uri!r}")
        if new_uuid != self._queue_uuid:
            logger.info(
                "Queue UUID discovered/refreshed: %s → %s",
                self._queue_uuid, new_uuid,
            )
            # New UUID → reset modification number counter
            self._last_mod_number = 0
        self._queue_uuid = new_uuid
        return new_uuid

    @property
    def queue_uuid(self) -> Optional[str]:
        """The current known UUID, or None if not yet discovered."""
        return self._queue_uuid

    # ─── Snapshot ───────────────────────────────────────────────────────

    def get_jobs_snapshot(self, since_mod_number: Optional[int] = None) -> dict:
        """Full snapshot of the print job queue.

        Z9 firmware quirk (discovered empirically):
        the ``/jobs/all?fromModificationNumber=N`` endpoint
        is a **DELTA endpoint**, not a "snapshot ≥ N" filter. When
        nothing has changed since ``N`` (typical case between 2 polls),
        the firmware returns a minimal XML containing **just the envelope**
        of the jobs (``UUID``, ``URI``) **without** ``Settings`` or ``Status``
        — so without ``JobName``, ``UserName``, ``JobStatus``,
        ``MediaType``, ``PrintQuality``, etc.

        To avoid exposing snapshots with all business fields empty after
        the 1st poll, we always force
        ``fromModificationNumber=0`` (full snapshot). On a local LAN
        with ~1-10 jobs, the XML payload is ~5 KB every 3 s, which is
        negligible.

        :param since_mod_number: **Kept for backward compatibility but
            ignored in the query**. The firmware does not really support
            delta polling without losing info — see Z9 API doc
            (HP_DesignJet_Z9_API_Documentation.md, ``jobs/all`` section).

        Returns a structured dict:

        .. code-block:: python

           {
             "queue_status": "Running" | "Paused",
             "number_of_jobs": int,
             "modification_number": int,
             "timestamp": str (ISO 8601),
             "jobs": [
               {
                 "uuid": "ECAF...",
                 "name": "test_a.tif (1 page) - freeglaz",
                 "user": "user",
                 "source": "Application",
                 "pdl_name": "PDF",
                 "status": "WaitingToPrint" | "Processing" | "Deleted" | ...,
                 "completion_status": "OK" | "Cancelled" | None,
                 "hold_reason": "None" | ...,
                 "media_type_id": "9E489F02AE...",
                 "media_source": "ManualSheet" | "MainRoll" | ...,
                 "print_quality": "Best" | "Normal" | "Fast",
                 "max_detail": True | False,
                 "copies_requested": 1,
                 "number_of_pages": 1,
                 "page_size_mm": {"width": 167.6, "height": 221.1},
                 "progress_percentage": 0.0,
                 "preview_uri": "/.../Page/1/resources/preview" | None,
                 "submission_timestamp": "2026-05-23T14:49:45Z",
                 "completion_timestamp": "..." | None,
               },
               ...
             ]
           }

        On 404 ``Unkownw QueueID`` (the cached UUID is stale after a
        Z9 reboot): automatic re-discovery of the UUID + retry once
        with ``fromModificationNumber=0``.
        """
        if self._queue_uuid is None:
            self.discover_queue_uuid()

        # Always a full snapshot (cf. the delta quirk documented above).
        # The ``since_mod_number`` parameter is accepted for backward
        # compatibility but ignored — historically the client incremented
        # ``_last_mod_number`` after each poll, which produced empty
        # deltas from the 2nd poll onward.
        endpoint = (
            f"/JQ/JobQueue/{self._queue_uuid}/jobs/all"
            f"?fromModificationNumber=0"
        )

        try:
            xml_text = self._rest.get(endpoint)
        except Z9RESTError as e:
            if self._is_unknown_queue_id(e):
                logger.info(
                    "Queue UUID stale (404 Unkownw QueueID) — re-discovery",
                )
                self.discover_queue_uuid(force=True)
                endpoint = (
                    f"/JQ/JobQueue/{self._queue_uuid}/jobs/all"
                    f"?fromModificationNumber=0"
                )
                xml_text = self._rest.get(endpoint)
            else:
                raise

        snapshot = self._parse_jobs_xml(xml_text)
        # We keep ``_last_mod_number`` up to date for info / debug (a
        # caller could use it to detect that a snapshot is unchanged
        # between 2 polls), but we no longer send it in the query.
        self._last_mod_number = snapshot["modification_number"]
        return snapshot

    @staticmethod
    def _is_unknown_queue_id(err: Z9RESTError) -> bool:
        """Detect the firmware "Unkownw QueueID" pattern (HP typo)."""
        if err.status_code != 404:
            return False
        body = (err.body or "").lower()
        # The firmware literally uses "Unkownw QueueID" (sic).
        # We match broadly to tolerate a possible future correction.
        return "queueid" in body or "queue id" in body

    # ─── XML parser ─────────────────────────────────────────────────────

    @classmethod
    def _parse_jobs_xml(cls, xml_text: str) -> dict:
        """Parse a PIWS ``jobs/all`` response into a structured dict.

        The XML uses the ``http://www.hp.com/schemas/piws/v2_0`` namespace
        as the default namespace AND under the ``piws:`` prefix. lxml
        requires an explicit namespace map (no support for the default
        namespace in XPath / find).
        """
        # Encode to bytes: lxml prefers bytes to honor the XML encoding
        # declaration.
        root = etree.fromstring(xml_text.encode("utf-8"))

        # The root is ``<piws:JobQueueSnapshot>``. All children use the
        # default namespace PIWS_NS.
        mod_number = int(cls._findtext(root, "piws:ModificationNumber", "0"))
        status_node = root.find("piws:Status", _NS)
        queue_status = cls._findtext(status_node, "piws:JobQueueStatus", "Unknown")
        number_of_jobs = int(cls._findtext(status_node, "piws:NumberOfJobs", "0"))
        timestamp = cls._findtext(root, "piws:Timestamp", "")

        jobs = []
        jobs_root = root.find("piws:Jobs", _NS)
        if jobs_root is not None:
            for job_node in jobs_root.findall("piws:Job", _NS):
                parsed = cls._parse_job_node(job_node)
                if parsed is not None:
                    jobs.append(parsed)

        return {
            "queue_status": queue_status,
            "number_of_jobs": number_of_jobs,
            "modification_number": mod_number,
            "timestamp": timestamp,
            "jobs": jobs,
        }

    @classmethod
    def _parse_job_node(cls, job_node) -> Optional[dict]:
        """Parse a ``<piws:Job><piws:Job2DPrint>...`` into a business dict.

        Returns None if the format is unexpected (no Job2DPrint or
        missing UUID). We don't raise — a malformed job must not prevent
        the global list from being returned.
        """
        j2d = job_node.find("piws:Job2DPrint", _NS)
        if j2d is None:
            return None

        uuid = cls._findtext(j2d, "piws:UUID")
        if not uuid:
            return None

        settings = j2d.find("piws:Settings", _NS)
        status = j2d.find("piws:Status", _NS)
        accounting = j2d.find("piws:Accounting", _NS)
        progress = j2d.find("piws:Progress", _NS)
        pages = j2d.find("piws:Pages", _NS)

        # Page 1 — used to fetch dimensions, MediaType, preview_uri.
        # freeglaz always sends 1 page per job (cf. PRN builder), so
        # we only read the first one.
        page1 = pages.find("piws:Page", _NS) if pages is not None else None

        # Dimensions (in inches in the XML → mm)
        page_size = None
        if page1 is not None:
            page_settings = page1.find("piws:Settings", _NS)
            if page_settings is not None:
                page_size_node = page_settings.find("piws:PageSize", _NS)
                if page_size_node is not None:
                    w_in = cls._read_value_unit(page_size_node.find("piws:Width", _NS))
                    h_in = cls._read_value_unit(page_size_node.find("piws:Height", _NS))
                    if w_in is not None and h_in is not None:
                        page_size = {
                            "width":  round(w_in * INCH_TO_MM, 2),
                            "height": round(h_in * INCH_TO_MM, 2),
                        }

        page_settings = page1.find("piws:Settings", _NS) if page1 is not None else None
        media_type_id = cls._findtext(page_settings, "piws:MediaType") if page_settings is not None else ""
        media_source  = cls._findtext(page_settings, "piws:MediaSource") if page_settings is not None else ""
        print_quality = cls._findtext(page_settings, "piws:PrintQuality") if page_settings is not None else ""
        max_detail_str = cls._findtext(page_settings, "piws:MaximumDetailOn", "false") if page_settings is not None else "false"
        max_detail = max_detail_str.strip().lower() == "true"
        preview_uri = cls._findtext(page_settings, "piws:PreviewFilePath") if page_settings is not None else None
        if preview_uri == "":
            preview_uri = None

        return {
            "uuid": uuid,
            "name": cls._findtext(settings, "piws:JobName", ""),
            "user": cls._findtext(settings, "piws:UserName", ""),
            "source": cls._findtext(settings, "piws:Source", ""),
            "pdl_name": cls._findtext(settings, "piws:PDLName", ""),
            "status": cls._findtext(status, "piws:JobStatus", "Unknown"),
            "completion_status": cls._findtext(status, "piws:CompletionStatus") or None,
            "hold_reason": cls._findtext(status, "piws:HoldReason") or None,
            "media_type_id": media_type_id,
            "media_source": media_source,
            "print_quality": print_quality,
            "max_detail": max_detail,
            "copies_requested": int(cls._findtext(settings, "piws:RequestedCopies", "1")),
            "number_of_pages": int(cls._findtext(pages, "piws:NumberOfPages", "1")) if pages is not None else 1,
            "page_size_mm": page_size,
            "progress_percentage": float(cls._findtext(progress, "piws:ProgressPercentage", "0")) if progress is not None else 0.0,
            "preview_uri": preview_uri,
            "submission_timestamp": cls._findtext(accounting, "piws:SubmissionTimestamp") if accounting is not None else "",
            "completion_timestamp": cls._findtext(accounting, "piws:CompletionTimestamp") if accounting is not None else "",
        }

    @staticmethod
    def _findtext(node, xpath: str, default: str = "") -> str:
        """``node.findtext(xpath, default, _NS)`` but robust to node=None."""
        if node is None:
            return default
        return (node.findtext(xpath, default=default, namespaces=_NS) or "").strip()

    @classmethod
    def _read_value_unit(cls, node) -> Optional[float]:
        """Read ``<piws:Value>X</piws:Value><piws:Unit>in</piws:Unit>`` into
        a float. Assumes ``in`` (inches) — this is the only case
        observed empirically on Z9 jobs."""
        if node is None:
            return None
        val_str = cls._findtext(node, "piws:Value")
        if not val_str:
            return None
        try:
            return float(val_str)
        except ValueError:
            return None

    # ─── Control operations ─────────────────────────────────────────────

    def pause_queue(self) -> bool:
        """Pause the entire queue. Idempotent.

        Returns True if the request went out (status 2xx). Raises
        ``Z9Error`` or a descendant on a network error or unexpected
        firmware error.
        """
        self._ensure_uuid()
        self._rest.request(
            "PUT", f"/JQ/JobQueue/{self._queue_uuid}/control/pause",
            data=b"",
        )
        return True

    def resume_queue(self) -> bool:
        """Resume the queue. Idempotent."""
        self._ensure_uuid()
        self._rest.request(
            "PUT", f"/JQ/JobQueue/{self._queue_uuid}/control/resume",
            data=b"",
        )
        return True

    def cancel_job(self, job_uuid: str) -> bool:
        """Cancel a job. The job stays in the queue with
        ``CompletionStatus=Cancelled``. To make it disappear completely,
        then call ``remove_job``.

        Returns True if the command was accepted by the firmware,
        False if the firmware answers 502 (the job is already ``Deleted``,
        silent no-op on the Z9 side). Raises on any other error.
        """
        self._ensure_uuid()
        try:
            self._rest.request(
                "PUT",
                f"/JQ/JobQueue/{self._queue_uuid}/Job/{job_uuid}/control/cancel",
                data=b"",
            )
        except Z9RESTError as e:
            if e.status_code == 502:
                logger.info(
                    "cancel_job(%s) → 502 firmware (job probablement Deleted, no-op)",
                    job_uuid,
                )
                return False
            raise
        return True

    def remove_job(self, job_uuid: str) -> bool:
        """Remove a job from the queue (disappears completely).

        HP firmware quirk: the UUID is passed in the **query string**,
        not in the path. Endpoint:
        ``PUT /JobQueue/<uuid>/control/removeJob?UUID=<job_uuid>``.

        Returns True if OK, False on 502 (job already Deleted or
        purged).
        """
        self._ensure_uuid()
        # INFO log before each firmware call — lets us compare the
        # "individual trash" vs "clear_all loop" traces line by line in
        # case of behavior divergence.
        endpoint = f"/JQ/JobQueue/{self._queue_uuid}/control/removeJob?UUID={job_uuid}"
        logger.info("remove_job: PUT %s", endpoint)
        try:
            self._rest.request("PUT", endpoint, data=b"")
        except Z9RESTError as e:
            if e.status_code == 502:
                logger.info(
                    "remove_job(%s) → 502 firmware (no-op silencieux)",
                    job_uuid,
                )
                return False
            logger.warning(
                "remove_job(%s) → Z9RESTError %d : %s",
                job_uuid, e.status_code, e,
            )
            raise
        logger.info("remove_job(%s) → OK", job_uuid)
        return True

    def reprint_job(
        self,
        original_job_uuid: str,
        copies: int = 1,
    ) -> dict:
        """Trigger a reprint on the Z9 firmware.

        The firmware creates a **new** job in the queue with a new UUID
        + ``Priority=3`` (normal jobs have ``Priority=0``).
        The original stays in place (status unchanged). To identify the
        new UUID on the client side, see ``find_new_reprint_job``.

        Reprint endpoint quirks:

        - **URL without UUID in the path**: ``/JobQueue/Job/control/reprint``
          (unlike ``cancel`` / ``removeJob`` which put the UUID in the
          path). The UUIDs go in the XML body.
        - **Content-Type**: ``text/xml`` (not ``application/xml``).
        - **Request namespace**: ``http://www.hp.com/jq`` with an explicit
          ``jq:`` prefix required. **Different from the PIWS namespace**
          (``http://www.hp.com/schemas/piws/v2_0``) used everywhere else
          and in the response.
        - **Response namespace**: classic PIWS v2_0. So the request parser
          ≠ the response parser on the namespace side.
        - The response's ``<JobUUID>`` is the UUID **of the original**,
          not of the new one. The new UUID is discovered by polling
          ``/jobs/all`` after the reprint.

        :param original_job_uuid: firmware UUID of the job to reprint.
        :param copies: number of copies (default 1; exposed to allow a
            future "reprint N copies" feature — the current phase
            hardcodes 1 on the UI side).
        :return: dict ``{queue_uuid, original_uuid, status}`` parsed from
            the XML response.
        :raises Z9Error: if the firmware answers anything other than
            ``SuccessfullySubmitted``.
        """
        self._ensure_uuid()
        # Building the request XML — jq: namespace, mind the single
        # quotes (XML format expected by the firmware).
        # No f-string on the namespace to avoid a subtle typo.
        payload = (
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<jq:JobReprintRequest "
            "xmlns:jq='http://www.hp.com/jq' "
            "xmlns:xsi='http://www.w3.org/2001/XMLSchema-instance' "
            "xsi:schemaLocation='http://www.hp.com/jq JobQueue.xsd'>"
            f"<QueueUUID>{self._queue_uuid}</QueueUUID>"
            f"<JobUUID>{original_job_uuid}</JobUUID>"
            f"<Copies>{int(copies)}</Copies>"
            "</jq:JobReprintRequest>"
        )
        raw = self._rest.request(
            "POST",
            "/JQ/JobQueue/Job/control/reprint",
            data=payload.encode("utf-8"),
            extra_headers={
                "Content-Type": "text/xml",
                "Accept": "application/xml",
            },
        )
        # The response uses the classic PIWS namespace — we attempt the
        # namespaced parsing first, fallback without namespace in case
        # the firmware changes one day. If parsing fails, we raise.
        return self._parse_reprint_response(raw)

    @classmethod
    def _parse_reprint_response(cls, xml_text: str) -> dict:
        """Parse a ``<JobReprintResponse>`` response into a dict.

        Expected namespace: ``http://www.hp.com/schemas/piws/v2_0``.
        Observed format:

        .. code-block:: xml

           <JobReprintResponse xmlns='http://www.hp.com/schemas/piws/v2_0'>
             <QueueUUID>...</QueueUUID>
             <JobUUID>...</JobUUID>            <!-- UUID original -->
             <ResponseStatus>SuccessfullySubmitted</ResponseStatus>
           </JobReprintResponse>

        :raises Z9Error: if the XML is malformed or if ``ResponseStatus``
            ≠ ``"SuccessfullySubmitted"``.
        """
        try:
            root = etree.fromstring(xml_text.encode("utf-8"))
        except etree.XMLSyntaxError as e:
            raise Z9Error(f"Malformed reprint XML response: {e}")
        # The parser tolerates the default PIWS namespace; we search by
        # local name to be robust to possible variants.
        def _find_local(parent, local_name: str) -> Optional[str]:
            for child in parent.iter():
                tag = child.tag
                # Skip nodes that are not normal XML elements:
                # ``parent.iter()`` ALSO traverses comments
                # (``<!-- ... -->``) and processing-instructions. For
                # those, ``child.tag`` is not a str but the Cython
                # factory ``lxml.etree.Comment`` / ``ProcessingInstruction``,
                # which crashed ``"}" in tag`` (TypeError: cython
                # function not iterable). The Z9 firmware systematically
                # puts a "THIS DATA IS SUBJECT TO DISCLAIMER" comment in
                # its XML responses (observed on reprint responses).
                if not isinstance(tag, str):
                    continue
                # tag can be "{ns}LocalName" or "LocalName"
                local = tag.split("}", 1)[-1] if "}" in tag else tag
                if local == local_name and child.text:
                    return child.text.strip()
            return None

        status = _find_local(root, "ResponseStatus") or ""
        queue_uuid = _find_local(root, "QueueUUID") or ""
        original = _find_local(root, "JobUUID") or ""
        if status != "SuccessfullySubmitted":
            raise Z9Error(
                f"Reprint refused by firmware: ResponseStatus={status!r}"
            )
        return {
            "queue_uuid": queue_uuid,
            "original_uuid": original,
            "status": status,
        }

    def find_new_reprint_job(
        self,
        before_uuids: set[str],
        before_mod_number: int,
        *,
        poll_interval_s: float = 0.5,
        timeout_s: float = 5.0,
    ) -> Optional[str]:
        """After a reprint, identify the new job created by the firmware.

        Strategy: the caller snapshots ``before_uuids`` and
        ``before_mod_number`` BEFORE the call to ``reprint_job``. We poll
        ``/jobs/all`` until a new job appears with:
        - a ``firmware_uuid`` absent from ``before_uuids``
        - a ``ModificationNumber`` > ``before_mod_number``

        We do **not** filter on ``Priority=3`` here because the current
        parser does not expose the job's priority in the snapshot (only
        at the queue level). The "new UUID + more recent ModNumber"
        criterion is sufficient in practice.

        :return: new UUID, or ``None`` on timeout.
        """
        import time as _time
        deadline = _time.monotonic() + timeout_s
        while _time.monotonic() < deadline:
            try:
                snap = self.get_jobs_snapshot()
            except Z9Error:
                _time.sleep(poll_interval_s)
                continue
            if snap["modification_number"] <= before_mod_number:
                _time.sleep(poll_interval_s)
                continue
            current = {j["uuid"] for j in snap.get("jobs", []) if j.get("uuid")}
            new = current - before_uuids
            if new:
                return next(iter(new))
            _time.sleep(poll_interval_s)
        return None

    def clear_all(
        self,
        *,
        initial_delay_s: float = 0.2,
        backoff_factor: float = 2.0,
        max_delay_s: float = 2.0,
    ) -> tuple[int, int]:
        """Clear the queue via a ``remove_job`` loop with adaptive backoff.

        Pivot: gave up on ``POST /control/removeJobs``
        (persistent 405). Strictly aligned with the pattern that already
        works for the individual trash button — calls exactly
        ``self.remove_job(uuid)``, same firmware HTTP path.

        Calibration: a fixed 1.5 s delay
        would give 75 s for 50 jobs — unusable. We start at 200 ms
        and adapt based on firmware responses:

        - Consecutive successes → ``delay = max(initial_delay_s,
          delay * 0.9)`` (gentle deceleration, never below
          ``initial_delay_s``).
        - "Throttled" firmware error (429 Too Many Requests, 503
          Service Unavailable) → ``delay = min(max_delay_s, delay *
          backoff_factor)`` then retry once after this new delay.

        Best-effort: broad try/except ``Exception`` so the loop stops
        under NO pretext. All jobs are attempted; failures are counted
        in ``failed`` and logged individually.

        :return: ``(removed_count, failed_count)``. Never raises an
            exception — the caller always receives the counters.
        """
        import time as _time

        snapshot = self.get_jobs_snapshot()
        to_delete = [
            j for j in snapshot.get("jobs", [])
            if j.get("uuid") and j.get("status") != "Deleted"
        ]
        logger.info(
            "clear_all: starting loop on %d job(s) (queue=%s, initial_delay=%.0fms)",
            len(to_delete), self._queue_uuid, initial_delay_s * 1000,
        )
        removed, failed = 0, 0
        delay = initial_delay_s
        for j in to_delete:
            uuid = j["uuid"]
            try:
                # Literal call of the same method as the individual trash
                # button. If it works for 1, it should work for N —
                # possibly with a bit of pacing between calls.
                self.remove_job(uuid)
                removed += 1
                # Gentle deceleration after each success — if the Z9
                # keeps up, we speed up.
                delay = max(initial_delay_s, delay * 0.9)
            except Z9RESTError as e:
                if _is_firmware_throttled(e):
                    # Backoff: wait longer and retry this same job once.
                    delay = min(max_delay_s, delay * backoff_factor)
                    logger.warning(
                        "clear_all: throttle detected (HTTP %d) on %s — "
                        "backoff to %.0fms then retry",
                        e.status_code, uuid, delay * 1000,
                    )
                    _time.sleep(delay)
                    try:
                        self.remove_job(uuid)
                        removed += 1
                    except Exception as e2:  # noqa: BLE001
                        logger.warning(
                            "clear_all: failed %s after retry: %s", uuid, e2,
                        )
                        failed += 1
                else:
                    logger.warning(
                        "clear_all: failed %s (non-throttle): %s", uuid, e,
                    )
                    failed += 1
            except Exception as e:  # noqa: BLE001 — best-effort intentionnel
                logger.warning(
                    "clear_all: failed to delete %s: %s", uuid, e,
                )
                failed += 1
            _time.sleep(delay)

        logger.info(
            "clear_all: done — %d removed, %d failed (queue=%s)",
            removed, failed, self._queue_uuid,
        )
        return removed, failed

    def get_job_preview(self, job_uuid: str, page: int = 1) -> bytes:
        """Fetch a job's thumbnail preview (raw bytes).

        HTTP Basic admin auth **required** on the Z9 firmware side. The
        method propagates ``Z9AuthError`` if ``Z9_ADMIN_PWD`` is not
        configured on the client side.

        Observed format: binary image (JPEG or PNG depending on
        firmware, to be confirmed empirically — the caller must infer
        from the Content-Type or the magic bytes).

        :raises Z9AuthError: no admin auth available.
        :raises Z9RESTError: 500 if the job is in ``Deleted`` (cf.
            firmware quirks), other HTTP error.
        """
        self._ensure_uuid()
        return self._rest.get(
            f"/JQ/JobQueue/{self._queue_uuid}/Job/{job_uuid}/Page/{page}/resources/preview",
            auth=True, raw=True,
        )

    # ─── Internal helpers ───────────────────────────────────────────────

    def _ensure_uuid(self) -> None:
        """Ensure a UUID is known before an operation."""
        if self._queue_uuid is None:
            self.discover_queue_uuid()


def _is_firmware_throttled(err: Z9RESTError) -> bool:
    """Detect whether a firmware error indicates temporary throttling.

    Empirical criteria (to be adjusted based on what the Z9 returns in
    live validation):
    - 429 Too Many Requests (standard HTTP, rare in HP firmware but
      possible)
    - 503 Service Unavailable (firmware busy / saturated)
    - Body containing "busy" or "throttled" case-insensitive

    Non-throttle cases (= real error, no retry):
    - 502 on ``remove_job`` = job already Deleted, the ``remove_job``
      helper handles it silently (returns False, does not raise) — so we
      don't even see it here.
    - 404 Unkownw QueueID = handle via re-discover, not backoff.
    - Other 4xx/5xx = application bug, we log and continue.
    """
    if err.status_code in (429, 503):
        return True
    body_lower = (err.body or "").lower()
    if "busy" in body_lower or "throttled" in body_lower:
        return True
    return False
