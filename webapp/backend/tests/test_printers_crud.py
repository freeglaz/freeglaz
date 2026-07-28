"""Printer CRUD routes + z9_configured signal (IP setup, IP 3/3, C0)."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from lib.z9_client.exceptions import Z9ConnectionError
from webapp.backend.main import app
from webapp.backend.routes.status import get_z9


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    yield
    app.dependency_overrides.clear()


# --- CRUD -------------------------------------------------------------


def test_crud_lifecycle():
    with TestClient(app) as c:
        assert c.get("/api/printers").json() == {"printers": [], "active": None}

        # 1st printer -> active by default (onboarding)
        r = c.post("/api/printers", json={"ip": "192.168.1.50", "serial": "S1",
                                          "name": "Z9", "model_support": "validated"})
        assert r.status_code == 201
        assert r.json()["active"] is True
        assert r.json()["model_support"] == "validated"

        # 2nd -> not active
        c.post("/api/printers", json={"ip": "192.168.1.28", "serial": "S2", "name": "B"})
        body = c.get("/api/printers").json()
        assert len(body["printers"]) == 2
        assert body["active"]["serial"] == "S1"

        # activate S2 -> switch
        assert c.post("/api/printers/S2/activate").json()["active"]["serial"] == "S2"

        # edit ip/name
        r = c.put("/api/printers/S1", json={"ip": "192.168.1.99", "name": "renamed"})
        assert r.json()["ip"] == "192.168.1.99" and r.json()["name"] == "renamed"

        # delete
        assert c.delete("/api/printers/S1").json() == {"removed": "S1"}
        assert [p["serial"] for p in c.get("/api/printers").json()["printers"]] == ["S2"]


def test_crud_404_on_unknown():
    with TestClient(app) as c:
        assert c.put("/api/printers/NOPE", json={"ip": "x"}).status_code == 404
        assert c.post("/api/printers/NOPE/activate").status_code == 404
        assert c.delete("/api/printers/NOPE").status_code == 404


def test_admin_pwd_stored_but_redacted_over_api():
    from lib.z9_client import cache
    with TestClient(app) as c:
        # create WITH an admin password
        r = c.post("/api/printers", json={"ip": "192.168.1.50", "serial": "S1",
                                          "admin_pwd": "s3cret"})
        assert r.status_code == 201
        # response is redacted: flag exposed, cleartext NOT
        assert r.json()["has_admin_pwd"] is True
        assert "admin_pwd" not in r.json()
        # list is redacted too
        active = c.get("/api/printers").json()["active"]
        assert active["has_admin_pwd"] is True and "admin_pwd" not in active
        # but the cleartext IS persisted in the store (so from_env can use it)
        assert cache.active_printer()["admin_pwd"] == "s3cret"
        # clearing via "" removes it
        r = c.put("/api/printers/S1", json={"admin_pwd": ""})
        assert r.json()["has_admin_pwd"] is False
        assert "admin_pwd" not in cache.active_printer()


# --- z9_configured signal (3 states) ----------------------------------


def test_status_unconfigured_when_z9_none():
    app.dependency_overrides[get_z9] = lambda: None
    with TestClient(app) as c:
        body = c.get("/api/status").json()
    assert body["z9_configured"] is False          # -> onboarding/auto-open
    assert any(a["code"] == "Z9_UNREACHABLE" for a in body["alerts"])


def test_status_configured_but_offline():
    # Z9 known but not responding -> configured=True (no onboarding), offline.
    z9 = SimpleNamespace(device=MagicMock(), host="192.168.1.50")
    z9.device.status.side_effect = Z9ConnectionError("timeout")
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as c:
        body = c.get("/api/status").json()
    assert body["z9_configured"] is True
    assert body["ready"] is False
