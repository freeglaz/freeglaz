"""Copyright of ICC profiles: cprt = "No copyright, use freely" (honesty).

The computation comes from Argyll colprof → we do not appropriate the output.
manufacturer=HP (real printer), model=DesignJet Z9, desc=freeglaz stay unchanged.
"""
from lib.z9_client import config


def test_default_copyright_is_use_freely():
    assert config._DEFAULTS["colprof"]["copyright"] == "No copyright, use freely"
    # above all NOT the old one (appropriation) nor an Argyll credit (nonsense)
    assert config._DEFAULTS["colprof"]["copyright"] != "freeglaz Project"


def test_build_profile_defaults_honest():
    # Refine / direct profiling path: ProfilingOps.build_profile + run_pipeline
    # (NOT build_colprof_command). Their DEFAULTS must be HP + use-freely (the
    # A finding had missed this path → refine profile came out as "freeglaz Project").
    import inspect
    from lib.z9_client.profiling import ProfilingOps
    for fn in (ProfilingOps.build_profile, ProfilingOps.run_pipeline):
        p = inspect.signature(fn).parameters
        assert p["manufacturer"].default == "HP"
        assert p["copyright_str"].default == "No copyright, use freely"
        assert p["copyright_str"].default != "freeglaz Project"


def test_colprof_command_emits_honest_metadata():
    cmd = config.build_colprof_command(
        "chart", "freeglaz measures from chart", config._DEFAULTS)
    # -C = free copyright
    ci = cmd.index("-C")
    assert cmd[ci + 1] == "No copyright, use freely"
    # -A manufacturer = HP (correct, real HP printer) — unchanged
    assert cmd[cmd.index("-A") + 1] == "HP"
    # -M model = DesignJet Z9 — unchanged
    assert cmd[cmd.index("-M") + 1] == "DesignJet Z9"
    # -D desc = freeglaz (legitimate identification) — unchanged
    assert cmd[cmd.index("-D") + 1] == "freeglaz measures from chart"


def test_no_imposed_source_gamut_default():
    # Portability safeguard: NO -S imposed by default (source gamut = user choice),
    # and no macOS ColorSync / AdobeRGB1998 path in a default. Prevents reintroduction
    # of a non-portable system path (porting blocker #1).
    assert config._DEFAULTS["colprof"]["source_gamut"] == ""
    cmd = config.build_colprof_command("chart", "desc", config._DEFAULTS)
    assert "-S" not in cmd, "le défaut ne doit PAS imposer un gamut source -S"
    assert not any("/System/Library" in str(c) or "AdobeRGB1998" in str(c) for c in cmd)
    # Measurements UI default = -v -qh (without -S)
    from webapp.backend.routes.charts import _chart_default_colprof_flags
    assert _chart_default_colprof_flags() == ["-v", "-qh"]
    # no builtin colprof preset carries a -S / system path
    from lib.z9_client.strategies import BUILTINS
    for s in BUILTINS.values():
        assert "-S" not in s.flags and "/System" not in s.flags and "AdobeRGB" not in s.flags
