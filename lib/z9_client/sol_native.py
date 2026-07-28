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

"""NATIVE Z9 (SOL) measurement channel — raw socket transport, no DLL or Windows.

The Z9's embedded spectrophotometer is driven in pseudo-HTTP PLAIN TEXT
wrapped in a PJL JetDirect wrapper, over a SINGLE TCP socket on port 9100:
no TLS, no authentication, no handshake (the 1st byte sent is
already the PJL preamble). Framing: lines in ``\\n`` (NO CRLF, NO
``Host:``, NO HTTP version on the request line), body prefixed by
``Content-Length`` -> **raw socket mandatory** (``http.client``/``requests``
would impose Host/CRLF/version, absent from this dialect).

The byte sequences below are the EXACT wire format expected by the
Z9's native colorimetry channel (port 9100).

This native channel is meant to become the PRIMARY measurement channel (chart
with N free patches, standalone, cross-platform); the ``scan_only`` firmware stays
secondary (parity with the firmware measurement channel).

Milestones:
- **Milestone 1** (``probe_status``) — VALIDATED live: open SOL, read status,
  close. Harmless (no measurement).
- **Milestone 2** (``measure``) — full measurement: POST /Colorimetry/Scan + poll
  status + GET /Colorimetry/Result -> CGATS. HARDWARE ACT (the spectro scans the
  loaded chart). Dry-run by default on the CLI side; ``--go`` required to scan.
"""
from __future__ import annotations

import socket
import time
from typing import Optional

# No hardcoded DEFAULT_HOST: the spectro (port 9100) follows the RESOLVED IP — `host`
# is required. The callers (chart_scan_job, CLI `chart scan`) already pass
# `client.host`; the standalone CLI `_cli` resolves via Z9Client.from_env() (IP config).
DEFAULT_PORT = 9100        # JetDirect

# ── EXACT bytes of the native Z9 dialect (required wire format) ────────────────
PJL_ENTER_SOL = b"\x1b%-12345X@PJL ENTER LANGUAGE = SOL\n"   # open, 35 b
PJL_CLOSE = b"\x1b%-12345X@PJL EOJ\n\x1b%-12345X"            # EOJ + UEL, end C2S
REQ_STATUS = b"GET /Colorimetry/Status\n"                    # exactly 24 b
REQ_RESULT = b"GET /Colorimetry/Result\n"                    # -> CGATS in the body

# EXACT parameters of the POST /Colorimetry/Scan (A3 multipass chart, 464 patches).
# Field order reproduced as-is (313-byte body). For milestone 2 we
# replay THESE values so the CGATS is comparable to the firmware measurement channel.
REFERENCE_SCAN_FIELDS = [
    ("DistanceUnits", "Inches"),
    ("SkewMarksType", "Both"),
    ("ZeroReference", "MediaEdges"),
    ("ColorStabTime", "0"),
    ("ScanMeasures", "Spectral"),
    ("NumPatches", "464"),
    ("PatchesPerRow", "18"),
    ("NumScansPerPatch", "1"),
    ("GridType", "HexagonalShiftFirst"),
    ("SkewMarksToPatch_X", "0.594443"),
    ("FirstPatch_X", "1.22529"),
    ("FirstPatch_Y", "2.06272"),
    ("ToNextPatch_X", "0.560295"),
    ("ToNextPatch_Y", "0.512666"),
]

# OperationStatus (wire text). Success = OP_FINISHED_OK.
SCAN_SUCCESS = "OP_FINISHED_OK"
# Failure substrings (undocumented failure names — broad guard).
SCAN_ERROR_HINTS = ("ERROR", "FAIL", "CANCEL", "TIMEOUT", "SKEW", "ABORT")


class SolResponse:
    """A pseudo-HTTP response from the SOL channel."""

    def __init__(self, status_line: str, headers: dict, body: bytes):
        self.status_line = status_line   # e.g. "HTTP/1.1 200 OK"
        self.headers = headers           # headers (lowercase keys)
        self.body = body                 # raw body (Content-Length bytes)

    def fields(self) -> dict:
        """Body ``key = value\\n`` -> dict (e.g. ``OperationStatus`` = ``IDLE``)."""
        out = {}
        for line in self.body.decode("ascii", "replace").splitlines():
            if " = " in line:
                key, val = line.split(" = ", 1)
                out[key.strip()] = val.strip()
        return out

    def __repr__(self) -> str:
        return f"<SolResponse {self.status_line!r} body={len(self.body)}o>"


