"""sol_native host handling (IP parameterization).

No more hardcoded DEFAULT_HOST="192.168.1.50": the spectro (port 9100) follows the
RESOLVED IP, `host` is required (callers pass client.host; the standalone CLI resolves
via from_env).
"""
import pytest

from lib.z9_client import sol_native


def test_no_hardcoded_default_host():
    assert not hasattr(sol_native, "DEFAULT_HOST")


def test_solsession_uses_given_host():
    s = sol_native.SolSession("192.168.1.50")        # no connection here (lazy)
    assert s.host == "192.168.1.50"
    assert s.port == sol_native.DEFAULT_PORT


def test_measure_requires_host():
    with pytest.raises(TypeError):
        sol_native.measure()                          # host required (no default)


def test_measure_has_no_duration_timeout():
    """Regression (LOT 1): NO client-side duration timeout on the scan — the Z9
    concludes, never a fixed clock. A scan of any size must be able to run to term."""
    import inspect
    params = inspect.signature(sol_native.measure).parameters
    assert "timeout" not in params


def test_probe_status_requires_host():
    with pytest.raises(TypeError):
        sol_native.probe_status()


# ─── CLI guard: the native SOL channel is driven on a DesignJet Z9 only ─────


def _cli_against(monkeypatch, ident, argv):
    """Run the CLI against a mocked identification; any SOL dialogue fails the test."""
    from lib.z9_client.client import Z9Client

    def _boom(*a, **kw):
        raise AssertionError("no SOL dialogue expected")
    if isinstance(ident, Exception):
        def _ident(self):
            raise ident
    else:
        def _ident(self):
            return ident
    monkeypatch.setattr(Z9Client, "identification", _ident)
    monkeypatch.setattr(Z9Client, "close", lambda self: None)
    monkeypatch.setattr(sol_native, "probe_status", _boom)
    monkeypatch.setattr(sol_native, "measure", _boom)
    monkeypatch.setattr("sys.argv", ["sol_native", *argv, "--host", "1.2.3.4"])
    sol_native._cli()


@pytest.mark.parametrize("argv", [["measure", "--go"], ["measure"], ["status"]])
def test_cli_refuses_a_z9_pro(monkeypatch, argv):
    with pytest.raises(SystemExit) as e:
        _cli_against(monkeypatch, {"ModelName": "HP DesignJet Z9 Pro 64in PS"}, argv)
    assert "only supported on the DesignJet Z9" in str(e.value)


def test_cli_refuses_an_unidentified_printer(monkeypatch):
    from lib.z9_client.exceptions import Z9ConnectionError
    with pytest.raises(SystemExit) as e:
        _cli_against(monkeypatch, Z9ConnectionError("timeout"), ["measure", "--go"])
    assert "cannot identify" in str(e.value)


def test_cli_lets_a_z9_through(monkeypatch):
    # The guard passes; the (mocked) status probe is then reached.
    with pytest.raises(AssertionError, match="no SOL dialogue expected"):
        _cli_against(monkeypatch, {"ModelName": "HP DesignJet Z9 24in"}, ["status"])
