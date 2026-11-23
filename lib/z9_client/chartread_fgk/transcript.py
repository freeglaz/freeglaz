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

"""What the engine prints: bounded, de-duplicated, measured.

The case it is built for (B1, a real ColorMunki on macOS): the USB plug moves, the
instrument is lost, and ArgyllCMS's button thread retries without pause, printing
``icoms_usb_transaction: ReadPipeAsync failed with 0xe00002c0`` about 350 000 times a
second (38 million lines seen). A driver that keeps everything fills memory and disk.

:class:`Transcript` takes the raw bytes of the terminal and:

- **forwards** to the prompt parser every byte exactly once, except the complete lines that
  repeat the previous one beyond ``repeat_threshold`` times: prompts are still seen in the
  middle of a flood, and the parser's work does not grow with it. Bytes of an unfinished
  line are forwarded at once (prompts end without a newline);
- **logs** a readable transcript into a file with a hard size budget, plus a reserve kept
  for what matters later (notes from the session, summaries of repeated lines);
- **collapses** runs of identical lines into one line and a count (``[x10416505 …]``);
- **masks** instrument serial numbers in the log (they are kept in memory for the session);
- **measures** lines and bytes per second to tell a flood.

Per-line work only happens on lines that are not repeats of the previous one; a chunk made
only of the current repeated line is counted in C (``list.count``).

:class:`ActivityMonitor` turns these measures, the recognised progress and the CPU time of
the engine into a verdict: flooding, spinning (busy without output or progress), silent
when output was expected. Pure: time is always passed in.
"""
from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

MAX_LOG_BYTES = 1 << 20          # per engine run
RESERVE_BYTES = 128 << 10        # notes and summaries once the budget is spent
MAX_LINE = 4096
REPEAT_THRESHOLD = 20            # identical lines forwarded before the rest are only counted
FLOOD_LINES_PER_S = 2000
FLOOD_BYTES_PER_S = 256 << 10

_MASKS = (
    (re.compile(rb"^((?:Serial Number|Chip ID|Production No\.):[ \t]*)\S.*$"), rb"\1<masked>"),
    (re.compile(rb"(reflective white reference(?: S/N)? )[^,\s]+"), rb"\1<masked>"),
)
_NONPRINT = re.compile(rb"[\x00-\x08\x0b-\x1f\x7f-\xff]")


def mask_serials(line: bytes) -> bytes:
    for rx, rep in _MASKS:
        line = rx.sub(rep, line)
    return line


def _printable(line: bytes) -> str:
    return _NONPRINT.sub(lambda m: f"\\x{m.group()[0]:02x}".encode(), line).decode("ascii")


@dataclass(frozen=True)
class FloodState:
    flooding: bool
    seconds: int            # consecutive whole seconds above the threshold
    lines_per_s: int        # last whole second
    bytes_per_s: int


@dataclass(frozen=True)
class TranscriptStats:
    bytes_in: int
    lines_in: int
    lines_suppressed: int   # repeats counted, neither forwarded nor logged
    log_bytes: int
    log_capped: bool
    notes_dropped: int
    longest_run: int
    peak_lines_per_s: int


