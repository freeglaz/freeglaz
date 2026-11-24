"""The measurement conditions a merged ti3 keeps (lib/z9_client/multipass.py): DEVCALSTD and
INSTRUMENT_FILTER.

concat_ti3 builds the working ti3 of every profile made of several measurements: the average
of a chart's scans and the multi-source build (other charts, source profiles). colprof embeds
that ti3 in the ICC (targ), and freeglaz reads it back without a record when the profile is
used as a source. Each keyword is kept when every source states the same value, stays absent
when none states it (the historical SOL scans, a ColorMunki's filter), and the combination is
refused otherwise: different values, or a value stated by some sources only.

Synthetic ti3 only (test_build_inputs helpers): no Argyll, no instrument."""
import pytest

from lib.z9_client import build_inputs as bi
from lib.z9_client import measurement_identity as mi
from lib.z9_client.multipass import CONDITION_KEYWORDS, concat_ti3
from webapp.backend.tests.test_build_inputs import grid_rows, make_chart, ti3_text

SOL = "HP DesignJet Z9 embedded spectro"
MUNKI = "X-Rite ColorMunki"
I1PRO3 = "X-Rite i1 Pro 3"


def ti3(instrument=SOL, rows=None, **kw):
    """A synthetic ti3 read by ``instrument``; ``kw`` = DEVCALSTD / INSTRUMENT_FILTER, written
    as chartread writes them (no KEYWORD declaration)."""
    text = ti3_text(rows or grid_rows()).replace(f'TARGET_INSTRUMENT "{SOL}"', f'TARGET_INSTRUMENT "{instrument}"')
    extra = "".join(f'{k} "{v}"\n' for k, v in kw.items())
    return text.replace('KEYWORD "SPECTRAL_BANDS"\n', extra + 'KEYWORD "SPECTRAL_BANDS"\n')


def keywords(path):
    return mi.read_ti3_header(path.read_text(encoding="ascii", errors="replace")).keywords


def identity(path):
    return mi.identity_from_header(mi.read_ti3_header(path.read_text(encoding="ascii", errors="replace")))


def concat(tmp_path, *texts):
    paths = []
    for i, t in enumerate(texts):
        p = tmp_path / f"s{i + 1}.ti3"
        p.write_text(t, encoding="ascii")
        paths.append(p)
    out = tmp_path / "out.ti3"
    concat_ti3(paths, out, labels=[f"scan{i + 1}" for i in range(len(paths))], dedup_strategy="keep")
    return out


# ─── concat_ti3 ──────────────────────────────────────────────────────────────
def test_the_two_condition_keywords():
    assert CONDITION_KEYWORDS == ("DEVCALSTD", "INSTRUMENT_FILTER")


@pytest.mark.parametrize("instrument, kw, condition", [
    (MUNKI, {"DEVCALSTD": "XRGA"}, mi.CONDITION_FIXED),                          # ColorMunki: no filter
    (I1PRO3, {"DEVCALSTD": "XRGA", "INSTRUMENT_FILTER": "UVCUT"}, "M2"),
    (I1PRO3, {"DEVCALSTD": "XRGA", "INSTRUMENT_FILTER": "POLARIZED"}, "M3"),
])
def test_identical_conditions_are_kept(tmp_path, instrument, kw, condition):
    out = concat(tmp_path, ti3(instrument, **kw), ti3(instrument, **kw), ti3(instrument, **kw))
    k = keywords(out)
    assert {key: k.get(key) for key in CONDITION_KEYWORDS} == {key: kw.get(key) for key in CONDITION_KEYWORDS}
    ident = identity(out)
    assert (ident.calibration_standard, ident.condition) == ("XRGA", condition)


def test_a_single_source_keeps_its_conditions(tmp_path):
    out = concat(tmp_path, ti3(I1PRO3, DEVCALSTD="XRGA", INSTRUMENT_FILTER="UVCUT"))
    assert (identity(out).calibration_standard, identity(out).condition) == ("XRGA", "M2")


def test_historical_sources_without_conditions_stay_without(tmp_path):
    """SOL scans (and HP firmware profiles once extracted) state neither keyword: nothing is
    added, the merge is accepted as before."""
    out = concat(tmp_path, ti3(), ti3())
    assert not set(CONDITION_KEYWORDS) & set(keywords(out))
    ident = identity(out)
    assert (ident.method, ident.calibration_standard, ident.condition) == (mi.METHOD_SOL, None, mi.CONDITION_FIXED)


