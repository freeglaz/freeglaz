"""Z9 IP resolution at startup (IP configuration).

Order: --host (explicit constructor, CLI) > Z9_HOST (.env, dev) > active
printer from store.json > unconfigured (raises Z9Error → caught gracefully elsewhere).
"""
import pytest

from lib.z9_client import cache
from lib.z9_client.client import Z9Client
from lib.z9_client.exceptions import Z9Error


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    # Neutralize loading of the REAL .env (which sets Z9_HOST in this repo) so
    # we can test the "Z9_HOST absent" case.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("Z9_HOST", raising=False)
    monkeypatch.delenv("Z9_ADMIN_PWD", raising=False)


def test_explicit_host_constructor():
    # --host CLI case: Z9Client(host=…) directly, never via from_env.
    c = Z9Client(host="172.16.0.1")
    assert c.host == "172.16.0.1"


def test_resolve_uses_z9_host_env(monkeypatch):
    monkeypatch.setenv("Z9_HOST", "192.168.1.50")
    assert Z9Client.from_env().host == "192.168.1.50"


def test_resolve_falls_back_to_active_printer():
    cache.add_printer(ip="192.168.1.50", serial="S1", active=True)
    assert Z9Client.from_env().host == "192.168.1.50"


def test_env_takes_priority_over_store(monkeypatch):
    # Dev unchanged: .env/Z9_HOST takes priority over the store.json registry.
    cache.add_printer(ip="192.168.1.50", serial="S1", active=True)
    monkeypatch.setenv("Z9_HOST", "192.168.1.50")
    assert Z9Client.from_env().host == "192.168.1.50"


def test_inactive_printer_not_used():
    cache.add_printer(ip="192.168.1.50", serial="S1", active=False)
    with pytest.raises(Z9Error):
        Z9Client.from_env()


def test_unconfigured_raises():
    # Neither Z9_HOST nor active printer → raises (graceful state handled by lifespan/CLI).
    with pytest.raises(Z9Error):
        Z9Client.from_env()


# ─── admin password resolution (Z9_ADMIN_PWD env > active printer store) ──────


def test_admin_pwd_from_store():
    # webapp-only onboarding: password stored on the active printer, no env.
    cache.add_printer(ip="192.168.1.50", serial="S1", active=True, admin_pwd="s3cret")
    assert Z9Client.from_env().admin_pwd == "s3cret"


def test_admin_pwd_env_takes_priority_over_store(monkeypatch):
    cache.add_printer(ip="192.168.1.50", serial="S1", active=True, admin_pwd="from-store")
    monkeypatch.setenv("Z9_ADMIN_PWD", "from-env")
    assert Z9Client.from_env().admin_pwd == "from-env"


def test_admin_pwd_absent_is_none():
    cache.add_printer(ip="192.168.1.50", serial="S1", active=True)
    assert Z9Client.from_env().admin_pwd is None
