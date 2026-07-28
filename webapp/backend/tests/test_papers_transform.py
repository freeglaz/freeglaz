"""P1.A tests — transform SOAP MediumList to Paper contract."""
from pathlib import Path

import pytest

from lib.z9_client.papers import (
    _capabilities_normalized,
    _clc_status,
    _compute_finish,
    _inks_from_details,
    _is_custom_mediaid,
    _pick_icc_tickets,
    _resolve_donor,
    transform_all,
    transform_paper,
)
from lib.z9_client.parsers import parse_soap_medium_list

_FIXTURE = (
    Path(__file__).parent / "fixtures" / "medium_list_sample.xml"
).read_text(encoding="utf-8")


# ─── _compute_finish ──────────────────────────────────────────────────


@pytest.mark.parametrize("name, donor, expected", [
    # Gloss family
    ("HP Premium Instant-dry Gloss Photo Paper", None, "gloss"),
    ("CansonPhotolustreRC", None, "gloss"),       # lustre
    ("Hahnemühle Photo Satin", None, "gloss"),
    ("Ilford Galerie Baryta", None, "gloss"),
    ("Pearl Photo Paper", None, "gloss"),
    ("Metallic Foil Paper", None, "gloss"),
    # Matte family
    ("Hahnemühle Photo Rag 308g", None, "matte"),
    ("HP Matte Polypropylene", None, "matte"),
    ("Canson Aquarelle Rag", None, "matte"),
    ("Sample Cotton Paper", None, "matte"),
    # Canvas (first match: "satin" wins over "canvas" cf. dedicated test below)
    ("Pure Canvas Print", None, "canvas"),
    # Film
    ("HP Backlit Polyester Film", None, "film"),
    ("Generic Clear Film", None, "film"),
    # Other (no match)
    ("Unnamed Paper 2026", None, "other"),
    ("Plain Paper", None, "other"),
    # Fallback to donor_name when name has no keyword
    ("Mon papier 2026", "HP Universal Cotton Rag", "matte"),
    ("Mon papier 2026", "HP Premium Gloss", "gloss"),
    # Case and accent insensitive
    ("HAHNEMÜHLE PHOTO RAG", None, "matte"),
    ("Canson photolusTRE", None, "gloss"),
])
def test_compute_finish_keywords(name, donor, expected):
    assert _compute_finish(name, donor) == expected


def test_compute_finish_ambiguous_satin_canvas_takes_first_match():
    """A 'Satin Canvas' paper contains both "satin" (gloss) and
    "canvas" (canvas). In the _FINISH_KEYWORDS table, "satin" is
    listed BEFORE "canvas" -> first match wins -> finish='gloss'.

    Behavior aligned with the frozen order in the user brief (gloss
    keywords first, canvas in 3rd position). To make canvas take
    priority, the table would need reordering."""
    assert _compute_finish("HP Premium Satin Canvas", None) == "gloss"


# ─── _resolve_donor ───────────────────────────────────────────────────


def test_resolve_donor_returns_name_when_found():
    index = {
        "2090": {"name": "HP Premium Glossy", "short_name": "HP Glossy"},
        "4060": {"name": "HP Fine Art Paper", "short_name": "Fine Art"},
    }
    assert _resolve_donor("2090", index) == "HP Premium Glossy"
    assert _resolve_donor("4060", index) == "HP Fine Art Paper"


def test_resolve_donor_returns_none_when_unknown():
    assert _resolve_donor("9999", {"2090": {"name": "X"}}) is None


def test_resolve_donor_returns_none_on_empty_donor_id():
    assert _resolve_donor(None, {"2090": {"name": "X"}}) is None
    assert _resolve_donor("", {"2090": {"name": "X"}}) is None


def test_resolve_donor_falls_back_to_short_name():
    index = {"2090": {"name": "", "short_name": "ShortOnly"}}
    assert _resolve_donor("2090", index) == "ShortOnly"


# ─── _clc_status ──────────────────────────────────────────────────────
# The CLC status is now cross-checked: primary source LEDM
# (4 statuses valid/stale/pending/never), fallback SOAP <Calibration>
# (2 statuses valid/stale) if LEDM absent for this MEDIAID.


