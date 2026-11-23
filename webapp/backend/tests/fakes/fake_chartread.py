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
            if cls == "command":                        # chartread: printf("\n") for a command
                out("\n")
            return key
        if button_at is not None and time.monotonic() >= button_at:
            log_key("ready", b"", "button")
            return b""
        time.sleep(0.05)


# ─── "measure": a small strip-reading chartread with -K checkpoints ─────────
TARGETS = {"colormunki": "X-Rite ColorMunki", "i1pro3": "X-Rite i1 Pro 3"}


def _read_cgats(path):
    kw, fmt, rows, state = {}, [], [], "head"
    for ln in open(path, encoding="latin-1").read().splitlines():
        s = ln.strip()
        if state == "data":
            if s == "END_DATA":
                break
            if s:
                rows.append(s.split())
        elif s == "BEGIN_DATA_FORMAT":
            state = "fmt"
        elif state == "fmt":
            if s == "END_DATA_FORMAT":
                state = "head"
            else:
                fmt += s.split()
        elif s == "BEGIN_DATA":
            state = "data"
        elif s and " " in s:
            k, v = s.split(None, 1)
            kw.setdefault(k, v.strip('"'))
    return kw, fmt, rows


def _label(loc):
    return loc.strip('"').rstrip("0123456789")


