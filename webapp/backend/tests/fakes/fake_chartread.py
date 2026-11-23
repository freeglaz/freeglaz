#!/usr/bin/env python3
"""TEST ONLY: a fake of chartread-fgK's TERMINAL behaviour. No instrument, no USB, no
file, no network: it prints chartread's texts, reads keys the way ArgyllCMS does, and
does what a scenario says.

    fake_chartread.py <scenario.json>

Scenario: {"max_seconds": 20, "keys_log": "<path>", "steps": [ {...}, ... ]}. It always
dies after ``max_seconds`` (SIGALRM), whatever the steps do.

Keys are read like ``spectro/conv.c`` (Unix): ICANON and ECHO switched off only while
reading, up to 3 bytes per read, the first one used; the keyboard buffer is emptied with
``tcflush``, BEFORE the prompt for chartread.c's prompts and AFTER it for the calibration
prompts of instappsup.c (``race_ms`` widens that window). At the strip prompt the keys
are classified like chartread's ``inst_set_uih`` table: command, abort, or a TRIGGER (a
measurement would start). Every key read is logged (JSON lines) with its class.
"""
import json
import os
import select
import signal
import sys
import termios
import time

ESC, CTRL_C = b"\x1b", b"\x03"
COMMAND_KEYS, ABORT_KEYS = set(b"fFbBnNgGdD"), set(b"qQ\x1b\x03")
TEXT = {
    "calibration": ("{instruction}\n and then hit any key to continue,\n or hit Esc or Q to abort: "),
    "calibration_retry": "Calibration failed with '{message}'\nHit any key to retry, or Esc or Q to abort:\n",
    "ready": ("\nReady to read strip pass {pass}{suffix}\n"
              "Press 'f' to move forward, 'b' to move back, 'n' for next unread,\n"
              " 'd' when done, Esc or 'q' to quit {save}.\n"
              "Trigger instrument switch or any other key to start:"),
    "stopped": "\n\nStrip read stopped at user request!\nHit Esc or 'q' to give up, any other key to retry:",
    "misread": "\nStrip read failed due to misread ({reason})\nHit Esc to give up, any other key to retry:",
    "comms_failed": "\nStrip read failed due to communication problem.\nHit Esc or 'q' to give up, any other key to retry:",
    "strip_error": ("\nStrip read failed due unexpected error :'{message}'\n"
                    "Hit Esc or 'q' to give up, any other key to retry:"),
    "instrument_error": "Got '{message}' error.\nHit Esc or 'q' to give up, any other key to retry:",
    "wrong_strip": ("\n(Warning) Seem to have read strip pass {read} rather than {expected}!\n"
                    "Hit Return to use it anyway, any other key to retry, Esc or 'q' to give up:"),
    "unexpected": ("\nThere is at least one patch with an very unexpected response! (DeltaE {de:f})\n"
                   "Hit Return to use it anyway, any other key to retry, Esc or  'q' to give up:"),
    "done_confirm": "\nDone ? - At least one unread patch ({id}, {loc}), Are you sure [y/n]: ",
}
# empty_con_chars() position: "before" the prompt is printed (chartread.c), "after" it
# (instappsup.c calibration prompts), or "none" (chartread.c "unexpected error" branch).
FLUSH = {"calibration": "after", "calibration_retry": "after", "strip_error": "none"}
CHECKPOINT = {
    "full": "Checkpoint: {n} patches saved to '{path}'\n",
    "reduced": ("\n(Warning) Checkpoint durability reduced: F_FULLFSYNC not supported for the file, only "
                "fsync() done: the drive cache may not be flushed\nCheckpoint: {n} patches saved to "
                "'{path}' (reduced durability)\n"),
    "not_durable": ("\n(Warning) Checkpoint not durable: flushing directory '.' failed (Input/output error),"
                    " the new file may not survive a power loss\nCheckpoint: {n} patches saved to '{path}'"
                    " (not durable)\n"),
    "refused": ("\n(Warning) Checkpoint not saved: write error on '{path}.tmp' (No space left on device)\n"
                "The readings are kept in memory, the previous file is unchanged.\n"),
}
_log = None


def out(text, fd=1):
    data = text.encode("latin-1") if isinstance(text, str) else text
    while data:
        n = os.write(fd, data)
        data = data[n:]


def log_key(prompt, key, cls=None, **extra):
    if _log is not None:
        _log.write(json.dumps({"t": time.monotonic(), "prompt": prompt, "key": key.decode("latin-1"),
                               "class": cls, **extra}) + "\n")
        _log.flush()


