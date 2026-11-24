# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""The prompts of ``chartread-fgK`` (ArgyllCMS 3.5.0 strip mode + checkpoint patch v3), as
typed events, and the only keys freeglaz may type in answer.

**Recognition** (:class:`PromptParser`), line by line. A prompt that waits for a key is
several lines whose LAST one has no newline: the parser remembers the head line it saw
(``Ready to read strip pass C``, ``Strip read failed due to misread (…)``…) and emits the
event when the tail arrives (``…any other key to start:``), from the unfinished line. Lines
printed in between by another thread (USB errors) change nothing; a tail whose head was
missed still gives a usable, conservative event. The texts are the format strings of
``spectro/chartread.c``, ``spectro/instappsup.c`` and the v3 patch; the ColorMunki ones up
to the first strip prompt are confirmed by a real transcript (B1).

**Keys** (:data:`COMMANDS`, :class:`KeyGate`). What chartread does with a key at its strip
prompt decides everything: ``f b n d`` (and ``g``, which means ``d``) are commands, ``Esc
q ^C`` stop, and EVERY other key starts a measurement. So:

- "continue" and "retry" are always ``n``: at a calibration or failure prompt any key but
  Esc/q continues, and if a ``n`` ever reached the strip prompt it would be a navigation
  ("next unread"), never a measurement;
- the only key that would start a measurement there, Return (accept an unexpected value),
  is sent once and never resent; accepting a wrong strip is not offered at all;
- nothing that starts a reading is exposed: a reading is started with the instrument's
  own button;
- one key per prompt, at least ``min_delay`` after the prompt's last character, never a
  queue of keys;
- one automatic resend, only at the calibration prompts: there (``instappsup.c``) Argyll
  prints the prompt THEN empties the keyboard buffer, so an early key can be thrown away
  and the session would hang. A key it does take is followed at once by a newline, so total
  silence for ``resend_after`` means the key was lost. Everywhere in ``chartread.c`` the
  buffer is emptied BEFORE the prompt is printed: no key is lost there, no resend.
"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Callable, Optional

from .transcript import mask_serials_text

MAX_LINE = 4096