def measure(cfg):
    args = sys.argv[2:]
    base, resume, nocal = args[-1], "-r" in args, "-N" in args
    flt = args[args.index("-F") + 1] if "-F" in args else None
    kw, fmt, rows = _read_cgats(base + ".ti2")
    i_id, i_loc = fmt.index("SAMPLE_ID"), fmt.index("SAMPLE_LOC")
    rgb_i = [fmt.index(c) for c in ("RGB_R", "RGB_G", "RGB_B")]
    values = json.load(open(cfg["values"]))
    labels = sorted({_label(r[i_loc]) for r in rows}, key=lambda s: (len(s), s))
    members = {lab: [k for k, r in enumerate(rows) if _label(r[i_loc]) == lab and r[i_id] != "0"]
               for lab in labels}
    read = {}                                            # row index -> measured value strings
    if resume and os.path.exists(base + ".ti3"):
        _, f3, r3 = _read_cgats(base + ".ti3")
        where = {r[i_loc]: k for k, r in enumerate(rows)}
        for r in r3:
            read[where[r[f3.index("SAMPLE_LOC")]]] = r[f3.index("XYZ_X"):]
    nb = len(next(iter(values.values()))) - 3
    target = cfg.get("target_instrument", TARGETS[cfg.get("type", "colormunki")])

    def write(final=False, crash=None):
        head = ["CTI3   ", "", 'DESCRIPTOR "Argyll Calibration Target chart information 3"',
                'ORIGINATOR "Argyll chartread"', f'CREATED "{time.ctime()} {time.monotonic_ns()}"',
                'DEVICE_CLASS "OUTPUT"', 'COLOR_REP "iRGB_XYZ"', f'TARGET_INSTRUMENT "{target}"']
        if cfg.get("instrument_filter"):                 # a wrong keyword, for refusal tests
            head.append(f'INSTRUMENT_FILTER "{cfg["instrument_filter"]}"')
        elif flt == "u":
            head.append('INSTRUMENT_FILTER "UVCUT"')
        head += [f'DEVCALSTD "{cfg.get("devcalstd", "XRGA")}"', f'SPECTRAL_BANDS "{nb}"',
                 f'SPECTRAL_START_NM "{cfg.get("start_nm", 380):.6f}"', ""]
        cols = ["SAMPLE_ID", "SAMPLE_LOC", "RGB_R", "RGB_G", "RGB_B", "XYZ_X", "XYZ_Y", "XYZ_Z"] + \
            [f"SPEC_{int(cfg.get('start_nm', 380) + 10 * j)}" for j in range(nb)]
        body = [" ".join([rows[k][i_id], rows[k][i_loc]] + [rows[k][i] for i in rgb_i] + read[k])
                for k in sorted(read)]
        text = "\n".join(head + [f"NUMBER_OF_FIELDS {len(cols)}", "BEGIN_DATA_FORMAT", " ".join(cols),
                                 "END_DATA_FORMAT", "", f"NUMBER_OF_SETS {len(body)}", "BEGIN_DATA"]
                         + body + ["END_DATA", ""]).encode()
        tmp = base + ".ti3.tmp"
        with open(tmp, "wb") as f:
            if crash == "during_write":
                f.write(text[:len(text) // 2])
                f.flush()
                os.kill(os.getpid(), signal.SIGKILL)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, base + ".ti3")
        if crash == "after_rename":
            os.kill(os.getpid(), signal.SIGKILL)
        return len(body)

    out(f"Steps in each Pass = {kw.get('STEPS_IN_PASS', '?')}\n"
        f"Passes in each Strip = {kw.get('PASSES_IN_STRIPS2', '1').split(',')[0]}\n")
    out(f"Instrument Type:   {cfg.get('type_name', 'ColorMunki')}\n")
    if cfg.get("serial"):
        out(f"Serial Number: {cfg['serial']}\n")
    if not (nocal and cfg.get("calibration_valid", True)):
        out("\n")
        if key_prompt("calibration", {"instruction": cfg.get("instruction",
                                      "Set instrument sensor to calibration position,")})[0] in ABORT_KEYS:
            return 0

    def unread(lab):
        return any(k not in read for k in members[lab])

    def next_unread(cur):
        for i in range(1, len(labels) + 1):
            if unread(labels[(cur + i) % len(labels)]):
                return (cur + i) % len(labels)
        return cur

    cur = next((i for i, lab in enumerate(labels) if unread(lab)), 0)
    trig = nck = 0
    while True:
        lab, all_done = labels[cur], not any(unread(x) for x in labels)
        step = {"pass": lab, "all_read": all_done, "read_before": not unread(lab)}
        if "button_ms" in cfg and not all_done and trig < cfg.get("button_limit", 10 ** 6):
            step["button_ms"] = cfg["button_ms"]        # the user presses the instrument's button
        key = ready(step)
        if key and key[0] in COMMAND_KEYS:
            c = key.lower()
            if c == b"f":
                cur = (cur + 1) % len(labels)
            elif c == b"b":
                cur = (cur - 1) % len(labels)
            elif c == b"n":
                cur = next_unread(cur)
            else:                                        # d (and g, as chartread)
                if not all_done:
                    first = next(k for x in labels for k in members[x] if k not in read)
                    if key_prompt("done_confirm", {"id": rows[first][i_id],
                                                   "loc": rows[first][i_loc].strip('"')}) not in (b"y", b"Y"):
                        continue
                if cfg.get("final") == "fail":
                    out(f"{os.path.basename(sys.argv[0])}: Error - Write error : write error on "
                        f"'{base}.ti3.tmp' (No space left on device)\n")
                    return 1
                write(final=True)
                if cfg.get("final") == "reduced":
                    out("\n(Warning) Checkpoint durability reduced: F_FULLFSYNC not supported for the "
                        "file, only fsync() done: the drive cache may not be flushed\n")
                return 0
            continue
        if key and key[0] in ABORT_KEYS:
            if key_prompt("stopped", {})[0] in ABORT_KEYS:
                return 0
            continue
        trig += 1                                        # a reading (button or trigger key)
        act = cfg.get("actions", {}).get(str(trig), "")
        if act == "kill":
            os.kill(os.getpid(), signal.SIGKILL)
        if act == "misread":
            if key_prompt("misread", {})[0] in ABORT_KEYS:
                return 0
            continue
        accepted = False
        if act in ("wrong_strip", "unexpected"):
            k = key_prompt(act, {"read": labels[(cur + 3) % len(labels)], "expected": lab})
            if k[0] in ABORT_KEYS:
                return 0
            if k not in (b"\r", b"\n"):
                continue
            accepted = True
        scale = float(act.split("=")[1]) if act.startswith("scale=") else 1.0
        for k in members[lab]:
            v = values[rows[k][i_loc].strip('"')]
            read[k] = [f"{x * scale:.6f}" for x in v]
        if not accepted:
            out(" Strip read OK\n")
        nck += 1
        mode = cfg.get("checkpoints", {}).get(str(nck), "full")
        if mode == "refused":
            out(CHECKPOINT["refused"].format(n=0, path=base + ".ti3"))
        else:
            n = write(crash={"crash_during_write": "during_write",
                             "crash_after_rename": "after_rename"}.get(mode))
            out(CHECKPOINT[mode if mode in CHECKPOINT else "full"].format(n=n, path=base + ".ti3"))
            if mode == "crash_after_line":
                os.kill(os.getpid(), signal.SIGKILL)
        cur = next_unread(cur)


def run(step):
    if "measure" in step:
        sys.exit(measure(step["measure"]))
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
