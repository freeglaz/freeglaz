"""Smoke tests CLI free chart: `freeglaz chart create/list/formats/profile`.

Loads the freeglaz script as a module and invokes the handlers with a Namespace.
`chart create` now requires the Z9 (tag = RESIDENT profile of the slot read
live) -> we inject a FAKE client (export_icc writes an ICC from the assets) and
monkeypatch resolve_paper_interactive. Store redirected to tmp via FREEGLAZ_STORE_ROOT.
"""
import importlib.util
import json
import sys
from argparse import Namespace
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from lib.z9_client.exceptions import Z9Error

_SCRIPT = Path(__file__).resolve().parents[3] / "freeglaz"
_RESIDENT = (Path(__file__).resolve().parent / "fixtures"
             / "synthetic_test_resident_A.icc")   # synthetic stand-in for the resident profile


@pytest.fixture(scope="module")
def cli():
    loader = SourceFileLoader("freeglaz_cli_chart_test", str(_SCRIPT))
    spec = importlib.util.spec_from_loader("freeglaz_cli_chart_test", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["freeglaz_cli_chart_test"] = mod
    loader.exec_module(mod)
    return mod


class _FakePaperOps:
    def __init__(self, icc_src, fail=False):
        self._icc = icc_src
        self._fail = fail

    def export_icc(self, *, ref, output_path, gloss_enhancer, quality,
                   color_space, _pre_resolved=None):
        if self._fail:
            raise Z9Error("slot illisible (simulé)")
        Path(output_path).write_bytes(Path(self._icc).read_bytes())


_SERIAL = "CLITESTSN"


class _FakeClient:
    def __init__(self, icc_src=_RESIDENT, fail=False):
        self.host = "127.0.0.1"
        self.paper = _FakePaperOps(icc_src, fail=fail)

    def identification(self):
        # store.get_serial(client) (per-serial bridge) reads identification()
        return {"SerialNumber": _SERIAL}


def _patch_resolve(cli, monkeypatch):
    monkeypatch.setattr(cli, "resolve_paper_interactive",
                        lambda client, ref: {"id": "MEDIAID0001", "name": ref})


def _write_ti1(path: Path, n: int):
    lines = ['CGATS.17', 'KEYWORD "SAMPLE_ID"', 'COLOR_REP "RGB"',
             'BEGIN_DATA_FORMAT', 'SAMPLE_ID RGB_R RGB_G RGB_B', 'END_DATA_FORMAT',
             f'NUMBER_OF_SETS {n}', 'BEGIN_DATA']
    for i in range(n):
        v = round(100.0 * i / max(1, n - 1), 4)
        lines.append(f'{i+1} {v} {v} {v}')
    lines += ['END_DATA', '']
    path.write_text('\n'.join(lines), encoding='utf-8')


def _create_args(ti1, **ov):
    base = dict(ti1=str(ti1), format="a3", columns=12, paper="Papier Test",
                gloss_enhancer="OFF", source="test", dpi=300.0)
    base.update(ov)
    return Namespace(**base)


def test_cli_chart_create_then_list(cli, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    _patch_resolve(cli, monkeypatch)
    ti1 = tmp_path / "c.ti1"
    _write_ti1(ti1, 60)

    rc = cli.cmd_chart_create(_create_args(ti1), client=_FakeClient())
    assert rc == 0
    out = capsys.readouterr().out
    assert "chart_id" in out and "CHT-" in out and "resident" in out

    charts = list((tmp_path / "store" / "charts" / _SERIAL).iterdir())  # per-serial
    assert len(charts) == 1
    desc = json.loads((charts[0] / "chart.json").read_text())
    assert (charts[0] / "chart.tif").is_file()
    # resident tag recorded (not sRGB)
    assert desc["color_management"]["tag_source"] == "resident-live"
    assert desc["color_management"]["gloss_enhancer"] == "OFF"

    rc = cli.cmd_chart_list(Namespace(json=True), client=None)
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 1 and data[0]["patch_count"] == 60


def test_cli_chart_create_requires_gloss(cli, tmp_path, monkeypatch, capsys):
    """--gloss-enhancer required (GE slot determines the resident)."""
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    _patch_resolve(cli, monkeypatch)
    ti1 = tmp_path / "c.ti1"
    _write_ti1(ti1, 60)
    rc = cli.cmd_chart_create(_create_args(ti1, gloss_enhancer=None),
                              client=_FakeClient())
    assert rc == 1
    cap = capsys.readouterr()
    assert "gloss-enhancer" in (cap.out + cap.err)


def test_cli_chart_create_blocks_if_resident_unreadable(cli, tmp_path, monkeypatch,
                                                        capsys):
    """Slot unreadable live -> BLOCKS (no cache/sRGB fallback)."""
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    _patch_resolve(cli, monkeypatch)
    ti1 = tmp_path / "c.ti1"
    _write_ti1(ti1, 60)
    rc = cli.cmd_chart_create(_create_args(ti1), client=_FakeClient(fail=True))
    assert rc == 1
    cap = capsys.readouterr()
    assert "resident" in (cap.out + cap.err).lower()
    assert not list((tmp_path / "store" / "charts").iterdir()) \
        if (tmp_path / "store" / "charts").exists() else True


def test_cli_chart_create_columns_ignored_no_overlap(cli, tmp_path, monkeypatch, capsys):
    """NON-FIXED-STEP model: `--columns` is IGNORED (native density imposed -> cols derived).
    A large `--columns` no longer creates overlap (risk eliminated by construction):
    a2/35 requested -> cols derived 26 -> gap ~ 7.9 mm -> generation OK, no refusal."""
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    _patch_resolve(cli, monkeypatch)
    ti1 = tmp_path / "c.ti1"
    _write_ti1(ti1, 200)
    rc = cli.cmd_chart_create(_create_args(ti1, format="a2", columns=35),
                              client=_FakeClient())
    assert rc == 0   # columns=35 ignored -> cols derived 26 -> no overlap -> success
    cap = capsys.readouterr()
    assert "overlap" not in (cap.out + cap.err).lower()


def test_cli_chart_create_rejects_height(cli, tmp_path, monkeypatch, capsys):
    """A4 + 220 patches -> HEIGHT refusal (a4 cols=12, max 17 rows = 204 patches)."""
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    _patch_resolve(cli, monkeypatch)
    ti1 = tmp_path / "c.ti1"
    _write_ti1(ti1, 220)
    rc = cli.cmd_chart_create(_create_args(ti1, format="a4", columns=11),
                              client=_FakeClient())
    assert rc == 1
    cap = capsys.readouterr()
    assert "height" in (cap.out + cap.err)


def test_cli_chart_list_empty(cli, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    rc = cli.cmd_chart_list(Namespace(json=False), client=None)
    assert rc == 0
    assert "No chart" in capsys.readouterr().out


def test_cli_chart_formats(cli, capsys):
    rc = cli.cmd_chart_formats(Namespace(patches=None), client=None)
    assert rc == 0
    out = capsys.readouterr().out
    assert "204" in out and "468" in out and "1080" in out  # A4/A3/A2 (cols packed to max)
    assert "rouleau" in out.lower() or "roll" in out.lower()
    rc = cli.cmd_chart_formats(Namespace(patches=300), client=None)
    assert rc == 0
    assert "a3" in capsys.readouterr().out


def test_cli_chart_print_refuses_non_resident(cli, tmp_path, monkeypatch, capsys):
    """chart print refuses a chart without resident tag (e.g. old sRGB)."""
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    # build a 'legacy' descriptor without resident color_management
    cdir = tmp_path / "store" / "charts" / _SERIAL / "CHT-legacy"   # per-serial (enumeration)
    cdir.mkdir(parents=True)
    (cdir / "chart.tif").write_bytes(b"II*\x00")
    (cdir / "chart.json").write_text(json.dumps({
        "files": {"tiff": "chart.tif"}, "geometry": {"cols": 11, "patch_count": 100},
        "media": {"format_key": "a4"},
        "scanLayout": {"placement": {"offset_x_mm": 10, "offset_y_mm": 20}},
        "color_management": {"tag_source": None},
    }))
    rc = cli.cmd_chart_print(
        Namespace(chart_id="CHT-legacy", paper=None, gloss=None, quality="HIGH",
                  mediasource="AUTO", yes=True), client=_FakeClient())
    assert rc == 1
    cap = capsys.readouterr()
    assert "resident" in (cap.out + cap.err)


def test_cli_chart_profile_without_scan(cli, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    _patch_resolve(cli, monkeypatch)
    ti1 = tmp_path / "c.ti1"
    _write_ti1(ti1, 60)
    assert cli.cmd_chart_create(_create_args(ti1), client=_FakeClient()) == 0
    cid = next(t for t in capsys.readouterr().out.split() if t.startswith("CHT-"))
    rc = cli.cmd_chart_profile(Namespace(chart_id=cid, colprof_flags=None),
                               client=None)
    assert rc == 1
    cap = capsys.readouterr()
    assert "No ti3" in (cap.out + cap.err)
