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

"""Run the engine on a pseudo-terminal that is its CONTROLLING terminal.

Why a controlling terminal: if freeglaz dies (crash, kill, power button on the window),
the kernel closes the master side and sends SIGHUP to the session whose controlling
terminal it was, so chartread stops and releases the instrument. A PTY merely used as
stdin/stdout (``subprocess.Popen(start_new_session=True)``) leaves an orphan that keeps the
instrument open (lab X17, Linux and macOS).

How: ``os.openpty()``; the slave is set up in the parent (no echo, no output processing,
size); then ``fork`` and, in the child, nothing but ``login_tty`` (setsid + TIOCSCTTY +
dup2, in C), the reset of inherited signal state, ``chdir`` and ``execve``. Everything the
child needs is prepared before the fork. ``fork`` in a multi-threaded process (uvicorn)
is safe here because the child never runs Python code that takes a lock before ``execve``.

Terminal settings chosen for the driver:
  - ECHO off: a key we send is never echoed back, so "something was printed after the key"
    means chartread itself reacted (the key-resend rule depends on it). Argyll switches
    ICANON and ECHO off only while it reads a key, then restores what it found;
  - output processing off (no LF→CRLF): lines arrive as chartread wrote them.

Stopping, :meth:`PtyProcess.terminate`: Ctrl-C through the terminal (SIGINT to the
foreground group), then SIGTERM, then SIGKILL to the whole process group, then the
group is checked empty. The default budget (2.5 s) leaves room, within the 5 s freeglaz
waits for its backend at shutdown, for the prompt-aware pause done before it. Readings
are not at stake: with -K every accepted strip pass is already on disk.

Nothing here talks to an instrument or decides what to type: that is the session's job.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import errno
import fcntl
import os
import select
import signal
import struct
import sys
import termios
import time
import warnings
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

READ_SIZE = 65536
# (step, seconds to wait for the process after it). interrupt = Ctrl-C through the PTY.
DEFAULT_STOP_STEPS: tuple[tuple[str, float], ...] = (("interrupt", 1.0), ("terminate", 1.0),
                                                     ("kill", 0.5))
_RESET_SIGNALS = tuple(s for s in (getattr(signal, n, None) for n in
                                   ("SIGHUP", "SIGINT", "SIGQUIT", "SIGTERM", "SIGPIPE", "SIGXFSZ",
                                    "SIGTTIN", "SIGTTOU", "SIGTSTP", "SIGCHLD", "SIGALRM"))
                       if s is not None)


class ProcessGone(OSError):
    """The process exited, or its terminal is closed."""


@dataclass
class StopReport:
    outcome: str                     # already_exited | interrupt | terminate | kill | not_stopped
    returncode: Optional[int]
    elapsed: float
    group_empty: bool
    steps: list[tuple[str, float]] = field(default_factory=list)   # (step, seconds since start)


def _wait_readable(fd: int, timeout: float) -> bool:
    """select() on macOS (its poll() does not handle tty devices), poll() elsewhere (no
    FD_SETSIZE limit in a backend with many descriptors)."""
    if sys.platform == "darwin":
        r, _, _ = select.select([fd], [], [], max(0.0, timeout))
        return bool(r)
    p = select.poll()
    p.register(fd, select.POLLIN | select.POLLPRI | select.POLLHUP | select.POLLERR)
    return bool(p.poll(max(0, int(timeout * 1000))))


class PtyProcess:
    """One engine process on its own PTY and session (process group id = pid)."""

    def __init__(self, pid: int, master_fd: int, argv: Sequence[str]):
        self.pid = pid
        self.pgid = pid                  # login_tty → setsid: new session and group
        self.argv = list(argv)
        self._fd: Optional[int] = master_fd
        self._returncode: Optional[int] = None
        self.started_at = time.monotonic()

    # ─── start ──────────────────────────────────────────────────────────────
    @classmethod
    def spawn(cls, argv: Sequence[str], *, cwd: str, env: dict, size: tuple[int, int] = (24, 80)
              ) -> "PtyProcess":
        """Start ``argv`` (argv[0] an absolute path, never searched in PATH) in ``cwd``."""
        argv = list(argv)
        if not argv or not os.path.isabs(argv[0]):
            raise ValueError("argv[0] must be an absolute path")
        path = os.fsencode(argv[0])
        argv_b = [os.fsencode(a) for a in argv]
        env_b = {os.fsencode(k): os.fsencode(v) for k, v in env.items()}
        cwd_b = os.fsencode(cwd)
        if not os.path.isdir(cwd):
            raise FileNotFoundError(cwd)
        master, slave = os.openpty()
        try:
            attrs = termios.tcgetattr(slave)
            attrs[1] &= ~termios.OPOST                                  # oflag: no LF→CRLF
            attrs[3] &= ~(termios.ECHO | termios.ECHONL)                # lflag: no echo
            termios.tcsetattr(slave, termios.TCSANOW, attrs)
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", size[0], size[1], 0, 0))
            with warnings.catch_warnings():
                # CPython warns about fork() with threads; the child below only makes system
                # calls until execve (no lock, no allocation by other modules).
                warnings.filterwarnings("ignore", category=DeprecationWarning,
                                        message=r".*use of fork\(\) may lead to deadlocks.*")
                pid = os.fork()
        except BaseException:
            os.close(master)
            os.close(slave)
            raise
        if pid == 0:                                                    # child
            try:
                os.close(master)
                os.login_tty(slave)
                signal.pthread_sigmask(signal.SIG_SETMASK, ())
                for s in _RESET_SIGNALS:            # an ignored signal survives execve
                    signal.signal(s, signal.SIG_DFL)
                os.chdir(cwd_b)
                os.execve(path, argv_b, env_b)
            except BaseException:
                try:
                    os.write(2, b"freeglaz: could not start the engine\n")
                except OSError:
                    pass
            os._exit(127)
        os.close(slave)
        return cls(pid, master, argv)

    # ─── I/O ────────────────────────────────────────────────────────────────
    @property
    def fd(self) -> Optional[int]:
        return self._fd

    def read(self, timeout: float) -> Optional[bytes]:
        """Up to 64 KiB of output, ``b""`` when nothing came within ``timeout``, None once the
        terminal is closed (process gone, or :meth:`close` called)."""
        if self._fd is None:
            return None
        if not _wait_readable(self._fd, timeout):
            return b""
        try:
            data = os.read(self._fd, READ_SIZE)
        except OSError as e:
            if e.errno in (errno.EIO, errno.EBADF):      # Linux: slave side closed
                return None
            raise
        return data or None                              # macOS: b"" at end of file

    def write(self, data: bytes) -> None:
        if self._fd is None:
            raise ProcessGone(errno.EBADF, "terminal closed")
        try:
            os.write(self._fd, data)
        except OSError as e:
            if e.errno in (errno.EIO, errno.EBADF, errno.ENXIO):
                raise ProcessGone(e.errno, "terminal closed") from None
            raise

    # ─── state ──────────────────────────────────────────────────────────────
    def poll(self) -> Optional[int]:
        """Return code (negative: killed by that signal) once the process exited, else None.
        Reaps the process."""
        if self._returncode is None:
            try:
                pid, status = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                self._returncode = -255          # reaped elsewhere: gone, status unknown
                return self._returncode
            if pid == self.pid:
                self._returncode = os.waitstatus_to_exitcode(status)
        return self._returncode

    def wait(self, timeout: float) -> Optional[int]:
        """:meth:`poll` until the process is gone or ``timeout`` expires. The end of file on
        the terminal comes when the process closes it, which can be an instant before the
        kernel has finished it."""
        end = time.monotonic() + timeout
        while self.poll() is None and time.monotonic() < end:
            time.sleep(0.005)
        return self._returncode

    @property
    def alive(self) -> bool:
        return self.poll() is None

    def cpu_seconds(self) -> Optional[float]:
        """User + system CPU time used so far, or None when it cannot be read."""
        return cpu_seconds(self.pid)

    def group_empty(self) -> bool:
        """No process left in the engine's group (call after the engine is reaped)."""
        try:
            os.killpg(self.pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False

    # ─── stop ───────────────────────────────────────────────────────────────
    def terminate(self, steps: Sequence[tuple[str, float]] = DEFAULT_STOP_STEPS,
                  on_output: Optional[Callable[[bytes], None]] = None) -> StopReport:
        """Graduated stop; keeps draining the terminal so the process is never stuck writing,
        and hands what it reads to ``on_output``. Returns once the process is reaped and its
        group is empty, or the steps are exhausted."""
        t0 = time.monotonic()
        report = StopReport("already_exited", self.poll(), 0.0, False)
        if report.returncode is None:
            report.outcome = "not_stopped"
            for step, wait in steps:
                report.steps.append((step, round(time.monotonic() - t0, 3)))
                try:
                    if step == "interrupt":
                        self.write(b"\x03")
                    elif step == "terminate":
                        os.killpg(self.pgid, signal.SIGTERM)
                    elif step == "kill":
                        os.killpg(self.pgid, signal.SIGKILL)
                    else:
                        raise ValueError(f"unknown stop step {step!r}")
                except (ProcessGone, ProcessLookupError):
                    pass
                deadline = time.monotonic() + wait
                while time.monotonic() < deadline and self.poll() is None:
                    data = self.read(0.02)
                    if data and on_output:
                        on_output(data)
                    elif data is None:
                        time.sleep(0.01)
                if self.poll() is not None:
                    report.outcome = step
                    break
        report.returncode = self.poll()
        report.group_empty = report.returncode is not None and self.group_empty()
        if report.returncode is not None and not report.group_empty:
            try:                                    # something else kept the group: kill it too
                os.killpg(self.pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            end = time.monotonic() + 0.5
            while time.monotonic() < end and not self.group_empty():
                time.sleep(0.02)
            report.group_empty = self.group_empty()
        report.elapsed = round(time.monotonic() - t0, 3)
        return report

    def close(self) -> Optional[StopReport]:
        """Stop the process if needed, then close the terminal."""
        report = self.terminate() if self.alive else None
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        return report

    def __enter__(self) -> "PtyProcess":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ─── CPU time of a process (activity monitoring) ────────────────────────────
def cpu_seconds(pid: int) -> Optional[float]:
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/stat", "rb") as f:
                stat = f.read()
            fields = stat[stat.rindex(b")") + 2:].split()    # after "(comm)", field 3 onward
            return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
        except (OSError, ValueError, IndexError):
            return None
    if sys.platform == "darwin":
        return _darwin_cpu_seconds(pid)
    return None


class _RusageInfoV0(ctypes.Structure):
    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16), ("ri_user_time", ctypes.c_uint64),
                ("ri_system_time", ctypes.c_uint64), ("ri_pkg_idle_wkups", ctypes.c_uint64),
                ("ri_interrupt_wkups", ctypes.c_uint64), ("ri_pageins", ctypes.c_uint64),
                ("ri_wired_size", ctypes.c_uint64), ("ri_resident_size", ctypes.c_uint64),
                ("ri_phys_footprint", ctypes.c_uint64), ("ri_proc_start_abstime", ctypes.c_uint64),
                ("ri_proc_exit_abstime", ctypes.c_uint64)]


class _TimebaseInfo(ctypes.Structure):
    _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]


def _darwin_cpu_seconds(pid: int) -> Optional[float]:
    """proc_pid_rusage (RUSAGE_INFO_V0). Times are in Mach absolute units (nanoseconds on
    Intel, 125/3 ns on Apple Silicon), converted with mach_timebase_info."""
    try:
        lib = ctypes.CDLL(ctypes.util.find_library("System") or "/usr/lib/libSystem.B.dylib")
        info = _RusageInfoV0()
        if lib.proc_pid_rusage(ctypes.c_int(pid), ctypes.c_int(0), ctypes.byref(info)) != 0:
            return None
        tb = _TimebaseInfo()
        lib.mach_timebase_info(ctypes.byref(tb))
        ns = (info.ri_user_time + info.ri_system_time) * tb.numer / max(1, tb.denom)
        return ns / 1e9
    except (OSError, AttributeError):
        return None
