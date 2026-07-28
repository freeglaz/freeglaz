"""Tests 16.1 — TRC layers + taxonomy of the ICC inspector.

Tests against the macOS ColorSync profiles and the ProStarRGB provided by
the user (flagship test case : ProPhoto/ROMM primaries + L* TRC).
"""
from pathlib import Path

import pytest

from lib.z9_client.inspect import InspectOps


REFS = {
    "sRGB": Path("/System/Library/ColorSync/Profiles/sRGB Profile.icc"),
    "AdobeRGB": Path("/System/Library/ColorSync/Profiles/AdobeRGB1998.icc"),
    "ROMM": Path("/System/Library/ColorSync/Profiles/ROMM RGB.icc"),
    "ITU-2020": Path("/System/Library/ColorSync/Profiles/ITU-2020.icc"),
    "ITU-709": Path("/System/Library/ColorSync/Profiles/ITU-709.icc"),
    "Display P3": Path("/System/Library/ColorSync/Profiles/Display P3.icc"),
    "ProStarRGB": Path.home() / ".local" / "share" / "icc" / "ProStarRGB.icc",
}


def _inspect(label: str):
    path = REFS[label]
    if not path.exists():
        pytest.skip(f"Référentiel absent : {path}")
    return InspectOps().inspect_icc_file(path)


def _eff(field: dict) -> str:
    """Return the effective value of a {auto, override, effective} field."""
    return field.get("effective") if isinstance(field, dict) else field


# ═════════════════════════ TRC ═════════════════════════

def test_trc_srgb():
    i = _inspect("sRGB")
    assert _eff(i.trc["family"]) == "srgb"
    assert i.trc["consistent_across_channels"] is True


def test_trc_adobergb():
    i = _inspect("AdobeRGB")
    assert _eff(i.trc["family"]) == "gamma_2.2"
    assert i.trc["consistent_across_channels"] is True


def test_trc_prophoto():
    i = _inspect("ROMM")
    assert _eff(i.trc["family"]) == "gamma_1.8"
    assert i.trc["consistent_across_channels"] is True


def test_trc_prostar_lstar():
    """Flagship test case 16.1 : ProStarRGB -> ProPhoto primaries + L* TRC.

    This profile illustrates why TRC and primaries must be separated : the
    name suggests ProPhoto (gamma 1.8) but the curve is L*. macOS ColorSync
    does not show it ; the freeglaz inspector must.
    """
    i = _inspect("ProStarRGB")
    assert _eff(i.trc["family"]) == "lstar"
    assert i.trc["primary_family_label"] == "L*"


def test_trc_rec709():
    i = _inspect("ITU-709")
    assert _eff(i.trc["family"]) == "rec709"


def test_trc_returns_override_pattern():
    """The family field follows the {auto, override, effective} pattern."""
    i = _inspect("sRGB")
    fam = i.trc["family"]
    assert isinstance(fam, dict)
    assert set(fam.keys()) == {"auto", "override", "effective"}
    assert fam["override"] is None
    assert fam["auto"] == fam["effective"]


def test_trc_per_channel_keys():
    i = _inspect("sRGB")
    assert "r" in i.trc["per_channel"]
    assert "g" in i.trc["per_channel"]
    assert "b" in i.trc["per_channel"]
    for ch in ("r", "g", "b"):
        assert "family" in i.trc["per_channel"][ch]
        assert "method" in i.trc["per_channel"][ch]


# ═════════════════════════ Taxonomy ═════════════════════════

def test_taxonomy_srgb():
    i = _inspect("sRGB")
    assert _eff(i.taxonomy["whitepoint"]) == "D65"
    assert _eff(i.taxonomy["primary_family"]) == "sRGB"
    assert _eff(i.taxonomy["gamut_class"]) == "sRGB"


def test_taxonomy_adobergb():
    i = _inspect("AdobeRGB")
    assert _eff(i.taxonomy["whitepoint"]) == "D65"
    assert _eff(i.taxonomy["primary_family"]) == "AdobeRGB"
    assert _eff(i.taxonomy["gamut_class"]) == "AdobeRGB"


def test_taxonomy_prophoto():
    i = _inspect("ROMM")
    assert _eff(i.taxonomy["whitepoint"]) == "D50"
    assert _eff(i.taxonomy["primary_family"]) == "ProPhoto/ROMM"
    assert _eff(i.taxonomy["gamut_class"]) == "ProPhoto"


def test_taxonomy_prostar_separates_primaries_from_trc():
    """ProStarRGB : ProPhoto/ROMM primaries but L* TRC. Verifies the two
    dimensions are properly separated (not mixed into a single field)."""
    i = _inspect("ProStarRGB")
    assert _eff(i.taxonomy["primary_family"]) == "ProPhoto/ROMM"
    assert _eff(i.trc["family"]) == "lstar"


