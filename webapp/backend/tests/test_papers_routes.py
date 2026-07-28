"""Tests P1.A — /api/papers endpoints."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from lib.z9_client.exceptions import Z9ConnectionError
from webapp.backend.main import app
from webapp.backend.routes.status import get_z9
from webapp.backend.services import paper_state

_FIXTURE = (
    Path(__file__).parent / "fixtures" / "medium_list_sample.xml"
).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Redirect paper_favorites/notes to tmp_path."""
    monkeypatch.setattr(paper_state, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paper_state, "FAVORITES_FILE", tmp_path / "paper_favorites.json")
    monkeypatch.setattr(paper_state, "NOTES_FILE", tmp_path / "paper_notes.json")
    yield


def _make_z9_mock(xml=_FIXTURE):
    """Z9Client mock whose paper.get_raw_xml() returns the fixture."""
    paper = MagicMock()
    paper.get_raw_xml.return_value = xml
    return SimpleNamespace(paper=paper, host="192.168.1.50")


# ─── GET /api/papers ──────────────────────────────────────────────────


def test_list_papers_returns_count_and_split():
    z9 = _make_z9_mock()
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.get("/api/papers")
    assert r.status_code == 200
    body = r.json()
    assert "papers" in body
    assert body["total"] == len(body["papers"])
    assert body["custom"] + body["factory"] == body["total"]
    # On the real fixture, we expect at least a few papers
    assert body["total"] > 10


def test_list_papers_contract_shape():
    z9 = _make_z9_mock()
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.get("/api/papers")
    p = r.json()["papers"][0]
    # Contract fields all present
    expected_keys = {
        "mediaid", "name", "category", "factory", "custom", "locked",
        "donor_id", "donor_name", "finish", "capabilities", "inks",
        "icc", "clc", "last_used", "favorite", "has_notes",
    }
    assert set(p.keys()) >= expected_keys


def test_list_papers_502_on_z9_error():
    z9 = SimpleNamespace(
        paper=MagicMock(get_raw_xml=MagicMock(
            side_effect=Z9ConnectionError("network down"),
        )),
        host="192.168.1.50",
    )
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.get("/api/papers")
    assert r.status_code == 502


def test_list_papers_503_when_z9_not_configured():
    app.dependency_overrides[get_z9] = lambda: None
    with TestClient(app) as client:
        r = client.get("/api/papers")
    assert r.status_code == 503


def test_list_papers_includes_favorite_and_has_notes():
    """If we toggle a favorite and add a note before listing,
    the flags must appear in the response."""
    paper_state.toggle_favorite("9E489F02AE027F9DD93191D872728C1D")  # Canson
    paper_state.set_notes("2001", "notes test")  # HP Premium Gloss

    z9 = _make_z9_mock()
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.get("/api/papers")
    papers = r.json()["papers"]
    canson = next(p for p in papers if p["mediaid"] == "9E489F02AE027F9DD93191D872728C1D")
    hp = next(p for p in papers if p["mediaid"] == "2001")
    assert canson["favorite"] is True
    assert hp["has_notes"] is True


# ─── POST /api/papers/{mediaid}/favorite ──────────────────────────────


def test_toggle_favorite_round_trip():
    mid = "9E489F02AE027F9DD93191D872728C1D"
    z9 = _make_z9_mock()
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        # 1st toggle: false -> true
        r1 = client.post(f"/api/papers/{mid}/favorite")
        assert r1.status_code == 200
        assert r1.json() == {"mediaid": mid, "favorite": True}
        # 2nd toggle: true -> false
        r2 = client.post(f"/api/papers/{mid}/favorite")
        assert r2.json()["favorite"] is False


def test_toggle_favorite_invalid_mediaid_returns_422():
    z9 = _make_z9_mock()
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        # Path regex rejects non-hex characters
        r = client.post("/api/papers/not-hex/favorite")
    assert r.status_code == 422


# ─── GET / PUT notes ──────────────────────────────────────────────────


