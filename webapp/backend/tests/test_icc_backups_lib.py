"""Unified per-serial ICC backup service (lib/z9_client/icc_backups.py).

Backups: paths backups/<serial>/<mediaid>/<ge_state>/, rotation 5 per slot,
mkdir-on-demand, listing by FS enumeration (without serial). No sidecar.
"""
import pytest

from lib.z9_client import icc_backups as b

SERIAL = "CNXXXXXXXX"
MID = "9E489F02AE027F9DD93191D872728C1D"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))


def test_ge_state_from_gloss():
    assert b.ge_state_from_gloss("FULLPAGE") == "on"
    assert b.ge_state_from_gloss("OFF") == "off"
    with pytest.raises(ValueError):
        b.ge_state_from_gloss("SINGLE")


def test_slot_dir_per_serial_and_mkdir():
    d = b.slot_dir(SERIAL, MID, "off")
    assert d.exists() and d.is_dir()
    assert d == b.backups_root() / SERIAL / MID / "off"


def test_slot_dir_requires_serial_and_valid_segments():
    with pytest.raises(ValueError):
        b.slot_dir("", MID, "off")
    with pytest.raises(ValueError):
        b.slot_dir(SERIAL, "zz", "off")          # mediaid not hex
    with pytest.raises(ValueError):
        b.slot_dir(SERIAL, MID, "bogus")         # ge_state outside {off,on,single}


def _write(serial, mediaid, ge, n=1):
    paths = []
    for _ in range(n):
        p = b.new_backup_path(serial, mediaid, ge)
        p.write_bytes(b"FAKEICC")
        paths.append(p)
    return paths


def test_new_backup_path_writes_under_serial():
    p = b.new_backup_path(SERIAL, MID, "on")
    p.write_bytes(b"x")
    assert p.parent == b.backups_root() / SERIAL / MID / "on"
    assert b.list_backups(SERIAL, MID, "on")[0] == p


def test_rotate_keeps_max_5(monkeypatch):
    # 7 backups with increasing timestamps → rotate keeps the 5 most recent
    import lib.z9_client.icc_backups as mod
    times = [f"2026-06-2{i}T10-00-00Z" for i in range(7)]
    it = iter(times)
    monkeypatch.setattr(mod, "_iso_compact_now", lambda: next(it))
    for _ in range(7):
        b.new_backup_path(SERIAL, MID, "off").write_bytes(b"x")
    assert len(b.list_backups(SERIAL, MID, "off")) == 7
    deleted = b.rotate(SERIAL, MID, "off")
    assert deleted == 2
    remaining = b.list_backups(SERIAL, MID, "off")
    assert len(remaining) == 5
    # the 5 kept = the most recent (lexically largest names)
    assert remaining[0].name == "2026-06-26T10-00-00Z.icc"


def test_latest_and_consume(monkeypatch):
    import lib.z9_client.icc_backups as mod
    it = iter(["2026-06-20T10-00-00Z", "2026-06-21T10-00-00Z"])
    monkeypatch.setattr(mod, "_iso_compact_now", lambda: next(it))
    b.new_backup_path(SERIAL, MID, "off").write_bytes(b"a")
    latest = b.new_backup_path(SERIAL, MID, "off")
    latest.write_bytes(b"b")
    assert b.latest_backup(SERIAL, MID, "off") == latest
    assert b.consume(latest) is True
    assert b.latest_backup(SERIAL, MID, "off").name == "2026-06-20T10-00-00Z.icc"


def test_list_any_enumerates_serials():
    _write(SERIAL, MID, "off", 1)
    _write("OTHERSN0001", MID, "off", 1)
    # without serial → union of the 2 Z9
    any_ = b.list_backups_any(MID, "off")
    assert len(any_) == 2
    # with serial → only this slot
    assert len(b.list_backups(SERIAL, MID, "off")) == 1


def test_backups_summary_serial_vs_enumeration():
    _write(SERIAL, MID, "off", 1)
    s_scoped = b.backups_summary(MID, "off", serial=SERIAL)
    s_any = b.backups_summary(MID, "off")
    assert s_scoped["count"] == 1 and s_any["count"] == 1
    assert s_scoped["latest"] is not None
    assert b.backups_summary(MID, "on")["count"] == 0   # other slot empty


def test_list_filters_non_backup_files():
    d = b.slot_dir(SERIAL, MID, "off")
    (d / "2026-05-25T07-30-15Z.icc").write_bytes(b"ok")
    (d / "garbage.txt").write_bytes(b"x")
    (d / "2026-05-25-bad-format.icc").write_bytes(b"x")
    names = [p.name for p in b.list_backups(SERIAL, MID, "off")]
    assert names == ["2026-05-25T07-30-15Z.icc"]


def test_summary_reconstructs_human_iso_and_size():
    d = b.slot_dir(SERIAL, MID, "off")
    (d / "2026-05-25T07-30-15Z.icc").write_bytes(b"x" * 1234)
    s = b.backups_summary(MID, "off", serial=SERIAL)
    assert s["latest"] == "2026-05-25T07:30:15Z"      # human ISO reconstructed
    assert s["items"][0]["size_bytes"] == 1234


def test_purge_media_removes_all_ge_states():
    _write(SERIAL, MID, "off", 1)
    _write(SERIAL, MID, "on", 1)
    assert b.purge_media(SERIAL, MID) is True
    assert b.list_backups_any(MID, "off") == []
    assert b.list_backups_any(MID, "on") == []
    assert b.purge_media(SERIAL, MID) is False           # idempotent
