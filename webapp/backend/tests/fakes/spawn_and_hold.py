"""TEST ONLY: a throw-away "driver" that starts the fake chartread and then just holds it,
so that a test can kill this driver and watch what happens to the fake.

    spawn_and_hold.py ctty|no-ctty <repo> <scenario.json>

ctty     PtyProcess.spawn (the PTY is the fake's controlling terminal)
no-ctty  subprocess.Popen(start_new_session=True) on a PTY that is NOT its controlling
         terminal (the orphan case of the lab, X17)

Prints the fake's pid, then drains its output until killed (at most 30 s)."""
import os
import subprocess
import sys
import time

mode, repo, scen = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, repo)
fake = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_chartread.py")
argv = [sys.executable, "-I", fake, scen]
env = {"PATH": "/usr/bin:/bin", "LANG": "C"}
if mode == "ctty":
    from lib.z9_client.chartread_fgk.pty_process import PtyProcess
    proc = PtyProcess.spawn(argv, cwd=os.path.dirname(scen), env=env)
    pid, fd = proc.pid, proc.fd
else:
    master, slave = os.openpty()
    p = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                         cwd=os.path.dirname(scen), env=env)
    os.close(slave)
    pid, fd = p.pid, master
print(pid, flush=True)
end = time.monotonic() + 30
while time.monotonic() < end:
    try:
        if not os.read(fd, 65536):
            time.sleep(0.05)
    except OSError:
        time.sleep(0.05)