def test_get_notes_empty_when_unknown():
    mid = "9E489F02AE027F9DD93191D872728C1D"
    z9 = _make_z9_mock()
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.get(f"/api/papers/{mid}/notes")
    assert r.status_code == 200
    assert r.json() == {"mediaid": mid, "notes": ""}


def test_put_notes_round_trip():
    mid = "9E489F02AE027F9DD93191D872728C1D"
    z9 = _make_z9_mock()
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r1 = client.put(f"/api/papers/{mid}/notes",
                        json={"notes": "## Test markdown\n\nHello"})
        assert r1.status_code == 200
        r2 = client.get(f"/api/papers/{mid}/notes")
        assert r2.json()["notes"] == "## Test markdown\n\nHello"


def test_put_notes_empty_clears_entry():
    mid = "9E489F02AE027F9DD93191D872728C1D"
    z9 = _make_z9_mock()
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        client.put(f"/api/papers/{mid}/notes", json={"notes": "real"})
        client.put(f"/api/papers/{mid}/notes", json={"notes": ""})
        r = client.get(f"/api/papers/{mid}/notes")
    assert r.json()["notes"] == ""


def test_put_notes_rejects_non_string_body():
    mid = "9E489F02AE027F9DD93191D872728C1D"
    z9 = _make_z9_mock()
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.put(f"/api/papers/{mid}/notes", json={"notes": 42})
    assert r.status_code == 422


# ─── POST /api/papers (P3.C1) ─────────────────────────────────


def _make_z9_with_lifecycle(xml=_FIXTURE):
    """Z9 mock with working PaperOps.create/delete/get_by_name."""
    paper = MagicMock()
    paper.get_raw_xml.return_value = xml
    paper.get_by_name.return_value = []
    paper.create.return_value = {
        "id": "ABCDEF0123456789ABCDEF0123456789",
        "name": "TestNew",
        "donor_id": "2001",
        "donor_name": "HP Premium Gloss",
        "outcome": "OK",
    }
    paper.delete.return_value = {
        "id": "9E489F02AE027F9DD93191D872728C1D",
        "name": "CansonPhotolustreRC0526",
        "outcome": "OK",
    }
    # store.get_serial(z9) (per-serial bridge, backups cleanup on delete) reads identification()
    return SimpleNamespace(paper=paper, host="192.168.1.50",
                           identification=lambda: {"SerialNumber": "CNXXXXXXXX"})


def test_create_paper_returns_paper_object():
    z9 = _make_z9_with_lifecycle()
    # The post-create refetch won't find the new MEDIAID in the
    # fixture, so Paper will be None in the response. The mediaid + donor
    # stay populated.
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.post(
            "/api/papers",
            json={"name": "TestNew", "donor_id": "2001"},
        )
    assert r.status_code == 201
    body = r.json()
    assert body["mediaid"] == "ABCDEF0123456789ABCDEF0123456789"
    assert body["donor_id"] == "2001"
    # paper.create called with the right params
    call = z9.paper.create.call_args
    assert call.kwargs == {"name": "TestNew", "donor": "2001", "language": "en_US"}


def test_create_paper_409_on_name_conflict():
    z9 = _make_z9_with_lifecycle()
    # get_by_name returns an existing paper -> conflict
    z9.paper.get_by_name.return_value = [{
        "id": "5D1C573685FB47A8F2A2D553B398A1C6",
        "name": "TestNew",
    }]
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.post(
            "/api/papers",
            json={"name": "TestNew", "donor_id": "2001"},
        )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["error"] == "name_conflict"
    assert detail["existing_mediaid"] == "5D1C573685FB47A8F2A2D553B398A1C6"
    # paper.create NOT called
    assert not z9.paper.create.called


def test_create_paper_force_bypasses_name_check():
    z9 = _make_z9_with_lifecycle()
    z9.paper.get_by_name.return_value = [{
        "id": "5D1C573685FB47A8F2A2D553B398A1C6",
        "name": "TestNew",
    }]
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.post(
            "/api/papers",
            json={"name": "TestNew", "donor_id": "2001", "force": True},
        )
    assert r.status_code == 201
    # get_by_name not called (force short-circuits the check)
    assert not z9.paper.get_by_name.called
    assert z9.paper.create.called