def test_taxonomy_whitepoint_xy_exposed():
    """whitepoint_xy must be exposed as a raw measurement (not wrapped)."""
    i = _inspect("ROMM")
    xy = i.taxonomy["whitepoint_xy"]
    assert xy is not None
    assert len(xy) == 2
    # D50 ≈ (0.3457, 0.3585)
    assert abs(xy[0] - 0.3457) < 0.005
    assert abs(xy[1] - 0.3585) < 0.005


def test_taxonomy_returns_override_pattern():
    i = _inspect("sRGB")
    for key in ("gamut_class", "whitepoint", "primary_family"):
        f = i.taxonomy[key]
        assert isinstance(f, dict)
        assert set(f.keys()) == {"auto", "override", "effective"}
        assert f["override"] is None


# ═════════════════════════ Serialization ═════════════════════════

def test_to_dict_includes_trc_and_taxonomy():
    i = _inspect("sRGB")
    d = InspectOps().to_dict(i)
    assert "trc" in d
    assert "taxonomy" in d
    assert d["trc"]["family"]["effective"] == "srgb"


# ═════════════════════════ 16.2 — Conformance ═════════════════════════

def test_conformance_srgb_compliant():
    """Standard sRGB profile -> compliant (correct v2 matrix profiles
    pass without warning, since D65 is a recognized illuminant)."""
    i = _inspect("sRGB")
    assert i.conformance["compliant"] is True
    assert i.conformance["level"] == "compliant"


def test_conformance_adobergb_compliant():
    i = _inspect("AdobeRGB")
    assert i.conformance["compliant"] is True


def test_conformance_levels_well_formed():
    """The returned structure has all expected fields."""
    i = _inspect("sRGB")
    c = i.conformance
    assert set(c.keys()) >= {"compliant", "level", "issues", "summary"}
    assert c["level"] in {"compliant", "warnings", "non_compliant"}
    assert set(c["summary"].keys()) == {"errors", "warnings", "infos"}


def test_conformance_corrupted_acsp(tmp_path):
    """File without an 'acsp' signature -> fatal error."""
    src = REFS["sRGB"]
    if not src.exists():
        pytest.skip("sRGB ref absent")
    data = bytearray(src.read_bytes())
    # Corrupt the acsp signature at offset 36
    data[36:40] = b'XXXX'
    fake = tmp_path / "corrupted.icc"
    fake.write_bytes(bytes(data))
    insp = InspectOps().inspect_icc_file(fake)
    assert insp.conformance["compliant"] is False
    assert insp.conformance["level"] == "non_compliant"
    codes = [iss["code"] for iss in insp.conformance["issues"]]
    assert "MISSING_ACSP_SIGNATURE" in codes


def test_conformance_issue_messages_present():
    """Every issue must have a message."""
    i = _inspect("sRGB")
    for issue in i.conformance["issues"]:
        assert issue["message"]
        assert issue["severity"] in {"error", "warning", "info"}
        assert issue["code"]


# ═════════════════════════ 16.2 — enriched LUT decoder ════════════════

def test_lut_decoding_cmyk_printer():
    """CMYK printer profile (GRACoL) with mft2 A2B0 : matrix + curves + grid."""
    path = Path("/Library/ColorSync/Profiles/GRACoL2006_Coated1v2.icc")
    if not path.exists():
        pytest.skip("GRACoL profile absent")
    insp = InspectOps().inspect_icc_file(path)
    a2b0 = next((t for t in insp.tags_details if t["signature"] == "A2B0"), None)
    assert a2b0 is not None
    d = a2b0["decoded"]
    assert d["lut_type"] in {"mft2", "mft1"}
    assert d["input_channels"] == 4  # CMYK
    assert d["output_channels"] == 3  # XYZ
    assert d.get("clut_dimensions")  # list of dims
    assert d.get("input_curves")
    assert d.get("output_curves")


def test_lut_input_curves_have_channel_labels():
    path = Path("/Library/ColorSync/Profiles/GRACoL2006_Coated1v2.icc")
    if not path.exists():
        pytest.skip("GRACoL absent")
    insp = InspectOps().inspect_icc_file(path)
    a2b0 = next((t for t in insp.tags_details if t["signature"] == "A2B0"), None)
    assert a2b0 is not None
    labels = [c["channel"] for c in a2b0["decoded"]["input_curves"]]
    assert labels == ["C", "M", "Y", "K"]


