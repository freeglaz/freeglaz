"""Event-driven sync — forced targeted refetch of a paper (#1).

- refetch_paper: purge + re-download ONE paper into the mirror, WITHOUT going
  through the getMediumListVersion gate (blind to profile changes).
- Non-regression: the sync_mirror loop, now factored on the same
  _fetch_paper_slots building block, still works end-to-end (forced smoke).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.z9_client import store, cache


def _icc_bytes() -> bytes:
    head = bytearray(128)
    head[36:40] = b"acsp"
    return bytes(head) + b"\x00" * 32


PAPER = {
    "id": "355783D031D38CD4F1D8B8426FE3C51A",
    "name": "Hahnemühle Fine Art Baryta Satin",
    "is_user_custom": True,
    "donor_id": None,
    "profiles": [
        {"gloss_enhancer": "OFF", "color_space": "PRINTER_RGB",
         "custom": True, "uuid": "u-off", "icc_name": "baryta GE OFF.icc",
         "date": "2026-05-13"},
        {"gloss_enhancer": "FULLPAGE", "color_space": "PRINTER_RGB",
         "custom": True, "uuid": "u-fp", "icc_name": "baryta GE ON",
         "date": "2026-06-03"},
    ],
}


class _FakePaperOps:
    def __init__(self, paper):
        self._paper = paper
        self.exported: list = []

    def details(self, ref):
        return self._paper

    def list_full(self):
        return [self._paper]

    def export_icc(self, ref, output_path, gloss_enhancer="FULLPAGE",
                   color_space="RGB", _pre_resolved=None, **kw):
        Path(output_path).write_bytes(_icc_bytes())
        self.exported.append((ref, gloss_enhancer, color_space, output_path))
        return {"md5": f"md5-{gloss_enhancer}", "size_bytes": 160}


class _FakeSoap:
    def get_medium_list_version(self):
        return {"version": "2026-06-03"}


class _FakeClient:
    def __init__(self, paper):
        self._freeglaz_serial_cache = "CNXXXXXXXX"
        self.paper = _FakePaperOps(paper)
        self.soap = _FakeSoap()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path))
    cache.ensure_store()
    return tmp_path


def test_refetch_paper_targeted_forced(env, tmp_path):
    client = _FakeClient(PAPER)
    res = store.refetch_paper(client, PAPER["id"])

    # 2 slots re-downloaded (forced, without version check)
    assert res["n_profiles_fetched"] == 2
    assert res["errors"] == []
    assert {ge for (_ref, ge, _cs, _p) in client.paper.exported} == {"OFF", "FULLPAGE"}

    # Files written into the mirror of the correct paper
    slug = cache.slugify(PAPER["name"])
    paper_dir = tmp_path / "mirror" / "CNXXXXXXXX" / "papers" / slug
    iccs = list(paper_dir.glob("*.icc"))
    assert len(iccs) == 2, iccs
    # #6: no double extension despite "baryta GE OFF.icc" as icc_name
    assert all(not p.name.endswith(".icc.icc") for p in iccs)

    # _mirror.json: paper index updated (UUIDs per slot)
    manifest = cache.read_mirror_manifest("CNXXXXXXXX")
    entry = manifest["papers"][slug]
    assert entry["profiles_uuids"] == {
        "OFF/PRINTER_RGB": "u-off", "FULLPAGE/PRINTER_RGB": "u-fp"}


def test_refetch_paper_bypasses_version_gate(env):
    """refetch_paper NEVER consults getMediumListVersion (forced)."""
    client = _FakeClient(PAPER)

    class _BoomSoap:
        def get_medium_list_version(self):
            raise AssertionError("refetch_paper ne doit PAS appeler le gate version")
    client.soap = _BoomSoap()

    res = store.refetch_paper(client, PAPER["id"])  # must not raise
    assert res["n_profiles_fetched"] == 2


def test_forget_paper_removes_from_mirror(env, tmp_path):
    """forget_paper (after medium deletion): purge the folder + remove
    the _mirror.json entry."""
    client = _FakeClient(PAPER)
    # First populate the mirror (forced sync), then "forget" the paper.
    store.sync_mirror(client, force=True)
    slug = cache.slugify(PAPER["name"])
    paper_dir = tmp_path / "mirror" / "CNXXXXXXXX" / "papers" / slug
    assert paper_dir.exists()

    res = store.forget_paper(client, PAPER["name"])
    assert res["removed_from_index"] is True
    assert not paper_dir.exists()
    manifest = cache.read_mirror_manifest("CNXXXXXXXX")
    assert slug not in (manifest.get("papers") or {})


def test_sync_mirror_still_works_after_refactor(env, tmp_path):
    """Smoke: the sync_mirror loop (factored on _fetch_paper_slots) runs
    end-to-end (force=True) — non-regression of the refactor."""
    client = _FakeClient(PAPER)
    res = store.sync_mirror(client, force=True)
    assert res["changed"] is True
    assert res["n_profiles_fetched"] == 2
    slug = cache.slugify(PAPER["name"])
    paper_dir = tmp_path / "mirror" / "CNXXXXXXXX" / "papers" / slug
    assert len(list(paper_dir.glob("*.icc"))) == 2
