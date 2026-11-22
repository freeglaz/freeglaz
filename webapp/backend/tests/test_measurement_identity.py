"""Measurement identity (lib.z9_client.measurement_identity) and the publication contract
(lib.z9_client.measurement_publish). Pure, offline: synthetic ti3 shaped like the real
outputs (SOL: profiling.build_cti3; chartread: TARGET_INSTRUMENT + DEVCALSTD + 36 bands)."""
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from lib.z9_client import argyll
from lib.z9_client import measurement_identity as mi
from lib.z9_client import measurement_publish as mp
from lib.z9_client import printtarg_chart

SOL = "HP DesignJet Z9 embedded spectro"
SOL_BANDS = [400 + 20 * i for i in range(16)]
EXT_BANDS = [380 + 10 * i for i in range(36)]


def grid(n=3):
    vals = [round(100 * k / (n - 1), 4) for k in range(n)]
    return [(str(i + 1), r, g, b) for i, (r, g, b) in
            enumerate((r, g, b) for r in vals for g in vals for b in vals)]


def ti3(rows=None, *, instrument=SOL, bands=SOL_BANDS, calstd=None, instrument_filter=None,
        loc=False, extra_header=()):
    rows = grid() if rows is None else rows
    cols = ["SAMPLE_ID"] + (["SAMPLE_LOC"] if loc else []) + ["RGB_R", "RGB_G", "RGB_B",
                                                              "XYZ_X", "XYZ_Y", "XYZ_Z"]
    cols += [f"SPEC_{b}" for b in bands]
    head = ["CTI3", "", 'DESCRIPTOR "test"']
    if instrument is not None:
        head += ['KEYWORD "TARGET_INSTRUMENT"', f'TARGET_INSTRUMENT "{instrument}"']
    if calstd:
        head.append(f'DEVCALSTD "{calstd}"')
    if instrument_filter:
        head += ['KEYWORD "INSTRUMENT_FILTER"', f'INSTRUMENT_FILTER "{instrument_filter}"']
    head += list(extra_header)
    lines = []
    for k, (sid, r, g, b) in enumerate(rows):
        spec = " ".join(f"{0.05 + (r + g + b) / 400 + j / 1000:.5f}" for j in range(len(bands)))
        lines.append(f"{sid} " + (f'"A{k + 1}" ' if loc else "")
                     + f"{r:.4f} {g:.4f} {b:.4f} {5 + r * .4:.4f} {5 + g * .5:.4f} {5 + b * .6:.4f} {spec}")
    head += [f"NUMBER_OF_FIELDS {len(cols)}", "BEGIN_DATA_FORMAT", " ".join(cols), "END_DATA_FORMAT",
             f"NUMBER_OF_SETS {len(rows)}", "BEGIN_DATA", *lines, "END_DATA"]
    return ("\n".join(head) + "\n").encode("ascii")


def colormunki_ti3(rows=None):
    return ti3(rows, instrument="X-Rite ColorMunki", bands=EXT_BANDS, calstd="XRGA", loc=True)


def i1pro3_ti3(rows=None, instrument_filter=None):
    return ti3(rows, instrument="X-Rite i1 Pro 3", bands=EXT_BANDS, calstd="XRGA", loc=True,
               instrument_filter=instrument_filter)


def record(model="colormunki", argv=("chartread", "-v", "chart"), *, sha=None, method="external",
           chart_id=None, **over):
    rec = {"schema": mi.RECORD_SCHEMA, "schema_version": 1,
           "ti3": {"name": "20261122_150000.ti3", "sha256": sha or "0" * 64},
           "method": method, "instrument": {"model": model, "serial": "SN-1"},
           "acquisition": {"tool": "chartread", "argv": list(argv) if argv is not None else None,
                           "measured_at": "2026-11-22T15:00:00Z", "software": {"argyll": "3.5.0"}},
           "chart": {"chart_id": chart_id}, "raw_files": []}
    rec.update(over)
    return rec


def ident(ti3_bytes, rec=None, **kw):
    if rec is None:
        return mi.identity_from_bytes(ti3_bytes, None, **kw)
    rec = {**rec, "ti3": {**rec["ti3"], "sha256": hashlib.sha256(ti3_bytes).hexdigest()}}
    return mi.identity_from_bytes(ti3_bytes, json.dumps(rec).encode(), **kw)


