"""printtarg charts on an A4 sheet (manual feed): ColorMunki only, one block of strips crossing
the sheet, within what the print pipeline's centred placement takes on the sheet as the Z9
measures it. The roll presets are unchanged. Z9 mocked, no hardware."""
import functools
import json
import math
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import tifffile
from fastapi.testclient import TestClient

from lib.z9_client import cache
from lib.z9_client import chart_geometry_refonte as G
from lib.z9_client import printtarg_chart as P
from lib.z9_client.argyll import find_argyll_binary
from lib.z9_client.printing import MECHANICAL_MARGINS_MM
from webapp.backend.main import app
from webapp.backend.models import LoadedPaper, PrintParams
from webapp.backend.routes import charts as charts_routes
from webapp.backend.services import print_geometry
from webapp.backend.tests.test_charts_routes import MID, SERIAL, _RESIDENT, _fake_z9
from webapp.backend.tests.test_printtarg_chart import (NOW, RESIDENT, _check_common,
                                                        _check_transverse, _ti1_rows, _write_ti1)

argyll = pytest.mark.skipif(not (find_argyll_binary("printtarg") and find_argyll_binary("targen")),
                            reason="printtarg/targen (Argyll) not installed")
# An A4 sheet as a Z9 24in reported it (manual feed, measured shorter than the nominal 297 mm)
A4_MEASURED = (210.52, 292.74)


def _sheet(w, h) -> LoadedPaper:
    return LoadedPaper(id=MID, name="A4 test", category="custom", is_factory=False,
                       media_source="MANUAL_FEED", sheet_width_mm=w, sheet_height_mm=h)


def _targen(d: Path, n: int) -> Path:
    subprocess.run([find_argyll_binary("targen"), "-d2", "-G", "-f", str(n), str(d / f"t{n}")],
                   check=True, capture_output=True, timeout=120)
    return d / f"t{n}.ti1"


def _orch_a4(ti1, root, **kw):
    kw.setdefault("dpi", 100)
    kw.setdefault("now", NOW)
    kw.setdefault("instrument", "colormunki")
    return P.orchestrate_printtarg_chart(ti1, media_key="a4", reference_icc_path=RESIDENT,
                                         gloss_enhancer="OFF", charts_root=root, **kw)


# ─── limits and offer (no Argyll needed) ────────────────────────────────────
def test_a4_limits_come_from_the_margins_and_the_measured_sheet():
    m = MECHANICAL_MARGINS_MM["MANUALFEED"]
    lim = P.sheet_chart_limits("a4")
    assert lim["assumed_sheet_h_mm"] == 297.0 - G.SHEET_HEIGHT_ALLOWANCE_MM
    assert lim["max_w_mm"] == 210.0 - 2 * max(m["left"], m["right"]) == 200.0
    assert lim["max_h_mm"] == pytest.approx(292.0 - 2 * max(m["top"], m["bottom"])) == 257.2
    assert P.printable_width_mm("a4") == 200.0
    assert P.printable_width_mm("roll24") == 600.0                # roll unchanged
    # the A4 preset's page width (where the transverse search starts) is the sheet's limit,
    # derived from the sheet and its margins, not typed in
    assert P.PRESETS[("colormunki", "a4")].page_w_mm == math.floor(lim["max_h_mm"]) == 257
    # a chart at the limits, centred by the print pipeline on the sheet as measured: inside
    g = print_geometry.compute_geometry(_sheet(*A4_MEASURED), PrintParams(), lim["max_w_mm"],
                                        lim["max_h_mm"])
    assert print_geometry.detect_geometry_issues(g, g.media_source) == ([], [])


def test_a4_is_offered_for_the_colormunki_only():
    opts = P.supported_options()
    assert opts["media"] == ["roll24", "a4"]
    presets = {i["key"]: [p["media_key"] for p in i["presets"]] for i in opts["instruments"]}
    assert presets == {"i1pro3plus": ["roll24"], "colormunki": ["roll24", "a4"]}
    pre = P.PRESETS[("colormunki", "a4")]
    assert pre.page_h_mm <= P.printable_width_mm("a4") and not pre.unlimited_strip
    assert P.PRESETS[("colormunki", "roll24")] == P.LayoutPreset(610.0, 450.0, False)
    assert P.PRESETS[("i1pro3plus", "roll24")] == P.LayoutPreset(610.0, 500.0, True)


