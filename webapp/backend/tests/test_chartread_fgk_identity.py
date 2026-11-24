"""Which device did chartread open? The run checks it from the driver's own -v block, before
any prompt, and nothing a run left behind without that check is ever adopted.

- The "Instrument Type" line is compared EXACTLY with the session's model (ArgyllCMS's text:
  "ColorMunki", seen on a real instrument; "X-Rite i1 Pro 3 Plus", from the ArgyllCMS 3.5.0
  sources, not yet seen on a real instrument). A serial number is required.
- A mismatch or a missing line stops the engine without answering any prompt; the session is
  kept as it is.
- A work file left by a run that never confirmed the device is set aside, never adopted.

The ColorMunki block below is the one a real instrument printed (P1c, 2026-11-24), with its
unit identifiers replaced by test values. Python fake only: no USB, no instrument."""
import json
import sys

import pytest

from lib.z9_client import measurement_identity as mi
from lib.z9_client.chartread_fgk import prompts as P
from lib.z9_client.chartread_fgk import run as R
from lib.z9_client.chartread_fgk import session as S
from lib.z9_client.chartread_fgk import transcript as TR
from lib.z9_client.chartread_fgk import usb_presence as U
from webapp.backend.tests.chartread_harness import FAKE
from webapp.backend.tests.session_harness import (TEST_SERIAL, data_rows, drive, kinds, make_chart,
                                                  python_engine)

# The -v block of a real ColorMunki, as chartread-fgK printed it on opening the device (only
# the serial number, chip id and production number are test values).
REAL_COLORMUNKI_BLOCK = (
    "Instrument Type:   ColorMunki\n"
    f"Serial Number:     {TEST_SERIAL}\n"
    "Firmware version:  288\n"
    "Chip ID:           00-0000000000000\n"
    "Version string:    'colormunki FW V1.32  Build Nr. 1303'\n"
    "Calibration Ver.:  6\n"
    "Production No.:    0000000\n")


def setup(tmp, *, model="colormunki", filt=None, n=78, steps=26, **fake):
    chart = make_chart(tmp, n_useful=n, steps=steps, model=model)
    exe, env = python_engine(tmp, chart, **fake)
    eng = S.EngineSpec.for_test(str(exe))
    return chart, S.Session.create(chart.dir, model=model, filter_option=filt, engine=eng), eng, env


def reconfigure(tmp, chart, *, before=None, **fake):
    """Same engine file (same sha256), another behaviour: ``before`` is text the device prints
    before the fake's own output (the real -v block)."""
    exe, _ = python_engine(tmp, chart, **fake)
    if before is not None:
        scen = tmp / "engine.json"
        data = json.loads(scen.read_text())
        data["steps"].insert(0, {"say": before})
        scen.write_text(json.dumps(data))
    return exe


def run_once(s, eng, env, tmp, **drive_kw):
    run = R.Run.start(S.Session.open(s.dir), eng, lock=S.InstrumentLock(tmp / "lock"), env=env)
    return run, drive(run, timeout=60, **drive_kw)


def reopen(s):
    return S.Session.open(s.dir)


# ─── the identity facts come from the models (no table of their own) ─────────
def test_usb_ids_and_family_come_from_the_models():
    assert U.MODEL_USB_IDS == {"colormunki": ((0x0971, 0x2007),), "i1pro3plus": ((0x0765, 0x6009),)}
    assert U.FAMILY_ONLY == frozenset({"i1pro3plus"})
    assert set(U.MODEL_USB_IDS) == set(mi.external_models())
    assert all(U.ARGYLL_USB_IDS.get(i) for ids in U.MODEL_USB_IDS.values() for i in ids)


def test_expected_instrument_types_are_argylls_text():
    assert mi.INSTRUMENT_MODELS["colormunki"].instrument_types == ("ColorMunki",)
    assert mi.INSTRUMENT_MODELS["i1pro3plus"].instrument_types == ("X-Rite i1 Pro 3 Plus",)
    assert all(mi.INSTRUMENT_MODELS[k].instrument_types for k in mi.external_models())