def _read_line(sock: socket.socket) -> bytes:
    """Read up to and including ``\\n``, byte by byte (LF dialect, no CRLF)."""
    buf = bytearray()
    while True:
        c = sock.recv(1)
        if not c:                        # socket closed by the peer
            break
        buf += c
        if c == b"\n":
            break
    return bytes(buf)


def _read_response(sock: socket.socket) -> SolResponse:
    """Read ONE response: status line, headers (terminated by a blank line),
    then ``Content-Length`` bytes of body (0 if the header is absent)."""
    status_line = _read_line(sock).rstrip(b"\n").decode("ascii", "replace")
    if not status_line:
        raise ConnectionError("empty SOL response (socket closed?)")
    headers: dict = {}
    while True:
        line = _read_line(sock)
        if line in (b"\n", b"", b"\r\n"):    # blank line = end of headers
            break
        txt = line.rstrip(b"\n").decode("ascii", "replace")
        if ":" in txt:
            key, val = txt.split(":", 1)
            headers[key.strip().lower()] = val.strip()
    n = int(headers.get("content-length", "0"))
    body = bytearray()
    while len(body) < n:
        chunk = sock.recv(n - len(body))
        if not chunk:
            break
        body += chunk
    return SolResponse(status_line, headers, bytes(body))


def build_scan_body(fields=REFERENCE_SCAN_FIELDS) -> bytes:
    """Serialize the scan fields into a ``key: value\\n`` text body (order preserved)."""
    return "".join(f"{k}: {v}\n" for k, v in fields).encode("ascii")


def build_scan_request(body: bytes) -> bytes:
    """Assemble the POST /Colorimetry/Scan request (LF headers + Content-Length)."""
    head = (b"POST /Colorimetry/Scan\n"
            b"Content-Type: text/plain\n"
            b"Content-Length: %d\n\n" % len(body))
    return head + body


class SolSession:
    """SOL session over a single 9100 socket (all ops go through it).

    Opens the session (PJL ENTER SOL) on entry, closes it cleanly (EOJ + UEL
    + close) on exit — even on exception (never leaves a 9100 socket
    open). Responses are pipelined: we read exactly one per request.
    """

    def __init__(self, host: str, port: int = DEFAULT_PORT,
                 timeout: float = 30.0):
        self.host, self.port, self.timeout = host, port, timeout
        self._sock: Optional[socket.socket] = None

    def __enter__(self) -> "SolSession":
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._sock.settimeout(self.timeout)
        self._sock.sendall(PJL_ENTER_SOL)
        return self

    def __exit__(self, *exc) -> None:
        if self._sock is not None:
            try:
                self._sock.sendall(PJL_CLOSE)
            except OSError:
                pass
            self._sock.close()
            self._sock = None

    def _request(self, raw: bytes) -> SolResponse:
        assert self._sock is not None, "session not open"
        self._sock.sendall(raw)
        return _read_response(self._sock)

    def get_status(self) -> SolResponse:
        return self._request(REQ_STATUS)

    def post_scan(self, body: bytes) -> SolResponse:
        return self._request(build_scan_request(body))

    def get_result(self) -> SolResponse:
        return self._request(REQ_RESULT)


def probe_status(host: str, port: int = DEFAULT_PORT,
                 timeout: float = 10.0) -> SolResponse:
    """MILESTONE 1 — minimal HARMLESS dialogue (open SOL, read status, close).
    NO POST /Scan, NO measurement."""
    with SolSession(host, port, timeout) as s:
        return s.get_status()


