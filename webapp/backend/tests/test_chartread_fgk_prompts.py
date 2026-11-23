"""PromptParser and KeyGate (lib.z9_client.chartread_fgk.prompts): chartread-fgK's texts as
events, and the keys freeglaz may type. Texts are chartread.c / instappsup.c format strings
and the v3 patch messages; one real ColorMunki transcript (B1, macOS, serials masked).
End-to-end cases run the fake chartread, which reads keys the way ArgyllCMS does."""
import random
import sys

import pytest

from lib.z9_client.chartread_fgk import prompts as PR
from webapp.backend.tests.chartread_harness import Pump, keys_read, spawn_fake

# B1, chartread -K v3, real ColorMunki on macOS: start, calibration, strip prompt, Esc, Esc.
B1 = ("Steps in each Pass = 26\nPasses in each Strip = 3\nInstrument Type:   ColorMunki\n"
      "Serial Number: <masked>\nFirmware version:  288\nChip ID: <masked>\n"
      "Version string:    'colormunki FW V1.32  Build Nr. 1303'\nCalibration Ver.:  6\n"
      "Production No.: <masked>\n\nSet instrument sensor to calibration position,\n"
      " and then hit any key to continue,\n or hit Esc or Q to abort: \nCalibration complete\n\n"
      "Ready to read strip pass A\nPress 'f' to move forward, 'b' to move back, 'n' for next unread,\n"
      " 'd' when done, Esc or 'q' to quit (passes read so far are saved).\n"
      "Trigger instrument switch or any other key to start:^[\n\n\n"
      "Strip read stopped at user request!\nHit Esc or 'q' to give up, any other key to retry:\n")

READY = ("\nReady to read strip pass {p}{s}\nPress 'f' to move forward, 'b' to move back, 'n' for next "
         "unread,\n 'd' when done, Esc or 'q' to quit (passes read so far are saved).\n"
         "Trigger instrument switch or any other key to start:")


def parse(text, chunks=None):
    pp, events, pos = PR.PromptParser(), [], 0
    for n in chunks or [len(text)]:
        events += pp.feed(text[pos:pos + n], 0.0)
        pos += n
    return events + pp.feed(text[pos:], 0.0)


def kinds(events):
    return [e.kind for e in events]


# ─── recognition ────────────────────────────────────────────────────────────
def test_the_real_colormunki_transcript():
    ev = parse(B1)
    assert kinds(ev) == ["steps_in_pass", "passes_in_strip"] + ["instrument_field"] * 7 + [
        "calibration_prompt", "calibration_done", "ready", "stopped"]
    cal = ev[9]
    assert cal.data == {"instruction": "Set instrument sensor to calibration position,",
                        "calibration": "sensor_position", "skippable": False}
    ready = ev[11]
    assert ready.data == {"pass": "A", "read_before": False, "all_read": False,
                          "checkpointing": True, "key_starts_reading": True}


def test_byte_by_byte_gives_the_same_events():
    text = B1 + READY.format(p="B", s="") + " Strip read OK\nCheckpoint: 52 patches saved to 'c.ti3'\n"
    whole = parse(text)
    rnd = random.Random(1)
    for chunks in ([1] * len(text), [rnd.randint(1, 40) for _ in range(len(text))]):
        split = parse(text, chunks)
        assert [(e.kind, e.data) for e in split] == [(e.kind, e.data) for e in whole]


@pytest.mark.parametrize("suffix,durability,warning", [
    ("", "full", None),
    (" (reduced durability)", "reduced", "ck_reduced"),
    (" (not durable)", "not_durable", "ck_not_durable"),
])
def test_checkpoints_and_their_durability(suffix, durability, warning):
    msg = {"ck_reduced": "\n(Warning) Checkpoint durability reduced: F_FULLFSYNC not supported for the "
                         "directory, only fsync() done: the drive cache may not be flushed\n",
           "ck_not_durable": "\n(Warning) Checkpoint not durable: flushing directory '/x' failed "
                             "(Input/output error), the new file may not survive a power loss\n",
           None: ""}[warning]
    ev = parse(READY.format(p="C", s="") + " Strip read OK\n" + msg
               + f"Checkpoint: 78 patches saved to 'CHT-1.ti3'{suffix}\n")
    assert kinds(ev) == ["ready", "strip_ok"] + ([warning] if warning else []) + ["checkpoint"]
    assert ev[-1].data == {"n": 78, "path": "CHT-1.ti3", "durability": durability}