# ─── complete lines ─────────────────────────────────────────────────────────
# (kind, pattern searched in one line). Kinds starting with "_" are heads or context: they
# update the parser's state and are not emitted.
_LINE_PATTERNS: list[tuple[str, str]] = [
    ("ck_refused", r"\(Warning\) Checkpoint not saved: (?P<ckf_msg>.*)"),
    ("ck_reduced", r"\(Warning\) Checkpoint durability reduced: (?P<ckr_msg>.*)"),
    ("ck_not_durable", r"\(Warning\) Checkpoint not durable: (?P<ckn_msg>.*)"),
    ("checkpoint", r"Checkpoint: (?P<ck_n>\d+) patches saved to '(?P<ck_path>[^']*)'"
                   r"(?P<ck_dur> \(reduced durability\)| \(not durable\))?$"),
    ("_wrong_strip", r"\(Warning\) Seem to have read strip pass (?P<ws_read>\S+) rather than "
                     r"(?P<ws_expected>\S+)!"),
    ("_unexpected", r"There is at least one patch with an very unexpected response! "
                    r"\(DeltaE (?P<ux_de>[0-9.]+)\)"),
    ("_ready", r"Ready to read strip pass (?P<rd_pass>\S+)(?P<rd_suffix>.*)"),
    ("_ready_save", r"'d' when done, Esc or 'q' to quit (?P<rd_save>\(passes read so far are saved\)"
                    r"|without saving)\."),
    ("strip_ok", r" Strip read OK(?P<ok_dtp> \(DTP51 offset fix of -?\d+ applied\))?"
                 r"(?P<ok_rev> \(Strip read in reverse direction\))?"),
    ("_misread", r"Strip read failed due to misread \((?P<mr_reason>.*)\)"),
    ("_comms_failed", r"Strip read failed due to communication problem\."),
    ("_strip_error", r"Strip read failed due unexpected error :'(?P<se_msg>.*)"),
    ("needcal", r"Strip read failed because instruments needs calibration"),
    ("sensor_position", r"Spot read failed due to the sensor being in the wrong position"),
    ("_stopped", r"Strip read stopped at user request!"),
    ("_instrument_error", r"Got '(?P<ie_msg>.*) error\."),
    ("_cal_continue", r"^[ \t]*(?:and then hit|Hit) any key to continue,"),
    ("calibration_failed", r"Calibration failed with '(?P<cf_msg>.*)"),
    ("calibration_done", r"Calibration complete"),
    ("steps_in_pass", r"Steps in each Pass = (?P<sip_n>\d+)"),
    ("passes_in_strip", r"Passes in each Strip = (?P<pis_n>\d+)"),
    ("instrument_field", r"(?P<if_name>Instrument Type|Serial Number|Firmware version|Version string"
                         r"|Calibration Ver\.|Chip ID|Production No\.):[ \t]*(?P<if_value>.*)"),
    # chartread.c start-up failures (it exits right after them, status 0)
    ("start_failed", r"Establishing communications with instrument failed with message '(?P<sf_msg>.*)"
                     r"|Initialising instrument failed with message '(?P<sf_msg2>.*)"
                     r"|(?P<sf_text>Unknown, inappropriate or no instrument detected"
                     r"|Setting requested filter not supported by instrument"
                     r"|Need reflection spot, strip, xy or chart reading capability,)"),
    ("instrument_not_spectral", r"Warning: Instrument isn't capable of spectral measurement"),
    ("chart_instrument", r"Warning: chart is for (?P<ci_chart>.*), using instrument (?P<ci_inst>.*)"),
    ("fatal", r"(?P<fe_prog>chartread[\w.-]*): Error - (?P<fe_msg>.*)"),
    # macOS IOKit transfer errors (usbio_ox.c), possibly glued to other text.
    ("usb_error", r"icoms_usb_transaction: (?P<ue_dir>Read|Write)PipeAsync failed with "
                  r"(?P<ue_code>0x[0-9a-fA-F]+)"),
]
_LINE = re.compile("|".join(f"(?P<{k.lstrip('_') + ('_h' if k.startswith('_') else '')}>{p})"
                            for k, p in _LINE_PATTERNS))

# ─── tails: the last, unfinished line of a prompt waiting for a key ─────────
_TAILS = re.compile(
    r"(?P<t_ready>Trigger instrument switch to start reading\.|Trigger instrument switch or any "
    r"other key to start:|Press any other key to start:)"
    r"|(?P<t_return>Hit Return to use it anyway, any other key to retry, Esc or {1,2}'q' to give up:)"
    r"|(?P<t_retry_q>Hit Esc or 'q' to give up, any other key to retry:)"
    r"|(?P<t_misread>Hit Esc to give up, any other key to retry:)"
    r"|(?P<t_cal>or hit Esc or Q to abort(?P<t_cal_skip>, or S to skip)?:)"
    r"|(?P<t_cal_retry>Hit any key to retry, or Esc or Q to abort:)"
    r"|(?P<t_done>Done \? - At least one unread patch \((?P<dc_id>[^,]*), (?P<dc_loc>[^)]*)\), "
    r"Are you sure \[y/n\]:)")

# Prompts that wait for a key (the others are information).
KEY_PROMPTS = frozenset({"calibration_prompt", "calibration_retry", "ready", "wrong_strip",
                         "unexpected", "misread", "comms_failed", "strip_error", "instrument_error",
                         "stopped", "retry_prompt", "done_confirm"})