class Transcript:
    def __init__(self, log_path: Optional[Path] = None, *, max_log_bytes: int = MAX_LOG_BYTES,
                 reserve_bytes: int = RESERVE_BYTES, max_line: int = MAX_LINE,
                 repeat_threshold: int = REPEAT_THRESHOLD, flood_lines_per_s: int = FLOOD_LINES_PER_S,
                 flood_bytes_per_s: int = FLOOD_BYTES_PER_S, recent: int = 50):
        self._log = open(log_path, "w", encoding="ascii", errors="replace") if log_path else None
        self._max_log, self._reserve = max_log_bytes, reserve_bytes
        self._max_line, self._threshold = max_line, repeat_threshold
        self._flood_lines, self._flood_bytes = flood_lines_per_s, flood_bytes_per_s
        self._t0: Optional[float] = None
        self._now = 0.0
        self._partial = b""
        self._fwd_partial = 0            # bytes of the unfinished line already forwarded
        self._run_line: Optional[bytes] = None
        self._run = 0
        self._run_since = 0.0
        self._recent: deque[str] = deque(maxlen=recent)
        self._buckets: deque[list] = deque(maxlen=16)    # [second, lines, bytes]
        self._bytes_in = self._lines_in = self._suppressed = 0
        self._log_bytes = self._notes_dropped = self._longest = self._peak = 0
        self._capped = False

    # ─── input ──────────────────────────────────────────────────────────────
    def feed(self, data: bytes, now: float) -> str:
        """Raw terminal bytes in; the text for the prompt parser out (see module doc)."""
        if self._t0 is None:
            self._t0 = now
        self._now = now
        self._bytes_in += len(data)
        self._bucket(now)[2] += len(data)
        buf = self._partial + data
        lines = buf.split(b"\n")
        tail = lines.pop()
        fwd = self._fwd_partial                  # prefix of buf already forwarded
        out: list[bytes] = []
        if lines:
            self._account(len(lines), now)
            if fwd:                              # the first line began in an earlier chunk
                out.append(lines[0][fwd:] + b"\n")
                self._take(lines[0], now, forwarded=True)
                lines = lines[1:]
                fwd = 0
            if (self._run_line is not None and self._run >= self._threshold and lines
                    and lines.count(self._run_line) == len(lines)):
                self._run += len(lines)                       # flood fast path (C speed)
                self._suppressed += len(lines)
            else:
                for line in lines:
                    if self._take(line, now):
                        out.append(line + b"\n")
        out.append(tail[fwd:])
        if len(tail) > self._max_line:           # an endless line: logged in pieces
            self._account(1, now)
            self._take(tail, now, forwarded=True)
            self._partial, self._fwd_partial = b"", 0
        else:
            self._partial, self._fwd_partial = tail, len(tail)
        return b"".join(out).decode("latin-1")

    def _bucket(self, now: float) -> list:
        sec = int(now)
        if not self._buckets or self._buckets[-1][0] != sec:
            self._buckets.append([sec, 0, 0])
        return self._buckets[-1]

    def _account(self, n_lines: int, now: float) -> None:
        b = self._bucket(now)
        b[1] += n_lines
        self._lines_in += n_lines
        self._peak = max(self._peak, b[1])

    def _take(self, line: bytes, now: float, forwarded: bool = False) -> bool:
        """One complete line (without its newline). Returns whether to forward it."""
        if line.endswith(b"\r"):
            line = line[:-1]
        if len(line) > self._max_line:
            line = line[:self._max_line] + b" [...truncated]"
        if line == self._run_line:
            self._run += 1
            if self._run > self._threshold:
                if not forwarded:
                    self._suppressed += 1
                return False
            return True
        self._end_run()
        self._run_line, self._run, self._run_since = line, 1, now
        text = _printable(mask_serials(line))
        if text.strip():
            self._recent.append(text)
        self._write(text, important=False)
        return True

    def _end_run(self) -> None:
        if self._run_line is not None and self._run > 1:
            self._longest = max(self._longest, self._run)
            text = _printable(mask_serials(self._run_line))
            self._write(f"[x{self._run - 1} more, {self._now - self._run_since:.1f} s] {text}",
                        important=True)
        self._run_line, self._run = None, 0

    # ─── log ────────────────────────────────────────────────────────────────
    def note(self, text: str, now: float) -> None:
        """A line from the session (an event, a decision), kept even once the raw budget is
        spent. The caller passes text that is already safe to keep (serials masked)."""
        self._now = now
        if self._t0 is None:
            self._t0 = now
        self._write("## " + text, important=True)

    def _write(self, text: str, important: bool) -> None:
        if self._log is None:
            return
        line = f"{self._now - (self._t0 or self._now):9.3f} {text}\n"
        n = len(line)
        if not important:
            if self._capped:
                return
            if self._log_bytes + n > self._max_log:
                self._capped = True
                line = (f"{self._now - (self._t0 or self._now):9.3f} [log budget of "
                        f"{self._max_log} bytes reached: output is counted, not written]\n")
                n = len(line)
        elif self._log_bytes + n > self._max_log + self._reserve:
            self._notes_dropped += 1
            return
        self._log.write(line)
        self._log_bytes += n

    # ─── measures ───────────────────────────────────────────────────────────
    def flood(self, now: float) -> FloodState:
        sec = int(now)
        whole = [b for b in self._buckets if b[0] < sec]
        last = whole[-1] if whole and whole[-1][0] == sec - 1 else None

        def over(b):
            return b[1] > self._flood_lines or b[2] > self._flood_bytes
        seconds, expect = 0, sec - 1
        for b in reversed(whole):
            if b[0] != expect or not over(b):
                break
            seconds += 1
            expect -= 1
        return FloodState(seconds > 0, seconds, last[1] if last else 0, last[2] if last else 0)

    def recent(self) -> list[str]:
        """Last distinct lines (serials masked), oldest first."""
        return list(self._recent)

    def stats(self) -> TranscriptStats:
        return TranscriptStats(self._bytes_in, self._lines_in, self._suppressed, self._log_bytes,
                               self._capped, self._notes_dropped,
                               max(self._longest, self._run), self._peak)

    def close(self, now: Optional[float] = None) -> TranscriptStats:
        if now is not None:
            self._now = now
        if self._partial:
            self._take(self._partial, self._now, forwarded=True)
            self._partial, self._fwd_partial = b"", 0
        self._end_run()
        st = self.stats()
        if self._log is not None:
            self._write(f"[end: {st.bytes_in} bytes, {st.lines_in} lines, {st.lines_suppressed} "
                        f"repeated lines not written, longest run {st.longest_run}]", important=True)
            self._log.close()
            self._log = None
        return st