@pytest.mark.parametrize("a, b, match", [
    ({"DEVCALSTD": "XRGA"}, {"DEVCALSTD": "XRDI"}, "Different DEVCALSTD: scan1=XRGA, scan2=XRDI"),
    ({"DEVCALSTD": "XRGA"}, {}, "DEVCALSTD stated by scan1 but not by scan2: ambiguous"),
    ({}, {"DEVCALSTD": "XRGA"}, "DEVCALSTD stated by scan2 but not by scan1: ambiguous"),
    ({"DEVCALSTD": "XRGA", "INSTRUMENT_FILTER": "UVCUT"}, {"DEVCALSTD": "XRGA", "INSTRUMENT_FILTER": "POLARIZED"},
     "Different INSTRUMENT_FILTER: scan1=UVCUT, scan2=POLARIZED"),
    ({"DEVCALSTD": "XRGA", "INSTRUMENT_FILTER": "UVCUT"}, {"DEVCALSTD": "XRGA"},       # M2 with M0/M1
     "INSTRUMENT_FILTER stated by scan1 but not by scan2: ambiguous"),
])
def test_different_or_partly_stated_conditions_are_refused(tmp_path, a, b, match):
    with pytest.raises(ValueError, match=match):
        concat(tmp_path, ti3(I1PRO3, **a), ti3(I1PRO3, **b))
    assert not (tmp_path / "out.ti3").exists()


def test_a_third_source_that_differs_is_refused(tmp_path):
    with pytest.raises(ValueError, match="Different DEVCALSTD: scan1=XRGA, scan2=XRGA, scan3=XRDI"):
        concat(tmp_path, ti3(MUNKI, DEVCALSTD="XRGA"), ti3(MUNKI, DEVCALSTD="XRGA"), ti3(MUNKI, DEVCALSTD="XRDI"))


# ─── the two build paths that use it ────────────────────────────────────────
def test_average_keeps_xrga(tmp_path):
    snap = make_chart(tmp_path, "CHT-A", {"a.ti3": ti3(MUNKI, DEVCALSTD="XRGA"),
                                         "b.ti3": ti3(MUNKI, DEVCALSTD="XRGA")})
    eff = bi.materialize(bi.plan_inputs(snap, [], bi.BuildRequest(base="average")), tmp_path / "w")
    assert eff.profile_ti3.name == "CHT-A_avg.ti3"
    ident = identity(eff.profile_ti3)
    assert (ident.instrument, ident.condition, ident.calibration_standard) == ("colormunki", mi.CONDITION_FIXED, "XRGA")


@pytest.mark.parametrize("b_kw, code", [({"DEVCALSTD": "XRDI"}, "measurement_identity_incompatible"),
                                        ({}, "measurement_identity_insufficient")])
def test_average_of_different_conditions_is_still_refused_by_the_identity_check(tmp_path, b_kw, code):
    """The existing identity check refuses these scans first; concat_ti3 is never reached."""
    snap = make_chart(tmp_path, "CHT-A", {"a.ti3": ti3(MUNKI, DEVCALSTD="XRGA"), "b.ti3": ti3(MUNKI, **b_kw)})
    with pytest.raises(bi.MeasurementIdentityError) as e:
        bi.materialize(bi.plan_inputs(snap, [], bi.BuildRequest(base="average")), tmp_path / "w")
    assert e.value.code == code


def _with_source_profile(tmp_path, scan_text, profile_text):
    cur = make_chart(tmp_path, "CHT-A", {"a.ti3": scan_text})
    icc = tmp_path / "repo" / "p.icc"
    icc.parent.mkdir()
    icc.write_bytes(b"ICC")
    plan = bi.plan_inputs(cur, [], bi.BuildRequest(source_profiles=(str(icc),)))
    return bi.materialize(plan, tmp_path / "w", extract_profile=lambda _i, out, _d: out.write_text(profile_text))


def test_multisource_keeps_m2_and_xrga(tmp_path):
    eff = _with_source_profile(tmp_path, ti3(I1PRO3, DEVCALSTD="XRGA", INSTRUMENT_FILTER="UVCUT"),
                               ti3(I1PRO3, rows=grid_rows(2), DEVCALSTD="XRGA", INSTRUMENT_FILTER="UVCUT"))
    assert eff.profile_ti3.name == "CHT-A_multisource.ti3"
    ident = identity(eff.profile_ti3)
    assert (ident.condition, ident.calibration_standard) == ("M2", "XRGA")


@pytest.mark.parametrize("profile_kw, match", [
    ({}, "DEVCALSTD stated by .* but not by"),                                   # e.g. an average made before the fix
    ({"DEVCALSTD": "XRGA"}, "INSTRUMENT_FILTER stated by .* but not by"),        # M2 with an unfiltered profile
    ({"DEVCALSTD": "XRGA", "INSTRUMENT_FILTER": "POLARIZED"}, "Different INSTRUMENT_FILTER"),
])
def test_multisource_with_other_conditions_is_refused(tmp_path, profile_kw, match):
    """A source profile is not identity-checked (paper guard only): concat_ti3 is the guard."""
    with pytest.raises(bi.BuildInputsError, match="multi-source concat refused.*" + match):
        _with_source_profile(tmp_path, ti3(I1PRO3, DEVCALSTD="XRGA", INSTRUMENT_FILTER="UVCUT"),
                             ti3(I1PRO3, rows=grid_rows(2), **profile_kw))


def test_historical_multisource_without_conditions_is_unchanged(tmp_path):
    eff = _with_source_profile(tmp_path, ti3(), ti3(rows=grid_rows(2)))
    assert not set(CONDITION_KEYWORDS) & set(keywords(eff.profile_ti3))
