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