def test_clc_status_ledm_completed_maps_to_valid():
    out = _clc_status({}, ledm_entry={"status": "completed",
                                       "timestamp": "2026-05-25T10:57:18Z"})
    assert out == {"status": "valid", "date": "2026-05-25T10:57:18Z"}


def test_clc_status_ledm_obsoleted_maps_to_stale():
    out = _clc_status({}, ledm_entry={"status": "obsoleted",
                                       "timestamp": "2020-09-26T08:00:00Z"})
    assert out == {"status": "stale", "date": "2020-09-26T08:00:00Z"}


def test_clc_status_ledm_pending_maps_to_pending_no_date():
    """P3 critical case: freshly created custom paper waiting for its
    first CLC. LEDM exposes pending with no timestamp."""
    out = _clc_status({}, ledm_entry={"status": "pending", "timestamp": None})
    assert out == {"status": "pending", "date": None}


def test_clc_status_ledm_notdone_maps_to_never():
    out = _clc_status({}, ledm_entry={"status": "notDone", "timestamp": None})
    assert out == {"status": "never", "date": None}


def test_clc_status_ledm_unknown_status_fallback_never():
    """Undocumented LEDM status -> defensive fallback "never"."""
    out = _clc_status({}, ledm_entry={"status": "weirdNewStatus", "timestamp": None})
    assert out == {"status": "never", "date": None}


def test_clc_status_fallback_soap_when_no_ledm():
    """Without ledm_entry, fall back to SOAP <Calibration> block."""
    out = _clc_status(
        {"calibration": {"date": "2026-05-11", "obsolete": False}},
        ledm_entry=None,
    )
    assert out == {"status": "valid", "date": "2026-05-11"}

    out = _clc_status(
        {"calibration": {"date": "2020-09-26", "obsolete": True}},
        ledm_entry=None,
    )
    assert out == {"status": "stale", "date": "2020-09-26"}


def test_clc_status_never_when_no_source():
    """No LEDM, no SOAP <Calibration> -> never."""
    assert _clc_status({}, ledm_entry=None) == {"status": "never", "date": None}
    assert _clc_status({"calibration": None}, ledm_entry=None) == {"status": "never", "date": None}
    assert _clc_status({"calibration": {}}, ledm_entry=None) == {"status": "never", "date": None}


def test_clc_status_ledm_priority_over_soap():
    """If both sources are present, LEDM wins."""
    out = _clc_status(
        {"calibration": {"date": "2020-09-26", "obsolete": True}},  # SOAP says stale
        ledm_entry={"status": "completed", "timestamp": "2026-05-25T10:00:00Z"},  # LEDM says valid
    )
    assert out["status"] == "valid"
    assert out["date"] == "2026-05-25T10:00:00Z"


# ─── _pick_icc_tickets ────────────────────────────────────────────────


def test_pick_icc_two_slots_when_ge_supported():
    profiles = [
        {"icc_name": "factory-off", "date": "2018-03-20", "custom": False, "gloss_enhancer": "OFF"},
        {"icc_name": "custom-off-newer", "date": "2024-01-15", "custom": True, "gloss_enhancer": "OFF"},
        {"icc_name": "factory-on", "date": "2018-03-20", "custom": False, "gloss_enhancer": "ON"},
        {"icc_name": "custom-on", "date": "2026-05-11", "custom": True, "gloss_enhancer": "ON"},
    ]
    out = _pick_icc_tickets(profiles, ge_supported=True)
    assert len(out) == 2
    # ge_off: pick custom-off-newer (date 2024 > 2018)
    assert out[0]["kind"] == "ge_off"
    assert out[0]["name"] == "custom-off-newer"
    assert out[0]["custom"] is True
    # ge_on: pick custom-on (2026 > 2018)
    assert out[1]["kind"] == "ge_on"
    assert out[1]["name"] == "custom-on"


def test_pick_icc_single_slot_when_ge_not_supported():
    profiles = [
        {"icc_name": "old",   "date": "2018-01-01", "custom": False, "gloss_enhancer": ""},
        {"icc_name": "newer", "date": "2026-03-01", "custom": True,  "gloss_enhancer": ""},
    ]
    out = _pick_icc_tickets(profiles, ge_supported=False)
    assert len(out) == 1
    assert out[0]["kind"] == "single"
    assert out[0]["name"] == "newer"