# ─── identity ────────────────────────────────────────────────────────────────
def test_historical_sol_scan_is_recognised_without_record():
    i = ident(ti3())
    assert (i.method, i.instrument, i.condition) == ("sol", "hp_z9_embedded", "fixed")
    assert i.origin == mi.ORIGIN_SOL_HEADER and i.complete
    d = i.to_dict()
    assert d["historical"] is True and d["has_record"] is False
    assert d["spectral"]["n_bands"] == 16 and d["spectral"]["step_nm"] == 20.0


def test_two_historical_sol_scans_are_compatible():
    a, b = ident(ti3()), ident(ti3(grid(4)))
    assert mi.compare_identities(a, b).status == mi.COMPATIBLE
    assert mi.check_combination([("a", a), ("b", b)]).compatible


def test_sol_and_external_are_incompatible():
    c = mi.compare_identities(ident(ti3()), ident(colormunki_ti3()))
    assert c.status == mi.INCOMPATIBLE
    assert {"method_differs", "instrument_differs", "spectral_differs"} <= {d.code for d in c.differences}


def test_same_bands_do_not_make_an_external_ti3_a_sol_scan():
    """Unknown instrument, SOL's own bands: never taken for a SOL scan."""
    unknown = ident(ti3(instrument=None))
    assert unknown.method is None and unknown.instrument is None
    assert mi.compare_identities(ident(ti3()), unknown).status == mi.INSUFFICIENT
    other = ident(ti3(instrument="Some Other Spectro"))
    assert other.method is None and other.provenance.reported_instrument == "Some Other Spectro"


def test_colormunki_and_i1pro3plus_are_incompatible_despite_equal_bands():
    cm = ident(colormunki_ti3(), record("colormunki"))
    i1 = ident(i1pro3_ti3(), record("i1pro3plus", ("chartread", "-F", "n", "chart")))
    assert cm.spectral_bands == i1.spectral_bands
    c = mi.compare_identities(cm, i1)
    assert c.status == mi.INCOMPATIBLE and "instrument_differs" in {d.code for d in c.differences}


def test_m0_and_m2_are_incompatible():
    m0 = ident(i1pro3_ti3(), record("i1pro3plus", ("chartread", "-F", "n", "chart")))
    m2 = ident(i1pro3_ti3(instrument_filter="UVCUT"), record("i1pro3plus", ("chartread", "-Fu", "chart")))
    assert (m0.condition, m2.condition) == ("M0", "M2")
    c = mi.compare_identities(m0, m2)
    assert c.status == mi.INCOMPATIBLE and [d.code for d in c.differences] == ["condition_differs"]


def test_same_instrument_and_condition_are_compatible():
    a = ident(i1pro3_ti3(instrument_filter="UVCUT"), record("i1pro3plus", ("chartread", "-F", "u", "x")))
    b = ident(i1pro3_ti3(grid(4), instrument_filter="UVCUT"), record("i1pro3plus", ("chartread", "-F", "u", "y")))
    assert a.provenance.argv != b.provenance.argv                 # provenance is never compared
    assert mi.compare_identities(a, b).status == mi.COMPATIBLE
    cm1, cm2 = ident(colormunki_ti3(), record()), ident(colormunki_ti3(grid(4)), record())
    assert cm1.condition == "fixed" and mi.compare_identities(cm1, cm2).compatible


def test_unknown_condition_is_never_taken_for_m0_or_m1():
    no_f = ident(i1pro3_ti3(), record("i1pro3plus", ("chartread", "-v", "chart")))
    assert no_f.condition == "unknown" and not no_f.complete
    for opt, cond in (("n", "M0"), ("5", "M1")):
        other = ident(i1pro3_ti3(), record("i1pro3plus", ("chartread", "-F", opt, "c")))
        assert other.condition == cond
        assert mi.compare_identities(no_f, other).status == mi.INSUFFICIENT
        assert mi.compare_identities(no_f, no_f).status == mi.INSUFFICIENT   # unknown == unknown: still unknown


