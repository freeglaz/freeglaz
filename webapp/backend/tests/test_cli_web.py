"""Tests of the ``freeglaz web`` handler (loads the script via importlib)."""
import importlib.util
import os
import sys
from argparse import Namespace
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "freeglaz"


@pytest.fixture(scope="module")
def cli():
    """Load the ``freeglaz`` script as a Python module.

    The file has no ``.py`` extension, so we go through an explicit
    ``SourceFileLoader`` rather than ``spec_from_file_location``
    (which returns None with no known loader for this extension).
    """
    loader = SourceFileLoader("freeglaz_cli_under_test", str(_SCRIPT))
    spec = importlib.util.spec_from_loader("freeglaz_cli_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["freeglaz_cli_under_test"] = mod
    loader.exec_module(mod)
    return mod


def _args(**overrides) -> Namespace:
    # ``build`` is the store_true flag added by commit 442d6f1
    # ("freeglaz web --build to build frontend before launching").
    # Without a matching field in the Namespace, ``cmd_web`` raises
    # AttributeError right at the ``if args.build:`` line — hence the
    # regression fixed here.
    base = dict(host="127.0.0.1", port=8765, no_browser=True,
                mock=False, reload=False, build=False)
    base.update(overrides)
    return Namespace(**base)


def test_cmd_web_mock_flag_sets_env_var(cli, monkeypatch):
    monkeypatch.delenv("FREEGLAZ_MOCK_PRINT", raising=False)
    with patch("webapp.backend.main.run") as mock_run:
        cli.cmd_web(_args(mock=True), client=None)
    assert os.environ.get("FREEGLAZ_MOCK_PRINT") == "1"
    mock_run.assert_called_once()


class _FakeThread:
    """Capture the browser-opener thread's target WITHOUT running it, so the
    test drives it deterministically (no real network wait)."""
    instances: list = []

    def __init__(self, target=None, daemon=None, **kw):
        self.target = target
        self.daemon = daemon
        self.started = False
        _FakeThread.instances.append(self)

    def start(self):
        self.started = True


def test_cmd_web_no_browser_does_not_open(cli):
    # no_browser=True → no opener thread scheduled at all (unchanged behavior).
    with patch("threading.Thread") as mock_thread, \
         patch("webapp.backend.main.run") as mock_run:
        cli.cmd_web(_args(no_browser=True), client=None)
    mock_thread.assert_not_called()
    mock_run.assert_called_once_with(host="127.0.0.1", port=8765, reload=False)


def test_cmd_web_opens_browser_when_server_ready(cli):
    # Default: a daemon opener thread is started; when it runs, it waits for the
    # server (wait_until_up) THEN opens the browser — no more fixed timer.
    import webbrowser
    _FakeThread.instances.clear()
    with patch("threading.Thread", _FakeThread), \
         patch("webapp.backend.main.run") as mock_run:
        cli.cmd_web(_args(no_browser=False, port=9000), client=None)
    assert len(_FakeThread.instances) == 1 and _FakeThread.instances[0].started
    mock_run.assert_called_once_with(host="127.0.0.1", port=9000, reload=False)
    # Drive the captured target with the server READY → browser opens on the URL.
    with patch("webapp.backend.main.wait_until_up", return_value=True) as mock_wait, \
         patch.object(webbrowser, "open") as mock_open:
        _FakeThread.instances[0].target()
    mock_wait.assert_called_once_with("127.0.0.1", 9000)
    mock_open.assert_called_once_with("http://127.0.0.1:9000/")


def test_cmd_web_does_not_open_when_server_never_ready(cli):
    # If the server never answers within the wait, we DON'T open a dead URL.
    import webbrowser
    _FakeThread.instances.clear()
    with patch("threading.Thread", _FakeThread), \
         patch("webapp.backend.main.run"):
        cli.cmd_web(_args(no_browser=False, port=9000), client=None)
    with patch("webapp.backend.main.wait_until_up", return_value=False), \
         patch.object(webbrowser, "open") as mock_open:
        _FakeThread.instances[0].target()
    mock_open.assert_not_called()
