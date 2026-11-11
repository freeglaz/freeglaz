"""ICC transform comparison (lib/z9_client/profile_diff.py) and the compare route, with a
deterministic fake lookup (no Argyll needed); the real xicclu path is exercised by the offline
bench."""
import json
import struct

import numpy as np
import pytest
from fastapi.testclient import TestClient

from lib.z9_client import profile_diff as pdf
from webapp.backend.main import app
from webapp.backend.tests.test_build_inputs import _chart, _fake_colprof, grid_rows, ti3_text
from webapp.backend.tests.test_exclusions import G5, excl, sid_of


def fake_lookup(bump_at=None, amp=3.0):
    """Lab = linear in RGB; profile 'B' gets a local bump around bump_at (0-1)."""
    def lookup(profile, pts, intent):
        out = []
        for r, g, b in pts:
            lab = [100 * (0.3 * r + 0.6 * g + 0.1 * b), 80 * (r - g), 80 * (g - b)]
            if "B" in str(profile) and bump_at is not None:
                d2 = sum((x - y) ** 2 for x, y in zip((r, g, b), bump_at))
                lab[0] += amp * np.exp(-d2 / 0.01)
            out.append(tuple(lab))
        return out
    return lookup


def tags_with(a2b0=True, wtpt=(0.8166, 0.8492, 0.7161)):
    t = {"A2B1": b"x"}
    if a2b0:
        t["A2B0"] = b"x"
    t["wtpt"] = b"XYZ \0\0\0\0" + struct.pack(">3i", *(int(v * 65536) for v in wtpt))
    t["bkpt"] = b"XYZ \0\0\0\0" + struct.pack(">3i", *(int(v * 65536) for v in (0.005, 0.005, 0.006)))
    rows = "\n".join(f"{i + 1} {r} {g} {b} 1 1 1" for i, (r, g, b) in enumerate([(0, 0, 0), (50, 50, 50), (100, 100, 100)]))
    t["targ"] = (b"text\0\0\0\0" + (f"CTI3\nBEGIN_DATA_FORMAT\nSAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z\n"
                                    f"END_DATA_FORMAT\nBEGIN_DATA\n{rows}\nEND_DATA\n").encode())
    return t


def test_identical_transforms_give_zero():
    r = pdf.compare_profiles("A.icc", "A2.icc", lookup=fake_lookup(), tag_reader=lambda p: tags_with())
    rel = r["intents"]["relative"]
    assert rel["grid"]["max"] == 0 and rel["measured"]["max"] == 0 and rel["measured"]["n"] == 3
    assert r["intents"]["perceptual"]["grid"]["max"] == 0 and r["method"]["perceptual_compared"]
    assert r["white_black"]["white_point_tag"]["de00"] == 0 and "printer_not_involved" in r["limits"]


def test_a_local_change_is_localized_and_named():
    bump = (0.5, 0.5, 0.5)
    r = pdf.compare_profiles("A.icc", "B.icc", lookup=fake_lookup(bump), grid_n=9,
                             focus=[{"rgb": [50, 50, 50], "label": "CHT/a.ti3/172"}], tag_reader=lambda p: tags_with())
    g = r["intents"]["relative"]["grid"]
    assert g["max"] > 2.0 and g["mean"] < 0.2                      # big locally, tiny on average
    assert g["top"][0]["rgb255"] == [128, 128, 128]
    (f,) = r["focus"]
    assert f["label"] == "CHT/a.ti3/172" and f["de00_at_point"] > 2.0
    assert f["near"]["max"] > 2.0 and f["elsewhere"]["p95"] < 0.5   # "mainly this region, elsewhere little"


def test_perceptual_skipped_when_a_profile_has_no_a2b0():
    reader = lambda p: tags_with(a2b0="B" not in str(p))  # noqa: E731
    r = pdf.compare_profiles("A.icc", "B.icc", lookup=fake_lookup(), tag_reader=reader)
    assert "perceptual" not in r["intents"] and not r["method"]["perceptual_compared"]


