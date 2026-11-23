"""Publication contract, gaps C1-C3 of note 135 (lib.z9_client.measurement_publish and the
record contract of lib.z9_client.measurement_identity). Pure, offline, synthetic ti3.

- C1: a NEW external reading is published only with its spectra; SOL and records already
  on disk are read as before.
- C2: the argv of an external record must be a real chartread reading: no simulation or
  foreign-engine option, no typed or spectrum-less mode, no other program. Declarative
  fields of a record (``simulation``, an engine "verified" flag) are never interpreted.
- C3: an empty ti3 is ``measurement_empty``, not ``measurement_unreadable``."""
import hashlib
import json

import pytest

from lib.z9_client import measurement_identity as mi
from lib.z9_client import measurement_publish as mp
from webapp.backend.tests.test_measurement_identity import (
    CID, EXT_BANDS, NOW, SOL, _chart_dir, _pub_record, _stage, _visible, colormunki_ti3, grid,
    ident, record, ti3)

IDS = [r[0] for r in grid()]


def _publish(tmp_path, data, rec=None):
    d = _chart_dir(tmp_path)
    return d, mp.publish_measurement(d, _stage(d, data), rec or _pub_record(), IDS, now=NOW)


def _refused(tmp_path, data, rec=None):
    d = _chart_dir(tmp_path)
    with pytest.raises(mp.PublishError) as e:
        mp.publish_measurement(d, _stage(d, data), rec or _pub_record(), IDS, now=NOW)
    assert _visible(d) == []                                  # nothing published
    return e.value


def cm_ti3(bands=EXT_BANDS, rows=None):
    return ti3(rows, instrument="X-Rite ColorMunki", bands=bands, calstd="XRGA", loc=True)


# ─── C1: spectra required for a new external reading ───────────────────────
@pytest.mark.parametrize("bands,reason", [
    ([], "no_spectra"),                                     # chartread -n, an XYZ-only replay
    ([550], "single_band"),
    ([380, 390, 410, 420], "irregular_grid"),
    ([380, 390, 390, 400], "irregular_grid"),               # a band twice
])
def test_external_reading_without_proper_spectra_is_refused(tmp_path, bands, reason):
    e = _refused(tmp_path, cm_ti3(bands))
    assert e.code == "measurement_not_spectral" and e.info["reason"] == reason


def test_non_numeric_spectral_value_is_refused(tmp_path):
    data = cm_ti3()
    assert data.count(b" 0.05000 ") == 1                    # first band of the black patch
    e = _refused(tmp_path, data.replace(b" 0.05000 ", b" nan ", 1))
    assert (e.code, e.info["reason"], e.info["column"]) == ("measurement_not_spectral", "non_numeric", "SPEC_380")


def test_regular_grids_are_published_including_rounded_names(tmp_path):
    _, out = _publish(tmp_path, cm_ti3())
    assert len(out.identity.spectral_bands) == 36
    rounded = [380, 383, 387, 390, 393, 397, 400]            # 3.33 nm, integer column names
    assert mp.check_spectral(cm_ti3(rounded).decode()) == tuple(float(b) for b in rounded)


def test_sol_publication_is_unchanged_without_spectra(tmp_path):
    """C1 is about external readings: a SOL record keeps its rules (no regression)."""
    rec = {"method": "sol", "instrument": {"model": "hp_z9_embedded", "serial": None},
           "acquisition": {"measured_at": None}, "chart": {"chart_id": CID}, "raw_files": []}
    _, out = _publish(tmp_path, ti3(instrument=SOL, bands=[]), rec)
    assert out.identity.method == "sol" and out.identity.spectral_bands == ()


def test_records_already_on_disk_are_still_read(tmp_path):
    """C1 is checked at publication only: an external ti3 without spectra published before
    it (X14's replay case) keeps its identity; it is just not combinable."""
    i = ident(cm_ti3([]), record())
    assert i.method == "external" and i.instrument == "colormunki" and i.spectral_bands == ()
    meas = tmp_path / CID / "measurements"
    meas.mkdir(parents=True)
    data = cm_ti3([])
    (meas / "old.ti3").write_bytes(data)
    rec = {**record(chart_id=CID), "ti3": {"name": "old.ti3", "sha256": hashlib.sha256(data).hexdigest()}}
    (meas / "old.json").write_text(json.dumps(rec))
    assert mi.describe_identity(meas / "old.ti3")["has_record"] is True


# ─── C2: provenance contract of the argv ────────────────────────────────────
@pytest.mark.parametrize("argv", [
    ("chartread", "--replay", "replay.txt", "c"),           # ChromIQ's simulation
    ("chartread", "--json", "c"),
    ("chartread", "-x", "c"), ("chartread", "-xl", "c"), ("chartread", "-xx", "c"),   # typed values
    ("chartread", "-n", "c"), ("chartread", "-nv", "c"),    # no spectra (one letter per argument)
    ("chartread", "-e", "c"), ("chartread", "-d", "c"), ("chartread", "-tv", "c"),     # not reflective
    ("chromiq-chartread", "--json", "--replay", "s", "c"),  # another program
    ("chromiq-chartread", "-v", "c"),
    ("fakeread", "c"),
    ("/usr/bin/python3", "replay.py"),
])
def test_simulation_and_forbidden_arguments_are_refused(argv):
    with pytest.raises(mi.MeasurementRecordError) as e:
        ident(colormunki_ti3(), record(argv=argv))
    assert e.value.code == "measurement_record_unsupported"