def test_refused_checkpoint():
    ev = parse(" Strip read OK\n\n(Warning) Checkpoint not saved: write error on 'c.ti3.tmp' (No space left "
               "on device)\nThe readings are kept in memory, the previous file is unchanged.\n")
    assert kinds(ev) == ["strip_ok", "ck_refused"]
    assert "No space left" in ev[1].data["message"]


def test_failure_prompts_and_their_heads():
    text = (READY.format(p="C", s="") + "\n\n(Warning) Seem to have read strip pass H rather than C!\n"
            "Hit Return to use it anyway, any other key to retry, Esc or 'q' to give up:n\n"
            + READY.format(p="C", s="") + "\nStrip read failed due to misread (Reading is too short)\n"
            "Hit Esc to give up, any other key to retry:n\n"
            + READY.format(p="C", s="") + "\nThere is at least one patch with an very unexpected response! "
            "(DeltaE 120.500000)\nHit Return to use it anyway, any other key to retry, Esc or  'q' to give up:\r\n"
            "Checkpoint: 78 patches saved to 'c.ti3'\n"
            + READY.format(p="D", s="") + "\nStrip read failed due to communication problem.\n"
            "Hit Esc or 'q' to give up, any other key to retry:n\n"
            "Got 'Instrument Access Failed' (Device busy) error.\nHit Esc or 'q' to give up, any other key to retry:n\n"
            "\nStrip read failed due unexpected error :'Hardware Failure' (x)\n"
            "Hit Esc or 'q' to give up, any other key to retry:n\n")
    ev = parse(text)
    assert kinds(ev) == ["ready", "wrong_strip", "ready", "misread", "ready", "unexpected", "checkpoint",
                         "ready", "comms_failed", "instrument_error", "strip_error"]
    assert ev[1].data == {"read": "H", "expected": "C"}
    assert ev[3].data == {"reason": "Reading is too short"}
    assert ev[5].data == {"delta_e": 120.5}                    # accepted: checkpoint without "Strip read OK"
    assert ev[9].data["message"].startswith("Instrument Access Failed")


def test_noise_between_head_and_tail_and_glued_errors():
    noise = "icoms_usb_transaction: ReadPipeAsync failed with 0xe00002c0\n" * 3
    ev = parse("\nStrip read failed due to misread (Reading is too short)\n" + noise
               + "Hit Esc to give up, any other key to retry:"
               + "icoms_usb_transaction: ReadPipeAsync failed withicoms_usb_transaction: ReadPipeAsync "
               "failed with 0xe00002d9\n")
    assert [k for k in kinds(ev) if k != "usb_error"] == ["misread"]
    assert ev[[e.kind for e in ev].index("misread")].data["reason"] == "Reading is too short"
    loss = [e for e in ev if e.kind == "usb_error"]
    assert loss and all(e.data["loss"] for e in loss)


def test_a_tail_whose_head_was_missed_is_conservative():
    ev = parse("Hit Esc or 'q' to give up, any other key to retry:")
    assert kinds(ev) == ["retry_prompt"] and set(PR.COMMANDS["retry_prompt"]) == {"retry", "give_up"}
    ev = parse("Hit Return to use it anyway, any other key to retry, Esc or 'q' to give up:")
    assert kinds(ev) == ["retry_prompt"]                       # no "accept" without knowing what it is


