"""Gloss Enhancer capability guard (O1): GE requested on a non-capable paper is
dropped to OFF and a warning is recorded — on every path (incl. CLI). A capable
paper (or unknown capability) leaves GE untouched (no regression)."""
import pytest

from lib.z9_client.client import Z9Client
from lib.z9_client.printing import (
    PrintJob, PrintOps, PrintResult, resolve_gloss_capability,
)


# ─── Pure decision function ─────────────────────────────────────────


@pytest.mark.parametrize("requested,capable,eff,has_warn", [
    ("FULLPAGE", False, "OFF",      True),   # not capable → dropped + warned
    ("FULLPAGE", None,  "OFF",      True),   # unknown → default-False → dropped
    ("FULLPAGE", True,  "FULLPAGE", False),  # capable → free choice, untouched
    ("OFF",      False, "OFF",      False),  # GE not requested → nothing to do
    ("OFF",      None,  "OFF",      False),
    ("OFF",      True,  "OFF",      False),
])
def test_resolve_gloss_capability(requested, capable, eff, has_warn):
    effective, warning = resolve_gloss_capability(requested, capable)
    assert effective == eff
    assert (warning is not None) == has_warn
    if has_warn:
        assert "Gloss Enhancer" in warning


# ─── Capability lookup (firmware caps → True/False/None) ────────────


class _FakePaper:
    def __init__(self, caps):
        self._caps = caps

    def capabilities(self, paper_id):
        if isinstance(self._caps, Exception):
            raise self._caps
        return self._caps


def _ops_with_caps(caps):
    client = Z9Client.__new__(Z9Client)
    client.host = "127.0.0.1"
    client.admin_pwd = None
    client.paper = _FakePaper(caps)
    return PrintOps(client)


@pytest.mark.parametrize("caps,expected", [
    ({"supports_gloss_enhancer": False}, False),
    ({"supports_gloss_enhancer": True},  True),
    ({"supports_gloss_enhancer": None},  None),
    ({}, None),                                   # key absent → None (permissive)
])
def test_gloss_enhancer_capable_lookup(caps, expected):
    assert _ops_with_caps(caps)._gloss_enhancer_capable("MID") is expected


def test_gloss_enhancer_capable_swallows_errors():
    from lib.z9_client.exceptions import Z9Error
    ops = _ops_with_caps(Z9Error("boom"))
    assert ops._gloss_enhancer_capable("MID") is None  # never breaks a print


# ─── The guard wiring (mutates job + records warning) ───────────────


def _job(gloss):
    return PrintJob(paper_id="MID", paper_name="Matte Rag", gloss=gloss)


@pytest.mark.parametrize("caps", [
    {"supports_gloss_enhancer": False},   # explicitly not capable
    {"supports_gloss_enhancer": None},    # unknown → default-False
    {},                                   # key absent → default-False
])
def test_guard_drops_ge_when_not_capable_or_unknown(caps):
    ops = _ops_with_caps(caps)
    job, result = _job("FULLPAGE"), PrintResult()
    ops._apply_gloss_guard(job, result)
    assert job.gloss == "OFF"
    assert result.warnings and "Gloss Enhancer" in result.warnings[0]


def test_guard_leaves_ge_only_when_explicitly_capable():
    ops = _ops_with_caps({"supports_gloss_enhancer": True})
    job, result = _job("FULLPAGE"), PrintResult()
    ops._apply_gloss_guard(job, result)
    assert job.gloss == "FULLPAGE"
    assert result.warnings == []
