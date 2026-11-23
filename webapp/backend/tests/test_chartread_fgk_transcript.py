"""Transcript and ActivityMonitor (lib.z9_client.chartread_fgk.transcript): bounded log,
repeated lines collapsed, flood measured, serial numbers masked, prompts still seen in a
flood; engine flooding, spinning or silent. Pure units, plus floods through the real PTY
with the fake chartread."""
import os
import random
import sys
import time
import tracemalloc

import pytest

from lib.z9_client.chartread_fgk.prompts import PromptParser
from lib.z9_client.chartread_fgk.transcript import ActivityMonitor, FloodState, Transcript
from webapp.backend.tests.chartread_harness import Pump, spawn_fake

USB = "icoms_usb_transaction: ReadPipeAsync failed with 0xe00002c0"


def feed_all(tr, data: bytes, chunks, t=0.0):
    out, pos = [], 0
    for n in chunks:
        out.append(tr.feed(data[pos:pos + n], t))
        pos += n
    out.append(tr.feed(data[pos:], t))
    return "".join(out)


# ─── forwarding and collapsing ──────────────────────────────────────────────
def test_repeats_beyond_the_threshold_are_counted_not_forwarded(tmp_path):
    tr = Transcript(tmp_path / "t.log", repeat_threshold=5)
    data = (b"A\n" * 50) + b"B\n" + (b"A\n" * 3)
    out = tr.feed(data, 0.0)
    assert out == "A\n" * 5 + "B\n" + "A\n" * 3
    st = tr.close(1.0)
    assert (st.lines_in, st.lines_suppressed, st.longest_run) == (54, 45, 50)
    log = (tmp_path / "t.log").read_text().splitlines()
    assert [l.split(None, 1)[1] for l in log[:4]] == ["A", "[x49 more, 0.0 s] A", "B", "A"]


def test_every_byte_is_forwarded_at_most_once_whatever_the_chunks():
    rnd = random.Random(7)
    lines = []
    for _ in range(400):
        lines += [rnd.choice(["x", "y", USB, "Ready to read strip pass C"])] * rnd.choice([1, 1, 2, 30])
    data = "".join(l + "\n" for l in lines).encode() + b"Trigger instrument switch or any other key to start:"
    for _ in range(20):
        tr = Transcript(None, repeat_threshold=3)
        cuts = sorted(rnd.sample(range(1, len(data)), 60))
        out = feed_all(tr, data, [b - a for a, b in zip([0] + cuts, cuts)])
        assert out.endswith("Trigger instrument switch or any other key to start:")
        fwd = out.split("\n")[:-1]
        # in order, never more often than written, every change of line kept
        changes = [l for i, l in enumerate(lines) if i == 0 or l != lines[i - 1]]
        it = iter(fwd)
        assert all(any(c == f for f in it) for c in changes)
        assert all(fwd.count(v) <= lines.count(v) for v in set(fwd))


def test_an_unfinished_line_is_forwarded_at_once_and_only_once():
    tr = Transcript(None)
    assert tr.feed(b"Trigger instrument switch ", 0) == "Trigger instrument switch "
    assert tr.feed(b"or any other key to start:", 0) == "or any other key to start:"
    assert tr.feed(b" Strip read OK\nnext", 0) == " Strip read OK\nnext"


def test_serial_numbers_are_masked_in_the_log_only(tmp_path):
    tr = Transcript(tmp_path / "t.log")
    out = tr.feed(b"Serial Number: 1234567\nChip ID: 01-02\nProduction No.: 9876\n"
                  b"Place the instrument on its reflective white reference S/N 4242,\n", 0)
    assert "1234567" in out                                  # the session still gets it
    tr.close(0)
    log = (tmp_path / "t.log").read_text()
    assert not any(s in log for s in ("1234567", "01-02", "9876", "4242"))
    assert log.count("<masked>") == 4 and not any("1234567" in r for r in tr.recent())