def test_i1pro3_header_alone_does_not_name_the_model():
    """'X-Rite i1 Pro 3' is the whole family: i1Pro 3 or 3 Plus. Only a record names it."""
    i = ident(i1pro3_ti3())
    assert i.method == "external" and i.instrument is None and i.condition == "unknown"
    uv = ident(i1pro3_ti3(instrument_filter="UVCUT"))
    assert uv.condition == "M2" and uv.instrument is None


def test_real_chartread_colormunki_header():
    """Header lines of a real chartread ColorMunki output (no INSTRUMENT_FILTER)."""
    i = ident(colormunki_ti3())
    assert (i.method, i.instrument, i.condition, i.calibration_standard) == \
        ("external", "colormunki", "fixed", "XRGA")
    assert i.spectral_bands[0] == 380 and i.spectral_bands[-1] == 730 and len(i.spectral_bands) == 36


def test_calibration_standard_unknown_vs_known():
    a = ident(colormunki_ti3())
    b = ident(ti3(instrument="X-Rite ColorMunki", bands=EXT_BANDS, calstd=None))
    c = ident(ti3(instrument="X-Rite ColorMunki", bands=EXT_BANDS, calstd="XRDI"))
    assert mi.compare_identities(a, b).status == mi.INSUFFICIENT
    assert mi.compare_identities(a, c).status == mi.INCOMPATIBLE


def test_unrecognised_instrument_filter_makes_the_condition_unknown():
    i = ident(ti3(instrument="X-Rite ColorMunki", bands=EXT_BANDS, calstd="XRGA",
                  instrument_filter="SOMETHING"))
    assert i.condition == "unknown"


def test_combination_reports_every_conflict_and_the_worst_status():
    sol, sol2 = ident(ti3()), ident(ti3(grid(4)))
    unknown, cm = ident(ti3(instrument=None)), ident(colormunki_ti3())
    d = mi.check_combination([("s1", sol), ("s2", sol2), ("u", unknown), ("cm", cm)])
    assert d.status == mi.INCOMPATIBLE and d.combined
    assert [(a, b) for a, b, _ in d.conflicts] == [("s1", "u"), ("s1", "cm")]
    assert mi.check_combination([("s1", sol), ("u", unknown)]).status == mi.INSUFFICIENT
    single = mi.check_combination([("u", unknown)])
    assert single.compatible and not single.combined          # one scan alone: nothing combined


def test_identity_dict_round_trip():
    for i in (ident(ti3()), ident(colormunki_ti3(), record()),
              ident(i1pro3_ti3(instrument_filter="UVCUT"), record("i1pro3plus", ("chartread", "-F", "u", "c")))):
        back = mi.identity_from_dict(json.loads(json.dumps(i.to_dict())))
        assert back.key() == i.key() and mi.compare_identities(back, i).compatible
    with pytest.raises(ValueError):
        mi.identity_from_dict({"identity_version": 1, "method": "sol", "instrument": "nope",
                               "condition": "fixed", "origin": "record"})


# ─── records: refused cleanly ────────────────────────────────────────────────
@pytest.mark.parametrize("mutate,code", [
    (lambda r: {**r, "schema": "other"}, "measurement_record_malformed"),
    (lambda r: {**r, "schema_version": 2}, "measurement_record_unsupported"),
    (lambda r: {**r, "schema_version": "1"}, "measurement_record_malformed"),
    (lambda r: {**r, "method": "manual"}, "measurement_record_malformed"),
    (lambda r: {**r, "instrument": {"model": "spyder"}}, "measurement_record_unsupported"),
    (lambda r: {**r, "instrument": {"model": "hp_z9_embedded"}}, "measurement_record_malformed"),
    (lambda r: {**r, "acquisition": {**r["acquisition"], "argv": None}}, "measurement_record_malformed"),
    (lambda r: {**r, "acquisition": {**r["acquisition"], "argv": []}}, "measurement_record_malformed"),
    (lambda r: {**r, "acquisition": {**r["acquisition"], "tool": "i1profiler"}}, "measurement_record_unsupported"),
    (lambda r: {**r, "acquisition": {**r["acquisition"], "argv": ["chartread", "-t", "c"]}},
     "measurement_record_unsupported"),
    (lambda r: {**r, "raw_files": [{"path": "x"}]}, "measurement_record_malformed"),
    (lambda r: {**r, "acquisition": {**r["acquisition"], "argv": ["chartread", "-F", "n", "c"]}},
     "measurement_record_mismatch"),                       # ColorMunki has no selectable filter
])
def test_malformed_or_unsupported_records_are_refused(mutate, code):
    with pytest.raises(mi.MeasurementRecordError) as e:
        ident(colormunki_ti3(), mutate(record()))
    assert e.value.code == code


