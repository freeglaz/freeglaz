"""Tests — job_mapping service (atomic JSON persistence)."""
import json
from pathlib import Path

import pytest

from webapp.backend.services import job_mapping


@pytest.fixture(autouse=True)
def _isolate_mapping(tmp_path, monkeypatch):
    """Redirect the mapping to a per-test tmp_path for isolation."""
    monkeypatch.setattr(job_mapping, "DATA_DIR", tmp_path)
    monkeypatch.setattr(job_mapping, "MAPPING_FILE", tmp_path / "job_mapping.json")
    yield


def test_load_empty_when_no_file():
    assert job_mapping.load() == {}


def test_register_then_lookup_roundtrips():
    job_mapping.register("FW-1", "JA5-1")
    assert job_mapping.lookup("FW-1") == "JA5-1"


def test_register_overwrites_existing():
    job_mapping.register("FW-1", "JA5-1")
    job_mapping.register("FW-1", "JA5-2")  # same firmware UUID, new jobacct5
    assert job_mapping.lookup("FW-1") == "JA5-2"


def test_register_persists_atomically(tmp_path):
    job_mapping.register("FW-1", "JA5-1")
    job_mapping.register("FW-2", "JA5-2")

    # The .json file exists and is readable
    raw = (tmp_path / "job_mapping.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data == {"FW-1": "JA5-1", "FW-2": "JA5-2"}

    # No orphan .tmp file (atomic write = os.replace)
    assert not (tmp_path / "job_mapping.json.tmp").exists()


def test_load_tolerates_corrupted_json(tmp_path):
    (tmp_path / "job_mapping.json").write_text("{ not valid json", encoding="utf-8")
    # No crash, fallback to empty dict
    assert job_mapping.load() == {}


def test_load_tolerates_non_dict_root(tmp_path):
    (tmp_path / "job_mapping.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert job_mapping.load() == {}


def test_load_filters_non_string_pairs(tmp_path):
    (tmp_path / "job_mapping.json").write_text(
        json.dumps({"FW-1": "JA5-1", "FW-2": 42, 999: "JA5-3"}),
        encoding="utf-8",
    )
    # Only the valid str/str pair is kept. The key 999 (int) becomes
    # "999" on JSON load → it's a str, so valid. We only filter out the
    # value 42 which is not a str.
    out = job_mapping.load()
    assert "FW-1" in out and out["FW-1"] == "JA5-1"
    assert "FW-2" not in out  # non-str value filtered out


def test_lookup_returns_none_when_unknown():
    job_mapping.register("FW-1", "JA5-1")
    assert job_mapping.lookup("FW-UNKNOWN") is None


def test_remove_jobacct5_purges_all_firmware_uuids_pointing_to_it():
    # Hardlink reprint case: 2 firmware UUIDs point to the same jobacct5
    job_mapping.register("FW-ORIGINAL", "JA5-SHARED")
    job_mapping.register("FW-REPRINT",  "JA5-SHARED")
    job_mapping.register("FW-OTHER",    "JA5-OTHER")

    removed = job_mapping.remove_jobacct5("JA5-SHARED")
    assert removed == 2
    assert job_mapping.lookup("FW-ORIGINAL") is None
    assert job_mapping.lookup("FW-REPRINT")  is None
    assert job_mapping.lookup("FW-OTHER")    == "JA5-OTHER"


def test_remove_jobacct5_returns_zero_when_no_match():
    job_mapping.register("FW-1", "JA5-1")
    assert job_mapping.remove_jobacct5("JA5-UNKNOWN") == 0


def test_remove_orphans_keeps_known_firmware_uuids():
    job_mapping.register("FW-ALIVE-1", "JA5-1")
    job_mapping.register("FW-ALIVE-2", "JA5-2")
    job_mapping.register("FW-DEAD",    "JA5-3")

    removed = job_mapping.remove_orphans({"FW-ALIVE-1", "FW-ALIVE-2"})
    assert removed == 1
    assert job_mapping.lookup("FW-ALIVE-1") == "JA5-1"
    assert job_mapping.lookup("FW-ALIVE-2") == "JA5-2"
    assert job_mapping.lookup("FW-DEAD")    is None


def test_all_entries_returns_full_snapshot():
    job_mapping.register("FW-1", "JA5-1")
    job_mapping.register("FW-2", "JA5-2")
    assert job_mapping.all_entries() == {"FW-1": "JA5-1", "FW-2": "JA5-2"}
