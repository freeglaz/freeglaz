"""Regression tests of the test-suite NETWORK GUARD (tests/netguard.py, installed by the
conftest): every attempt below targets a non-local address ON PURPOSE and must be
refused — including 192.168.1.50, the fixture address of the Z9 mocks, which could be a
real printer on someone's network. Loopback must keep working."""
import os
import socket
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from webapp.backend.tests import netguard

REPO_ROOT = Path(__file__).resolve().parents[3]
MOCK_PRINTER = "192.168.1.50"       # address used by the Z9 mocks of the suite
TEST_NET = "192.0.2.1"              # RFC 5737 documentation address


def test_guard_is_installed_for_the_whole_session():
    assert netguard.installed()
    assert socket.socket.connect.__name__ == "connect" and hasattr(socket.socket.connect, "__wrapped__")
    assert hasattr(socket.create_connection, "__wrapped__") and hasattr(socket.getaddrinfo, "__wrapped__")


@pytest.mark.parametrize("host,local", [
    ("127.0.0.1", True), ("127.8.9.10", True), ("::1", True), ("localhost", True), ("", True),
    ("0.0.0.0", True), ("::ffff:127.0.0.1", True),
    (MOCK_PRINTER, False), (TEST_NET, False), ("198.51.100.22", False), ("::ffff:192.168.1.50", False),
    ("printer.local", False), ("example.org", False), ("2001:db8::1", False),
])
def test_local_host_classification(host, local):
    assert netguard.is_local_host(host) is local


@pytest.mark.parametrize("host", [MOCK_PRINTER, TEST_NET])
def test_tcp_connection_to_a_printer_address_is_refused(host):
    with netguard.expect_blocked() as got:
        with pytest.raises(netguard.NetworkAccessBlocked):
            socket.create_connection((host, 443), timeout=1)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(netguard.NetworkAccessBlocked):
                s.connect((host, 8080))
            with pytest.raises(netguard.NetworkAccessBlocked):
                s.connect_ex((host, 9100))                     # raw printing port
        finally:
            s.close()
    assert len(got["inprocess"]) == 3 and all(host in a["target"] for a in got["inprocess"])


def test_name_resolution_and_datagrams_are_refused():
    with netguard.expect_blocked() as got:
        with pytest.raises(netguard.NetworkAccessBlocked):
            socket.getaddrinfo("designjet-z9.example", 443)
        with pytest.raises(netguard.NetworkAccessBlocked):
            socket.gethostbyname("designjet-z9.example")
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            with pytest.raises(netguard.NetworkAccessBlocked):
                u.sendto(b"x", (MOCK_PRINTER, 161))            # SNMP
            with pytest.raises(netguard.NetworkAccessBlocked):
                u.sendto(b"x", ("255.255.255.255", 5353))      # broadcast discovery
        finally:
            u.close()
    assert [a["kind"] for a in got["inprocess"]] == [
        "socket.getaddrinfo", "socket.gethostbyname", "socket.sendto", "socket.sendto"]


def test_keyword_address_cannot_bypass_the_guard():
    with netguard.expect_blocked() as got:
        with pytest.raises(netguard.NetworkAccessBlocked):
            socket.create_connection(address=(MOCK_PRINTER, 443), timeout=1)
        with pytest.raises(netguard.NetworkAccessBlocked):
            socket.getaddrinfo(host="designjet-z9.example", port=443)
    assert len(got["inprocess"]) == 2


def test_loopback_stays_allowed():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    t = threading.Thread(target=lambda: srv.accept()[0].sendall(b"ok"), daemon=True)
    t.start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as c:
            assert c.recv(2) == b"ok"
        assert socket.getaddrinfo("localhost", port)
    finally:
        srv.close()


def test_a_swallowed_attempt_is_still_recorded():
    """Production code catches OSError on network failures: the attempt must not vanish."""
    def careless_probe():
        try:
            socket.create_connection((MOCK_PRINTER, 443), timeout=1)
        except OSError:
            return "printer unreachable"
    with netguard.expect_blocked() as got:
        assert careless_probe() == "printer unreachable"
    assert len(got["inprocess"]) == 1


def test_python_subprocesses_inherit_the_guard():
    code = f"import socket; socket.create_connection(({MOCK_PRINTER!r}, 443), timeout=1)"
    with netguard.expect_blocked() as got:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and "network access blocked in tests" in r.stderr
    assert any(MOCK_PRINTER in line for line in got["subprocess"])


def test_ledm_fetch_without_a_mock_never_leaves_the_machine():
    """The original leak: GET /api/papers enriches papers with an HTTPS fetch of
    z9.host/Calibration/Calibration.xml. Here the LEDM cache is NOT mocked: the guard must
    stop the request (the route degrades as for an unreachable printer)."""
    from fastapi.testclient import TestClient
    from webapp.backend.main import app
    from webapp.backend.routes.status import get_z9
    from webapp.backend.services import ledm_calibration_cache as lcc

    xml = (Path(__file__).parent / "fixtures" / "medium_list_sample.xml").read_text(encoding="utf-8")
    paper = MagicMock()
    paper.get_raw_xml.return_value = xml
    app.dependency_overrides[get_z9] = lambda: SimpleNamespace(paper=paper, host=MOCK_PRINTER)
    old_cache, old_reader = lcc._cache, lcc._reader
    lcc._cache, lcc._reader = None, None
    try:
        with netguard.expect_blocked() as got:
            r = TestClient(app).get("/api/papers")
    finally:
        app.dependency_overrides.clear()
        lcc._cache, lcc._reader = old_cache, old_reader
    assert r.status_code == 200
    assert got["inprocess"] and all(MOCK_PRINTER in a["target"] for a in got["inprocess"])


def test_a_test_that_touches_the_network_fails_even_if_it_swallows_the_error(tmp_path):
    """Meta-test: an inner pytest session with this conftest as plugin runs a careless test
    that swallows the error → it must be reported as an error, with the address."""
    t = tmp_path / "test_careless.py"
    t.write_text(textwrap.dedent(f"""
        import socket
        def test_careless():
            try:
                socket.create_connection(({MOCK_PRINTER!r}, 443), timeout=1)
            except OSError:
                pass
    """))
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    with netguard.expect_blocked():
        r = subprocess.run([sys.executable, "-m", "pytest", "-p", "webapp.backend.tests.conftest",
                            str(t), "-q", "-p", "no:cacheprovider", "--rootdir", str(tmp_path)],
                           capture_output=True, text=True, cwd=REPO_ROOT, env=env, timeout=120)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "non-local network access attempted" in out and MOCK_PRINTER in out, out