def test_the_log_has_a_hard_budget_and_a_reserve_for_notes(tmp_path):
    tr = Transcript(tmp_path / "t.log", max_log_bytes=4000, reserve_bytes=1000)
    tr.feed(b"".join(f"distinct line {i}\n".encode() for i in range(5000)), 0)
    tr.note("checkpoint 26 patches, full", 1)
    for i in range(200):
        tr.note(f"note {i}", 2)
    st = tr.close(3)
    assert st.log_capped and st.notes_dropped > 0
    size = os.path.getsize(tmp_path / "t.log")
    assert size <= 4000 + 1000 + 200
    assert "## checkpoint 26 patches, full" in (tmp_path / "t.log").read_text()


def test_an_endless_line_is_bounded():
    tr = Transcript(None, max_line=1000)
    out = tr.feed(b"." * 100_000, 0)
    assert len(out) == 100_000 and len(tr._partial) == 0      # forwarded once, not kept
    assert tr.feed(b"..\nok\n", 0) == "..\nok\n"


def test_flood_is_measured_per_second():
    tr = Transcript(None, flood_lines_per_s=1000)
    tr.feed((USB + "\n").encode() * 3000, 10.2)
    assert tr.flood(10.9) == FloodState(False, 0, 0, 0)      # the second is not over yet
    f = tr.flood(11.1)
    assert f.flooding and f.seconds == 1 and f.lines_per_s == 3000
    tr.feed(b"x\n", 12.5)
    assert not tr.flood(13.0).flooding                        # a quiet second ends it


# ─── floods: bounded, prompts still seen ────────────────────────────────────
def test_millions_of_lines_keep_memory_and_cpu_bounded():
    chunk = ((USB + "\n").encode() * 1100)
    peaks = []
    for total in (300_000, 3_000_000):
        tr, pp = Transcript(None), PromptParser()
        tracemalloc.start()
        n, c0 = 0, time.process_time()
        while n < total:
            pp.feed(tr.feed(chunk, n / 3e6), n / 3e6)
            n += 1100
        peaks.append(tracemalloc.get_traced_memory()[1])
        tracemalloc.stop()
        cpu = time.process_time() - c0
    assert peaks[1] < 1.5 * peaks[0] + 64 * 1024 and peaks[1] < 2 * 1024 * 1024
    assert tr.stats().lines_suppressed >= 3_000_000 - 20
    assert cpu < 15                                            # ~1 s measured, traced


def test_a_prompt_split_and_interleaved_in_a_flood_is_recognised():
    tr, pp = Transcript(None), PromptParser()
    noise = (USB + "\n").encode() * 5000
    text = (noise + b"\nReady to read strip pass C\n" + (b"icoms_usb_transaction: ReadPipeAsync "
            b"failed with 0xe00002d9\n") * 40 + b"Press 'f' to move forward, 'b' to move back, "
            b"'n' for next unread,\n 'd' when done, Esc or 'q' to quit (passes read so far are saved).\n"
            + noise + b"Trigger instrument switch or any other key to start:" + USB.encode() + b"\n" + noise)
    events, rnd = [], random.Random(3)
    pos = 0
    while pos < len(text):
        n = rnd.randint(1, 70000)
        events += pp.feed(tr.feed(text[pos:pos + n], 0), 0)
        pos += n
    ready = [e for e in events if e.kind == "ready"]
    assert len(ready) == 1 and ready[0].data["pass"] == "C" and ready[0].data["checkpointing"]