def test_pick_icc_empty_profiles_returns_empty():
    assert _pick_icc_tickets([], ge_supported=True) == []
    assert _pick_icc_tickets([], ge_supported=False) == []


def test_pick_icc_fullpage_treated_as_ge_on():
    """Regression: the Z9 firmware emits ``<GlossEnhancer>FULLPAGE``
    in the ProfilingTicket to signal GE ON, not ``"ON"``. The initial
    P1.A brief assumed "ON" and the ge_on slot silently disappeared
    from the UI. We want FULLPAGE treated as ge_on (and the defensive
    "ON" compatibility kept)."""
    profiles = [
        {"icc_name": "factory-off", "date": "2018-05-29", "custom": False, "gloss_enhancer": "OFF"},
        {"icc_name": "factory-on-fullpage", "date": "2018-05-29", "custom": False, "gloss_enhancer": "FULLPAGE"},
        {"icc_name": "custom-off-newer", "date": "2026-05-20", "custom": True, "gloss_enhancer": "OFF"},
    ]
    out = _pick_icc_tickets(profiles, ge_supported=True)
    assert len(out) == 2
    assert out[0]["kind"] == "ge_off"
    assert out[0]["name"] == "custom-off-newer"
    assert out[0]["custom"] is True
    assert out[1]["kind"] == "ge_on"
    assert out[1]["name"] == "factory-on-fullpage"
    assert out[1]["custom"] is False
    assert out[1]["date"] == "2018-05-29"


def test_pick_icc_missing_ge_on_emits_only_ge_off():
    """If GE is supported but there is no GE ON profile, emit just the
    OFF slot (no artificial empty slot). The UI will show "no GE ON
    profile" through the absence of that slot."""
    profiles = [
        {"icc_name": "off", "date": "2018-03-20", "custom": False, "gloss_enhancer": "OFF"},
    ]
    out = _pick_icc_tickets(profiles, ge_supported=True)
    assert len(out) == 1
    assert out[0]["kind"] == "ge_off"


# ─── _capabilities_normalized ─────────────────────────────────────────


def test_capabilities_normalized_string_to_bool():
    caps = {
        "Borderless": "1",
        "GlossEnhancerSupported": "1",
        "MaxDetailSupported": "0",
        "ScanningSupported": "1",
    }
    assert _capabilities_normalized(caps) == {
        "borderless": True, "ge": True, "max_detail": False, "scanning": True,
    }


def test_capabilities_normalized_missing_keys_default_false():
    assert _capabilities_normalized({}) == {
        "borderless": False, "ge": False, "max_detail": False, "scanning": False,
    }


# ─── _inks_from_details ───────────────────────────────────────────────


def test_inks_from_details_pk_mk_both_true():
    details = {"Uses Photo Black": "1", "Uses Matte Black": "1"}
    assert _inks_from_details(details) == {"pk": True, "mk": True}


def test_inks_from_details_pk_only():
    details = {"Uses Photo Black": "1", "Uses Matte Black": "0"}
    assert _inks_from_details(details) == {"pk": True, "mk": False}


def test_inks_from_details_missing_keys():
    assert _inks_from_details({}) == {"pk": False, "mk": False}
    assert _inks_from_details(None or {}) == {"pk": False, "mk": False}


# Note: no _weight_from_details test. The function was removed (the
# SOAP Grammage (g/sqm) field is misleading, it is an internal HP ID
# distinguishing opaque/transparent, not a weight). Cf docstring of
# the ``lib.z9_client.papers`` module.


# ─── _is_custom_mediaid ───────────────────────────────────────────────


def test_is_custom_mediaid_hex32_returns_true():
    """Real customs have a 32-char hex hash MEDIAID (created host-side,
    e.g. freeglaz)."""
    assert _is_custom_mediaid("9E489F02AE027F9DD93191D872728C1D") is True
    assert _is_custom_mediaid("6942DB437B91997EDC2C504EC342E570") is True
    assert _is_custom_mediaid("164C02B0147A00695EBF1DCD3E02620A") is True
    # Mixed case accepted
    assert _is_custom_mediaid("9e489f02ae027f9dd93191d872728c1d") is True


