"""Printer IP connection test (IP configuration, C1).

3 statuses (ok/unreachable/not_a_designjet) + 2 support levels (validated if
DesignJet Z9, untested otherwise). Mock identification (never a real network).
"""
import pytest
from fastapi.testclient import TestClient

from lib.z9_client import printer_probe
from lib.z9_client.client import Z9Client
from lib.z9_client.exceptions import Z9ConnectionError, Z9RESTError
from webapp.backend.main import app
from webapp.backend.routes import printers as printers_routes


def _ident(monkeypatch, value):
    """Force Z9Client.identification (the probe's throwaway client) + close no-op."""
    if isinstance(value, Exception):
        def _raise(self):
            raise value
        monkeypatch.setattr(Z9Client, "identification", _raise)
    else:
        monkeypatch.setattr(Z9Client, "identification", lambda self: value)
    monkeypatch.setattr(Z9Client, "close", lambda self: None)


# ─── lib: test_connection ────────────────────────────────────────────


def test_ok_validated_real_z9(monkeypatch):
    _ident(monkeypatch, {"ModelName": "HP DesignJet Z9 24in",
                         "ModelNumber": "Z9 24in", "PartNumber": "W3Z71A",
                         "SerialNumber": "CNXXXXXXXX", "FWVersion": "JGR9_09_26"})
    r = printer_probe.test_connection("192.168.1.50")
    assert r["status"] == "ok"
    assert r["model"] == "HP DesignJet Z9 24in"
    assert r["serial"] == "CNXXXXXXXX"
    assert r["firmware"] == "JGR9_09_26"
    assert r["part"] == "W3Z71A"
    assert r["model_support"] == "validated"


def test_ok_validated_z9_44in(monkeypatch):
    # No strict equality: a Z9 44in must also pass as validated.
    _ident(monkeypatch, {"ModelName": "HP DesignJet Z9 44in", "SerialNumber": "X"})
    r = printer_probe.test_connection("1.2.3.4")
    assert r["status"] == "ok" and r["model_support"] == "validated"


def test_ok_untested_other_designjet(monkeypatch):
    _ident(monkeypatch, {"ModelName": "HP DesignJet Z3200ps 44in", "SerialNumber": "Y"})
    r = printer_probe.test_connection("1.2.3.4")
    assert r["status"] == "ok" and r["model_support"] == "untested"


def test_not_a_designjet(monkeypatch):
    _ident(monkeypatch, {"ModelName": "Synology DiskStation"})
    r = printer_probe.test_connection("192.168.1.51")
    assert r["status"] == "not_a_designjet"


def test_not_a_designjet_empty_identification(monkeypatch):
    _ident(monkeypatch, {})
    assert printer_probe.test_connection("1.2.3.4")["status"] == "not_a_designjet"


def test_unreachable_on_connection_error(monkeypatch):
    _ident(monkeypatch, Z9ConnectionError("Cannot reach 192.168.1.99"))
    r = printer_probe.test_connection("192.168.1.99")
    assert r["status"] == "unreachable"


def test_responded_with_http_error_is_not_designjet(monkeypatch):
    # A host that responds HTTP 404 on /Identification.xml -> not a Z9 PIWS.
    _ident(monkeypatch, Z9RESTError(404, "https://x/Identification.xml", "not found"))
    assert printer_probe.test_connection("1.2.3.4")["status"] == "not_a_designjet"


def test_empty_ip_unreachable():
    assert printer_probe.test_connection("")["status"] == "unreachable"


# ─── route: POST /api/printers/test (no Z9 auth) ───────────────────


def test_route_test_ok(monkeypatch):
    _ident(monkeypatch, {"ModelName": "HP DesignJet Z9 24in", "SerialNumber": "CNXXXXXXXX"})
    with TestClient(app) as client:
        r = client.post("/api/printers/test", json={"ip": "192.168.1.50"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["serial"] == "CNXXXXXXXX"
    assert body["model_support"] == "validated"


def test_route_unreachable(monkeypatch):
    _ident(monkeypatch, Z9ConnectionError("timeout"))
    with TestClient(app) as client:
        r = client.post("/api/printers/test", json={"ip": "192.168.1.99"})
    assert r.status_code == 200 and r.json()["status"] == "unreachable"


def test_route_rejects_empty_ip():
    with TestClient(app) as client:
        r = client.post("/api/printers/test", json={"ip": ""})
    assert r.status_code == 422   # min_length=1