def test_create_paper_422_on_empty_name():
    z9 = _make_z9_with_lifecycle()
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.post("/api/papers", json={"name": "   ", "donor_id": "2001"})
    assert r.status_code == 422


def test_create_paper_422_on_forbidden_chars():
    z9 = _make_z9_with_lifecycle()
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.post(
            "/api/papers",
            json={"name": "Test <script>", "donor_id": "2001"},
        )
    assert r.status_code == 422


def test_create_paper_refuses_non_ascii():
    # Free text input (case 1): we REJECT accents/non-ASCII (422), no silent strip
    # (avoids the `ü` mluc encoding bug downstream). Papers FETCHED from the Z9 keep
    # their accents — they don't go through this creation route.
    z9 = _make_z9_with_lifecycle()
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.post(
            "/api/papers",
            json={"name": "Hahnemühle Côté Mat", "donor_id": "2001"},
        )
    assert r.status_code == 422
    # ASCII variant accepted
    with TestClient(app) as client:
        r2 = client.post(
            "/api/papers",
            json={"name": "Hahnemuhle Cote Mat", "donor_id": "2001"},
        )
    assert r2.status_code == 201


def test_create_paper_502_on_soap_error():
    z9 = _make_z9_with_lifecycle()
    from lib.z9_client.exceptions import Z9Error
    z9.paper.create.side_effect = Z9Error("newCustomMedium failed")
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.post(
            "/api/papers",
            json={"name": "TestNew", "donor_id": "2001"},
        )
    assert r.status_code == 502


# ─── DELETE /api/papers/{mediaid} (P3.C1) ─────────────────────


def test_delete_paper_success(tmp_path, monkeypatch):
    from lib.z9_client import icc_backups
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    SERIAL = "CNXXXXXXXX"

    z9 = _make_z9_with_lifecycle()
    app.dependency_overrides[get_z9] = lambda: z9
    mid = "9E489F02AE027F9DD93191D872728C1D"

    # Create orphan backups for this mediaid under backups/<serial>/<mid>/
    icc_backups.new_backup_path(SERIAL, mid, "off").write_bytes(b"x")
    media_dir = icc_backups.backups_root() / SERIAL / mid
    assert media_dir.exists()

    with TestClient(app) as client:
        r = client.delete(f"/api/papers/{mid}")
    assert r.status_code == 200
    body = r.json()
    assert body["mediaid"] == mid
    assert body["outcome"] == "OK"
    # Backups of the deleted paper (purge_media per-serial)
    assert not media_dir.exists()


def test_delete_paper_403_on_factory_format():
    """Webapp-side prefilter: short numeric MEDIAID -> 403 without
    calling SOAP (saves a round-trip)."""
    z9 = _make_z9_with_lifecycle()
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.delete("/api/papers/2001")
    assert r.status_code == 403
    assert not z9.paper.delete.called


def test_delete_paper_403_on_factory_from_lib():
    """If the mediaid passes the prefilter but the lib refuses, we
    relay the 403 with the exact message from the lib."""
    z9 = _make_z9_with_lifecycle()
    from lib.z9_client.exceptions import Z9PaperError
    z9.paper.delete.side_effect = Z9PaperError(
        "Refus de supprimer un papier factory : 'HP Coated' (id=2001)"
    )
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.delete("/api/papers/9E489F02AE027F9DD93191D872728C1D")
    assert r.status_code == 403


def test_delete_paper_409_when_loaded():
    z9 = _make_z9_with_lifecycle()
    from lib.z9_client.exceptions import Z9PaperError
    z9.paper.delete.side_effect = Z9PaperError(
        "Refusing to delete 'CansonPhotolustreRC0526': it is the paper "
        "currently loaded on the roll."
    )
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.delete("/api/papers/9E489F02AE027F9DD93191D872728C1D")
    assert r.status_code == 409


def test_delete_paper_invalid_mediaid_422():
    z9 = _make_z9_with_lifecycle()
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.delete("/api/papers/not-hex")
    assert r.status_code == 422