def test_one_table_masks_the_unit_identifiers():
    assert P._mask is TR.mask_serials_text
    text = TR.mask_serials_text(REAL_COLORMUNKI_BLOCK + "  Serial Number:     12345\n")
    assert TEST_SERIAL not in text and "12345" not in text and "0000000" not in text
    assert TR.mask_serials(f"Serial Number:     {TEST_SERIAL}".encode()) == b"Serial Number:     <masked>"


# ─── the real transcript ─────────────────────────────────────────────────────
def test_the_real_colormunki_block_comes_before_any_prompt():
    """P1c run 1 (calibration) and run 2 (-r -N: the strip prompt right away)."""
    for prompt in ("\nSet instrument sensor to calibration position,\n and then hit any key to continue,\n"
                   " or hit Esc or Q to abort: ",
                   "\nReady to read strip pass B\n"
                   "Press 'f' to move forward, 'b' to move back, 'n' for next unread,\n"
                   " 'd' when done, Esc or 'q' to quit (passes read so far are saved).\n"
                   "Trigger instrument switch or any other key to start:"):
        ev = P.PromptParser().feed("Steps in each Pass = 8\nPasses in each Strip = 8\n"
                                   + REAL_COLORMUNKI_BLOCK + prompt, 0.0)
        names = [e.data.get("name") for e in ev if e.kind == "instrument_field"]
        assert names[:2] == ["Instrument Type", "Serial Number"]
        first_prompt = next(i for i, e in enumerate(ev) if e.kind in R.IDENTITY_GATED)
        assert all(e.kind == "instrument_field" for e in ev[first_prompt - len(names):first_prompt])
        assert next(e for e in ev if e.kind == "instrument_field").data["value"] == "ColorMunki"


def test_a_real_colormunki_block_lets_the_session_run_as_before(tmp_path):
    chart, s, eng, env = setup(tmp_path)
    reconfigure(tmp_path, chart, before=REAL_COLORMUNKI_BLOCK, type_name=None, serial=None)
    run, ns = run_once(s, eng, env, tmp_path)
    s2 = reopen(s)
    assert run.ended["end"] == "finished" and run.identity_ok
    assert s2.data["runs"][0]["identity"] == "confirmed"
    assert (s2.data["instrument"]["serial"], s2.data["instrument"]["instrument_type"]) == (TEST_SERIAL, "ColorMunki")
    assert "error" not in kinds(ns)


def test_a_resume_is_checked_before_the_strip_prompt(tmp_path):
    """-r -N: no calibration, the strip prompt comes right after the -v block (P1c run 2)."""
    chart, s, eng, env = setup(tmp_path)

    def pause_after_one(n, run):
        if n["kind"] == "checkpoint" and not run.stopping:
            run.command("pause")
    run_once(s, eng, env, tmp_path, on_notice=pause_after_one)
    reconfigure(tmp_path, chart, before=REAL_COLORMUNKI_BLOCK, type_name=None, serial=None)
    run, ns = run_once(s, eng, env, tmp_path)
    assert {"-r", "-N"} <= set(reopen(s).data["runs"][1]["argv"])
    assert "calibration_prompt" not in kinds(ns) and run.ended["end"] == "finished"
    assert reopen(s).data["runs"][1]["identity"] == "confirmed"


# ─── refusals: the engine is stopped, no prompt answered, the session kept ───
@pytest.mark.parametrize("model, filt, type_name, code, found", [
    ("colormunki", None, "X-Rite i1 Pro 3 Plus", "instrument_type_mismatch", "X-Rite i1 Pro 3 Plus"),
    ("i1pro3plus", "n", "X-Rite i1 Pro 3", "instrument_type_mismatch", "X-Rite i1 Pro 3"),
    ("i1pro3plus", "n", "ColorMunki", "instrument_type_mismatch", "ColorMunki"),
    ("colormunki", None, "ColorMunki Photo", "instrument_type_mismatch", "ColorMunki Photo"),
    ("colormunki", None, None, "instrument_type_unknown", None),
])
def test_another_or_an_unnamed_device_is_refused(tmp_path, model, filt, type_name, code, found):
    chart, s, eng, env = setup(tmp_path, model=model, filt=filt, type_name=type_name)
    run, ns = run_once(s, eng, env, tmp_path)
    s2 = reopen(s)
    assert run.ended["end"] == f"error:{code}"
    err = run.ended["error"]
    assert err["kind"] == code and err.get("found") == found
    assert err["expected"] == mi.INSTRUMENT_MODELS[model].instrument_types[0]
    assert not {"calibration_prompt", "ready", "strip_acquired", "checkpoint"} & set(kinds(ns))
    assert s2.data["runs"][0]["identity"] == "refused" and s2.summary()["passes_saved"] == 0
    assert s2.data["status"] == S.OPEN and not s2.data.get("suspended")
    assert s2.data["instrument"]["serial"] is None              # never the wrong device's


