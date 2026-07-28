"""send_raw — reliable socket transport (regression for the 0096 truncation bug).

The old `nc -w 5` + 120s subprocess timeout truncated large PRNs (the Z9 ingests
slowly → nc cut off → incomplete PDF → belen 0090-0007-0096). These tests use a
local mock JetDirect server that reads SLOWLY (simulating backpressure) and
verify send_raw delivers EVERY byte, closes write, drains the reply, returns 0.
"""
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from lib.z9_client.printing import PrintOps
from lib.z9_client.exceptions import Z9SendError


def _ops():
    return PrintOps(client=SimpleNamespace(host="127.0.0.1"))


class _MockJetDirect:
    """Accept one connection, read all bytes (slowly), reply ~6 opaque bytes,
    close — like the Z9 on :9100. Records how many bytes it received."""

    def __init__(self, chunk_delay=0.0):
        self.received = 0
        self.saw_eof = False
        self._chunk_delay = chunk_delay
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    def _serve(self):
        conn, _ = self._srv.accept()
        with conn:
            while True:
                # small recv → simulates slow ingestion / backpressure
                b = conn.recv(64 * 1024)
                if not b:
                    self.saw_eof = True
                    break
                self.received += len(b)
                if self._chunk_delay:
                    time.sleep(self._chunk_delay)
            conn.sendall(b"\x00\x00\x00\x00\x00\x00")   # opaque Z9-style reply
        self._srv.close()

    def join(self, timeout=10):
        self._t.join(timeout)


def test_sends_all_bytes_under_backpressure(tmp_path):
    payload = b"A" * (5 * 1024 * 1024) + b"Z"      # ~5MB, must arrive whole
    prn = tmp_path / "job.prn"
    prn.write_bytes(payload)
    srv = _MockJetDirect(chunk_delay=0.002)         # slow reader
    rc = _ops().send_raw(prn, port=srv.port)
    srv.join()
    assert rc == 0                                  # historical contract
    assert srv.received == len(payload)             # EVERY byte delivered
    assert srv.saw_eof                              # client closed write (shutdown)


def test_returns_zero_on_success(tmp_path):
    prn = tmp_path / "small.prn"
    prn.write_bytes(b"hello z9")
    srv = _MockJetDirect()
    assert _ops().send_raw(prn, port=srv.port) == 0
    srv.join()
    assert srv.received == 8


def test_missing_prn_raises():
    with pytest.raises(Z9SendError, match="not found"):
        _ops().send_raw("/nonexistent/does-not-exist.prn", port=1)


def test_connection_refused_raises(tmp_path):
    prn = tmp_path / "x.prn"
    prn.write_bytes(b"data")
    # Grab an ephemeral port then close it → guaranteed nobody listening.
    tmp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tmp.bind(("127.0.0.1", 0))
    dead_port = tmp.getsockname()[1]
    tmp.close()
    # connect fails → Z9SendError (not a silent success, unlike the old nc rc=0)
    with pytest.raises(Z9SendError, match="failed"):
        _ops().send_raw(prn, port=dead_port)


def test_peer_closes_early_raises(tmp_path):
    """Server closes immediately → sendall breaks → Z9SendError, never a silent
    'success' on a truncated send (the exact trap of the old nc path)."""
    prn = tmp_path / "big.prn"
    prn.write_bytes(b"B" * (8 * 1024 * 1024))

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def _serve():
        conn, _ = srv.accept()
        conn.close()          # slam it shut before reading
        srv.close()

    threading.Thread(target=_serve, daemon=True).start()
    with pytest.raises(Z9SendError):
        _ops().send_raw(prn, port=port)