def _raw_read(timeout):
    """conv.c next_con_char / poll_con_char."""
    orig = termios.tcgetattr(0)
    new = [x[:] if isinstance(x, list) else x for x in orig]
    new[3] &= ~(termios.ICANON | termios.ECHO)
    new[6][termios.VMIN], new[6][termios.VTIME] = 1, 0
    termios.tcsetattr(0, termios.TCSANOW, new)
    try:
        r, _, _ = select.select([0], [], [], timeout)
        return os.read(0, 3)[:1] if r else b""
    finally:
        termios.tcsetattr(0, termios.TCSANOW, orig)


def next_con_char():
    return _raw_read(None)


def poll_con_char():
    return _raw_read(0)


def empty_con_chars():
    termios.tcflush(0, termios.TCIFLUSH)


def key_prompt(kind, step):
    params = {"instruction": "Set instrument sensor to calibration position,", "message": "x",
              "reason": "Reading is too short", "read": "B", "expected": "A", "de": 120.5,
              "id": "17", "loc": "B1", **step}
    flush = FLUSH.get(kind, "before")
    if flush == "before":
        empty_con_chars()
    out(TEXT[kind].format(**params))
    if flush == "after":
        time.sleep(step.get("race_ms", 0) / 1000)
        empty_con_chars()
    key = next_con_char()
    log_key(kind, key)
    time.sleep(step.get("stall_ms", 0) / 1000)
    out("\n")
    if kind == "calibration" and key not in ABORT_KEYS:
        time.sleep(step.get("duration_ms", 0) / 1000)
        out("Calibration complete\n")
    return key


def ready(step):
    suffix = " (!! ALL ROWS READ !!)" if step.get("all_read") else \
        " (This row has been read)" if step.get("read_before") else ""
    save = "(passes read so far are saved)" if step.get("checkpointing", True) else "without saving"
    out(TEXT["ready"].format(**{"pass": step.get("pass", "A"), "suffix": suffix, "save": save}))
    button_at = time.monotonic() + step["button_ms"] / 1000 if "button_ms" in step else None
    while True:                                         # the driver's read_strip waiting loop
        key = poll_con_char()
        if key:
            cls = "command" if key[0] in COMMAND_KEYS else "abort" if key[0] in ABORT_KEYS else "TRIGGER"
            log_key("ready", key, cls)
            out("\n")
            return key
        if button_at is not None and time.monotonic() >= button_at:
            log_key("ready", b"", "button")
            return b""
        time.sleep(0.05)


def run(step):
    if "say" in step:
        out(step["say"], 2 if step.get("stderr") else 1)
    elif "prompt" in step:
        if step["prompt"] == "ready":
            ready(step)
        else:
            key_prompt(step["prompt"], step)
    elif "strip_ok" in step:
        out(" Strip read OK" + (" (Strip read in reverse direction)" if step.get("reversed") else "") + "\n")
    elif "checkpoint" in step:
        out(CHECKPOINT[step["checkpoint"]].format(n=step.get("n", 26), path=step.get("path", "chart.ti3")))
    elif "flood" in step:
        f = step["flood"]
        lines = f["lines"] if "lines" in f else [f["line"]]
        block = "".join(l + "\n" for l in lines).encode("latin-1")
        batch = max(1, f.get("batch", 4096) // len(lines))
        left = f["count"] // len(lines)
        fd = 2 if f.get("stderr") else 1
        while left > 0:
            n = min(batch, left)
            out(block * n, fd)
            left -= n
    elif "sleep" in step:
        time.sleep(step["sleep"])
    elif "spin" in step:
        end = time.monotonic() + step["spin"]
        x = 0
        while time.monotonic() < end:
            x += 1
    elif "ignore" in step:
        for name in step["ignore"]:
            signal.signal(getattr(signal, name), signal.SIG_IGN)
    elif "grandchild" in step:
        pid = os.fork()
        if pid == 0:
            time.sleep(step["grandchild"])
            os._exit(0)
        out(f"GRANDCHILD {pid}\n")
    elif "ctty" in step:
        mask = sorted(int(s) for s in signal.pthread_sigmask(signal.SIG_BLOCK, []))
        hup = "default" if signal.getsignal(signal.SIGHUP) == signal.SIG_DFL else "changed"
        out(f"CTTY sid={os.getsid(0)} pid={os.getpid()} pgrp={os.getpgrp()} "
            f"tcpgrp={os.tcgetpgrp(0)} hup={hup} mask={mask}\n")
    elif "exit" in step:
        sys.exit(step["exit"])
    else:
        raise SystemExit(f"unknown step {step}")


def main():
    global _log
    scen = json.load(open(sys.argv[1]))
    signal.alarm(int(scen.get("max_seconds", 20)))      # never runs for ever
    if scen.get("keys_log"):
        _log = open(scen["keys_log"], "a")
    for step in scen["steps"]:
        run(step)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:                           # Ctrl-C through the terminal
        os._exit(130)