def test_is_custom_mediaid_short_numeric_returns_false():
    """Factory papers have a short numeric ID."""
    assert _is_custom_mediaid("1000") is False
    assert _is_custom_mediaid("2001") is False
    assert _is_custom_mediaid("2090") is False
    assert _is_custom_mediaid("4060") is False


def test_is_custom_mediaid_edge_cases():
    assert _is_custom_mediaid(None) is False
    assert _is_custom_mediaid("") is False
    assert _is_custom_mediaid("9E489F02") is False  # too short
    # 32 chars but not hex
    assert _is_custom_mediaid("GGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG") is False


# ─── End-to-end on the real fixture ────────────────────────────────────


def test_transform_all_on_live_capture():
    """End-to-end validation: parser + transformer on the real Z9 dump
    (54 papers at the time of the capture)."""
    raw = parse_soap_medium_list(_FIXTURE)
    papers = transform_all(raw)
    assert len(papers) >= 40  # tolerance, the Z9 list evolves
    # Each paper has the minimal contract structure
    for p in papers:
        assert isinstance(p["mediaid"], str)
        assert isinstance(p["name"], str)
        assert isinstance(p["factory"], bool)
        assert isinstance(p["custom"], bool)
        assert p["factory"] != p["custom"]
        assert isinstance(p["capabilities"], dict)
        assert set(p["capabilities"]) == {"borderless", "ge", "max_detail", "scanning"}
        assert isinstance(p["inks"], dict)
        assert set(p["inks"]) == {"pk", "mk"}
        assert p["finish"] in {"gloss", "matte", "canvas", "film", "other"}
        assert p["clc"]["status"] in {"valid", "stale", "never"}
        assert isinstance(p["icc"], list)
        assert p["last_used"] is None  # Phase 1 fallback
        assert isinstance(p["favorite"], bool)
        assert isinstance(p["has_notes"], bool)


def test_transform_canson_fixture():
    """Regression on Fixture 1 of the brief (Canson custom GE)."""
    raw = parse_soap_medium_list(_FIXTURE)
    papers = transform_all(raw)
    p = next(p for p in papers if p["mediaid"] == "9E489F02AE027F9DD93191D872728C1D")
    assert p["name"] == "CansonPhotolustreRC0526"
    assert p["factory"] is False
    assert p["custom"] is True
    assert p["locked"] is True
    assert p["donor_id"] == "2090"
    assert p["finish"] == "gloss"
    assert p["capabilities"]["ge"] is True
    assert p["clc"]["status"] == "valid"
    assert p["clc"]["date"] == "2026-05-11"


def test_transform_hahnemuhle_fixture():
    """Regression on Fixture 2 of the brief (Hahnemühle 308g, CLC stale)."""
    raw = parse_soap_medium_list(_FIXTURE)
    papers = transform_all(raw)
    p = next(p for p in papers if p["mediaid"] == "2C786AC69B053030696739D5EAC34E34")
    assert "Hahnemühle" in p["name"]
    assert p["finish"] == "matte"
    assert p["capabilities"]["ge"] is False
    assert p["clc"]["status"] == "stale"
    assert p["clc"]["date"] == "2020-09-26"


