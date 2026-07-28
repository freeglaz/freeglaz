"""Sync must not produce a double .icc.icc extension.

The z9_icc_name exposed by the Z9 may ALREADY end with .icc (e.g.
"freeglaz roundtrip test 2026-05-13.icc"). mirror_profile_path must then
strip the .icc before adding the marker + the extension, otherwise -> ....icc.icc.
"""
from lib.z9_client import cache


def test_icc_name_already_has_ext_no_double(tmp_path, monkeypatch):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path))
    p = cache.mirror_profile_path(
        "CNXXXXXXXX", "Hahnemühle FA Baryta Satin",
        "FULLPAGE", "PRINTER_RGB", custom=True,
        icc_name="freeglaz roundtrip test 2026-05-13.icc",
    )
    assert p.name.endswith(".icc")
    assert not p.name.endswith(".icc.icc")
    assert ".icc.icc" not in p.name
    assert p.name == "freeglaz roundtrip test 2026-05-13.icc"


def test_icc_name_without_ext_gets_single(tmp_path, monkeypatch):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path))
    p = cache.mirror_profile_path(
        "CNXXXXXXXX", "P", "OFF", "PRINTER_RGB", custom=True,
        icc_name="sans extension",
    )
    assert p.name == "sans extension.icc"


def test_factory_icc_name_with_ext_no_double(tmp_path, monkeypatch):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path))
    p = cache.mirror_profile_path(
        "CNXXXXXXXX", "P", "OFF", "PRINTER_RGB", custom=False,
        icc_name="HP factory.icc",
    )
    assert p.name.endswith(".freeglaz-factory.icc")
    assert ".icc.icc" not in p.name
