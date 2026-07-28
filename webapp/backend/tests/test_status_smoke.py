"""Smoke tests for the /api/status endpoint (Z9Client mocked) + SSE."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from lib.z9_client.exceptions import Z9ConnectionError
from webapp.backend.main import app
from webapp.backend.routes.status import get_z9


class _FakeDevice:
    def status(self):
        return {
            "identification": {
                "ModelName": "HP DesignJet Z9 24in",
                "SerialNumber": "CNXXXXXXXX",
                "FwReleaseName": "JGR9_09_26_06.1",
            },
            "loaded_paper_id": None,
            "loaded_paper_name": None,
            "loaded_paper_source": None,
            "loaded_paper_source_label": None,
            "loaded_paper_width_mm": None,
            "loaded_paper_length_mm": None,
            "ink_levels": {"magenta": 72.0, "post-treatment": 12.0},
            "ink_warnings": [
                {"color": "post-treatment", "state": "Warning", "status": "GroupLow"},
            ],
            "global_status": "WithAlerts",
        }

    def device_status(self):
        return {
            "ActivitiesOverview": {"MostRelevantActivity": {"Name": "NoActivity"}},
        }


class _FakePaper:
    def get(self, _id):
        return None

    def capabilities(self, _id):
        return None


def _fake_z9():
    return SimpleNamespace(device=_FakeDevice(), paper=_FakePaper())


@pytest.fixture
def client():
    app.state.capabilities_cache = {}
    app.dependency_overrides[get_z9] = _fake_z9
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_status_no_paper_returns_alerts(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.json()

    assert data["model"] == "HP DesignJet Z9 24in"
    assert data["serial"] == "CNXXXXXXXX"
    assert data["firmware"] == "JGR9_09_26_06.1"
    assert data["loaded_paper"] is None
    assert data["state_text"] == "Active alerts"
    assert data["ready"] is False

    inks = {i["color"]: i for i in data["inks"]}
    assert inks["magenta"]["level_pct"] == 72
    assert inks["magenta"]["state"] == "ok"
    assert inks["post-treatment"]["state"] == "warning"
    assert inks["post-treatment"]["label"] == "Gloss enhancer"

    codes = [a["code"] for a in data["alerts"]]
    assert "NO_PAPER" in codes
    assert "INK_POST-TREATMENT" in codes


def test_status_includes_argyll_field(client):
    # Argyll availability (resolved at startup) is surfaced on every Status.
    from webapp.backend.models import ArgyllStatus
    app.state.argyll = ArgyllStatus(
        ok=True, bin_ok=True, ref_ok=True, missing=[],
        bin_dir="/opt/homebrew/bin", ref_dir="/opt/homebrew/opt/argyll-cms/ref")
    data = client.get("/api/status").json()
    assert data["argyll"] == {
        "ok": True, "bin_ok": True, "ref_ok": True, "missing": [],
        "bin_dir": "/opt/homebrew/bin", "ref_dir": "/opt/homebrew/opt/argyll-cms/ref"}


def test_status_argyll_missing_reported(client):
    from webapp.backend.models import ArgyllStatus
    app.state.argyll = ArgyllStatus(
        ok=False, bin_ok=True, ref_ok=False, missing=["ref"])
    data = client.get("/api/status").json()
    assert data["argyll"]["ok"] is False
    assert data["argyll"]["missing"] == ["ref"]


def test_status_z9_unreachable(client):
    def _raise():
        raise Z9ConnectionError("timeout")

    broken = SimpleNamespace(
        device=SimpleNamespace(status=_raise, device_status=lambda: {}),
        paper=_FakePaper(),
    )
    app.dependency_overrides[get_z9] = lambda: broken

    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert data["ready"] is False
    assert data["state_text"] == "Z9 unreachable"
    assert data["alerts"][0]["severity"] == "error"
    assert data["alerts"][0]["code"] == "Z9_UNREACHABLE"


# ═══════════════════════════════════════════════════════════════════════
# SSE — /api/status/events
# ═══════════════════════════════════════════════════════════════════════


class _FakePushyFakeSubscriber:
    """Fake subscriber that immediately pushes N events on subscribe.

    Simulates a subscriber that would already have a snapshot + pending
    updates — useful for testing the SSE route without depending on the
    real timing of an asyncio loop.
    """
    def __init__(self, initial: dict, pending: list[tuple[str, dict]] = None):
        self._initial = initial
        self._pending = pending or []

    def subscribe(self, cb):
        cb("status_full", dict(self._initial))
        for event_type, data in self._pending:
            cb(event_type, data)

    def unsubscribe(self, cb):
        pass

    def current_snapshot(self):
        return dict(self._initial)


_FAKE_DASHBOARD = {
    "identification": {
        "ModelName": "HP DesignJet Z9 24in",
        "SerialNumber": "CNXXXXXXXX",
        "FwReleaseName": "JGR9_09_26_06.1",
    },
    "loaded_paper_id": None,
    "loaded_paper_name": None,
    "loaded_paper_source": None,
    "loaded_paper_source_label": None,
    "loaded_paper_width_mm": None,
    "loaded_paper_length_mm": None,
    "ink_levels": {"magenta": 72.0},
    "ink_warnings": [],
    "global_status": "Ready",
}


def _parse_sse_block(line_iter):
    """Read until a complete SSE block (event: + data: + blank line).

    httpx.iter_lines() may return lines with a residual \\r (SSE spec
    uses \\r\\n) -> explicit strip before the "blank line" test.
    """
    event_name, data_str = None, None
    for raw_line in line_iter:
        line = raw_line.rstrip("\r\n")
        if not line:
            if event_name is not None or data_str is not None:
                return event_name, data_str
            continue
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_str = line.split(":", 1)[1].strip()
    return event_name, data_str


def test_sse_status_unconfigured_emits_valid_status_full(client):
    """If z9_status_subscriber is None (no printer configured), the SSE route
    no longer returns 503 (legitimate state != error): it emits a VALID stream
    whose 1st event is a "not configured" status_full (z9_configured=false) -> the
    front triggers onboarding. We consume ONE event from the generator (without
    going through the HTTP stream which hangs under TestClient, see next test)."""
    import asyncio
    from starlette.requests import Request
    from webapp.backend.routes import status as status_mod
    # The client fixture ran the lifespan; we override AFTER.
    app.state.z9_status_subscriber = None

    async def _first_event():
        scope = {"type": "http", "app": app, "method": "GET",
                 "headers": [], "query_string": b"", "path": "/api/status/events"}
        req = Request(scope, receive=lambda: None)
        resp = await status_mod.stream_status_events(req)
        assert resp.status_code == 200            # no more 503
        async for chunk in resp.body_iterator:
            return chunk

    chunk = asyncio.run(_first_event())
    text = chunk.decode() if isinstance(chunk, (bytes, bytearray)) else str(chunk)
    assert "status_full" in text
    assert '"z9_configured":false' in text


def test_sse_status_subscribes_and_pushes_snapshot(client):
    """Verify WITHOUT the HTTP layer (which hangs on stream close with
    sse-starlette+httpx TestClient) that the route does call
    sub.subscribe -> cb("status_full", snapshot) -> mapped via
    _dashboard_to_status. Unit test of the subscribe path.

    The end-to-end streaming scenario is validated live via:
        curl -N http://127.0.0.1:8765/api/status/events
    """
    from webapp.backend.routes.status import _dashboard_to_status

    sub = _FakePushyFakeSubscriber(_FAKE_DASHBOARD)
    received: list[tuple[str, dict]] = []
    sub.subscribe(lambda et, d: received.append((et, d)))

    # subscribe() immediately pushes a status_full
    assert len(received) == 1
    event_type, dashboard = received[0]
    assert event_type == "status_full"

    # The mapper correctly transforms the dashboard into a conforming Status
    status = _dashboard_to_status(dashboard, z9=None, caps_cache={})
    assert status.model == "HP DesignJet Z9 24in"
    assert status.serial == "CNXXXXXXXX"
    assert status.loaded_paper is None
    assert any(i.color == "magenta" for i in status.inks)


def test_sse_status_subscribe_pending_events_propagate(client):
    """Same but with pending events in the subscriber queue."""
    from webapp.backend.routes.status import _dashboard_to_status

    updated = dict(_FAKE_DASHBOARD)
    updated["ink_levels"] = {"magenta": 30.0, "yellow": 50.0}
    updated["ink_warnings"] = [
        {"color": "magenta", "state": "Warning", "status": "GroupLow"},
    ]
    sub = _FakePushyFakeSubscriber(
        _FAKE_DASHBOARD,
        pending=[("status_diff", updated)],
    )
    received: list[tuple[str, dict]] = []
    sub.subscribe(lambda et, d: received.append((et, d)))

    # Must have received status_full THEN status_diff in order
    assert [r[0] for r in received] == ["status_full", "status_diff"]

    # The diff correctly mapped: magenta goes to warning
    status_diff = _dashboard_to_status(received[1][1], z9=None, caps_cache={})
    inks_by_color = {i.color: i for i in status_diff.inks}
    assert inks_by_color["magenta"].level_pct == 30
    assert inks_by_color["magenta"].state == "warning"
    assert any(a.code == "INK_MAGENTA" for a in status_diff.alerts)