@pytest.mark.parametrize("argv", [
    ("chartread", "-v", "c"),
    ("/opt/homebrew/bin/chartread", "-v", "-K", "-r", "-N", "c"),
    ("C:\\Argyll_V3.5.0\\bin\\chartread.exe", "-v", "c"),
    ("/app/chartread-fgK/chartread-fgK", "-v", "-c", "1", "-K", "c"),
])
def test_real_chartread_readings_are_accepted(argv):
    assert ident(colormunki_ti3(), record(argv=argv)).method == "external"


def test_filter_values_are_not_options():
    """-F n is the M0 condition, not -n: only arguments starting with '-' are options."""
    from webapp.backend.tests.test_measurement_identity import i1pro3_ti3
    assert ident(i1pro3_ti3(), record("i1pro3plus", ("chartread", "-F", "n", "c"))).condition == "M0"
    assert ident(i1pro3_ti3(), record("i1pro3plus", ("chartread", "-Fn", "c"))).condition == "M0"


def test_declarative_fields_are_never_taken_as_proof():
    """A record cannot vouch for itself: claiming a verified engine does not let a replay
    through, and a record that says it is a simulation is not read differently either
    (synthetic test data stays explicitly labelled; what it may prove is decided by the
    backend from a session it ran, later)."""
    claim = {"engine": {"verified": True, "sha256": "0" * 64}}
    with pytest.raises(mi.MeasurementRecordError):
        ident(colormunki_ti3(), record(argv=("chartread", "--replay", "s", "c"), **claim))
    i = ident(colormunki_ti3(), record(simulation={"note": "lab, synthetic values"}, **claim))
    assert i.method == "external" and "verified" not in json.dumps(i.to_dict())


def test_x14_shaped_records_keep_their_outcome(tmp_path):
    """The X14 lab bench: its spectral readings still publish; its XYZ-only replay is now
    refused by C1 (that gap was the point)."""
    rec = {"method": "external", "instrument": {"model": "colormunki", "serial": None},
           "acquisition": {"tool": "chartread", "argv": ["chartread", "-v", "-K", "-N", "-r", "chart"],
                           "measured_at": "2026-11-23T00:19:32+01:00",
                           "software": {"engine": "chartread (ArgyllCMS 3.5.0 + -K patch)",
                                        "origin": "SIMULATED (checkpoint lab, fake instrument, no hardware)"}},
           "chart": {"chart_id": None}, "raw_files": [],
           "simulation": {"note": "note 136 lab; synthetic values, not a measurement"}}
    d, out = _publish(tmp_path, cm_ti3(), rec)
    assert json.loads(out.record_path.read_text())["simulation"]["note"].startswith("note 136")
    replay = {**rec, "acquisition": {**rec["acquisition"], "argv": ["chartread", "-v", "chart"]}}
    with pytest.raises(mp.PublishError) as e:
        mp.publish_measurement(d, _stage(d, cm_ti3([])), replay, IDS, now=NOW)
    assert e.value.code == "measurement_not_spectral"


# ─── C3: an empty ti3 ───────────────────────────────────────────────────────
def _header_only(with_data_block=True):
    lines = ["CTI3", "", 'DESCRIPTOR "Argyll Calibration Target chart information 3"',
             'KEYWORD "TARGET_INSTRUMENT"', 'TARGET_INSTRUMENT "X-Rite ColorMunki"',
             "NUMBER_OF_FIELDS 8", "BEGIN_DATA_FORMAT",
             "SAMPLE_ID SAMPLE_LOC RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z", "END_DATA_FORMAT",
             "NUMBER_OF_SETS 0"]
    if with_data_block:
        lines += ["BEGIN_DATA", "END_DATA"]
    return ("\n".join(lines) + "\n").encode()


@pytest.mark.parametrize("data", [b"", b"  \n\n", _header_only(), _header_only(with_data_block=False)])
def test_empty_ti3_is_classified_empty(tmp_path, data):
    e = _refused(tmp_path, data)
    assert e.code == "measurement_empty" and (e.info["expected"], e.info["found"]) == (len(IDS), 0)


TRUNCATED = _header_only().replace(b"END_DATA\n", b"1 A1 0 0 0 1 1 1\n")    # no END_DATA


@pytest.mark.parametrize("data", [b"FAKE", TRUNCATED])
def test_unreadable_is_not_empty(tmp_path, data):
    assert _refused(tmp_path, data).code == "measurement_unreadable"