@pytest.mark.parametrize("argv,instrument_filter,code", [
    (("chartread", "-F", "n", "-F", "u", "c"), None, "measurement_record_malformed"),     # -F twice
    (("chartread", "c", "-F"), None, "measurement_record_malformed"),                     # no value
    (("chartread", "-F", "z", "c"), None, "measurement_record_malformed"),                # unknown
    (("chartread", "-F", "6", "c"), "D65", "measurement_record_unsupported"),             # D65: not offered
    (("chartread", "-F", "p", "c"), "POLARIZED", "measurement_record_unsupported"),       # M3: not offered
    (("chartread", "-F", "n", "c"), "UVCUT", "measurement_record_mismatch"),              # argv vs ti3
    (("chartread", "-F", "u", "c"), None, "measurement_record_mismatch"),                 # M2 without trace
])
def test_filter_records_are_checked_against_the_ti3(argv, instrument_filter, code):
    with pytest.raises(mi.MeasurementRecordError) as e:
        ident(i1pro3_ti3(instrument_filter=instrument_filter), record("i1pro3plus", argv))
    assert e.value.code == code


def test_record_is_bound_to_its_ti3_and_chart():
    good = colormunki_ti3()
    rec = {**record(), "ti3": {"name": "x.ti3", "sha256": hashlib.sha256(good).hexdigest()}}
    with pytest.raises(mi.MeasurementRecordError) as e:            # another ti3 (one byte changed)
        mi.identity_from_bytes(good.replace(b"0.05", b"0.06", 1), json.dumps(rec).encode())
    assert e.value.code == "measurement_record_mismatch"
    with pytest.raises(mi.MeasurementRecordError) as e:            # reported instrument differs
        ident(i1pro3_ti3(), record("colormunki"))
    assert e.value.code == "measurement_record_mismatch"
    with pytest.raises(mi.MeasurementRecordError) as e:            # record of another chart
        ident(good, record(chart_id="CHT-20261122-1500-AA"), chart_id="CHT-20261122-1500-BB")
    assert e.value.code == "measurement_record_mismatch"
    with pytest.raises(mi.MeasurementRecordError) as e:
        mi.identity_from_bytes(good, b"{not json")
    assert e.value.code == "measurement_record_malformed"


def test_read_identity_and_describe_never_raise(tmp_path):
    meas = tmp_path / "CHT-20261122-1500-AA" / "measurements"
    meas.mkdir(parents=True)
    (meas / "a.ti3").write_bytes(ti3())
    assert mi.read_identity(meas / "a.ti3").origin == mi.ORIGIN_SOL_HEADER
    (meas / "a.json").write_text("{}")
    assert mi.describe_identity(meas / "a.ti3")["error"]["code"] == "measurement_record_malformed"
    (meas / "b.ti3").write_text("FAKE")
    assert mi.describe_identity(meas / "b.ti3")["error"]["code"] == "ti3_unreadable"


# ─── registry and chart contract ─────────────────────────────────────────────
def test_external_models_are_the_printtarg_whitelist():
    assert set(mi.external_models()) == set(printtarg_chart.INSTRUMENTS)


def test_expected_measurement_follows_the_layout():
    legacy = mi.expected_measurement({"geometry": {"patch_count": 60}})          # no layout
    assert legacy["layout"] == "honeycomb" and legacy["method"] == "sol"
    assert legacy["instruments"] == ["hp_z9_embedded"] and legacy["printer_families"] == ["z9"]
    pt = mi.expected_measurement({"layout": "printtarg", "roll_layout": "transverse",
                                  "instrument": {"key": "i1pro3plus"},
                                  "geometry": {"patch_count": 800, "total_strips": 40}})
    assert (pt["method"], pt["instruments"], pt["requires_printer"]) == ("external", ["i1pro3plus"], False)
    assert pt["n_strips"] == 40 and pt["printer_families"] is None
    bad = mi.expected_measurement({"layout": "printtarg", "instrument": {"key": "spyder"}})
    assert bad["instruments"] == [] and bad["instrument_unknown"] == "spyder"
    assert mi.expected_measurement({"layout": "hexagon"})["method"] is None


