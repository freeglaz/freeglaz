"""Tests P4 — wake_z9 service + /api/wake route."""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests
from fastapi.testclient import TestClient

from webapp.backend.main import app
from webapp.backend.routes.status import get_z9
from webapp.backend.services import wake_service


@pytest.fixture(autouse=True)
def _short_timeouts(monkeypatch):
    """Reduce service timings for sub-second tests."""
    monkeypatch.setattr(wake_service, "ROOT_PROBE_TIMEOUT_S", 1)
    monkeypatch.setattr(wake_service, "POLL_INTERVAL_S", 0.05)
    monkeypatch.setattr(wake_service, "POLL_TIMEOUT_S", 0.5)
    yield


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _fake_session(responses):
    """Build a fake HTTPS session that returns the successive responses
    passed as argument. Each entry:
      - int: HTTP code, mock SimpleNamespace
      - Exception: raised by ``get()`` / ``post()``

    get() and post() consume the same queue to keep the mock simple.
    """
    session = MagicMock()
    iter_responses = iter(responses)

    def respond(url, **kwargs):
        item = next(iter_responses)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(status_code=item)
    session.get  = MagicMock(side_effect=respond)
    session.post = MagicMock(side_effect=respond)
    return session


# --- _trigger_wakeup (POST /wakeup.htm/config) ------------------------


def test_trigger_wakeup_returns_true_on_405(monkeypatch):
    """Code most frequently returned in practice (24/05/2026
    empirical): EmWeb replies 405 Method Not Allowed but still wakes
    the Z9. To be treated as a success."""
    monkeypatch.setattr(wake_service, "make_z9_session",
                        lambda: _fake_session([405]))
    assert wake_service._trigger_wakeup("192.168.1.50") is True


def test_trigger_wakeup_returns_true_on_200_or_302_303(monkeypatch):
    """Case where the correct body was found and EmWeb accepts it
    cleanly (302/303 = redirect after form submission, 200 =
    direct acknowledgement)."""
    for code in (200, 302, 303):
        monkeypatch.setattr(wake_service, "make_z9_session",
                            lambda c=code: _fake_session([c]))
        assert wake_service._trigger_wakeup("192.168.1.50") is True


def test_trigger_wakeup_returns_false_on_unexpected_code(monkeypatch):
    """Unlisted code (404, 500, 503...) = wakeup not confirmed."""
    monkeypatch.setattr(wake_service, "make_z9_session",
                        lambda: _fake_session([404]))
    assert wake_service._trigger_wakeup("192.168.1.50") is False


def test_trigger_wakeup_returns_false_on_tcp_timeout(monkeypatch):
    monkeypatch.setattr(
        wake_service, "make_z9_session",
        lambda: _fake_session([requests.exceptions.ConnectTimeout()]),
    )
    assert wake_service._trigger_wakeup("192.168.1.50") is False


def test_trigger_wakeup_returns_false_on_host_down(monkeypatch):
    monkeypatch.setattr(
        wake_service, "make_z9_session",
        lambda: _fake_session([requests.exceptions.ConnectionError("Host is down")]),
    )
    assert wake_service._trigger_wakeup("192.168.1.50") is False


