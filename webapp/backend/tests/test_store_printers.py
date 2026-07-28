"""Registry of known printers in store.json (IP configuration, B-IP 1/3).

List {ip, serial, name, active} — native json (not the TOML). At most one active.
ensure_store does NOT create printers (empty = not configured).
"""
import json

import pytest

from lib.z9_client import cache


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))


def test_empty_by_default():
    assert cache.read_printers() == []
    assert cache.active_printer() is None


def test_ensure_store_does_not_create_printers():
    cache.ensure_store()
    assert "printers" not in cache.read_store_manifest()
    assert cache.read_printers() == []


def test_add_printer_writes_store_json():
    e = cache.add_printer(ip="192.168.1.50", serial="CNXXXXXXXX", name="Z9 atelier",
                          active=True, model_support="validated")
    assert e == {"ip": "192.168.1.50", "serial": "CNXXXXXXXX", "name": "Z9 atelier",
                 "active": True, "model_support": "validated"}
    # persisted in store.json
    manifest = json.loads((cache.root_dir() / "store.json").read_text())
    assert manifest["printers"][0]["serial"] == "CNXXXXXXXX"
    # other manifest keys preserved
    assert "store_version" in manifest
    assert cache.active_printer()["ip"] == "192.168.1.50"


def test_add_is_upsert_by_serial():
    cache.add_printer(ip="192.168.1.50", serial="S1", name="old")
    cache.add_printer(ip="192.168.1.99", serial="S1", name="new")
    printers = cache.read_printers()
    assert len(printers) == 1
    assert printers[0]["ip"] == "192.168.1.99" and printers[0]["name"] == "new"


def test_at_most_one_active():
    cache.add_printer(ip="1.1.1.1", serial="A", active=True)
    cache.add_printer(ip="2.2.2.2", serial="B", active=True)   # deactivates A
    actives = [p for p in cache.read_printers() if p["active"]]
    assert len(actives) == 1 and actives[0]["serial"] == "B"


def test_set_active_switches():
    cache.add_printer(ip="1.1.1.1", serial="A", active=True)
    cache.add_printer(ip="2.2.2.2", serial="B")
    assert cache.set_active_printer("B") is True
    assert cache.active_printer()["serial"] == "B"
    assert cache.set_active_printer("UNKNOWN") is False


def test_update_printer_ip_and_name_not_active():
    cache.add_printer(ip="1.1.1.1", serial="A", name="n", active=True)
    e = cache.update_printer("A", ip="9.9.9.9", name="renamed")
    assert e["ip"] == "9.9.9.9" and e["name"] == "renamed"
    assert e["active"] is True                       # update does not touch active
    assert cache.update_printer("UNKNOWN", ip="x") is None


def test_remove_printer():
    cache.add_printer(ip="1.1.1.1", serial="A")
    cache.add_printer(ip="2.2.2.2", serial="B")
    assert cache.remove_printer("A") is True
    assert [p["serial"] for p in cache.read_printers()] == ["B"]
    assert cache.remove_printer("A") is False        # already absent


def test_add_printer_stores_admin_pwd():
    e = cache.add_printer(ip="1.1.1.1", serial="A", active=True, admin_pwd="s3cret")
    assert e["admin_pwd"] == "s3cret"
    assert cache.active_printer()["admin_pwd"] == "s3cret"


def test_add_printer_without_pwd_omits_key():
    e = cache.add_printer(ip="1.1.1.1", serial="A")
    assert "admin_pwd" not in e


def test_update_printer_sets_and_clears_admin_pwd():
    cache.add_printer(ip="1.1.1.1", serial="A", active=True)
    # set
    assert cache.update_printer("A", admin_pwd="pw1")["admin_pwd"] == "pw1"
    # None = leave unchanged
    assert cache.update_printer("A", name="x")["admin_pwd"] == "pw1"
    # "" = explicit clear
    assert "admin_pwd" not in cache.update_printer("A", admin_pwd="")
