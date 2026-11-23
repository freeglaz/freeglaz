"""Checkpoint validation (lib.z9_client.chartread_fgk.checkpoint): a TI3 written by
chartread-fgK against the chart's .ti2 and against the previous validated state. Pure,
synthetic printtarg-shaped charts (2 000 and 5 000 patches)."""
import time

import pytest

from lib.z9_client.chartread_fgk import checkpoint as C
from webapp.backend.tests.session_harness import make_chart, ti3_bytes


@pytest.fixture(scope="module")
def chart(tmp_path_factory):
    return make_chart(tmp_path_factory.mktemp("c"), n_useful=2000, steps=26)


@pytest.fixture(scope="module")
def plan(chart):
    return C.ChartPlan.from_ti2(chart.ti2.read_bytes())


def ok(data, plan, **kw):
    return C.validate(data, plan, model="colormunki", condition="fixed", **kw)


def refused(data, plan, code, **kw):
    with pytest.raises(C.CheckpointInvalid) as e:
        ok(data, plan, **kw)
    assert e.value.code == code, e.value.message
    return e.value


def test_the_plan_follows_printtarg(plan):
    assert len(plan.passes) == 77 and plan.passes[:3] == ("A", "B", "C") and plan.passes[25:28] == ("Z", "AA", "AB")
    assert plan.n_useful == 2000 and plan.padding == 2
    assert sum(len(v) for v in plan.pass_ids.values()) == 2000
    assert sorted(plan.passes, key=C.pass_order) == list(plan.passes)


def test_partial_and_complete_states(chart, plan):
    st = ok(ti3_bytes(chart, chart.labels[:5]), plan)
    assert st.passes == tuple(chart.labels[:5]) and st.n_patches == plan.useful_in(chart.labels[:5])
    assert not st.complete and st.identity.devcalstd == "XRGA" and len(st.identity.bands) == 36
    full = ok(ti3_bytes(chart, chart.labels), plan, previous=st)
    assert full.complete and full.n_patches == 2000
    assert all(full.digests[p] == st.digests[p] for p in st.passes)
    empty = ok(ti3_bytes(chart, []), plan)
    assert empty.passes == () and empty.identity is None


def test_a_large_chart_is_validated_quickly(tmp_path):
    big = make_chart(tmp_path, n_useful=5000, steps=20, model="i1pro3plus")
    plan = C.ChartPlan.from_ti2(big.ti2.read_bytes())
    data = ti3_bytes(big, big.labels, instrument_filter="UVCUT")
    t0 = time.process_time()
    st = C.validate(data, plan, model="i1pro3plus", condition="M2")
    assert st.complete and st.n_patches == 5000 and len(st.passes) == 250
    assert time.process_time() - t0 < 2.0


def _edit(data: bytes, fn) -> bytes:
    head, rest = data.decode().split("BEGIN_DATA\n", 1)
    rows, tail = rest.split("\nEND_DATA", 1)
    rows = fn(rows.splitlines())
    head = head.replace(head[head.index("NUMBER_OF_SETS"):].split("\n")[0], f"NUMBER_OF_SETS {len(rows)}")
    return (head + "BEGIN_DATA\n" + "\n".join(rows) + "\nEND_DATA" + tail).encode()