# ─── Argyll ──────────────────────────────────────────────────────────────────
def test_chartread_is_optional_and_never_gates_check_argyll(monkeypatch):
    assert "chartread" in argyll.OPTIONAL_BINARIES and "chartread" not in argyll.REQUIRED_BINARIES
    real = argyll.find_argyll_binary
    monkeypatch.setattr(argyll, "find_argyll_binary", lambda n: None if n == "chartread" else real(n))
    rep = argyll.check_argyll()
    assert rep["optional"]["chartread"] is None and "chartread" not in rep["missing"]


def test_instrument_env_excludes_the_stuck_macos_port_only_on_macos(monkeypatch):
    port = "/dev/cu.Bluetooth-Incoming-Port"
    env = argyll.instrument_env({"PATH": "/x"}, platform="darwin")
    assert env["ARGYLL_EXCLUDE_SERIAL_SCAN"] == port and env["PATH"] == "/x"
    env = argyll.instrument_env({"ARGYLL_EXCLUDE_SERIAL_SCAN": "/dev/cu.other"}, platform="darwin")
    assert env["ARGYLL_EXCLUDE_SERIAL_SCAN"] == f"/dev/cu.other;{port}"
    env = argyll.instrument_env({"ARGYLL_EXCLUDE_SERIAL_SCAN": port}, platform="darwin")
    assert env["ARGYLL_EXCLUDE_SERIAL_SCAN"] == port                         # no duplicate
    assert "ARGYLL_EXCLUDE_SERIAL_SCAN" not in argyll.instrument_env({}, platform="linux")
    monkeypatch.delenv("ARGYLL_EXCLUDE_SERIAL_SCAN", raising=False)
    argyll.instrument_env(platform="darwin")
    assert "ARGYLL_EXCLUDE_SERIAL_SCAN" not in os.environ                    # never mutated


# ─── publication ─────────────────────────────────────────────────────────────
CID = "CHT-20261122-1500-AA"
NOW = datetime(2026, 11, 22, 15, 0, 0)


def _chart_dir(tmp_path):
    d = tmp_path / CID
    (d / "external").mkdir(parents=True)
    return d


def _stage(d, data, name="staged.ti3"):
    p = d / "external" / name
    p.write_bytes(data)
    return p


def _visible(d):
    meas = d / "measurements"
    return sorted(p.name for p in meas.iterdir()) if meas.is_dir() else []


def _pub_record(model="colormunki", argv=("chartread", "-v", "chart")):
    r = record(model, argv, chart_id=CID)
    for k in ("schema", "schema_version", "ti3"):
        r.pop(k)
    return r


def test_publish_writes_the_record_then_the_unchanged_ti3(tmp_path):
    d = _chart_dir(tmp_path)
    data = colormunki_ti3()
    out = mp.publish_measurement(d, _stage(d, data), _pub_record(), [r[0] for r in grid()], now=NOW)
    assert out.stem == "20261122_150000" and out.n_readings == 27
    assert out.ti3_path.read_bytes() == data                               # bytes unchanged
    rec = json.loads(out.record_path.read_text())
    assert rec["ti3"]["sha256"] == hashlib.sha256(data).hexdigest() and rec["schema_version"] == 1
    assert out.identity.origin == mi.ORIGIN_RECORD and mi.read_identity(out.ti3_path).key() == out.identity.key()
    assert _visible(d) == ["20261122_150000.json", "20261122_150000.ti3"]
    assert not (d / mp.PUBLISH_DIR_NAME).exists()                         # no leftover
    again = mp.publish_measurement(d, _stage(d, data), _pub_record(), [r[0] for r in grid()], now=NOW)
    assert again.stem == "20261122_150000_1"                              # never overwrites