def measure(host: str, port: int = DEFAULT_PORT,
            fields=REFERENCE_SCAN_FIELDS, poll_interval: float = 1.5,
            on_status=None) -> bytes:
    """MILESTONE 2 — full measurement of a LOADED chart -> CGATS (bytes).

    ⚠️ HARDWARE ACT: triggers the spectro scan on the physically
    loaded chart. The ``fields`` must describe the real chart (by default = the
    464-patch reference chart). Sequence over a single session:
    POST /Scan (->202) -> poll GET /Status until OP_FINISHED_OK -> GET /Result.

    NO client-side DURATION timeout: a scan of any size (400, 2000, 20000 patches)
    runs to its END — it is the Z9 that concludes (OP_FINISHED_OK / error status),
    never a client clock. A fixed timeout would arbitrarily kill long scans (2000
    patches sat right at the edge of the old 1500 s cap → flaky). The SOL session
    is NEVER torn down on a duration basis (that would risk aborting the spectro
    pass mid-scan); it closes only on genuine completion / error / disconnection.

    The ONLY time bound is per-request: the SolSession socket timeout
    (``max(60, poll_interval*4)`` s). A RUNNING scan keeps answering STATUS polls;
    if the Z9 stops answering a poll within that window, that is a DISCONNECTION
    (distinct from "scan too long") → raised as an explicit "Z9 unreachable"
    error, NOT a duration timeout.

    :param on_status: optional callback(operation_status:str, fields:dict) at each poll.
    :raises RuntimeError: failure status, POST refused, or Z9 unreachable (no STATUS response).
    :return: the raw CGATS (body of GET /Result).
    """
    poll_timeout = max(60.0, poll_interval * 4)
    with SolSession(host, port, timeout=poll_timeout) as s:
        post = s.post_scan(build_scan_body(fields))
        if "202" not in post.status_line and "200" not in post.status_line:
            raise RuntimeError(f"POST /Scan refused: {post.status_line!r}")

        last = None
        while True:
            try:
                st = s.get_status()
            except OSError as e:
                # The Z9 did not answer this STATUS poll within the socket timeout.
                # A running scan keeps answering → this is a DISCONNECTION, not a
                # "scan too long". We do NOT retry on the same session (a timed-out
                # read desyncs the pipelined protocol); we surface a clear error.
                raise RuntimeError(
                    f"Z9 unreachable during scan (no STATUS response within "
                    f"{poll_timeout:.0f}s): {e}") from e
            op = st.fields().get("OperationStatus", "?")
            if op != last:
                if on_status:
                    on_status(op, st.fields())
                last = op
            if op == SCAN_SUCCESS:
                break
            if any(h in op.upper() for h in SCAN_ERROR_HINTS):
                raise RuntimeError(f"measurement failed: OperationStatus = {op}")
            time.sleep(poll_interval)

        res = s.get_result()
        if "200" not in res.status_line or not res.body:
            raise RuntimeError(f"GET /Result invalid: {res.status_line!r}")
        return res.body


# ── Milestone CLI (probe) ────────────────────────────────────────────────────

def _cli() -> None:
    import argparse
    from datetime import datetime
    from pathlib import Path

    ap = argparse.ArgumentParser(description="Probe the native SOL measurement channel (Z9).")
    ap.add_argument("op", nargs="?", default="status", choices=["status", "measure"],
                    help="status = milestone 1 (harmless); measure = milestone 2 (real scan)")
    ap.add_argument("--host", default=None,
                    help="Z9 IP (default: IP configuration resolution — "
                         "Z9_HOST/.env or active printer from store.json)")
    ap.add_argument("--go", action="store_true",
                    help="measure: ACTUALLY triggers the scan (otherwise dry-run)")
    ap.add_argument("--out", default=None, help="measure: output CGATS path")
    args = ap.parse_args()

    # explicit --host (standalone override) otherwise unified resolution (single source
    # = Z9Client.from_env: Z9_HOST > store.json active). No more hardcoded 192.168.1.50.
    if not args.host:
        from .client import Z9Client
        from .exceptions import Z9Error
        try:
            args.host = Z9Client.from_env().host
        except Z9Error as e:
            ap.error(f"no IP resolved ({e}) — pass --host <ip>")

    if args.op == "status":
        print(f"[milestone 1] SOL status probe → {args.host}:{DEFAULT_PORT} (harmless)")
        r = probe_status(args.host)
        print("  status line :", r.status_line)
        print("  fields      :", r.fields())
        print("  OperationStatus =", r.fields().get("OperationStatus"))
        return

    # op == measure
    body = build_scan_body()
    if not args.go:
        print("[milestone 2 — DRY-RUN] no scan triggered. POST /Colorimetry/Scan that WOULD be sent:")
        print("-" * 60)
        print(build_scan_request(body).decode("ascii"))
        print("-" * 60)
        print(f"Current state of the Z9 ({args.host}):")
        r = probe_status(args.host)
        print("  ", r.status_line, "|", r.fields())
        print("→ re-run with --go (chart loaded!) for the real measurement.")
        return

    out = Path(args.out) if args.out else Path("out") / (
        "sol_measure_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".cgats")
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"[milestone 2 — REAL MEASUREMENT] {args.host} → {out}")
    cgats = measure(args.host, on_status=lambda op, f: print(f"   OperationStatus = {op}"))
    out.write_bytes(cgats)
    print(f"  CGATS written: {out} ({len(cgats)} B)")


if __name__ == "__main__":
    _cli()