# Argyll prints these THEN empties the keyboard buffer (instappsup.c): a key can be lost.
RESEND_PROMPTS = frozenset({"calibration_prompt", "calibration_retry"})
# macOS IOKit codes seen while the ColorMunki was being lost (B1).
USB_LOSS_CODES = frozenset({"0xe00002c0", "0xe00002d9", "0xe00002d8", "0xe000404f"})

ESC = b"\x1b"
_RETRY = {"retry": b"n", "give_up": ESC}
COMMANDS: dict[str, dict[str, bytes]] = {
    "calibration_prompt": {"continue": b"n", "abort": ESC},
    "calibration_retry": {"retry": b"n", "abort": ESC},
    "ready": {"next": b"f", "prev": b"b", "next_unread": b"n", "finish": b"d", "pause": ESC},
    "wrong_strip": dict(_RETRY),                         # accepting a wrong strip: never
    "unexpected": {**_RETRY, "accept": b"\r"},
    "misread": dict(_RETRY), "comms_failed": dict(_RETRY), "strip_error": dict(_RETRY),
    "instrument_error": dict(_RETRY), "stopped": dict(_RETRY),
    "retry_prompt": dict(_RETRY),                        # a retry prompt whose head was missed
    "done_confirm": {"no": b"n"},                        # never "y"
}
# What the strip prompt does NOT treat as a measurement trigger (chartread.c inst_set_uih).
NOT_TRIGGER_AT_READY = frozenset(b"fFbBnNgGdDqQ\x1b\x03")


@dataclass(frozen=True)
class Event:
    kind: str
    t: float                      # time the text that completed it was read
    data: dict = field(default_factory=dict)
    text: str = ""

    def public(self) -> "Event":
        """Copy safe to log or show: serial numbers masked."""
        data = dict(self.data)
        if self.kind == "instrument_field" and data.get("name") in ("Serial Number", "Chip ID",
                                                                     "Production No."):
            data["value"] = "<masked>"
        if "instruction" in data:
            data["instruction"] = _mask(data["instruction"])
        return replace(self, data=data, text=_mask(self.text))


_mask = mask_serials_text           # one table of unit identifiers to mask (transcript.py)


def _calibration_kind(instruction: str) -> str:
    s = instruction.lower()
    if "calibration position" in s:
        return "sensor_position"          # ColorMunki: turn the dial to calibration
    if "reflective white reference" in s:
        return "white_reference"          # i1Pro family: on its white tile
    if "reflective white calibration" in s:
        return "white_calibration"
    return "other"