@pytest.mark.parametrize("kw, code", [
    ({"instrument": "i1pro3plus"}, "unsupported_media"),
    ({"roll_layout": "pages"}, "invalid_roll_layout"),
])
def test_a4_refusals_before_any_file(tmp_path, kw, code):
    ti1 = tmp_path / "a.ti1"
    _write_ti1(ti1, _ti1_rows(10))
    root = tmp_path / "charts"
    with pytest.raises(P.PrinttargChartError) as e:
        _orch_a4(ti1, root, **kw)
    assert e.value.code == code and not root.exists()


# ─── real printtarg ─────────────────────────────────────────────────────────
@argyll
def test_real_colormunki_a4_64_patches(tmp_path):
    """64 patches: 8 strips of 8 crossing the sheet, one block, no padding, within the sheet;
    the print pipeline places it centred, unscaled and unturned, inside the printable area."""
    ti1 = _targen(tmp_path, 64)
    res = _orch_a4(ti1, tmp_path / "charts")
    desc = _check_common(res, ti1)
    _check_transverse(desc)
    g, media = desc["geometry"], desc["media"]
    assert (g["steps_in_pass"], g["total_strips"], g["n_pages"], g["padding_count"]) == (8, 8, 1, 0)
    assert g["passes_in_strips"] == [8] and g["patch_count"] == g["sets"] == 64
    assert abs(g["tiff_w_mm"] - 190.0) < 0.3 and g["tiff_h_mm"] <= 257.2
    assert g["roll_length_mm"] is None                              # a sheet: no roll consumed
    assert (media["format_key"], media["source"], media["height_mm"]) == ("a4", "sheet", 297.0)
    assert media["mechanical_margins_mm"] == MECHANICAL_MARGINS_MM["MANUALFEED"]
    assert media["sheet_limits"] == P.sheet_chart_limits("a4")
    assert desc["printtarg"]["page_h_mm"] == 190.0
    assert desc["layout_search"]["max_page_length_mm"] == 257
    assert [s["label"] for s in desc["strips"]] == list("ABCDEFGH")
    # same ColorMunki patch as on the roll: printtarg's own size, only the count changes
    pitch = [(s["x_mm"][1] - s["x_mm"][0]) / g["steps_in_pass"] for s in desc["strips"]]
    assert all(14.5 < p < 15.3 for p in pitch)
    # the print pipeline: centred on the sheet as measured, size = the TIFF's own, inside
    info = tifffile.TiffFile(Path(res.chart_dir) / "chart.tif")
    w_px, h_px = info.pages[0].shape[1], info.pages[0].shape[0]
    info.close()
    w_mm, h_mm = w_px / 100 * 25.4, h_px / 100 * 25.4
    geom = print_geometry.compute_geometry(_sheet(*A4_MEASURED), PrintParams(), w_mm, h_mm)
    assert print_geometry.detect_geometry_issues(geom, geom.media_source) == ([], [])
    assert (geom.image_width_mm, geom.image_height_mm) == (w_mm, h_mm)
    assert geom.centered_x and geom.centered_y and geom.media_source == "MANUAL_FEED"


@argyll
def test_real_a4_refuses_a_chart_longer_than_the_sheet(tmp_path):
    ti1 = _targen(tmp_path, 120)                         # 15 strips of 8: two blocks
    root = tmp_path / "charts"
    with pytest.raises(P.PrinttargChartError) as e:
        _orch_a4(ti1, root)
    assert e.value.code == "too_many_strips_for_sheet"
    assert not any(root.rglob("chart.json")) and not any(root.rglob("*.tif"))


# ─── routes ─────────────────────────────────────────────────────────────────
@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    yield TestClient(app)
    app.dependency_overrides.clear()
    for tif in tmp_path.rglob("*.tif"):
        tif.unlink(missing_ok=True)


def _a4_z9():
    z9 = _fake_z9(source="MANUAL_FEED")
    st = dict(z9.device.status.return_value)
    st.update(loaded_paper_width_mm=A4_MEASURED[0], loaded_paper_length_mm=A4_MEASURED[1])
    z9.device.status.return_value = st
    return z9