def test_structural_refusals(chart, plan):
    good = ti3_bytes(chart, chart.labels[:4])
    refused(good[: len(good) // 2], plan, "ti3_truncated")
    refused(good.replace(b"END_DATA\n", b""), plan, "ti3_truncated")
    refused(good.replace(b"NUMBER_OF_SETS", b"NUMBER_OF_SETS 1\n#", 1), plan, "ti3_truncated")
    refused(b"\x00\xff garbage", plan, "ti3_unreadable")
    refused(good.replace(b"XYZ_Y", b"XYZ_Q"), plan, "columns_missing")
    refused(_edit(good, lambda r: r + [r[0]]), plan, "duplicate_id")
    refused(_edit(good, lambda r: r[:-1] + ["99999 " + r[-1].split(None, 1)[1]]), plan, "unknown_id")
    refused(_edit(good, lambda r: r[:-1] + ["0 " + r[-1].split(None, 1)[1]]), plan, "padding_id")

    def move(rows):
        t = rows[0].split()
        t[1] = '"ZZ99"'
        return [" ".join(t)] + rows[1:]
    refused(_edit(good, move), plan, "location_mismatch")

    def recolour(rows):
        t = rows[0].split()
        t[2] = f"{float(t[2]) + 1:.4f}"
        return [" ".join(t)] + rows[1:]
    refused(_edit(good, recolour), plan, "rgb_mismatch")
    refused(_edit(good, lambda r: r[:-1]), plan, "partial_pass")

    def nan(rows):
        t = rows[0].split()
        t[6] = "nan"
        return [" ".join(t)] + rows[1:]
    refused(_edit(good, nan), plan, "xyz_invalid")


def test_spectral_refusals_follow_the_publication_rule(chart, plan):
    assert refused(ti3_bytes(chart, chart.labels[:2], nb=0), plan, "not_spectral").info["reason"] == "no_spectra"
    assert refused(ti3_bytes(chart, chart.labels[:2], nb=1), plan, "not_spectral").info["reason"] == "single_band"
    ok(ti3_bytes(chart, chart.labels[:2], nb=31, start_nm=400, step_nm=10), plan)    # another grid: accepted


def test_instrument_and_condition(chart, plan):
    refused(ti3_bytes(chart, chart.labels[:2], target="X-Rite i1 Pro 3"), plan, "instrument_mismatch")
    refused(ti3_bytes(chart, chart.labels[:2], instrument_filter="UVCUT"), plan, "filter_mismatch")
    i1 = dict(model="i1pro3plus")
    data = ti3_bytes(chart, chart.labels[:2], target="X-Rite i1 Pro 3", instrument_filter="UVCUT")
    assert C.validate(data, plan, condition="M2", **i1).identity.instrument_filter == "UVCUT"
    for cond in ("M0", "M1"):
        with pytest.raises(C.CheckpointInvalid) as e:
            C.validate(data, plan, condition=cond, **i1)
        assert e.value.code == "filter_mismatch"
    with pytest.raises(C.CheckpointInvalid):
        C.validate(ti3_bytes(chart, chart.labels[:2], target="X-Rite i1 Pro 3"), plan, condition="M2", **i1)


def test_announced_count(chart, plan):
    data = ti3_bytes(chart, chart.labels[:3])
    st = ok(data, plan, announced=plan.useful_in(chart.labels[:3]))
    assert st.n_patches == plan.useful_in(chart.labels[:3])
    refused(data, plan, "count_mismatch", announced=st.n_patches + 26)


def test_against_the_previous_state(chart, plan):
    prev = ok(ti3_bytes(chart, chart.labels[:5]), plan)
    nxt = chart.labels[5]
    ok(ti3_bytes(chart, chart.labels[:6]), plan, previous=prev, may_add={nxt})
    refused(ti3_bytes(chart, chart.labels[:7]), plan, "pass_unexpected", previous=prev, may_add={nxt})
    refused(ti3_bytes(chart, chart.labels[:4]), plan, "pass_lost", previous=prev)
    changed = ti3_bytes(chart, chart.labels[:5], scale={"C": 1.02})
    assert refused(changed, plan, "pass_changed", previous=prev).info["passes"] == ["C"]
    ok(changed, plan, previous=prev, may_change={"C"})            # a re-read chartread announced
    refused(ti3_bytes(chart, chart.labels[:6], devcalstd="XRDI"), plan, "identity_changed", previous=prev)
    refused(ti3_bytes(chart, chart.labels[:6], nb=31, start_nm=400), plan, "identity_changed", previous=prev)


def test_removing_a_pass_for_a_correction(chart, plan):
    data = ti3_bytes(chart, chart.labels[:6])
    prev = ok(data, plan)
    new = C.without_pass(data, plan, "C")
    st = ok(new, plan, previous=prev, may_add=set(), may_remove={"C"})
    assert "C" not in st.passes and len(st.passes) == 5
    assert all(st.digests[p] == prev.digests[p] for p in st.passes)
    refused(new, plan, "pass_lost", previous=prev)                # not without saying so
    with pytest.raises(C.CheckpointInvalid):
        C.without_pass(data, plan, "QQ")