class PromptParser:
    """Text in (from :meth:`Transcript.feed`), events out, in the order they were printed."""

    def __init__(self):
        self._partial = ""
        self._tail_end = 0                # tail already emitted from the unfinished line, up to
        self._head: Optional[tuple[str, dict]] = None
        self._ready_save: Optional[str] = None
        self._recent: deque[str] = deque(maxlen=4)     # meaningful lines (calibration text)
        self._cal_instruction = ""

    def feed(self, text: str, t: float) -> list[Event]:
        if not text:
            return []
        self._partial += text.replace("\r", "")
        *lines, self._partial = self._partial.split("\n")
        events: list[Event] = []
        for line in lines:
            if self._tail_end:            # the line whose tail was already emitted
                line, self._tail_end = line[self._tail_end:], 0
            self._line(line, t, events)
        if len(self._partial) > MAX_LINE:
            keep = self._partial[-512:]
            self._tail_end = max(0, self._tail_end - (len(self._partial) - len(keep)))
            self._partial = keep
        m = _TAILS.search(self._partial, self._tail_end)
        if m:
            ev = self._tail(m, t)
            if ev is not None:
                events.append(ev)
            self._tail_end = m.end()
        return events

    def _line(self, line: str, t: float, events: list[Event]) -> None:
        """Every message of one complete line, in order: heads, information, and tails of
        prompts that end with their own newline or share a line with other text."""
        pos, meaningful = 0, bool(line.strip())
        while pos <= len(line):
            m, tm = _LINE.search(line, pos), _TAILS.search(line, pos)
            if m is None and tm is None:
                break
            if tm is not None and (m is None or tm.start() < m.start()):
                ev = self._tail(tm, t)
                if ev is not None:
                    events.append(ev)
                pos = max(tm.end(), pos + 1)
                continue
            name, g = m.lastgroup, m.groupdict()
            if name == "cal_continue_h":
                self._cal_instruction = self._recent[-1].strip() if self._recent else ""
                meaningful = False
            elif name == "ready_save_h":
                self._ready_save = g["rd_save"]
            elif name.endswith("_h"):
                head = name[:-2]
                self._head = (head, self._head_data(head, g))
                if head == "ready":
                    self._ready_save = None
            else:
                if name == "usb_error":
                    meaningful = False
                events.append(Event(name, t, self._info_data(name, g), m.group(name)))
            pos = max(m.end(), pos + 1)
        if meaningful:
            self._recent.append(line)

    @staticmethod
    def _head_data(head: str, g: dict) -> dict:
        if head == "ready":
            suffix = g["rd_suffix"].strip()
            return {"pass": g["rd_pass"], "read_before": "This row has been read" in suffix,
                    "all_read": "ALL ROWS READ" in suffix}
        if head == "wrong_strip":
            return {"read": g["ws_read"], "expected": g["ws_expected"]}
        if head == "unexpected":
            return {"delta_e": float(g["ux_de"])}
        if head == "misread":
            return {"reason": g["mr_reason"]}
        if head in ("strip_error", "instrument_error"):
            return {"message": g["se_msg"] if head == "strip_error" else g["ie_msg"]}
        return {}

    @staticmethod
    def _info_data(name: str, g: dict) -> dict:
        if name == "checkpoint":
            dur = {None: "full", " (reduced durability)": "reduced", " (not durable)": "not_durable"}
            return {"n": int(g["ck_n"]), "path": g["ck_path"], "durability": dur[g["ck_dur"]]}
        if name in ("ck_refused", "ck_reduced", "ck_not_durable"):
            return {"message": g["ckf_msg"] or g["ckr_msg"] or g["ckn_msg"]}
        if name == "strip_ok":
            return {"reversed": g["ok_rev"] is not None, "dtp_offset": g["ok_dtp"] is not None}
        if name == "start_failed":
            return {"message": g["sf_msg"] or g["sf_msg2"] or g["sf_text"]}
        if name == "calibration_failed":
            return {"message": g["cf_msg"]}
        if name == "chart_instrument":
            return {"chart": g["ci_chart"], "instrument": g["ci_inst"]}
        if name in ("steps_in_pass", "passes_in_strip"):
            return {"n": int(g["sip_n"] or g["pis_n"])}
        if name == "instrument_field":
            return {"name": g["if_name"], "value": g["if_value"].strip()}
        if name == "fatal":
            return {"program": g["fe_prog"], "message": g["fe_msg"]}
        if name == "usb_error":
            code = g["ue_code"].lower()
            return {"direction": g["ue_dir"], "code": code, "loss": code in USB_LOSS_CODES}
        return {}

    def _take_head(self, *kinds: str) -> Optional[tuple[str, dict]]:
        if self._head is not None and self._head[0] in kinds:
            head, self._head = self._head, None
            return head
        return None

    def _tail(self, m: re.Match, t: float) -> Optional[Event]:
        tail, text = m.lastgroup, m.group(0)        # the outer group closes last
        if tail == "t_ready":
            head = self._take_head("ready")
            data = dict(head[1]) if head else {"pass": None, "read_before": False, "all_read": False}
            data["checkpointing"] = bool(self._ready_save and self._ready_save.startswith("(passes"))
            data["key_starts_reading"] = "any other key" in text
            self._ready_save = None
            return Event("ready", t, data, text)
        if tail == "t_return":
            head = self._take_head("wrong_strip", "unexpected")
            return Event(head[0], t, head[1], text) if head else Event("retry_prompt", t, {}, text)
        if tail == "t_retry_q":
            head = self._take_head("comms_failed", "strip_error", "instrument_error", "stopped")
            return Event(head[0], t, head[1], text) if head else Event("retry_prompt", t, {}, text)
        if tail == "t_misread":
            head = self._take_head("misread")
            return Event("misread", t, head[1] if head else {"reason": None}, text)
        if tail == "t_cal":
            ins = self._cal_instruction
            self._cal_instruction = ""
            return Event("calibration_prompt", t, {"instruction": ins, "calibration": _calibration_kind(ins),
                                                    "skippable": m.group("t_cal_skip") is not None}, text)
        if tail == "t_cal_retry":
            return Event("calibration_retry", t, {}, text)
        if tail == "t_done":
            return Event("done_confirm", t, {"id": m.group("dc_id"), "loc": m.group("dc_loc")}, text)
        return None