def test_lut_curves_downsampled():
    """The curves must be downsampled to <= 64 points."""
    path = Path("/Library/ColorSync/Profiles/GRACoL2006_Coated1v2.icc")
    if not path.exists():
        pytest.skip("GRACoL absent")
    insp = InspectOps().inspect_icc_file(path)
    a2b0 = next((t for t in insp.tags_details if t["signature"] == "A2B0"), None)
    for c in a2b0["decoded"]["input_curves"]:
        assert len(c["samples"]) <= 64


def test_lut_decoding_matrix_profile_has_no_luts():
    """A matrix-only profile (sRGB) must not have A2B/B2A LUTs."""
    i = _inspect("sRGB")
    lut_sigs = {t["signature"] for t in i.tags_details
                if t["signature"] in {"A2B0", "A2B1", "B2A0", "B2A1"}}
    assert not lut_sigs


# ═════════════════════════ 16.2 — Internals ═════════════════════════

def test_internals_structure():
    i = _inspect("sRGB")
    assert "hp91" in i.internals
    assert "custom_mapping_by_hue" in i.internals
    assert "cied" in i.internals


def test_internals_hp91_absent_on_srgb():
    """Non-HP profile -> hp91.present = False."""
    i = _inspect("sRGB")
    assert i.internals["hp91"]["present"] is False
    assert i.internals["custom_mapping_by_hue"]["present"] is False


def test_internals_to_dict_serializable():
    """The internals block must be JSON-serializable via to_dict."""
    import json
    i = _inspect("sRGB")
    d = InspectOps().to_dict(i)
    json.dumps(d)  # must not raise


# ═════════════════════════ 16.3 / 16.3.4 — Gamut 3D ═════════════════════════

def test_gamut_extract_srgb_device_surface_grid():
    """Single device_surface_grid method on sRGB (16.3.4)."""
    from lib.z9_client.gamut import extract_gamut_mesh
    p = REFS["sRGB"]
    if not p.exists():
        pytest.skip("sRGB ref absent")
    m = extract_gamut_mesh(str(p))
    assert m["n_vertices"] > 1500
    assert m["n_triangles"] > 2500
    assert m["volume_lab"] > 500_000
    assert "device_surface_grid" in m["extraction_method"]
    assert m["wireframe_style"] == "cage_device_grid"
    assert len(m["colors_srgb"]) == m["n_vertices"]
    # All vertices must lie in a valid Lab cube
    for L, a, b in m["vertices"]:
        assert -0.5 <= L <= 100.5
        assert -128.5 <= a <= 127.5
        assert -128.5 <= b <= 127.5


def test_gamut_ordering_srgb_adobergb_prophoto():
    """sRGB < AdobeRGB < ProPhoto in Lab volume."""
    from lib.z9_client.gamut import extract_gamut_mesh
    if not all(REFS[n].exists() for n in ("sRGB", "AdobeRGB", "ROMM")):
        pytest.skip("references absent")
    a = extract_gamut_mesh(str(REFS["sRGB"]))
    b = extract_gamut_mesh(str(REFS["AdobeRGB"]))
    c = extract_gamut_mesh(str(REFS["ROMM"]))
    assert a["volume_lab"] < b["volume_lab"] < c["volume_lab"]


def test_gamut_mesh_vertices_serializable():
    from lib.z9_client.gamut import extract_gamut_mesh
    import json
    p = REFS["sRGB"]
    if not p.exists():
        pytest.skip("sRGB absent")
    m = extract_gamut_mesh(str(p))
    json.dumps(m)


def test_clut_scatter_resolution_9():
    from lib.z9_client.gamut import extract_clut_scatter
    p = REFS["sRGB"]
    if not p.exists():
        pytest.skip("sRGB absent")
    s = extract_clut_scatter(str(p), intent="perceptual", resolution=9)
    assert s["n_points"] == 9 ** 3
    assert len(s["scatter"]) == 9 ** 3
    # Each entry : [L, a, b, r, g, b]
    assert all(len(pt) == 6 for pt in s["scatter"])
    # sRGB colors in [0, 1]
    for pt in s["scatter"][:50]:
        assert 0 <= pt[3] <= 1 and 0 <= pt[4] <= 1 and 0 <= pt[5] <= 1


def test_clut_scatter_resolution_17():
    from lib.z9_client.gamut import extract_clut_scatter
    p = REFS["sRGB"]
    if not p.exists():
        pytest.skip("sRGB absent")
    s = extract_clut_scatter(str(p), resolution=17)
    assert s["n_points"] == 17 ** 3


def test_intent_for_lut_tag():
    from lib.z9_client.gamut import intent_for_lut_tag
    assert intent_for_lut_tag("A2B0") == "perceptual"
    assert intent_for_lut_tag("A2B1") == "relative"
    assert intent_for_lut_tag("A2B2") == "saturation"
    assert intent_for_lut_tag("B2A0") == "perceptual"
    assert intent_for_lut_tag("XYZ ") == "perceptual"  # default fallback