def test_calibration_prompts():
    i1 = parse("Place the instrument on its reflective white reference S/N 123456,\n"
               " and then hit any key to continue,\n or hit Esc or Q to abort, or S to skip: ")
    assert kinds(i1) == ["calibration_prompt"]
    assert i1[0].data["calibration"] == "white_reference" and i1[0].data["skippable"]
    assert "123456" in i1[0].data["instruction"] and "123456" not in str(i1[0].public().data)
    retry = parse("Calibration failed with 'Wrong Sensor Position' (x)\nHit any key to retry, or Esc or Q to abort:\n")
    assert kinds(retry) == ["calibration_failed", "calibration_retry"]
    again = parse("\nStrip read failed because instruments needs calibration\n"
                  "Set instrument sensor to calibration position,\n and then hit any key to continue,\n"
                  " or hit Esc or Q to abort: ")
    assert kinds(again) == ["needcal", "calibration_prompt"]


def test_strip_prompt_variants_done_confirm_and_errors():
    ev = parse(READY.format(p="F", s=" (!! ALL ROWS READ !!)") + "\n"
               + READY.format(p="B", s=" (This row has been read)") + "\n"
               "\nDone ? - At least one unread patch (17, B1), Are you sure [y/n]: \n"
               "Establishing communications with instrument failed with message 'No device' (x)\n"
               "chartread-fgK: Error - Write error : renaming 'c.ti3.tmp' failed\n"
               "\n\nSpot read failed due to the sensor being in the wrong position\n(Sensor should be in "
               "surface position)\n Strip read OK (Strip read in reverse direction)\n")
    assert kinds(ev) == ["ready", "ready", "done_confirm", "start_failed", "fatal", "sensor_position", "strip_ok"]
    assert ev[0].data["all_read"] and not ev[0].data["read_before"]
    assert ev[1].data["read_before"] and not ev[1].data["all_read"]
    assert ev[2].data == {"id": "17", "loc": "B1"}
    assert ev[4].data["program"] == "chartread-fgK" and ev[6].data["reversed"]


def test_without_K_the_strip_prompt_says_so():
    ev = parse(READY.format(p="A", s="").replace("(passes read so far are saved)", "without saving"))
    assert ev[0].data["checkpointing"] is False


def test_public_events_mask_serials():
    ev = parse("Serial Number: 1234567\n")[0]
    assert ev.data["value"] == "1234567" and ev.public().data["value"] == "<masked>"
    assert "1234567" not in ev.public().text


# ─── keys ───────────────────────────────────────────────────────────────────
def test_no_command_can_start_a_measurement_by_accident():
    """Every key freeglaz may send, were it to land on the strip prompt, is a command or a
    stop — except Return (accept an unexpected value), which is never resent."""
    for prompt, cmds in PR.COMMANDS.items():
        for cmd, key in cmds.items():
            assert len(key) == 1
            if key[0] not in PR.NOT_TRIGGER_AT_READY:
                assert (prompt, cmd, key) == ("unexpected", "accept", b"\r")
                assert prompt not in PR.RESEND_PROMPTS
    assert "accept" not in PR.COMMANDS["wrong_strip"]
    assert all(b"y" not in c.values() and b"g" not in c.values() for c in PR.COMMANDS.values())
    assert PR.COMMANDS["ready"] == {"next": b"f", "prev": b"b", "next_unread": b"n",
                                    "finish": b"d", "pause": b"\x1b"}


class Clock:
    def __init__(self):
        self.written = []

    def write(self, b):
        self.written.append(b)


def ev(kind, t=0.0, **data):
    return PR.Event(kind, t, data)


def test_gate_waits_sends_once_and_never_queues():
    c = Clock()
    g = PR.KeyGate(c.write, min_delay=0.1)
    with pytest.raises(PR.KeyRefused) as e:
        g.request("continue")
    assert e.value.code == "no_prompt"
    g.on_event(ev("calibration_prompt", 10.0))
    g.request("continue")
    assert g.tick(10.05) is None and c.written == []            # too early
    assert g.tick(10.1) == b"n" and c.written == [b"n"]
    for cmd in ("continue", "abort"):
        with pytest.raises(PR.KeyRefused) as e:
            g.request(cmd)
        assert e.value.code == "already_answered"
    g.on_output(10.12)                                          # chartread reacted
    assert g.tick(20.0) is None and c.written == [b"n"]