# ─── activity ───────────────────────────────────────────────────────────────
OK, FLOODING, SPINNING, SILENT = "ok", "flooding", "spinning", "silent"


@dataclass(frozen=True)
class Verdict:
    status: str             # ok | flooding | spinning | silent
    detail: str = ""


class ActivityMonitor:
    """Is the engine still doing something useful?

    - flooding: the transcript has been above its flood threshold for ``flood_seconds``
      with no progress (no recognised event) meanwhile;
    - spinning: it used more than ``spin_cpu_ratio`` of a CPU over the last ``spin_seconds``
      with no progress (a busy loop that prints nothing: a lost instrument on Linux may look
      like this, since Argyll's Linux USB code does not print transfer errors);
    - silent: the caller expected output within ``expect_output_within`` seconds (after a
      key, at start-up) and none came. Waiting for the user to read a strip is NOT silence:
      the caller then passes no expectation."""

    def __init__(self, *, flood_seconds: float = 1.0, spin_cpu_ratio: float = 0.8,
                 spin_seconds: float = 10.0):
        self.flood_seconds = flood_seconds
        self.spin_cpu_ratio, self.spin_seconds = spin_cpu_ratio, spin_seconds
        self._last_output: Optional[float] = None
        self._last_progress: Optional[float] = None
        self._cpu: deque[tuple[float, float]] = deque()

    def on_output(self, now: float) -> None:
        self._last_output = now

    def on_progress(self, now: float) -> None:
        self._last_progress = now
        self._cpu.clear()

    def sample_cpu(self, cpu_seconds: Optional[float], now: float) -> None:
        if cpu_seconds is None:
            return
        self._cpu.append((now, cpu_seconds))
        while len(self._cpu) > 2 and now - self._cpu[1][0] >= self.spin_seconds:
            self._cpu.popleft()

    def cpu_ratio(self) -> Optional[float]:
        if len(self._cpu) < 2:
            return None
        (t0, c0), (t1, c1) = self._cpu[0], self._cpu[-1]
        return (c1 - c0) / (t1 - t0) if t1 > t0 else None

    def verdict(self, now: float, flood: Optional[FloodState] = None,
                expect_output_within: Optional[float] = None, since: Optional[float] = None) -> Verdict:
        """``since``: when the expectation started (a key sent, the process started)."""
        if flood is not None and flood.flooding:
            # Only the flooded whole seconds that began after the last progress count. (Comparing
            # the time since progress with the flooded seconds left a window of 1 − frac(progress)
            # per second, a few ms at worst: a long flood could go unnoticed for seconds.)
            clean = flood.seconds if self._last_progress is None else \
                min(flood.seconds, int(now) - math.ceil(self._last_progress))
            if clean >= self.flood_seconds:
                return Verdict(FLOODING, f"{flood.lines_per_s} lines/s for {clean} s")
        ratio = self.cpu_ratio()
        if ratio is not None and ratio >= self.spin_cpu_ratio:
            span = self._cpu[-1][0] - self._cpu[0][0]
            if span >= self.spin_seconds:
                return Verdict(SPINNING, f"{ratio:.0%} CPU for {span:.1f} s without progress")
        if expect_output_within is not None and since is not None:
            last = self._last_output if self._last_output is not None and self._last_output >= since else None
            if last is None and now - since >= expect_output_within:
                return Verdict(SILENT, f"no output {now - since:.1f} s after it was expected")
        return Verdict(OK)