# ═════════════════════════ 16.3.1 — gamt tag ═════════════════════════

def test_gamt_extract_on_profile_with_gamt():
    """Profile with a gamt tag (GRACoL CMYK) -> iso-surface extracted."""
    from lib.z9_client.gamut import extract_gamt_mesh
    p = Path("/Library/ColorSync/Profiles/GRACoL2006_Coated1v2.icc")
    if not p.exists():
        pytest.skip("GRACoL absent")
    m = extract_gamt_mesh(str(p))
    assert m["n_vertices"] > 500
    assert m["n_triangles"] > 1000
    assert m["volume_lab"] > 100_000
    assert m["extraction_method"] == "gamt_isosurface"
    assert m["source"] == "gamt_tag"


def test_gamt_extract_404_on_profile_without_gamt():
    """sRGB has no gamt tag -> ValueError."""
    from lib.z9_client.gamut import extract_gamt_mesh
    p = REFS["sRGB"]
    if not p.exists():
        pytest.skip("sRGB absent")
    with pytest.raises(ValueError, match="gamt"):
        extract_gamt_mesh(str(p))


def test_hp91_raw_fields_not_truncated():
    """Regression : a long HP91 field (e.g. reference_medium.color_mapping ~535 chars) must
    NOT be truncated in the internals block. Display bug : `_build_internals` capped at
    [:200] -> color_mapping cut at "space_target.cyan: in" (loss of the cyan/green/
    yellow mappings) ; cap raised to 2000."""
    from lib.z9_client.inspect import ProfileInspection, _build_internals
    long_val = "custom_mapping(" + ", ".join(f"radius.{i}: 30" for i in range(50)) + ")"
    assert len(long_val) > 200, "le cas de test doit dépasser l'ancien cap 200"
    insp = ProfileInspection()
    insp.is_hp_ingenium = True
    insp.hp91_config = {"saturation perceptual": {"reference_medium.color_mapping": long_val}}
    rf = _build_internals(insp)["hp91"]["raw_fields"]
    got = rf["saturation perceptual.reference_medium.color_mapping"]
    assert got == long_val, f"valeur tronquée : {len(got)}/{len(long_val)} car"


def test_gamut_extract_with_intent():
    """The intent must appear in extraction_method."""
    from lib.z9_client.gamut import extract_gamut_mesh
    p = REFS["sRGB"]
    if not p.exists():
        pytest.skip("sRGB absent")
    for intent in ("perceptual", "relative", "saturation"):
        m = extract_gamut_mesh(str(p), intent=intent)
        assert m["extraction_method"].endswith(intent)
        assert m["n_vertices"] > 0


# ═════════════════════════ 16.3.4 — single device_surface_grid ═════════════════════════

def test_gamut_wireframe_style_cage_device_grid():
    """device_surface_grid always produces wireframe_style cage_device_grid
    with edges (12 cube edges + internal grid)."""
    from lib.z9_client.gamut import extract_gamut_mesh
    p = REFS["sRGB"]
    if not p.exists():
        pytest.skip("sRGB absent")
    m = extract_gamut_mesh(str(p))
    assert m["wireframe_style"] == "cage_device_grid"
    assert isinstance(m["wireframe_edges"], list)
    # Internal grid resolution=17 step=4 : per face, 3 lines ×
    # 2 directions × 16 segments = 96 segments. 6 faces = 576. Plus
    # 12 cube edges × 16 segments = 192. Total >= 768.
    assert len(m["wireframe_edges"]) >= 700


def test_device_surface_grid_raises_on_lab_only():
    """device_surface_grid on a pure Lab profile -> ValueError (no RGB)."""
    from lib.z9_client.gamut import extract_gamut_mesh
    p = Path("/System/Library/ColorSync/Profiles/Generic Lab Profile.icc")
    if not p.exists():
        pytest.skip("Lab profile absent")
    with pytest.raises(ValueError, match=r"RGB"):
        extract_gamut_mesh(str(p))


def test_gamut_extract_cached_separates_intents(tmp_path, monkeypatch):
    """The disk cache must separate intents (distinct keys)."""
    from lib.z9_client import gamut as g
    p = REFS["sRGB"]
    if not p.exists():
        pytest.skip("sRGB absent")
    # Isolate the cache in tmp_path to avoid polluting the global cache
    monkeypatch.setattr(g, "_cache_dir", lambda: tmp_path)
    g.extract_gamut_mesh_cached(str(p), intent="perceptual")
    g.extract_gamut_mesh_cached(str(p), intent="saturation")
    files = set(tmp_path.glob("*.json"))
    assert len(files) == 2, f"expected 2 distinct intents in cache, got {files}"