def test_gate_drops_an_unsent_answer_when_a_new_prompt_comes():
    c = Clock()
    g = PR.KeyGate(c.write, min_delay=0.5)
    g.on_event(ev("ready", 1.0, all_read=False))
    g.request("next")
    g.on_event(ev("misread", 1.2, reason="x"))                  # before the key was due
    assert g.tick(5.0) is None and c.written == []
    g.request("retry")
    assert g.tick(5.0) == b"n"


@pytest.mark.parametrize("prompt,command,code", [
    ("ready", "finish", "not_all_read"),
    ("ready", "accept", "not_allowed"),
    ("ready", "start", "not_allowed"),
    ("wrong_strip", "accept", "not_allowed"),
    ("done_confirm", "yes", "not_allowed"),
    ("stopped", "continue", "not_allowed"),
])
def test_gate_refuses(prompt, command, code):
    g = PR.KeyGate(Clock().write)
    g.on_event(ev(prompt, 0.0, all_read=False))
    with pytest.raises(PR.KeyRefused) as e:
        g.request(command)
    assert e.value.code == code


def test_finish_only_when_every_row_is_read():
    c = Clock()
    g = PR.KeyGate(c.write)
    g.on_event(ev("ready", 0.0, all_read=True))
    g.request("finish")
    assert g.tick(1.0) == b"d"


def test_resend_only_at_calibration_once_and_only_after_silence():
    c = Clock()
    g = PR.KeyGate(c.write, min_delay=0.1, resend_after=1.5)
    g.on_event(ev("calibration_prompt", 0.0))
    g.request("continue")
    g.tick(0.1)
    assert g.tick(1.5) is None                                  # not yet
    assert g.tick(1.7) == b"n" and g.tick(5.0) is None          # once, never twice
    assert [x[2] for x in g.log] == ["continue", "continue (resent)"]
    for prompt, cmd in (("misread", "retry"), ("ready", "pause"), ("unexpected", "accept"), ("stopped", "give_up")):
        c2 = Clock()
        g2 = PR.KeyGate(c2.write, min_delay=0.1, resend_after=1.5)
        g2.on_event(ev(prompt, 0.0, all_read=False))
        g2.request(cmd)
        g2.tick(0.1)
        assert g2.tick(10.0) is None and len(c2.written) == 1, prompt


# ─── end to end, with the fake's Argyll-like keyboard ───────────────────────
def _respond(e, g):
    if e.kind == "calibration_prompt":
        g.request("continue")
    elif e.kind == "ready" and e.data["all_read"]:
        g.request("finish")
    elif e.kind in ("wrong_strip", "misread", "unexpected", "comms_failed"):
        g.request("retry")


posix = pytest.mark.skipif(not sys.platform.startswith(("linux", "darwin")), reason="POSIX PTY")


@posix
def test_the_keyboard_flush_race_is_absorbed_by_one_resend(tmp_path):
    """Calibration prompt: Argyll prints, then empties the buffer 300 ms later: the key sent
    after 100 ms is thrown away; silence → one resend → calibration goes on."""
    proc, keys = spawn_fake(tmp_path, [{"prompt": "calibration", "race_ms": 300, "duration_ms": 100},
                                       {"prompt": "ready", "pass": "A", "all_read": True}, {"exit": 0}])
    pump = Pump(proc, min_delay=0.1)
    pump.run(timeout=15, respond=_respond)
    assert [x[2] for x in pump.gate.log] == ["continue", "continue (resent)", "finish"]
    assert [(k["prompt"], k["key"]) for k in keys_read(keys)] == [("calibration", "n"), ("ready", "d")]
    assert "calibration_done" in pump.kinds() and proc.poll() == 0
    proc.close()