# ─── keys ───────────────────────────────────────────────────────────────────
class KeyRefused(ValueError):
    """``code``: no_prompt | already_answered | not_allowed | not_all_read."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass
class _Answer:
    prompt: Event
    command: Optional[str] = None
    pending: Optional[bytes] = None       # key chosen, waiting for min_delay
    key: Optional[bytes] = None           # key written
    sent_at: Optional[float] = None
    resent_at: Optional[float] = None


class KeyGate:
    """The only path from a decision to a key. ``request()`` validates a command against the
    current prompt; ``tick()`` writes it when its time has come and does the single allowed
    resend. Nothing is queued beyond the one answer to the one current prompt: a new prompt
    drops an answer not yet written."""

    def __init__(self, write: Callable[[bytes], None], *, min_delay: float = 0.1,
                 resend_after: float = 1.5):
        self._write = write
        self.min_delay, self.resend_after = min_delay, resend_after
        self._answer: Optional[_Answer] = None
        self._last_output = float("-inf")
        self.log: list[tuple[float, str, str, bytes]] = []    # (t, prompt, command, key)

    @property
    def prompt(self) -> Optional[Event]:
        """The key prompt still free to be answered, if any."""
        a = self._answer
        return a.prompt if a is not None and a.command is None else None

    def on_event(self, ev: Event) -> None:
        if ev.kind in KEY_PROMPTS:
            self._answer = _Answer(ev)

    def on_output(self, now: float) -> None:
        self._last_output = now

    def request(self, command: str) -> None:
        a = self._answer
        if a is None:
            raise KeyRefused("no_prompt", "no prompt is waiting for a key")
        if a.command is not None:
            raise KeyRefused("already_answered", f"the {a.prompt.kind} prompt already got {a.command!r}")
        allowed = COMMANDS.get(a.prompt.kind, {})
        if command not in allowed:
            raise KeyRefused("not_allowed", f"{command!r} is not allowed at the {a.prompt.kind} prompt")
        if command == "finish" and not a.prompt.data.get("all_read"):
            raise KeyRefused("not_all_read", "finish only once chartread reports every row read")
        a.command, a.pending = command, allowed[command]

    def tick(self, now: float) -> Optional[bytes]:
        """Write the requested key once allowed, or the one resend. Returns what was written."""
        a = self._answer
        if a is None or a.command is None:
            return None
        if a.key is None:
            if now < a.prompt.t + self.min_delay:
                return None
            a.key, a.sent_at = a.pending, now
            self._write(a.key)
            self.log.append((now, a.prompt.kind, a.command, a.key))
            return a.key
        if (a.prompt.kind in RESEND_PROMPTS and a.resent_at is None
                and self._last_output < a.sent_at and now - a.sent_at >= self.resend_after):
            a.resent_at = now
            self._write(a.key)
            self.log.append((now, a.prompt.kind, a.command + " (resent)", a.key))
            return a.key
        return None