def test_transform_factory_custom_split_by_mediaid_format():
    """Regression: the factory/custom criterion must be based on the
    MEDIAID format (32 hex = custom, short numeric = factory), not on
    the XML <factory> attribute which wrongly returns "0" on some
    factory papers added via firmware update.

    User brief acceptance criterion: 7 customs + 47 factory = 54 total
    on the live fixture."""
    raw = parse_soap_medium_list(_FIXTURE)
    papers = transform_all(raw)
    customs = [p for p in papers if p["custom"]]
    factory = [p for p in papers if p["factory"]]

    assert len(papers) == 54, f"Attendu 54, obtenu {len(papers)}"
    assert len(customs) == 7, (
        f"Attendu 7 customs, obtenu {len(customs)} : "
        f"{[p['name'] for p in customs]}"
    )
    assert len(factory) == 47, f"Attendu 47 factory, obtenu {len(factory)}"

    # No paper double-classified
    for p in papers:
        assert p["factory"] != p["custom"]

    # All customs have a 32-char hex MEDIAID
    for p in customs:
        assert len(p["mediaid"]) == 32, (
            f"Custom {p['name']!r} n'a pas un MEDIAID hex 32 ("
            f"{p['mediaid']!r})"
        )

    # All factory papers have a short MEDIAID (< 32 chars)
    for p in factory:
        assert len(p["mediaid"]) < 32, (
            f"Factory {p['name']!r} a un MEDIAID inattendu ("
            f"{p['mediaid']!r})"
        )

    # The 7 expected customs (from the reference paper list)
    custom_names = {p["name"] for p in customs}
    expected_customs = {
        "CansonPhotolustreRC0526",
        "Profil Papier couché HP_user",
        "Canson Photolustre RC 2021",
        "Hahnemühle Photo Rag 308g",
        "HP Recycled Satin Canvas",
        "Canson Baryta Photographique",
        "Hahnemühle Fine Art Baryta Satin",
    }
    assert custom_names == expected_customs, (
        f"Customs attendus {expected_customs}, obtenus {custom_names}"
    )


def test_transform_canson_baryta_exposes_both_ge_slots():
    """Regression: Canson Baryta Photographique (custom, GE supported)
    must expose **2 ICC slots** in the contract — a ge_off (custom
    freeglaz May 2026) + a ge_on (HP factory from 2018, firmware value
    GlossEnhancer=FULLPAGE)."""
    raw = parse_soap_medium_list(_FIXTURE)
    papers = transform_all(raw)
    p = next(p for p in papers if p["mediaid"] == "164C02B0147A00695EBF1DCD3E02620A")
    assert p["name"] == "Canson Baryta Photographique"
    assert p["capabilities"]["ge"] is True

    kinds = [slot["kind"] for slot in p["icc"]]
    assert "ge_off" in kinds, f"slot ge_off manquant, slots={kinds}"
    assert "ge_on"  in kinds, f"slot ge_on manquant, slots={kinds}"
    assert len(p["icc"]) == 2

    ge_off = next(s for s in p["icc"] if s["kind"] == "ge_off")
    ge_on  = next(s for s in p["icc"] if s["kind"] == "ge_on")
    assert ge_off["custom"] is True
    assert ge_off["name"] == "freeglaz_CansonBarytaPhotographique_GEOFF, GE OFF"
    assert ge_off["date"] == "2026-05-20"
    assert ge_on["custom"] is False
    assert ge_on["name"] == "HPDesignjetZ9CansonBarytaPhotographiqueGEON"
    assert ge_on["date"] == "2018-05-29"


def test_transform_factory_fixture():
    """Regression on Fixture 3 of the brief (HP Premium Instant-dry Gloss)."""
    raw = parse_soap_medium_list(_FIXTURE)
    papers = transform_all(raw)
    p = next(p for p in papers if p["mediaid"] == "2001")
    assert p["factory"] is True
    assert p["donor_id"] is None  # no donor for factory papers
    assert p["donor_name"] is None
    assert p["finish"] == "gloss"
    assert p["clc"]["status"] == "never"
    assert p["clc"]["date"] is None


def test_transform_injects_favorites_and_notes_flags():
    """The favorite and has_notes flags are injected from local state."""
    raw = parse_soap_medium_list(_FIXTURE)
    favorites = {"9E489F02AE027F9DD93191D872728C1D": True}
    notes_keys = {"2001"}
    papers = transform_all(raw, favorites=favorites, notes_keys=notes_keys)
    canson = next(p for p in papers if p["mediaid"] == "9E489F02AE027F9DD93191D872728C1D")
    hp = next(p for p in papers if p["mediaid"] == "2001")
    other = next(p for p in papers if p["mediaid"] == "2C786AC69B053030696739D5EAC34E34")
    assert canson["favorite"] is True and canson["has_notes"] is False
    assert hp["favorite"] is False and hp["has_notes"] is True
    assert other["favorite"] is False and other["has_notes"] is False