def _body(**kw):
    b = {"media_key": "a4", "paper_mediaid": MID, "gloss_enhancer": "OFF",
         "targen_flags": "-d2 -G -f 64", "layout": "printtarg", "instrument": "colormunki"}
    b.update(kw)
    return b


@argyll
def test_route_creates_an_a4_colormunki_chart(client, monkeypatch):
    monkeypatch.setattr(charts_routes._ptc, "orchestrate_printtarg_chart",
                        functools.partial(P.orchestrate_printtarg_chart, dpi=100))
    app.dependency_overrides[charts_routes.get_chart_z9] = _a4_z9
    r = client.post("/api/charts", json=_body())
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["media_key"], body["n_patches"], body["total_strips"], body["steps_in_pass"],
            body["n_pages"]) == ("a4", 64, 8, 8, 1)
    assert body["roll_length_mm"] is None and abs(body["tiff_w_mm"] - 190) < 0.3
    assert body["media_source"] == "sheet"                        # the UI words a sheet, not a roll
    desc = json.loads((Path(body["chart_dir"]) / "chart.json").read_text())
    assert desc["paper"]["serial"] == SERIAL and desc["media"]["format_key"] == "a4"


@argyll
def test_route_refuses_the_i1pro3plus_on_a4(client):
    app.dependency_overrides[charts_routes.get_chart_z9] = _a4_z9
    r = client.post("/api/charts", json=_body(instrument="i1pro3plus"))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "unsupported_media"


CID = "CHT-20261124-1600-A4"


_A4_MEDIA = {"format_key": "a4", "source": "sheet", "width_mm": 210.0, "height_mm": 297.0}


def _install_a4_chart(w_mm=190.0, h_mm=241.0, dpi=300, media=_A4_MEDIA) -> Path:
    """A printtarg chart on disk; ``media`` = its descriptor's media block (None: no block)."""
    d = cache.charts_dir(SERIAL) / CID
    d.mkdir(parents=True)
    w, h = round(w_mm / 25.4 * dpi), round(h_mm / 25.4 * dpi)
    icc = _RESIDENT.read_bytes()
    tifffile.imwrite(d / "chart.tif", np.full((h, w, 3), 30000, np.uint16), photometric="rgb",
                     resolution=(dpi, dpi), resolutionunit="INCH",
                     extratags=[(34675, "B", len(icc), icc, False)])
    desc = {"schema_version": 1, "chart_id": CID, "created_at": "2026-11-24T16:00:00",
            "layout": "printtarg", "instrument": {"key": "colormunki", "printtarg_selector": "CM"},
            **({} if media is None else {"media": {**media, "dpi": dpi}}),
            "paper": {"name": "A4 test", "media_id": MID, "gloss_enhancer": "OFF", "serial": SERIAL},
            "geometry": {"patch_count": 64, "n_pages": 1},
            "color_management": {"tag_source": "resident-live", "gloss_enhancer": "OFF"},
            "files": {"tiff": "chart.tif", "ti2": f"printtarg/{CID}.ti2"}}
    (d / "chart.json").write_text(json.dumps(desc))
    return d


def test_print_a4_chart_on_the_loaded_sheet_is_centred_and_unscaled(client, monkeypatch):
    d = _install_a4_chart()
    app.dependency_overrides[charts_routes.get_chart_z9] = _a4_z9
    sent = []

    def _fake_send(self, job, **kw):
        sent.append(job)
        return SimpleNamespace(duration_seconds=2.0, prn_size_bytes=1)
    monkeypatch.setattr("lib.z9_client.printing.PrintOps.send", _fake_send)
    r = client.post(f"/api/charts/{CID}/print", json={"quality": "HIGH"})
    assert r.status_code == 200, r.text
    assert len(sent) == 1
    job = sent[0]
    w_px, h_px = round(190.0 / 25.4 * 300), round(241.0 / 25.4 * 300)
    assert job.media_source == "MANUALFEED" and job.orientation == 0
    assert (job.image_w_mm, job.image_h_mm) == pytest.approx((w_px / 300 * 25.4, h_px / 300 * 25.4))
    assert job.offset_x_mm == pytest.approx((A4_MEASURED[0] - job.image_w_mm) / 2)
    desc = json.loads((d / "chart.json").read_text())
    pl = desc["print_placement"]
    assert pl["media_source"] == "MANUAL_FEED" and desc["printed_at"]
    assert pl["offset_y_mm"] == pytest.approx((A4_MEASURED[1] - job.image_h_mm) / 2)
    # the record says what was printed: the TIFF's own size, on the sheet as measured
    assert (pl["image_w_mm"], pl["image_h_mm"]) == pytest.approx((job.image_w_mm, job.image_h_mm))
    assert (pl["sheet_w_mm"], pl["sheet_h_mm"]) == pytest.approx(A4_MEASURED)


