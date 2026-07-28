"""Tests P1.A — paper_state service (favorites + JSON notes)."""
import pytest

from webapp.backend.services import paper_state


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Redirect JSON files to tmp_path for test isolation."""
    monkeypatch.setattr(paper_state, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paper_state, "FAVORITES_FILE", tmp_path / "paper_favorites.json")
    monkeypatch.setattr(paper_state, "NOTES_FILE", tmp_path / "paper_notes.json")
    yield


# --- Favorites --------------------------------------------------------


def test_load_favorites_empty_when_no_file():
    assert paper_state.load_favorites() == {}


def test_toggle_favorite_adds_then_removes():
    assert paper_state.toggle_favorite("MED-A") is True
    assert paper_state.is_favorite("MED-A") is True
    # Re-toggle = removes
    assert paper_state.toggle_favorite("MED-A") is False
    assert paper_state.is_favorite("MED-A") is False


def test_toggle_favorite_multiple():
    paper_state.toggle_favorite("A")
    paper_state.toggle_favorite("B")
    paper_state.toggle_favorite("C")
    favs = paper_state.load_favorites()
    assert set(favs) == {"A", "B", "C"}
    paper_state.toggle_favorite("B")
    favs = paper_state.load_favorites()
    assert set(favs) == {"A", "C"}


def test_favorites_persisted_atomically(tmp_path):
    paper_state.toggle_favorite("X")
    # No orphan .tmp after the write
    assert not (tmp_path / "paper_favorites.json.tmp").exists()
    assert (tmp_path / "paper_favorites.json").exists()


def test_load_favorites_filters_falsy_values(tmp_path):
    """If the JSON contains a falsy value (False, "" etc.), it is
    filtered out — only truthy values count as favorites."""
    (tmp_path / "paper_favorites.json").write_text(
        '{"A": true, "B": false, "C": true}', encoding="utf-8",
    )
    assert paper_state.load_favorites() == {"A": True, "C": True}


def test_load_favorites_handles_corrupted_json(tmp_path):
    (tmp_path / "paper_favorites.json").write_text("{ broken", encoding="utf-8")
    assert paper_state.load_favorites() == {}


# --- Notes ------------------------------------------------------------


def test_get_notes_empty_when_unknown():
    assert paper_state.get_notes("UNKNOWN") == ""


def test_set_and_get_notes_round_trip():
    paper_state.set_notes("M1", "## Hello\n\nSome markdown.")
    assert paper_state.get_notes("M1") == "## Hello\n\nSome markdown."


def test_set_notes_empty_removes_key():
    """Empty or whitespace-only notes -> key removed from JSON (consistent
    with has_notes on the list_papers side)."""
    paper_state.set_notes("M1", "real content")
    assert paper_state.get_notes("M1") == "real content"
    paper_state.set_notes("M1", "")
    assert paper_state.get_notes("M1") == ""
    assert "M1" not in paper_state.notes_keys()


def test_set_notes_whitespace_only_removes_key():
    paper_state.set_notes("M1", "content")
    paper_state.set_notes("M1", "   \n\n  ")
    assert "M1" not in paper_state.notes_keys()


def test_notes_keys_returns_only_non_empty():
    paper_state.set_notes("A", "real notes")
    paper_state.set_notes("B", "")  # empty -> not in the set
    paper_state.set_notes("C", "more notes")
    assert paper_state.notes_keys() == {"A", "C"}


def test_notes_handles_unicode():
    """Hahnemühle -> utf-8 OK in markdown."""
    txt = "Notes sur le **Hahnemühle**\n\n- Tonalité froide légère\n- Voir spectre OBA"
    paper_state.set_notes("MED-HM", txt)
    assert paper_state.get_notes("MED-HM") == txt