def test_control_grid_is_reproducible():
    assert pdf.control_grid(3)[:3] == [(0.0, 0.0, 0.0), (0.0, 0.0, 0.5), (0.0, 0.0, 1.0)] and len(pdf.control_grid(9)) == 729


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    yield TestClient(app)
    app.dependency_overrides.clear()


def _build(client, cid, name):
    fp = client.post(f"/api/charts/{cid}/profile/preview", json={"policy": "apply_all"}).json()["inputs_fingerprint"]
    r = client.post(f"/api/charts/{cid}/profile", json={"policy": "apply_all", "expected_fingerprint": fp, "name": name})
    assert r.status_code == 200, r.text
    return r.json()["build_id"]


def test_compare_route_uses_stored_versions_and_derives_focus(client, monkeypatch):
    cid, d = _chart(client, {"a.ti3": ti3_text(G5)})
    _fake_colprof(monkeypatch)
    a = _build(client, cid, "ref")
    sid = sid_of(G5, (50, 50, 50))
    excl(client, cid, "a.ti3", sid)
    b = _build(client, cid, "new")
    seen = {}

    def fake_compare(ia, ib, focus, grid_n, top_n):
        seen.update(ia=ia, ib=ib, focus=focus, grid_n=grid_n)
        return {"intents": {}, "focus": [{"label": f["label"], "de00_at_point": 1.0} for f in focus],
                "white_black": {}, "limits": [], "method": {}}
    monkeypatch.setattr(pdf, "compare_profiles", fake_compare)
    r = client.post(f"/api/charts/{cid}/builds/compare", json={"a": a, "b": b, "grid_n": 5})
    assert r.status_code == 200, r.text
    j = r.json()
    assert seen["ia"] == d / "builds" / a / "profile.icc" and seen["ib"] == d / "builds" / b / "profile.icc"   # never rebuilt
    assert seen["focus"] == [{"rgb": [50.0, 50.0, 50.0], "label": f"{cid}/a.ti3/{sid}", "excluded_in": "b"}]
    assert j["focus"][0]["excluded_in"] == "b" and j["a"]["label"] == "ref" and j["b"]["label"] == "new"
    assert j["identical_files"] is False
    assert client.post(f"/api/charts/{cid}/builds/compare", json={"a": a, "b": "20990101T000000Z-zzzzzz"}).status_code == 404
    from lib.z9_client.argyll import ArgyllNotFound
    monkeypatch.setattr(pdf, "compare_profiles", lambda *a, **k: (_ for _ in ()).throw(ArgyllNotFound("xicclu")))
    assert client.post(f"/api/charts/{cid}/builds/compare", json={"a": a, "b": b}).json()["detail"]["error"] == "argyll_missing"


ARGYLL_REF = __import__("pathlib").Path("/usr/share/color/argyll/ref")


@pytest.mark.skipif(not (ARGYLL_REF / "sRGB.icm").is_file() or not (ARGYLL_REF / "ClayRGB1998.icm").is_file()
                    or __import__("shutil").which("xicclu") is None, reason="ArgyllCMS (xicclu + ref profiles) not installed")
def test_real_xicclu_lookups_on_argyll_reference_profiles():
    """The real lookup path (xicclu, float Lab) on two matrix profiles shipped with Argyll: a profile
    against itself gives exactly 0; sRGB against ClayRGB (Adobe RGB 1998 primaries) differs in the
    saturated greens, same D50-adapted white. No targ tag → no measured set, no A2B0 → relative only."""
    srgb, clay = ARGYLL_REF / "sRGB.icm", ARGYLL_REF / "ClayRGB1998.icm"
    same = pdf.compare_profiles(srgb, srgb, grid_n=5)
    assert same["intents"]["relative"]["grid"]["max"] == 0 and same["intents"]["relative"]["measured"] is None
    assert not same["method"]["perceptual_compared"]
    diff = pdf.compare_profiles(srgb, clay, grid_n=5, focus=[{"rgb": [0, 100, 50], "label": "green"}])
    g = diff["intents"]["relative"]["grid"]
    assert g["n"] == 125 and g["max"] > 5 and g["top"][0]["rgb255"][1] == 255
    assert diff["white_black"]["device_white"]["de00"] < 0.01
    assert diff["focus"][0]["de00_at_point"] > 5