@pytest.mark.parametrize("rows,why", [
    (grid()[:-1], "missing"),
    (grid() + [grid()[0]], "duplicates"),
    (grid() + [("999", 0.0, 0.0, 0.0)], "unexpected"),
    (grid() + [("0", 100.0, 100.0, 100.0)], "padding"),
    (grid()[:-1] + [("999", 0.0, 0.0, 0.0)], "missing"),                # same row count, wrong set
])
def test_incomplete_readings_are_never_published(tmp_path, rows, why):
    d = _chart_dir(tmp_path)
    with pytest.raises(mp.PublishError) as e:
        mp.publish_measurement(d, _stage(d, colormunki_ti3(rows)), _pub_record(),
                               [r[0] for r in grid()], now=NOW)
    assert e.value.code == "measurement_incomplete" and e.value.info[why]
    assert _visible(d) == []


def test_inconsistent_record_is_refused_before_writing(tmp_path):
    d = _chart_dir(tmp_path)
    with pytest.raises(mp.PublishError) as e:          # a ColorMunki record for an i1Pro 3 ti3
        mp.publish_measurement(d, _stage(d, i1pro3_ti3()), _pub_record(), [r[0] for r in grid()], now=NOW)
    assert e.value.code == "measurement_record_mismatch" and _visible(d) == []


def test_failure_before_the_ti3_leaves_no_scan_and_no_record(tmp_path, monkeypatch):
    d = _chart_dir(tmp_path)
    seen = {}

    def _boom(src, dst):
        seen["src"], seen["listing"] = Path(src), _visible(d)
        raise OSError("disk full")
    monkeypatch.setattr(mp, "_link_no_overwrite", _boom)
    with pytest.raises(OSError):
        mp.publish_measurement(d, _stage(d, colormunki_ti3()), _pub_record(), [r[0] for r in grid()], now=NOW)
    assert seen["listing"] == ["20261122_150000.json"]          # the record was in place first
    assert seen["src"].parent.name == mp.PUBLISH_DIR_NAME and not seen["src"].name.endswith(".ti3")
    assert _visible(d) == [] and not (d / mp.PUBLISH_DIR_NAME).exists()


def test_orphan_record_is_invisible_and_its_stem_never_reused(tmp_path):
    d = _chart_dir(tmp_path)
    (d / "measurements").mkdir()
    (d / "measurements" / "20261122_150000.json").write_text("{}")      # crash between steps 4 and 5
    (d / "measurements" / "20261122_150000_1.ti3").write_bytes(ti3())   # a SOL scan, same second
    assert [p.name for p in mp.find_orphan_records(d)] == ["20261122_150000.json"]
    out = mp.publish_measurement(d, _stage(d, colormunki_ti3()), _pub_record(), [r[0] for r in grid()], now=NOW)
    assert out.stem == "20261122_150000_2"


def test_staged_file_must_be_outside_measurements(tmp_path):
    d = _chart_dir(tmp_path)
    (d / "measurements").mkdir()
    p = d / "measurements" / "x.tmp"
    p.write_bytes(colormunki_ti3())
    with pytest.raises(mp.PublishError) as e:
        mp.publish_measurement(d, p, _pub_record(), [r[0] for r in grid()], now=NOW)
    assert e.value.code == "measurement_staged_in_place"


def test_sweep_removes_only_stale_temporary_files(tmp_path):
    d = _chart_dir(tmp_path)
    (d / "measurements").mkdir()
    (d / mp.PUBLISH_DIR_NAME).mkdir()
    old = (d / mp.PUBLISH_DIR_NAME / "20261122_150000.ab12.tmp")
    rec_tmp = d / "measurements" / ".20261122_150000.json.cd34.tmp"
    young = d / "measurements" / ".20261122_150001.json.ef56.tmp"
    keep = [d / "measurements" / "20261122_150000.ti3", d / "measurements" / "20261122_150000.json"]
    for p in (old, rec_tmp, young, *keep):
        p.write_text("x")
    for p in (old, rec_tmp):
        os.utime(p, (1, 1))
    assert sorted(mp.sweep_publish_leftovers(d)) == sorted([old.name, rec_tmp.name])
    assert young.exists() and all(p.exists() for p in keep)
    assert not (d / mp.PUBLISH_DIR_NAME).exists()