@pytest.mark.skipif(not sys.platform.startswith(("linux", "darwin")), reason="POSIX PTY")
def test_three_million_lines_through_the_pty(tmp_path):
    """B1's flood, through the real terminal: the prompt in the middle is seen, the flood
    verdict comes after about a second, the log stays tiny, the stop is immediate."""
    other = "icoms_usb_transaction: ReadPipeAsync failed with 0xe00002d9"
    proc, _ = spawn_fake(tmp_path, [
        {"say": "Instrument Type:   ColorMunki\n"},
        {"flood": {"line": USB, "count": 1_500_000, "stderr": True}},
        {"say": "\nReady to read strip pass C\n"}, {"flood": {"line": other, "count": 30}},
        {"say": "Press 'f' to move forward, 'b' to move back, 'n' for next unread,\n"
                " 'd' when done, Esc or 'q' to quit (passes read so far are saved).\n"},
        {"say": "Trigger instrument switch or any other key to start:"},
        {"flood": {"line": USB, "count": 1_500_000, "stderr": True}},
        {"sleep": 30}], max_seconds=60)
    pump = Pump(proc, log_path=tmp_path / "flood.log")
    c0, t0 = time.process_time(), time.monotonic()
    ready = pump.run(timeout=60, until=lambda e: e.kind == "ready")
    assert ready.data["pass"] == "C"
    pump.run(timeout=60, stop_on=("flooding",))
    assert pump.verdicts[-1][1].status == "flooding" and time.monotonic() - t0 < 30
    rep = proc.terminate(on_output=lambda b: pump.transcript.feed(b, time.monotonic()))
    st = pump.transcript.close(time.monotonic())
    assert rep.group_empty and rep.elapsed < 2.8
    assert st.lines_in > 1_500_000 and st.lines_suppressed > 0.98 * st.lines_in
    assert os.path.getsize(tmp_path / "flood.log") < 8192
    assert time.process_time() - c0 < 20
    proc.close()


# ─── activity verdicts ──────────────────────────────────────────────────────
def test_monitor_flooding_needs_duration_without_progress():
    m = ActivityMonitor(flood_seconds=2)
    flood1, flood2 = FloodState(True, 1, 5000, 0), FloodState(True, 2, 5000, 0)
    assert m.verdict(10, flood1).status == "ok"
    assert m.verdict(11, flood2).status == "flooding"
    m.on_progress(10.5)
    assert m.verdict(11, flood2).status == "ok"              # something useful happened meanwhile


def test_monitor_spinning_and_silence():
    m = ActivityMonitor(spin_cpu_ratio=0.8, spin_seconds=4)
    for t in range(6):
        m.sample_cpu(0.95 * t, float(t))
    v = m.verdict(5.0)
    assert v.status == "spinning"
    m.on_progress(5.0)
    assert m.verdict(5.0).status == "ok"
    idle = ActivityMonitor(spin_seconds=4)
    for t in range(6):
        idle.sample_cpu(0.01 * t, float(t))
    assert idle.verdict(5.0).status == "ok"                   # waiting for the user is not spinning
    assert idle.verdict(5.0, expect_output_within=1.0, since=3.0).status == "silent"
    idle.on_output(3.5)
    assert idle.verdict(5.0, expect_output_within=1.0, since=3.0).status == "ok"
    assert idle.verdict(5.0).status == "ok"                   # no expectation: never silent


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="CPU time from /proc")
def test_a_spinning_engine_is_detected(tmp_path):
    proc, _ = spawn_fake(tmp_path, [{"say": "Instrument Type:   ColorMunki\n"}, {"spin": 10}])
    pump = Pump(proc, monitor=ActivityMonitor(spin_cpu_ratio=0.5, spin_seconds=1.5))
    t0 = time.monotonic()
    pump.run(timeout=8, stop_on=("spinning",))
    assert pump.verdicts[-1][1].status == "spinning" and time.monotonic() - t0 < 6
    assert proc.terminate().group_empty
    proc.close()


def test_a_silent_engine_is_detected(tmp_path):
    proc, _ = spawn_fake(tmp_path, [{"sleep": 10}])
    pump = Pump(proc)
    pump.run(timeout=5, stop_on=("silent",), expect_output_within=1.0)
    assert pump.verdicts[-1][1].status == "silent"
    assert proc.terminate().group_empty
    proc.close()