def test_print_a4_chart_too_tall_for_the_sheet_is_refused(client, monkeypatch):
    _install_a4_chart(h_mm=262.0)                       # taller than a centred image may be

    def _boom(self, job, **kw):
        raise AssertionError("nothing must be sent")
    monkeypatch.setattr("lib.z9_client.printing.PrintOps.send", _boom)
    app.dependency_overrides[charts_routes.get_chart_z9] = _a4_z9
    r = client.post(f"/api/charts/{CID}/print", json={"quality": "HIGH"})
    assert r.status_code == 422 and "printable area" in r.json()["detail"]


# ─── a chart goes on the format it was laid out for ─────────────────────────
@pytest.mark.parametrize("media_key, source, w, h, why", [
    ("a4", "MANUAL_FEED", *A4_MEASURED, None),                  # A4 as the Z9 measures it
    ("a4", "MANUAL_FEED", 210.0, 297.0, None),                  # nominal A4
    ("a4", "SHEET", 210.0, 297.0, None),
    ("a4", "ROLL", 610.0, None, "source"),                      # an A4 chart on a roll
    ("a4", "MANUAL_FEED", 297.0, 420.0, "sheet_format"),        # on an A3 sheet
    ("a4", "MANUAL_FEED", 210.0, 280.0, "sheet_format"),        # on a much shorter sheet
    ("roll24", "MANUAL_FEED", 210.0, 297.0, "source"),          # a roll chart on a sheet
    ("roll24", "ROLL", 610.0, None, None),
    ("roll24", "ROLL", 1118.0, None, None),     # roll width: interface + printable-area check
    ("a4", "ROLL2", None, None, None),          # left to the ROLL2 guard of the print pipeline
    # a sheet chart needs a sheet the printer identifies: source AND both dimensions
    ("a4", "MANUAL_FEED", None, None, "sheet_unidentified"),
    ("a4", "MANUAL_FEED", 210.0, None, "sheet_unidentified"),
    ("a4", "MANUAL_FEED", None, 297.0, "sheet_unidentified"),
    ("a4", "MANUAL_FEED", 0.0, 297.0, "sheet_unidentified"),
    ("a4", "?", 210.0, 297.0, "source_unknown"),
    ("a4", "", 210.0, 297.0, "source_unknown"),
    ("a4", None, 210.0, 297.0, "source_unknown"),
    ("a4", "TRAY", 210.0, 297.0, "source_unknown"),
    # a chart naming no known format: refused, never guessed
    (None, "MANUAL_FEED", 210.0, 297.0, "chart_format_unknown"),
    ("a5", "MANUAL_FEED", 148.0, 210.0, "chart_format_unknown"),
    # roll charts: unchanged (unknown or second-roll sources stay with the print pipeline)
    ("roll24", "?", None, None, None),
    ("roll24", "ROLL2", None, None, None),
    ("roll24", "SHEET", 210.0, 297.0, "source"),
])
def test_loaded_media_mismatch(media_key, source, w, h, why):
    assert P.loaded_media_mismatch(media_key, media_source=source, width_mm=w, height_mm=h) == why


def _no_send(monkeypatch) -> list:
    """PrintOps.send replaced: every call is recorded (and fails), so a test can prove that no
    job left. The route turns a send failure into a 422, never into the 409 / 422 asserted."""
    calls = []

    def _boom(self, job, **kw):
        calls.append(job)
        raise AssertionError("nothing must be sent")
    monkeypatch.setattr("lib.z9_client.printing.PrintOps.send", _boom)
    return calls


