"""Tests P2.B — ICC action routes (/api/papers/{id}/icc/{ge_state})."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from lib.z9_client.exceptions import Z9ConnectionError, Z9Error
from lib.z9_client import icc_backups          # unified per-serial service (B-backups)
from webapp.backend.main import app
from webapp.backend.routes.status import get_z9

MID = "9E489F02AE027F9DD93191D872728C1D"
SERIAL = "CNXXXXXXXX"


def _make_fake_icc(size: int = 1024) -> bytes:
    """Generate valid ICC bytes: 36 bytes header padding + 'acsp' + rest."""
    header = b"\x00" * 36 + b"acsp"
    return header + b"\xff" * (size - len(header))


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _isolate_backups(tmp_path, monkeypatch):
    # The unified service writes under cache.root_dir()/backups/<serial>/… → isolate
    # via the store root (no more separate BACKUPS_DIR under webapp/data/).
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    yield


@pytest.fixture
def fake_z9():
    """Z9 mock with PaperOps.export_icc / import_icc / delete_profile."""
    paper = MagicMock()

    def _fake_export(ref, output_path, **kwargs):
        Path(output_path).write_bytes(_make_fake_icc())
        return {"output_path": output_path, "size_bytes": 1024}

    paper.export_icc.side_effect = _fake_export
    paper.import_icc.return_value = {
        "outcome": "OK",
        "icc_name": "TestProfile",
        "ticket_date": "2026-05-25",
    }
    paper.delete_profile.return_value = {
        "outcome": "OK",
        "deleted_icc_name": "OldProfile",
    }
    # store.get_serial(z9) (per-serial bridge) reads z9.identification()
    return SimpleNamespace(paper=paper, host="192.168.1.50",
                           identification=lambda: {"SerialNumber": SERIAL})


# ─── GET /icc/{ge_state} — Export ─────────────────────────────────────


def test_export_icc_returns_bytes_and_headers(fake_z9):
    """Fallback case: fake ICC without a readable desc tag → filename
    ``{mediaid}_{ge_state}.icc``."""
    app.dependency_overrides[get_z9] = lambda: fake_z9
    with TestClient(app) as client:
        r = client.get(f"/api/papers/{MID}/icc/off")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/vnd.iccprofile"
    assert "attachment" in r.headers["content-disposition"]
    assert f"{MID}_off.icc" in r.headers["content-disposition"]
    # Body contains the ICC signature
    assert r.content[36:40] == b"acsp"


def test_export_icc_filename_from_desc_tag():
    """Happy path case: real ICC with a desc tag → filename based on the
    desc tag (human) rather than the firmware MEDIAID."""
    sRGB_path = (
        Path(__file__).parents[3]
        / "lib" / "z9_client" / "assets" / "sRGB_IEC61966-2.1.icc"
    )
    sRGB_bytes = sRGB_path.read_bytes()

    paper = MagicMock()

    def _fake_export(ref, output_path, **kwargs):
        Path(output_path).write_bytes(sRGB_bytes)
        return {"output_path": output_path, "size_bytes": len(sRGB_bytes)}

    paper.export_icc.side_effect = _fake_export
    z9 = SimpleNamespace(paper=paper, host="192.168.1.50")

    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.get(f"/api/papers/{MID}/icc/off")
    assert r.status_code == 200
    # The desc tag of sRGB IEC61966 = "sRGB IEC61966-2.1" → sanitized to
    # "sRGB_IEC61966-2.1" (space → underscore, dot/dash OK)
    cd = r.headers["content-disposition"]
    assert "sRGB_IEC61966-2.1.icc" in cd
    # No more MEDIAID in the filename when desc is readable
    assert MID not in cd


def test_export_icc_maps_ge_states(fake_z9):
    """Check that the UI ge_state is mapped to the SOAP gloss_enhancer."""
    app.dependency_overrides[get_z9] = lambda: fake_z9
    with TestClient(app) as client:
        client.get(f"/api/papers/{MID}/icc/off")
        client.get(f"/api/papers/{MID}/icc/on")
        client.get(f"/api/papers/{MID}/icc/single")
    calls = fake_z9.paper.export_icc.call_args_list
    assert calls[0].kwargs["gloss_enhancer"] == "OFF"
    assert calls[1].kwargs["gloss_enhancer"] == "FULLPAGE"
    assert calls[2].kwargs["gloss_enhancer"] == "OFF"


def test_export_icc_rejects_invalid_ge_state(fake_z9):
    app.dependency_overrides[get_z9] = lambda: fake_z9
    with TestClient(app) as client:
        r = client.get(f"/api/papers/{MID}/icc/invalid")
    assert r.status_code == 422


def test_export_icc_502_on_z9_error(fake_z9):
    fake_z9.paper.export_icc.side_effect = Z9ConnectionError("network down")
    app.dependency_overrides[get_z9] = lambda: fake_z9
    with TestClient(app) as client:
        r = client.get(f"/api/papers/{MID}/icc/off")
    assert r.status_code == 502


def test_export_icc_503_when_z9_not_configured():
    app.dependency_overrides[get_z9] = lambda: None
    with TestClient(app) as client:
        r = client.get(f"/api/papers/{MID}/icc/off")
    assert r.status_code == 503


# ─── PUT /icc/{ge_state} — Replace ────────────────────────────────────


def test_import_icc_validates_acsp_header(fake_z9):
    app.dependency_overrides[get_z9] = lambda: fake_z9
    with TestClient(app) as client:
        r = client.put(
            f"/api/papers/{MID}/icc/off",
            files={"file": ("fake.icc", b"\x00" * 200, "application/octet-stream")},
        )
    assert r.status_code == 422
    # Structured rejection {code, message} so the frontend can localize it.
    detail = r.json()["detail"]
    assert detail["code"] == "icc_invalid_signature"
    assert "acsp" in detail["message"]


def test_import_icc_rejects_too_small(fake_z9):
    app.dependency_overrides[get_z9] = lambda: fake_z9
    with TestClient(app) as client:
        r = client.put(
            f"/api/papers/{MID}/icc/off",
            files={"file": ("tiny.icc", b"x", "application/octet-stream")},
        )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "icc_too_small"


def test_import_icc_creates_backup_and_calls_setprofile(fake_z9):
    app.dependency_overrides[get_z9] = lambda: fake_z9
    with TestClient(app) as client:
        r = client.put(
            f"/api/papers/{MID}/icc/off",
            files={"file": ("new.icc", _make_fake_icc(), "application/octet-stream")},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "OK"
    assert body["backup_created"] is not None
    # export_icc called for backup, then import_icc
    assert fake_z9.paper.export_icc.called
    assert fake_z9.paper.import_icc.called
    # Backup file present on disk
    backups = icc_backups.list_backups(SERIAL, MID, "off")
    assert len(backups) == 1


def test_import_icc_502_on_z9_setprofile_error(fake_z9):
    fake_z9.paper.import_icc.side_effect = Z9Error("setProfile failed")
    app.dependency_overrides[get_z9] = lambda: fake_z9
    with TestClient(app) as client:
        r = client.put(
            f"/api/papers/{MID}/icc/off",
            files={"file": ("new.icc", _make_fake_icc(), "application/octet-stream")},
        )
    assert r.status_code == 502


# ─── DELETE /icc/{ge_state} — Restore factory ─────────────────────────


def test_restore_factory_calls_delete_profile_with_backup(fake_z9):
    app.dependency_overrides[get_z9] = lambda: fake_z9
    with TestClient(app) as client:
        r = client.delete(f"/api/papers/{MID}/icc/on")
    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "OK"
    assert body["backup_created"] is not None
    # backup created before delete
    assert fake_z9.paper.export_icc.called
    assert fake_z9.paper.delete_profile.called
    # Mapping ge_state on → FULLPAGE
    assert fake_z9.paper.delete_profile.call_args.kwargs["gloss_enhancer"] == "FULLPAGE"


def test_restore_factory_502_on_delete_error(fake_z9):
    fake_z9.paper.delete_profile.side_effect = Z9Error("deleteProfile failed")
    app.dependency_overrides[get_z9] = lambda: fake_z9
    with TestClient(app) as client:
        r = client.delete(f"/api/papers/{MID}/icc/off")
    assert r.status_code == 502


def test_restore_factory_keeps_backup_when_delete_fails(fake_z9):
    """P2-bis regression: if the SOAP delete_profile fails after the
    backup was created, the backup persists (the user can replay later,
    or rollback)."""
    fake_z9.paper.delete_profile.side_effect = Z9Error("deleteProfile failed")
    app.dependency_overrides[get_z9] = lambda: fake_z9
    with TestClient(app) as client:
        client.delete(f"/api/papers/{MID}/icc/off")
    # The backup created before delete_profile must not be cleaned up
    backups = icc_backups.list_backups(SERIAL, MID, "off")
    assert len(backups) == 1


def test_rollback_round_trip_after_restore_factory(fake_z9):
    """P2-bis regression: Restore factory creates a backup that Rollback
    can consume (custom → restore factory → rollback → custom).

    We simulate the real flow: delete_profile succeeds, the backup
    contains the previous custom profile, then the rollback calls
    import_icc with that backup → we return to the initial custom profile."""
    app.dependency_overrides[get_z9] = lambda: fake_z9
    with TestClient(app) as client:
        # 1. Restore factory → backup created
        r1 = client.delete(f"/api/papers/{MID}/icc/off")
        assert r1.status_code == 200
        backups = icc_backups.list_backups(SERIAL, MID, "off")
        assert len(backups) == 1
        backup_path = backups[0]

        # 2. Rollback → import_icc called with the backup path, backup consumed
        r2 = client.post(f"/api/papers/{MID}/icc/off/rollback")
        assert r2.status_code == 200
        body = r2.json()
        assert body["consumed"] is True
        assert body["restored_from"] == backup_path.name

    # import_icc called with the right path
    call = fake_z9.paper.import_icc.call_args
    assert call.kwargs["icc_path"] == str(backup_path)
    # The backup was consumed
    assert icc_backups.list_backups(SERIAL, MID, "off") == []


# ─── GET /backups ─────────────────────────────────────────────────────


def test_list_backups_empty(fake_z9):
    app.dependency_overrides[get_z9] = lambda: fake_z9
    with TestClient(app) as client:
        r = client.get(f"/api/papers/{MID}/icc/off/backups")
    assert r.status_code == 200
    assert r.json() == {"count": 0, "latest": None, "items": []}


def test_list_backups_after_import(fake_z9):
    app.dependency_overrides[get_z9] = lambda: fake_z9
    with TestClient(app) as client:
        client.put(
            f"/api/papers/{MID}/icc/off",
            files={"file": ("a.icc", _make_fake_icc(), "application/octet-stream")},
        )
        r = client.get(f"/api/papers/{MID}/icc/off/backups")
    body = r.json()
    assert body["count"] == 1
    assert body["latest"] is not None


# ─── POST /rollback ───────────────────────────────────────────────────


def test_rollback_404_when_no_backup(fake_z9):
    app.dependency_overrides[get_z9] = lambda: fake_z9
    with TestClient(app) as client:
        r = client.post(f"/api/papers/{MID}/icc/off/rollback")
    assert r.status_code == 404


def test_rollback_consumes_latest_backup(fake_z9):
    """Create 2 backups (by hand, with distinct timestamps), rollback →
    consumes the most recent, the 1st remains.

    We avoid going through 2 successive PUTs because the compact ISO
    format has second granularity, so 2 imports < 1s would overwrite
    their backup mutually.
    """
    app.dependency_overrides[get_z9] = lambda: fake_z9
    d = icc_backups.slot_dir(SERIAL, MID, "off")
    older = d / "2026-05-25T07-30-15Z.icc"
    newer = d / "2026-05-25T07-31-15Z.icc"
    older.write_bytes(_make_fake_icc())
    newer.write_bytes(_make_fake_icc())
    before = icc_backups.list_backups(SERIAL, MID, "off")
    assert len(before) == 2
    assert before[0].name == newer.name

    with TestClient(app) as client:
        r = client.post(f"/api/papers/{MID}/icc/off/rollback")
    assert r.status_code == 200
    body = r.json()
    assert body["restored_from"] == newer.name
    assert body["consumed"] is True
    # One backup fewer, the oldest remains
    after = icc_backups.list_backups(SERIAL, MID, "off")
    assert len(after) == 1
    assert after[0].name == older.name


def test_rollback_calls_import_icc_with_backup_path(fake_z9):
    app.dependency_overrides[get_z9] = lambda: fake_z9
    # Prepare 1 backup directly
    p = icc_backups.new_backup_path(SERIAL, MID, "single")
    p.write_bytes(_make_fake_icc())
    with TestClient(app) as client:
        r = client.post(f"/api/papers/{MID}/icc/single/rollback")
    assert r.status_code == 200
    # import_icc called with the backup path
    call = fake_z9.paper.import_icc.call_args
    assert call.kwargs["icc_path"] == str(p)
    assert call.kwargs["gloss_enhancer"] == "OFF"  # single → OFF


def test_rollback_preserves_original_icc_name(fake_z9):
    """P2-ter regression: the rollback must re-read the desc tag of the
    backup and pass it as ``icc_name`` to ``import_icc``, so that the Z9
    firmware preserves the original name instead of overwriting the desc
    tag with a timestamp."""
    sRGB_path = (
        Path(__file__).parents[3]
        / "lib" / "z9_client" / "assets" / "sRGB_IEC61966-2.1.icc"
    )
    app.dependency_overrides[get_z9] = lambda: fake_z9
    # Prepare a backup containing a real ICC (sRGB IEC61966-2.1) whose
    # desc tag = "sRGB IEC61966-2.1"
    p = icc_backups.new_backup_path(SERIAL, MID, "off")
    p.write_bytes(sRGB_path.read_bytes())
    with TestClient(app) as client:
        r = client.post(f"/api/papers/{MID}/icc/off/rollback")
    assert r.status_code == 200
    body = r.json()
    # The restored name is exposed in the response
    assert body["restored_icc_name"] == "sRGB IEC61966-2.1"
    # import_icc called with icc_name explicitly (not None)
    call = fake_z9.paper.import_icc.call_args
    assert call.kwargs["icc_name"] == "sRGB IEC61966-2.1"


def test_rollback_fallback_icc_name_when_desc_unreadable(fake_z9):
    """If the backup binary has no readable desc tag (corrupted, minimal
    fake), ``icc_name`` stays None — the firmware will use its default.
    No crash of the route."""
    app.dependency_overrides[get_z9] = lambda: fake_z9
    # Backup containing the minimal fake ICC (acsp header + filler, no
    # valid desc tag per Pillow)
    p = icc_backups.new_backup_path(SERIAL, MID, "off")
    p.write_bytes(_make_fake_icc())
    with TestClient(app) as client:
        r = client.post(f"/api/papers/{MID}/icc/off/rollback")
    assert r.status_code == 200
    body = r.json()
    assert body["restored_icc_name"] is None
    call = fake_z9.paper.import_icc.call_args
    assert call.kwargs["icc_name"] is None


# ─── Backup rotation max 5 ────────────────────────────────────────────


def test_backups_rotated_to_5_after_6_imports(fake_z9):
    """6 successive imports → only 5 backups kept (the oldest gone)."""
    app.dependency_overrides[get_z9] = lambda: fake_z9
    with TestClient(app) as client:
        # Create 6 backups by doing 6 successive imports
        import time as _time
        for i in range(6):
            client.put(
                f"/api/papers/{MID}/icc/off",
                files={"file": (f"{i}.icc", _make_fake_icc(), "application/octet-stream")},
            )
            _time.sleep(1.05)  # guarantees distinct ISO timestamps to the second
    backups = icc_backups.list_backups(SERIAL, MID, "off")
    assert len(backups) == 5