def test_the_right_i1pro3plus_type_goes_on(tmp_path):
    chart, s, eng, env = setup(tmp_path, model="i1pro3plus", filt="n")
    run, _ = run_once(s, eng, env, tmp_path)
    assert run.ended["end"] == "finished"
    assert reopen(s).data["instrument"]["instrument_type"] == "X-Rite i1 Pro 3 Plus"


def test_no_serial_number_is_refused(tmp_path):
    chart, s, eng, env = setup(tmp_path, serial=None)
    run, ns = run_once(s, eng, env, tmp_path)
    assert run.ended["end"] == "error:instrument_serial_unknown"
    assert "calibration_prompt" not in kinds(ns) and reopen(s).summary()["passes_saved"] == 0


def test_no_command_before_the_device_is_identified(tmp_path):
    chart, s, eng, env = setup(tmp_path)
    run = R.Run.start(S.Session.open(s.dir), eng, lock=S.InstrumentLock(tmp_path / "lock"), env=env)
    try:
        with pytest.raises(S.SessionError) as e:
            run.command("calibrate")                         # nothing read from the engine yet
        assert e.value.code == "instrument_identity_unconfirmed"
    finally:
        drive(run, timeout=60)


# ─── whatever an unconfirmed run left is never adopted ──────────────────────
@pytest.mark.parametrize("identity, adopted", [("refused", False), ("pending", False),
                                               ("confirmed", True), (None, True)])
def test_a_work_file_from_an_unconfirmed_run_is_set_aside(tmp_path, identity, adopted):
    """chartread saved strip 5 and the run died before freeglaz validated it (the existing
    crash_after_rename case). After a confirmed run (or a run recorded before this check), it
    is recovered as before; after a run that never confirmed the device, it is set aside."""
    chart, s, eng, env = setup(tmp_path, n=26 * 6, checkpoints={"5": "crash_after_rename"})
    run, _ = run_once(s, eng, env, tmp_path)
    s2 = reopen(s)
    assert s2.summary()["passes_saved"] == 4 and s2.data["runs"][0]["identity"] == "confirmed"
    if identity is None:
        s2.data["runs"][0].pop("identity")                   # a run recorded before this check
    else:
        s2.data["runs"][0]["identity"] = identity
    s2._save()
    actions = reopen(s).reconcile()
    s3 = reopen(s)
    if adopted:
        assert s3.summary()["passes_saved"] == 5 and any("recovered 1 pass" in a for a in actions)
    else:
        assert s3.summary()["passes_saved"] == 4, actions
        assert any("identity_unconfirmed" in a for a in actions), actions
        aside = list(s3.work_dir.glob(f"{chart.chart_id}.ti3.unconfirmed-*"))
        assert len(aside) == 1 and len(data_rows(aside[0].read_bytes())) == 5 * 26
        assert s3.work_ti3.read_bytes() == s3.validated.read_bytes()


def test_the_fake_still_prints_what_the_harness_asks(tmp_path):
    """Guard for the harness itself: the default fake names the chart's model."""
    chart = make_chart(tmp_path, n_useful=26, steps=26, model="i1pro3plus")
    exe, _ = python_engine(tmp_path, chart)
    scen = json.loads((tmp_path / "engine.json").read_text())
    m = scen["steps"][0]["measure"]
    assert (m["type_name"], m["serial"]) == ("X-Rite i1 Pro 3 Plus", TEST_SERIAL)
    assert str(FAKE) in exe.read_text() and sys.executable in exe.read_text()