@posix
def test_waiting_longer_than_the_race_needs_no_resend(tmp_path):
    proc, keys = spawn_fake(tmp_path, [{"prompt": "calibration", "race_ms": 150},
                                       {"prompt": "ready", "pass": "A", "all_read": True}, {"exit": 0}])
    pump = Pump(proc, min_delay=0.4)
    pump.run(timeout=15, respond=_respond)
    assert [x[2] for x in pump.gate.log] == ["continue", "finish"]
    proc.close()


@posix
def test_a_stalled_engine_never_turns_the_resend_into_a_measurement(tmp_path):
    """Worst case: the key WAS taken but chartread stalls 2.5 s before printing anything, so
    the gate resends. The extra 'n' waits in the buffer and reaches the strip prompt: it is a
    navigation command there, never a trigger."""
    proc, keys = spawn_fake(tmp_path, [{"prompt": "calibration", "stall_ms": 2500},
                                       {"prompt": "ready", "pass": "A", "button_ms": 3000},
                                       {"prompt": "ready", "pass": "B", "all_read": True}, {"exit": 0}])
    pump = Pump(proc, min_delay=0.1)
    pump.run(timeout=20, respond=_respond)
    log = keys_read(keys)
    assert [x[2] for x in pump.gate.log][:2] == ["continue", "continue (resent)"]
    assert ("ready", "n", "command") in [(k["prompt"], k["key"], k["class"]) for k in log]
    assert not [k for k in log if k["class"] == "TRIGGER"]
    proc.close()


@posix
def test_a_full_session_through_failures_and_checkpoints(tmp_path):
    proc, keys = spawn_fake(tmp_path, [
        {"say": "Steps in each Pass = 26\nPasses in each Strip = 3\nInstrument Type:   ColorMunki\n\n"},
        {"prompt": "calibration"},
        {"prompt": "ready", "pass": "A", "button_ms": 150}, {"strip_ok": True}, {"checkpoint": "full", "n": 26},
        {"prompt": "ready", "pass": "B", "button_ms": 150}, {"prompt": "wrong_strip", "read": "D", "expected": "B"},
        {"prompt": "ready", "pass": "B", "button_ms": 150}, {"prompt": "misread"},
        {"prompt": "ready", "pass": "B", "button_ms": 150}, {"strip_ok": True}, {"checkpoint": "reduced", "n": 52},
        {"prompt": "ready", "pass": "C", "button_ms": 150}, {"prompt": "unexpected", "de": 140.0},
        {"prompt": "ready", "pass": "C", "button_ms": 150}, {"strip_ok": True},
        {"checkpoint": "not_durable", "n": 78},
        {"prompt": "ready", "pass": "C", "all_read": True}, {"exit": 0}])
    pump = Pump(proc)
    pump.run(timeout=20, respond=_respond)
    cks = [e.data["durability"] for e in pump.events if e.kind == "checkpoint"]
    assert cks == ["full", "reduced", "not_durable"]
    assert [x[2] for x in pump.gate.log] == ["continue", "retry", "retry", "retry", "finish"]
    log = keys_read(keys)
    assert not [k for k in log if k["class"] == "TRIGGER"] and proc.poll() == 0
    proc.close()


@posix
def test_pause_is_escape_then_give_up(tmp_path):
    proc, keys = spawn_fake(tmp_path, [{"prompt": "ready", "pass": "C"}, {"prompt": "stopped"}, {"exit": 0}])
    pump = Pump(proc)

    def respond(e, g):
        g.request({"ready": "pause", "stopped": "give_up"}[e.kind])
    pump.run(timeout=10, respond=respond)
    assert [(k["prompt"], k["key"], k["class"]) for k in keys_read(keys)] == \
        [("ready", "\x1b", "abort"), ("stopped", "\x1b", None)]
    proc.close()
