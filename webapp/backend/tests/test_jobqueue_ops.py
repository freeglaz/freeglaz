"""Phase 1 tests — JobQueueOps: discovery, PIWS XML parsing,
control ops, 404/502 firmware handling."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from lib.z9_client.exceptions import Z9Error, Z9RESTError
from lib.z9_client.jobqueue import INCH_TO_MM, JobQueueOps


_FIXTURES = Path(__file__).parent / "fixtures"
_JOBS_XML_SAMPLE = (_FIXTURES / "jobs_all_sample.xml").read_text(encoding="utf-8")
_JOBS_XML_REAL = (_FIXTURES / "jobs_all_sample_real.xml").read_text(encoding="utf-8")

# Example payload returned by /JobQueueCollection (JSON format observed
# on the Z9 firmware on 23/05/2026).
_COLLECTION_JSON = json.dumps({
    "JobQueueCollection": {
        "Queue": {
            "Name": "PRINT",
            "Persistence": "Persistent",
            "Type": "Print",
            "URI": "/LFPWebServices/PI/JQ/JobQueue/5805be78-cd86-4875-afb9-9c21113dcca0",
        },
    },
})


def _make_client(rest_mock: MagicMock):
    """Build a minimal Z9Client-like object to pass to JobQueueOps.

    ``host`` is exposed (used by ``remove_jobs`` for the Origin header
    required by EmWeb — fix 25/05/2026).
    """
    return SimpleNamespace(rest=rest_mock, host="192.168.1.50")


# ─── Discovery ────────────────────────────────────────────────────────


def test_discover_queue_uuid_extracts_from_uri():
    rest = MagicMock()
    rest.get.return_value = _COLLECTION_JSON
    ops = JobQueueOps(_make_client(rest))

    uuid = ops.discover_queue_uuid()
    assert uuid == "5805be78-cd86-4875-afb9-9c21113dcca0"
    assert ops.queue_uuid == uuid
    rest.get.assert_called_once_with(
        "/JQ/JobQueueCollection",
        extra_headers={"Accept": "application/json"},
    )


def test_discover_uuid_sends_accept_json_header():
    """Regression: without ``Accept: application/json``, the Z9
    does content negotiation and returns XML, which crashes
    ``json.loads``. We check that the header is indeed sent."""
    rest = MagicMock()
    rest.get.return_value = _COLLECTION_JSON
    ops = JobQueueOps(_make_client(rest))
    ops.discover_queue_uuid()

    # The extra_headers kwarg must contain Accept: application/json
    _, kwargs = rest.get.call_args
    assert "extra_headers" in kwargs
    assert kwargs["extra_headers"].get("Accept") == "application/json"


def test_discover_queue_uuid_cached_until_force():
    rest = MagicMock()
    rest.get.return_value = _COLLECTION_JSON
    ops = JobQueueOps(_make_client(rest))

    u1 = ops.discover_queue_uuid()
    u2 = ops.discover_queue_uuid()
    assert u1 == u2
    # No 2nd REST call (cache)
    rest.get.assert_called_once()

    # force=True triggers a new fetch
    rest.get.return_value = json.dumps({
        "JobQueueCollection": {"Queue": {
            "Name": "PRINT", "Type": "Print",
            "URI": "/LFPWebServices/PI/JQ/JobQueue/new-uuid-after-reboot",
        }}
    })
    u3 = ops.discover_queue_uuid(force=True)
    assert u3 == "new-uuid-after-reboot"
    assert ops.queue_uuid == "new-uuid-after-reboot"
    assert rest.get.call_count == 2


def test_discover_queue_uuid_handles_queue_list_format():
    """If JSON exposes Queue as a list (future multi-queue firmware case),
    we take the first one of type Print."""
    rest = MagicMock()
    rest.get.return_value = json.dumps({
        "JobQueueCollection": {"Queue": [
            {"Name": "SCAN",  "Type": "Scan",  "URI": "/.../JobQueue/scan-uuid"},
            {"Name": "PRINT", "Type": "Print", "URI": "/.../JobQueue/print-uuid"},
        ]}
    })
    ops = JobQueueOps(_make_client(rest))
    assert ops.discover_queue_uuid() == "print-uuid"


def test_discover_queue_uuid_raises_on_malformed_response():
    rest = MagicMock()
    rest.get.return_value = json.dumps({"JobQueueCollection": {"Queue": {}}})
    ops = JobQueueOps(_make_client(rest))
    with pytest.raises(Z9Error):
        ops.discover_queue_uuid()


# ─── Parser XML ───────────────────────────────────────────────────────


def test_parse_jobs_xml_extracts_queue_status():
    snap = JobQueueOps._parse_jobs_xml(_JOBS_XML_SAMPLE)
    assert snap["queue_status"] == "Paused"
    assert snap["number_of_jobs"] == 6
    assert snap["modification_number"] == 56
    assert snap["timestamp"] == "2026-05-23T13:44:36Z"


def test_parse_jobs_xml_extracts_jobs_list():
    snap = JobQueueOps._parse_jobs_xml(_JOBS_XML_SAMPLE)
    # At least the 2 jobs visible in the sample fixture (per NumberOfJobs=6
    # only the first ones are in the truncated sample — we take what we get).
    assert len(snap["jobs"]) >= 2

    # First job: Deleted/Cancelled, legacy JobName "Z9_job"
    j0 = snap["jobs"][0]
    assert j0["uuid"] == "04D86CBA-C93C-4BCA-B7AF-1C03CC76CE31"
    assert j0["name"] == "Z9_job"
    assert j0["user"] == "user"
    assert j0["status"] == "Deleted"
    assert j0["completion_status"] == "Cancelled"
    assert j0["media_source"] == "ManualSheet"
    assert j0["media_type_id"] == "9E489F02AE027F9DD93191D872728C1D"
    assert j0["print_quality"] == "Best"
    assert j0["max_detail"] is True
    assert j0["copies_requested"] == 1
    assert j0["number_of_pages"] == 1

    # Page size in mm (XML exposes it in inches) — 5.368056 in × 25.4 = 136.35 mm
    assert j0["page_size_mm"]["width"]  == pytest.approx(5.368056 * INCH_TO_MM, abs=0.05)
    assert j0["page_size_mm"]["height"] == pytest.approx(10.086111 * INCH_TO_MM, abs=0.05)

    assert j0["preview_uri"].endswith("/Page/1/resources/preview")
    assert j0["submission_timestamp"] == "2026-05-23T14:49:45Z"

    # 2nd job: WaitingToPrint with freeglaz JobName
    j1 = snap["jobs"][1]
    assert j1["uuid"] == "32222D2B-191C-402A-944C-CB719C44869F"
    assert j1["name"] == "source.tif (1 page) - freeglaz"
    assert j1["status"] == "Paused"  # JobStatus = "Paused" because the queue is paused
    assert j1["completion_status"] == "OK"


def test_parses_all_job_fields_from_live_capture():
    """Regression on the ``empty business fields`` diagnosis: we parse
    an XML captured *live* from the Z9 on 23/05/2026 and check that
    each business field is correctly extracted (not empty, not
    'Unknown'). If this test passes but /api/jobs still returns
    empty fields in prod, the bug is elsewhere (stale subscriber,
    backend not reloaded, FastAPI serialization...)."""
    snap = JobQueueOps._parse_jobs_xml(_JOBS_XML_REAL)

    assert snap["queue_status"] == "Paused"
    assert snap["number_of_jobs"] == 1
    assert snap["modification_number"] == 70
    assert snap["timestamp"] == "2026-05-23T16:30:56Z"
    assert len(snap["jobs"]) == 1

    j = snap["jobs"][0]
    assert j["uuid"] == "32222D2B-191C-402A-944C-CB719C44869F"
    assert j["name"] == "source.tif (1 page) - freeglaz"
    assert j["user"] == "user"
    assert j["source"] == "Application"
    assert j["pdl_name"] == "PDF"
    assert j["status"] == "Deleted"
    assert j["completion_status"] == "OK"
    assert j["hold_reason"] == "None"
    assert j["media_type_id"] == "9E489F02AE027F9DD93191D872728C1D"
    assert j["media_source"] == "ManualSheet"
    assert j["print_quality"] == "Best"
    assert j["max_detail"] is True
    assert j["copies_requested"] == 1
    assert j["number_of_pages"] == 1

    # in → mm conversion with tolerance (XML values are 6.298611 in
    # × 7.513889 in, i.e. 160.0 × 190.9 mm to ~0.05 precision).
    assert j["page_size_mm"]["width"]  == pytest.approx(160.0, abs=0.5)
    assert j["page_size_mm"]["height"] == pytest.approx(190.9, abs=0.5)

    assert j["preview_uri"].startswith("/LFPWebServices/PI/JQ/JobQueue/")
    assert j["preview_uri"].endswith("/Page/1/resources/preview")


def test_parse_jobs_xml_tolerant_to_missing_fields():
    """A job without Settings or without Pages must not crash the parser."""
    minimal = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<piws:JobQueueSnapshot xmlns="http://www.hp.com/schemas/piws/v2_0"'
        ' xmlns:piws="http://www.hp.com/schemas/piws/v2_0">'
        '<ModificationNumber>1</ModificationNumber>'
        '<Status><JobQueueStatus>Running</JobQueueStatus>'
        '<NumberOfJobs>1</NumberOfJobs></Status>'
        '<Timestamp>2026-01-01T00:00:00Z</Timestamp>'
        '<Jobs><Job><Job2DPrint><UUID>ABC123</UUID></Job2DPrint></Job></Jobs>'
        '</piws:JobQueueSnapshot>'
    )
    snap = JobQueueOps._parse_jobs_xml(minimal)
    assert snap["queue_status"] == "Running"
    assert len(snap["jobs"]) == 1
    assert snap["jobs"][0]["uuid"] == "ABC123"
    assert snap["jobs"][0]["name"] == ""  # no Settings, but we have a default


def test_parse_jobs_xml_skips_non_job2dprint_entries():
    """A <Job> without <Job2DPrint> (future scan / cut job case) is ignored."""
    xml = (
        '<piws:JobQueueSnapshot xmlns="http://www.hp.com/schemas/piws/v2_0"'
        ' xmlns:piws="http://www.hp.com/schemas/piws/v2_0">'
        '<ModificationNumber>1</ModificationNumber>'
        '<Status><JobQueueStatus>Running</JobQueueStatus>'
        '<NumberOfJobs>2</NumberOfJobs></Status>'
        '<Timestamp>2026-01-01T00:00:00Z</Timestamp>'
        '<Jobs>'
        '<Job><JobUnknown><UUID>X</UUID></JobUnknown></Job>'
        '<Job><Job2DPrint><UUID>VALID</UUID></Job2DPrint></Job>'
        '</Jobs></piws:JobQueueSnapshot>'
    )
    snap = JobQueueOps._parse_jobs_xml(xml)
    assert len(snap["jobs"]) == 1
    assert snap["jobs"][0]["uuid"] == "VALID"


# ─── Snapshot end-to-end ──────────────────────────────────────────────


def test_get_jobs_snapshot_always_requests_modification_zero():
    """Regression DELTA bug: the ``/jobs/all`` endpoint is a
    DELTA endpoint when passed ``fromModificationNumber=N>0``,
    which returns a minimal XML without Settings/Status. We always
    force ``=0`` to get the full snapshot."""
    rest = MagicMock()
    rest.get.side_effect = [
        _COLLECTION_JSON,           # discovery
        _JOBS_XML_SAMPLE,           # 1st jobs/all
        _JOBS_XML_SAMPLE,           # 2nd jobs/all
        _JOBS_XML_SAMPLE,           # 3rd jobs/all
    ]
    ops = JobQueueOps(_make_client(rest))
    ops.get_jobs_snapshot()  # 1st
    ops.get_jobs_snapshot()  # 2nd
    ops.get_jobs_snapshot()  # 3rd

    # 4 REST calls: 1 discovery + 3 snapshots
    assert rest.get.call_count == 4

    # All jobs/all calls (indices 1, 2, 3) must use
    # fromModificationNumber=0 — not the ModificationNumber of the
    # previous snapshot.
    for call in rest.get.call_args_list[1:]:
        path = call.args[0]
        assert "jobs/all" in path
        assert "fromModificationNumber=0" in path, (
            f"Appel doit toujours utiliser fromModificationNumber=0, "
            f"trouvé : {path!r}"
        )


def test_get_jobs_snapshot_rediscovers_on_unknown_queueid_404():
    """404 'Unkownw QueueID' → automatic re-discovery + retry once."""
    rest = MagicMock()
    rest.get.side_effect = [
        _COLLECTION_JSON,                                          # 1st discovery
        Z9RESTError(404, "https://z9/.../jobs/all", "Unkownw QueueID"),  # snapshot fail
        json.dumps({                                                # re-discovery
            "JobQueueCollection": {"Queue": {
                "Name": "PRINT", "Type": "Print",
                "URI": "/LFPWebServices/PI/JQ/JobQueue/post-reboot-uuid",
            }}
        }),
        _JOBS_XML_SAMPLE,                                          # snapshot retry
    ]
    ops = JobQueueOps(_make_client(rest))

    snap = ops.get_jobs_snapshot()
    assert ops.queue_uuid == "post-reboot-uuid"
    assert snap["queue_status"] == "Paused"

    # 4 calls total: initial discovery + jobs/all (404) + re-discovery
    # + jobs/all retry
    assert rest.get.call_count == 4
    # The retry uses fromModificationNumber=0 (reset after UUID change)
    retry_call = rest.get.call_args_list[-1]
    assert "fromModificationNumber=0" in retry_call.args[0]


def test_get_jobs_snapshot_raises_on_non_unknown_queue_404():
    """404 without the word QueueID in the body does NOT trigger re-discovery."""
    rest = MagicMock()
    rest.get.side_effect = [
        _COLLECTION_JSON,
        Z9RESTError(404, "/...", "Not Found"),  # generic 404
    ]
    ops = JobQueueOps(_make_client(rest))
    with pytest.raises(Z9RESTError):
        ops.get_jobs_snapshot()


# ─── Control operations ───────────────────────────────────────────────


def _ops_with_known_uuid(rest=None):
    rest = rest or MagicMock()
    ops = JobQueueOps(_make_client(rest))
    ops._queue_uuid = "QUEUE-UUID"
    return ops, rest


def test_pause_queue_puts_to_control_endpoint():
    ops, rest = _ops_with_known_uuid()
    assert ops.pause_queue() is True
    rest.request.assert_called_once()
    method, path = rest.request.call_args.args[:2]
    assert method == "PUT"
    assert path == "/JQ/JobQueue/QUEUE-UUID/control/pause"


def test_resume_queue_puts_to_control_endpoint():
    ops, rest = _ops_with_known_uuid()
    assert ops.resume_queue() is True
    method, path = rest.request.call_args.args[:2]
    assert method == "PUT"
    assert path == "/JQ/JobQueue/QUEUE-UUID/control/resume"


def test_cancel_job_returns_true_on_2xx():
    ops, rest = _ops_with_known_uuid()
    assert ops.cancel_job("JOB-X") is True
    method, path = rest.request.call_args.args[:2]
    assert method == "PUT"
    assert path == "/JQ/JobQueue/QUEUE-UUID/Job/JOB-X/control/cancel"


def test_cancel_job_returns_false_on_502():
    rest = MagicMock()
    rest.request.side_effect = Z9RESTError(502, "/", "Bad Gateway")
    ops = JobQueueOps(_make_client(rest))
    ops._queue_uuid = "QUEUE-UUID"
    # 502 = job already Deleted, treated as no-op → False but no exception
    assert ops.cancel_job("JOB-DELETED") is False


def test_cancel_job_raises_on_non_502_error():
    rest = MagicMock()
    rest.request.side_effect = Z9RESTError(500, "/", "Internal Error")
    ops = JobQueueOps(_make_client(rest))
    ops._queue_uuid = "QUEUE-UUID"
    with pytest.raises(Z9RESTError):
        ops.cancel_job("JOB-X")


def test_remove_job_passes_uuid_in_query_string():
    """HP firmware quirk: removeJob expects the UUID in query, not in path."""
    ops, rest = _ops_with_known_uuid()
    assert ops.remove_job("JOB-Y") is True
    method, path = rest.request.call_args.args[:2]
    assert method == "PUT"
    assert path == "/JQ/JobQueue/QUEUE-UUID/control/removeJob?UUID=JOB-Y"


def test_remove_job_returns_false_on_502():
    rest = MagicMock()
    rest.request.side_effect = Z9RESTError(502, "/", "Bad Gateway")
    ops = JobQueueOps(_make_client(rest))
    ops._queue_uuid = "QUEUE-UUID"
    assert ops.remove_job("ALREADY-GONE") is False


def test_get_job_preview_uses_admin_auth_and_raw():
    ops, rest = _ops_with_known_uuid()
    rest.get.return_value = b"\x89PNG\r\n..."  # bytes simulating a PNG
    out = ops.get_job_preview("JOB-Z", page=1)
    assert out == b"\x89PNG\r\n..."
    # Check the path + auth flags
    rest.get.assert_called_once_with(
        "/JQ/JobQueue/QUEUE-UUID/Job/JOB-Z/Page/1/resources/preview",
        auth=True, raw=True,
    )


# ─── Reprint ──────────────────────────────────────────────────────────


_REPRINT_RESPONSE_OK = (
    "<?xml version='1.0' encoding='UTF-8'?>"
    "<JobReprintResponse xmlns='http://www.hp.com/schemas/piws/v2_0'>"
    "<QueueUUID>5805be78-cd86-4875-afb9-9c21113dcca0</QueueUUID>"
    "<JobUUID>5CC83FA2-1234-4ABC-9DEF-000000000001</JobUUID>"
    "<ResponseStatus>SuccessfullySubmitted</ResponseStatus>"
    "</JobReprintResponse>"
)

# Variant with the HP comment that the firmware systematically
# inserts in its XML responses (cf. fixture jobs_all_sample_real.xml).
# Regression of the live bug 24/05/2026: without skipping Comment in
# parent.iter(), _find_local crashed with TypeError on the cython
# function _Comment.
_REPRINT_RESPONSE_WITH_COMMENT = (
    "<?xml version='1.0' encoding='UTF-8'?>"
    "<JobReprintResponse xmlns='http://www.hp.com/schemas/piws/v2_0'>"
    "<!--\nTHIS DATA IS SUBJECT TO DISCLAIMER(S) INCLUDED WITH THE PRODUCT OF ORIGIN.\n-->"
    "<QueueUUID>5805be78-cd86-4875-afb9-9c21113dcca0</QueueUUID>"
    "<JobUUID>5CC83FA2-1234-4ABC-9DEF-000000000001</JobUUID>"
    "<ResponseStatus>SuccessfullySubmitted</ResponseStatus>"
    "</JobReprintResponse>"
)

_REPRINT_RESPONSE_REFUSED = (
    "<?xml version='1.0' encoding='UTF-8'?>"
    "<JobReprintResponse xmlns='http://www.hp.com/schemas/piws/v2_0'>"
    "<QueueUUID>U</QueueUUID><JobUUID>J</JobUUID>"
    "<ResponseStatus>FailedToSubmit</ResponseStatus>"
    "</JobReprintResponse>"
)


def test_reprint_job_posts_correct_xml_payload():
    rest = MagicMock()
    rest.request.return_value = _REPRINT_RESPONSE_OK
    ops, rest = _ops_with_known_uuid(rest)

    out = ops.reprint_job("5CC83FA2-1234-4ABC-9DEF-000000000001", copies=1)
    assert out["status"] == "SuccessfullySubmitted"
    assert out["original_uuid"] == "5CC83FA2-1234-4ABC-9DEF-000000000001"

    # Check the request: POST on the endpoint without UUID in path
    method, path = rest.request.call_args.args[:2]
    assert method == "POST"
    assert path == "/JQ/JobQueue/Job/control/reprint"

    # Headers: Content-Type text/xml (not application/xml)
    kwargs = rest.request.call_args.kwargs
    headers = kwargs.get("extra_headers", {})
    assert headers.get("Content-Type") == "text/xml"

    # Body: jq: namespace and all required fields
    body = kwargs.get("data", b"")
    body_str = body.decode("utf-8") if isinstance(body, bytes) else body
    assert "xmlns:jq='http://www.hp.com/jq'" in body_str
    assert "<jq:JobReprintRequest" in body_str
    assert "<QueueUUID>QUEUE-UUID</QueueUUID>" in body_str
    assert "<JobUUID>5CC83FA2-1234-4ABC-9DEF-000000000001</JobUUID>" in body_str
    assert "<Copies>1</Copies>" in body_str


def test_reprint_job_passes_custom_copies():
    rest = MagicMock()
    rest.request.return_value = _REPRINT_RESPONSE_OK
    ops, rest = _ops_with_known_uuid(rest)
    ops.reprint_job("JOB-X", copies=5)

    body = rest.request.call_args.kwargs["data"].decode("utf-8")
    assert "<Copies>5</Copies>" in body


def test_reprint_job_raises_when_firmware_refuses():
    rest = MagicMock()
    rest.request.return_value = _REPRINT_RESPONSE_REFUSED
    ops, rest = _ops_with_known_uuid(rest)
    with pytest.raises(Z9Error, match="Reprint refused"):
        ops.reprint_job("JOB-X")


def test_reprint_response_with_hp_disclaimer_comment_parses_ok():
    """Regression of the live bug 24/05/2026: the Z9 firmware inserts a
    comment ``<!-- THIS DATA IS SUBJECT TO DISCLAIMER ... -->``
    in all its XML responses. ``parent.iter()`` ALSO traverses
    comments, and their ``.tag`` is not a str but the
    Cython factory ``Comment`` → TypeError on ``"}" in tag``.

    The fix must skip non-element nodes (comments + PI). We
    check here that parsing correctly extracts the 3 fields
    despite the presence of the comment."""
    rest = MagicMock()
    rest.request.return_value = _REPRINT_RESPONSE_WITH_COMMENT
    ops, rest = _ops_with_known_uuid(rest)

    out = ops.reprint_job("5CC83FA2-1234-4ABC-9DEF-000000000001", copies=1)
    assert out["status"] == "SuccessfullySubmitted"
    assert out["queue_uuid"] == "5805be78-cd86-4875-afb9-9c21113dcca0"
    assert out["original_uuid"] == "5CC83FA2-1234-4ABC-9DEF-000000000001"


def test_reprint_job_raises_on_malformed_response():
    rest = MagicMock()
    rest.request.return_value = "<not valid xml"
    ops, rest = _ops_with_known_uuid(rest)
    with pytest.raises(Z9Error, match="Malformed"):
        ops.reprint_job("JOB-X")


def test_find_new_reprint_job_detects_new_uuid():
    rest = MagicMock()
    rest.get.side_effect = [
        # 1st poll: a new job appeared, more recent mod number
        (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<piws:JobQueueSnapshot xmlns="http://www.hp.com/schemas/piws/v2_0"'
            ' xmlns:piws="http://www.hp.com/schemas/piws/v2_0">'
            '<ModificationNumber>100</ModificationNumber>'
            '<Status><JobQueueStatus>Running</JobQueueStatus>'
            '<NumberOfJobs>2</NumberOfJobs></Status>'
            '<Timestamp>2026-05-24T10:00:00Z</Timestamp>'
            '<Jobs>'
            '<Job><Job2DPrint><UUID>OLD-1</UUID></Job2DPrint></Job>'
            '<Job><Job2DPrint><UUID>NEW-REPRINT</UUID></Job2DPrint></Job>'
            '</Jobs></piws:JobQueueSnapshot>'
        ),
    ]
    ops, rest = _ops_with_known_uuid(rest)

    new_uuid = ops.find_new_reprint_job(
        before_uuids={"OLD-1"}, before_mod_number=50,
        poll_interval_s=0.01, timeout_s=0.5,
    )
    assert new_uuid == "NEW-REPRINT"


# ─── Clear queue via DELETE loop (pivot, 25/05/2026) ──────────────────


def test_clear_all_loops_remove_job_per_uuid():
    """clear_all() loops remove_job over each non-Deleted UUID."""
    rest = MagicMock()
    rest.get.return_value = _JOBS_XML_SAMPLE  # 6 jobs, 1 of them Deleted
    ops = JobQueueOps(_make_client(rest))
    ops._queue_uuid = "QUEUE-UUID"

    removed, failed = ops.clear_all(initial_delay_s=0)

    # 5 non-Deleted jobs in the fixture → 5 individual PUTs
    assert removed == 5
    assert failed == 0
    # Check that we made 5 PUT /control/removeJob calls
    put_calls = [c for c in rest.request.call_args_list if c.args[0] == "PUT"]
    assert len(put_calls) == 5
    # The Deleted job must NOT be in the calls
    paths = [c.args[1] for c in put_calls]
    for p in paths:
        assert "04D86CBA-C93C-4BCA-B7AF-1C03CC76CE31" not in p


def test_clear_all_continues_on_partial_failure():
    """If remove_job raises on a job, we move to the next and count
    it in failed_count."""
    rest = MagicMock()
    rest.get.return_value = _JOBS_XML_SAMPLE

    # Side effect: 2nd remove_job raises, the others OK
    call_count = {"n": 0}
    def request_side_effect(*args, **kwargs):
        if args[0] == "PUT":
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise Z9RESTError(500, "/x", "fail synthétique")
        return None
    rest.request.side_effect = request_side_effect

    ops = JobQueueOps(_make_client(rest))
    ops._queue_uuid = "QUEUE-UUID"
    removed, failed = ops.clear_all(initial_delay_s=0)

    # 5 attempts total, 1 failure → 4 removed, 1 failed
    assert removed == 4
    assert failed == 1


def test_clear_all_no_jobs_returns_zero_zero():
    """Empty queue or only Deleted jobs → (0, 0) without firmware call."""
    rest = MagicMock()
    rest.get.return_value = (
        '<piws:JobQueueSnapshot xmlns="http://www.hp.com/schemas/piws/v2_0"'
        ' xmlns:piws="http://www.hp.com/schemas/piws/v2_0">'
        '<ModificationNumber>1</ModificationNumber>'
        '<Status><JobQueueStatus>Running</JobQueueStatus>'
        '<NumberOfJobs>0</NumberOfJobs></Status>'
        '<Timestamp>X</Timestamp><Jobs></Jobs></piws:JobQueueSnapshot>'
    )
    ops = JobQueueOps(_make_client(rest))
    ops._queue_uuid = "QUEUE-UUID"
    removed, failed = ops.clear_all(initial_delay_s=0)
    assert (removed, failed) == (0, 0)
    # No PUT call
    put_calls = [c for c in rest.request.call_args_list if c.args[0] == "PUT"]
    assert put_calls == []


def _xml_snapshot_with_jobs(uuids: list[str]) -> str:
    """Helper: build an XML snapshot with N Pending jobs."""
    jobs_xml = "".join(
        '<Job><Job2DPrint><UUID>{u}</UUID>'
        '<Status><JobStatus>Pending</JobStatus></Status>'
        '</Job2DPrint></Job>'.format(u=u)
        for u in uuids
    )
    return (
        '<piws:JobQueueSnapshot xmlns="http://www.hp.com/schemas/piws/v2_0"'
        ' xmlns:piws="http://www.hp.com/schemas/piws/v2_0">'
        '<ModificationNumber>1</ModificationNumber>'
        f'<Status><JobQueueStatus>Running</JobQueueStatus>'
        f'<NumberOfJobs>{len(uuids)}</NumberOfJobs></Status>'
        '<Timestamp>X</Timestamp>'
        f'<Jobs>{jobs_xml}</Jobs></piws:JobQueueSnapshot>'
    )


def test_clear_all_uses_short_initial_delay(monkeypatch):
    """initial_delay_s=200ms by default (user tuning 25/05/2026:
    1.5 s × 50 jobs = 75 s = unusable). 3 consecutive OK jobs:
    each success decelerates slightly (delay × 0.9 but clamped to
    initial_delay_s) — so we stay at 200ms."""
    rest = MagicMock()
    rest.get.return_value = _xml_snapshot_with_jobs(["U1", "U2", "U3"])
    sleep_calls = []
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda s: sleep_calls.append(s))

    ops = JobQueueOps(_make_client(rest))
    ops._queue_uuid = "QUEUE-UUID"
    removed, failed = ops.clear_all(initial_delay_s=0.2)
    assert removed == 3
    # 3 sleeps of 200ms (never below, success keeps initial)
    assert sleep_calls == [0.2, 0.2, 0.2]


def test_clear_all_backoff_on_503(monkeypatch):
    """On 503 Service Unavailable, the delay doubles and we retry the
    same job once before moving to the next."""
    rest = MagicMock()
    rest.get.return_value = _xml_snapshot_with_jobs(["U1", "U2"])

    # 1st PUT (U1) → 503, retry → OK. 2nd PUT (U2) → OK.
    call_log = []
    def request_side_effect(method, path, *args, **kwargs):
        call_log.append((method, path))
        if method == "PUT" and "UUID=U1" in path and len(call_log) == 1:
            # First attempt on U1 → 503
            raise Z9RESTError(503, path, "service unavailable")
        return None
    rest.request.side_effect = request_side_effect

    sleep_calls = []
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda s: sleep_calls.append(s))

    ops = JobQueueOps(_make_client(rest))
    ops._queue_uuid = "QUEUE-UUID"
    removed, failed = ops.clear_all(
        initial_delay_s=0.2, backoff_factor=2.0, max_delay_s=2.0,
    )
    # U1 was retried after backoff → 2 successes total
    assert removed == 2
    assert failed == 0
    # Sleeps: backoff 0.4 (before retry) + 0.4 (post U1 success, no
    # decay yet since decay = 0.4*0.9 = 0.36, clamp min initial 0.2 →
    # next iteration sleep = 0.36) + decay U2 success
    # Check at least: 3 sleeps total (1 backoff + 2 post-job)
    assert len(sleep_calls) == 3
    # 1st sleep = backoff (delay doubled after 503)
    assert sleep_calls[0] == pytest.approx(0.4, abs=0.01)


def test_clear_all_backoff_fails_when_retry_also_throttles(monkeypatch):
    """If even the retry after backoff fails, we count it as failed
    and move to the next (no infinite loop)."""
    rest = MagicMock()
    rest.get.return_value = _xml_snapshot_with_jobs(["U1", "U2"])

    def request_side_effect(method, path, *args, **kwargs):
        if method == "PUT" and "UUID=U1" in path:
            # All attempts on U1 → 503
            raise Z9RESTError(503, path, "service unavailable")
        return None  # U2 OK
    rest.request.side_effect = request_side_effect

    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda s: None)

    ops = JobQueueOps(_make_client(rest))
    ops._queue_uuid = "QUEUE-UUID"
    removed, failed = ops.clear_all()
    assert removed == 1  # U2 only
    assert failed == 1  # U1 failed even after retry


def test_clear_all_non_throttle_error_no_retry(monkeypatch):
    """A NON-throttle firmware error (e.g. 500) does not trigger
    a retry — we count failed and move directly to the next."""
    rest = MagicMock()
    rest.get.return_value = _xml_snapshot_with_jobs(["U1", "U2"])

    call_count = {"U1_attempts": 0}
    def request_side_effect(method, path, *args, **kwargs):
        if method == "PUT" and "UUID=U1" in path:
            call_count["U1_attempts"] += 1
            raise Z9RESTError(500, path, "server error")
        return None
    rest.request.side_effect = request_side_effect

    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda s: None)

    ops = JobQueueOps(_make_client(rest))
    ops._queue_uuid = "QUEUE-UUID"
    removed, failed = ops.clear_all()
    assert removed == 1  # U2
    assert failed == 1  # U1 (no retry)
    assert call_count["U1_attempts"] == 1  # a single attempt


def test_find_new_reprint_job_returns_none_on_timeout():
    rest = MagicMock()
    # Mod number does not advance → we time out
    stale_xml = (
        '<piws:JobQueueSnapshot xmlns="http://www.hp.com/schemas/piws/v2_0"'
        ' xmlns:piws="http://www.hp.com/schemas/piws/v2_0">'
        '<ModificationNumber>50</ModificationNumber>'
        '<Status><JobQueueStatus>Running</JobQueueStatus>'
        '<NumberOfJobs>1</NumberOfJobs></Status>'
        '<Timestamp>X</Timestamp>'
        '<Jobs><Job><Job2DPrint><UUID>OLD-1</UUID></Job2DPrint></Job></Jobs>'
        '</piws:JobQueueSnapshot>'
    )
    rest.get.return_value = stale_xml
    ops, rest = _ops_with_known_uuid(rest)
    out = ops.find_new_reprint_job(
        before_uuids={"OLD-1"}, before_mod_number=50,
        poll_interval_s=0.01, timeout_s=0.05,
    )
    assert out is None


def test_operations_trigger_discovery_when_uuid_unknown():
    """If we haven't discovered the UUID yet, the operations must do it."""
    rest = MagicMock()
    rest.get.return_value = _COLLECTION_JSON
    ops = JobQueueOps(_make_client(rest))
    assert ops.queue_uuid is None

    ops.pause_queue()
    # discovery + pause = 2 REST calls (get for discovery, request for pause)
    rest.get.assert_called_once_with(
        "/JQ/JobQueueCollection",
        extra_headers={"Accept": "application/json"},
    )
    rest.request.assert_called_once()
    # The UUID is indeed discovered
    assert ops.queue_uuid == "5805be78-cd86-4875-afb9-9c21113dcca0"
