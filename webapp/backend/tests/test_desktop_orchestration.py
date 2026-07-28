"""Packaging — desktop orchestration (pywebview).

Tests the building blocks WITHOUT a GUI (the pywebview window is validated live
on Mac): free-port selection, waiting for /api/health, building the uvicorn
server, and the clear message if pywebview is missing.
"""
from __future__ import annotations

import socket

from webapp import desktop
from webapp.backend import main as backend_main


def test_free_port_prefers_then_falls_back():
    # preferred port free → returned as-is
    p = desktop._free_port(0)            # 0 → the OS picks, always free
    assert isinstance(p, int) and p > 0
    # verify the returned port can actually be bound
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((desktop.HOST, p))


def test_free_port_falls_back_when_preferred_busy():
    # occupy a port, then request it as preferred → fall back to another free one
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        busy.bind((desktop.HOST, 0))
        taken = busy.getsockname()[1]
        busy.listen(1)
        got = desktop._free_port(taken)
        assert got != taken and got > 0


def test_wait_until_up_false_when_nothing_listens():
    # free port (nothing listening) → short timeout → False, without raising.
    # desktop now delegates to main.wait_until_up (formerly _wait_health).
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((desktop.HOST, 0))
        free = s.getsockname()[1]
    assert backend_main.wait_until_up(desktop.HOST, free, timeout=0.5) is False


def test_run_background_and_health_roundtrip():
    """The programmatic server (main.run_background, SHARED CLI/desktop
    primitive) starts the real app, /api/health responds (is_up/wait_until_up),
    then shuts down cleanly via should_exit (core of the orchestration)."""
    port = desktop._free_port(0)
    server, t = backend_main.run_background(host=desktop.HOST, port=port)
    assert hasattr(server, "should_exit")
    try:
        assert backend_main.wait_until_up(desktop.HOST, port, timeout=10.0) is True
        assert backend_main.is_up(desktop.HOST, port) is True
    finally:
        server.should_exit = True
        t.join(timeout=5.0)
    assert not t.is_alive()             # clean shutdown


def test_main_returns_2_without_pywebview(monkeypatch):
    """pywebview missing → clear message + code 2 (no crash)."""
    import builtins
    real_import = builtins.__import__

    def _no_webview(name, *a, **k):
        if name == "webview":
            raise ImportError("simulated missing pywebview")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_webview)
    assert desktop.main([]) == 2
