"""Network guard for the test suite: NO test may reach a non-local address.

freeglaz drives real printers. A test that forgets a mock must never be able to
talk to a device, even one whose address happens to equal a fixture address
(e.g. 192.168.1.50). This module replaces the Python socket entry points so that
any connection, datagram or name resolution towards a non-loopback destination
raises :class:`NetworkAccessBlocked` and is RECORDED. The conftest fails the test
afterwards even when the code under test swallowed the error.

Loopback (127.0.0.0/8, ::1, "localhost") and AF_UNIX stay allowed: FastAPI's
TestClient is in-process, and a few tests run real servers on 127.0.0.1.

Coverage: everything going through ``socket`` (requests/urllib3, httpx, asyncio,
ssl, the Z9 clients). Python subprocesses inherit the guard through
``_netguard_site/sitecustomize.py`` (PYTHONPATH, set by the conftest) and report
their attempts in the file named by ``FREEGLAZ_NETGUARD_LOG``. Native tools
(Argyll, node) have no network code; for full runs, an OS-level network
namespace adds a second barrier (``scripts/run_tests_offline.sh``).
"""
from __future__ import annotations

import contextlib
import ipaddress
import os
import socket
import threading
import traceback

LOG_ENV = "FREEGLAZ_NETGUARD_LOG"

ATTEMPTS: list[dict] = []
_lock = threading.Lock()
_ORIGINALS: dict[str, object] = {}


class NetworkAccessBlocked(ConnectionRefusedError):
    """Raised instead of any network access to a non-local destination during tests.

    A ConnectionRefusedError (OSError) so that production code degrades exactly as on a
    real network failure; the attempt is recorded anyway and the test is failed."""


_LOCAL_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


def is_local_host(host) -> bool:
    """True for loopback/unspecified IPs and localhost names. Any other NAME would
    need DNS (itself a network access) → not local."""
    if host is None:
        return True
    if isinstance(host, (bytes, bytearray)):
        host = bytes(host).decode("ascii", "replace")
    h = str(host).strip().strip("[]").lower()
    if h == "" or h in _LOCAL_NAMES:
        return True
    try:
        ip = ipaddress.ip_address(h.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_unspecified


def _record_and_block(kind: str, target) -> None:
    entry = {"kind": kind, "target": repr(target), "pid": os.getpid(),
             "stack": "".join(traceback.format_stack(limit=14)[:-2])}
    with _lock:
        ATTEMPTS.append(entry)
    log = os.environ.get(LOG_ENV)
    if log:
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"{os.getpid()} {kind} {target!r}\n")
        except OSError:
            pass
    raise NetworkAccessBlocked(f"network access blocked in tests: {kind} {target!r}")


def _host_of(address):
    if isinstance(address, tuple) and address:
        return address[0]
    return address


def _is_unix(family) -> bool:
    return hasattr(socket, "AF_UNIX") and family == socket.AF_UNIX


def _guard_sock_method(name):
    orig = getattr(socket.socket, name)

    def guarded(self, address, *args, **kwargs):
        if not _is_unix(self.family) and not is_local_host(_host_of(address)):
            _record_and_block(f"socket.{name}", address)
        return orig(self, address, *args, **kwargs)

    guarded.__name__ = name
    guarded.__wrapped__ = orig
    return orig, guarded


def _guard_sendto():
    orig = socket.socket.sendto

    def sendto(self, data, *args):
        address = args[-1] if args else None
        if not _is_unix(self.family) and not is_local_host(_host_of(address)):
            _record_and_block("socket.sendto", address)
        return orig(self, data, *args)

    sendto.__wrapped__ = orig
    return orig, sendto


_FIRST_ARG_NAMES = {"create_connection": "address", "getaddrinfo": "host"}


def _guard_module_fn(name):
    orig = getattr(socket, name)
    kw = _FIRST_ARG_NAMES.get(name)

    def guarded(*args, **kwargs):
        target = args[0] if args else (kwargs.get(kw) if kw else None)
        host = _host_of(target) if name == "create_connection" else target
        if not is_local_host(host):
            _record_and_block(f"socket.{name}", target)
        return orig(*args, **kwargs)

    guarded.__name__ = name
    guarded.__wrapped__ = orig
    return orig, guarded


def install() -> None:
    """Idempotent. Replaces the socket entry points (class methods and module functions)."""
    if _ORIGINALS:
        return
    for meth in ("connect", "connect_ex"):
        orig, g = _guard_sock_method(meth)
        _ORIGINALS[f"socket.socket.{meth}"] = orig
        setattr(socket.socket, meth, g)
    orig, g = _guard_sendto()
    _ORIGINALS["socket.socket.sendto"] = orig
    socket.socket.sendto = g
    for fn in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
        orig, g = _guard_module_fn(fn)
        _ORIGINALS[f"socket.{fn}"] = orig
        setattr(socket, fn, g)


def uninstall() -> None:
    for key, orig in list(_ORIGINALS.items()):
        owner, attr = key.rsplit(".", 1)
        target = socket.socket if owner == "socket.socket" else socket
        setattr(target, attr, orig)
    _ORIGINALS.clear()


def installed() -> bool:
    return bool(_ORIGINALS)


def read_log_from(offset: int) -> tuple[list[str], int]:
    """Lines appended to the subprocess log since ``offset`` (bytes) → (lines, new offset)."""
    log = os.environ.get(LOG_ENV)
    if not log or not os.path.exists(log):
        return [], offset
    with open(log, "rb") as f:
        f.seek(offset)
        data = f.read()
    own = f"{os.getpid()} "                       # this process's attempts are already in ATTEMPTS
    lines = [l for l in data.decode("utf-8", "replace").splitlines() if l.strip() and not l.startswith(own)]
    return lines, offset + len(data)


def log_size() -> int:
    log = os.environ.get(LOG_ENV)
    try:
        return os.path.getsize(log) if log else 0
    except OSError:
        return 0


@contextlib.contextmanager
def expect_blocked():
    """For the guard's OWN tests: collect the attempts made inside the block (in-process
    and subprocesses) and remove them, so that the per-test check does not fail the test.
    Yields a dict filled on exit: {"inprocess": [...], "subprocess": [...]}."""
    got: dict[str, list] = {"inprocess": [], "subprocess": []}
    start, off = len(ATTEMPTS), log_size()
    try:
        yield got
    finally:
        with _lock:
            got["inprocess"] = ATTEMPTS[start:]
            del ATTEMPTS[start:]
        lines, _ = read_log_from(off)
        got["subprocess"] = lines
        log = os.environ.get(LOG_ENV)
        if log and os.path.exists(log):
            with open(log, "r+b") as f:          # consumed: truncate back
                f.truncate(off)