def _z9_loaded(source, w, h):
    """The fake Z9 of the chart routes, with the loaded media given (None = not reported)."""
    z9 = _fake_z9(source=source)
    st = dict(z9.device.status.return_value)
    st.update(loaded_paper_width_mm=w, loaded_paper_length_mm=h)
    z9.device.status.return_value = st
    return z9


def test_print_a4_chart_on_a_roll_is_refused(client, monkeypatch):
    """It would fit on the roll, but it is laid out for the sheet: refused, nothing sent."""
    d = _install_a4_chart()
    sent = _no_send(monkeypatch)
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9(source="ROLL")
    r = client.post(f"/api/charts/{CID}/print", json={"quality": "HIGH"})
    assert r.status_code == 409, r.text
    det = r.json()["detail"]
    assert (det["code"], det["reason"], det["chart_media"]) == ("chart_media_mismatch", "source", "a4")
    assert sent == [] and "printed_at" not in json.loads((d / "chart.json").read_text())


def test_print_a4_chart_on_an_a3_sheet_is_refused(client, monkeypatch):
    _install_a4_chart()
    sent = _no_send(monkeypatch)
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()   # 297 x 420
    r = client.post(f"/api/charts/{CID}/print", json={"quality": "HIGH"})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "sheet_format"
    assert sent == []


def test_print_roll_chart_on_a_sheet_is_refused_explicitly(client, monkeypatch):
    """A (small) roll chart that would fit on the A4 sheet: refused by format, not by size."""
    _install_a4_chart(w_mm=100.0, h_mm=60.0, media={"format_key": "roll24", "source": "roll",
                                                     "width_mm": 610.0, "height_mm": None})
    sent = _no_send(monkeypatch)
    app.dependency_overrides[charts_routes.get_chart_z9] = _a4_z9
    r = client.post(f"/api/charts/{CID}/print", json={"quality": "HIGH"})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "source"
    assert sent == []


@pytest.mark.parametrize("source, w, h, status, code, reason", [
    ("MANUAL_FEED", None, None, 409, "chart_media_mismatch", "sheet_unidentified"),
    ("MANUAL_FEED", 210.52, None, 409, "chart_media_mismatch", "sheet_unidentified"),
    ("?", 210.52, 292.74, 409, "chart_media_mismatch", "source_unknown"),
    ("", 210.52, 292.74, 409, "chart_media_mismatch", "source_unknown"),
])
def test_print_a4_chart_needs_an_identified_sheet(client, monkeypatch, source, w, h, status, code,
                                                  reason):
    """Unknown source or dimensions: the A4 chart is not sent, whatever might fit."""
    d = _install_a4_chart()
    sent = _no_send(monkeypatch)
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _z9_loaded(source, w, h)
    r = client.post(f"/api/charts/{CID}/print", json={"quality": "HIGH"})
    assert r.status_code == status, r.text
    det = r.json()["detail"]
    assert (det["code"], det["reason"]) == (code, reason)
    assert sent == [] and "printed_at" not in json.loads((d / "chart.json").read_text())


def test_print_a4_chart_on_roll2_keeps_the_z9_pro_refusal(client, monkeypatch):
    """The second roll of a Z9 Pro: the print pipeline's own refusal, unchanged."""
    d = _install_a4_chart()
    sent = _no_send(monkeypatch)
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _z9_loaded("ROLL2", None, None)
    r = client.post(f"/api/charts/{CID}/print", json={"quality": "HIGH"})
    assert r.status_code == 409 and "ROLL2" in r.json()["detail"], r.text
    assert sent == [] and "printed_at" not in json.loads((d / "chart.json").read_text())


@pytest.mark.parametrize("media", [None, {}, {"format_key": None}, {"format_key": "a5"}])
def test_print_chart_without_a_known_format_is_refused(client, monkeypatch, media):
    d = _install_a4_chart(media=media)
    assert ("media" in json.loads((d / "chart.json").read_text())) == (media is not None)
    sent = _no_send(monkeypatch)
    app.dependency_overrides[charts_routes.get_chart_z9] = _a4_z9
    r = client.post(f"/api/charts/{CID}/print", json={"quality": "HIGH"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "chart_format_unknown", r.text
    assert sent == [] and "printed_at" not in json.loads((d / "chart.json").read_text())