def test_trigger_wakeup_sends_correct_payload(monkeypatch):
    """Verify we POST to /wakeup.htm/config with the form-urlencoded
    body and the expected headers (Referer + Content-Type)."""
    captured = {}
    session = MagicMock()

    def post(url, **kwargs):
        captured["url"] = url
        captured["data"] = kwargs.get("data")
        captured["headers"] = kwargs.get("headers", {})
        return SimpleNamespace(status_code=405)

    session.post = MagicMock(side_effect=post)
    monkeypatch.setattr(wake_service, "make_z9_session", lambda: session)
    wake_service._trigger_wakeup("192.168.1.50")

    assert captured["url"] == "https://192.168.1.50/wakeup.htm/config"
    assert "hidden_wakeup_Addr=" in captured["data"]
    assert "WakeUp=" in captured["data"]  # "Reactivation" url-encoded
    assert captured["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert captured["headers"]["Referer"] == "https://192.168.1.50/"


# --- _ping_piws -------------------------------------------------------


def test_ping_piws_returns_true_on_200(monkeypatch):
    monkeypatch.setattr(wake_service, "make_z9_session",
                        lambda: _fake_session([200]))
    assert wake_service._ping_piws("192.168.1.50") is True


def test_ping_piws_returns_false_on_503(monkeypatch):
    """REST 503 = services not yet available, return False."""
    monkeypatch.setattr(wake_service, "make_z9_session",
                        lambda: _fake_session([503]))
    assert wake_service._ping_piws("192.168.1.50") is False


# --- wake_z9 pipeline -------------------------------------------------


def _setup_seq(monkeypatch, wakeup_codes, piws_codes):
    """Helper: monkeypatch a sequence of codes (wakeup first, then
    PIWS polls in a loop until exhausted)."""
    sessions = []
    for code in wakeup_codes:
        sessions.append(_fake_session([code]))
    for code in piws_codes:
        sessions.append(_fake_session([code]))
    iter_sessions = iter(sessions)
    monkeypatch.setattr(
        wake_service, "make_z9_session",
        lambda: next(iter_sessions),
    )


@pytest.mark.asyncio
async def test_wake_z9_succeeds_on_first_piws_200(monkeypatch):
    """Wakeup 405 (typical EmWeb case) + immediate PIWS 200 -> awake."""
    _setup_seq(monkeypatch, wakeup_codes=[405], piws_codes=[200])
    result = await wake_service.wake_z9("192.168.1.50")
    assert result["status"] == "awake"
    assert result["elapsed_seconds"] >= 0


@pytest.mark.asyncio
async def test_wake_z9_succeeds_after_a_few_503(monkeypatch):
    """Wakeup OK then PIWS 503, 503, 200 — wake in 2 polls (~ 0.1 s)."""
    _setup_seq(
        monkeypatch,
        wakeup_codes=[405],
        piws_codes=[503, 503, 200],
    )
    result = await wake_service.wake_z9("192.168.1.50")
    assert result["status"] == "awake"


@pytest.mark.asyncio
async def test_wake_z9_timeout_when_piws_never_200(monkeypatch):
    """Wakeup triggered but PIWS stays at 503 -> timeout after POLL_TIMEOUT_S."""
    monkeypatch.setattr(wake_service, "_trigger_wakeup", lambda host: True)
    monkeypatch.setattr(wake_service, "_ping_piws", lambda host: False)
    result = await wake_service.wake_z9("192.168.1.50")
    assert result["status"] == "timeout"
    assert "REST" in result["detail"]
    # elapsed >= POLL_TIMEOUT_S (with tolerance)
    assert result["elapsed_seconds"] >= 0.4


@pytest.mark.asyncio
async def test_wake_z9_unreachable_when_wakeup_fails(monkeypatch):
    """POST wakeup fails (TCP timeout) -> unreachable, no PIWS polling."""
    monkeypatch.setattr(wake_service, "_trigger_wakeup", lambda host: False)
    piws_calls = []
    monkeypatch.setattr(
        wake_service, "_ping_piws",
        lambda host: (piws_calls.append(1), False)[1],
    )
    result = await wake_service.wake_z9("192.168.1.50")
    assert result["status"] == "unreachable"
    assert "TCP" in result["detail"] or "réseau" in result["detail"]
    assert piws_calls == []  # PIWS never polled


# --- /api/wake route --------------------------------------------------


def test_wake_endpoint_returns_awake_status(monkeypatch):
    z9 = SimpleNamespace(host="192.168.1.50")
    app.dependency_overrides[get_z9] = lambda: z9
    monkeypatch.setattr(wake_service, "_trigger_wakeup", lambda host: True)
    monkeypatch.setattr(wake_service, "_ping_piws", lambda host: True)
    with TestClient(app) as client:
        r = client.post("/api/wake")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "awake"


def test_wake_endpoint_returns_unreachable_when_apache_dead(monkeypatch):
    z9 = SimpleNamespace(host="192.168.1.50")
    app.dependency_overrides[get_z9] = lambda: z9
    monkeypatch.setattr(wake_service, "_trigger_wakeup", lambda host: False)
    with TestClient(app) as client:
        r = client.post("/api/wake")
    assert r.status_code == 200  # HTTP 200 even on failure, status=unreachable
    body = r.json()
    assert body["status"] == "unreachable"


def test_wake_endpoint_returns_timeout_when_piws_never_200(monkeypatch):
    z9 = SimpleNamespace(host="192.168.1.50")
    app.dependency_overrides[get_z9] = lambda: z9
    monkeypatch.setattr(wake_service, "_trigger_wakeup", lambda host: True)
    monkeypatch.setattr(wake_service, "_ping_piws", lambda host: False)
    with TestClient(app) as client:
        r = client.post("/api/wake")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "timeout"


def test_wake_endpoint_returns_503_when_z9_not_configured():
    app.dependency_overrides[get_z9] = lambda: None
    with TestClient(app) as client:
        r = client.post("/api/wake")
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_wake_z9_does_not_block_other_routes(monkeypatch):
    """During the 30 s polling, the event loop stays free for other
    routes. Simplified test: launch wake_z9 + another coroutine in
    parallel and verify the 2nd does not wait for the 1st."""
    monkeypatch.setattr(wake_service, "_trigger_wakeup", lambda host: True)
    monkeypatch.setattr(wake_service, "_ping_piws", lambda host: False)
    # POLL_TIMEOUT_S already at 0.5 s via fixture

    async def fast_task():
        await asyncio.sleep(0.05)
        return "done"

    wake_task = asyncio.create_task(wake_service.wake_z9("192.168.1.50"))
    other = await fast_task()
    assert other == "done"  # does not wait for wake_z9
    wake_result = await wake_task
    assert wake_result["status"] == "timeout"
