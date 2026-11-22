"""Printer model family (lib printer_model): Z9 / Z9 Pro / other DesignJet.

The Z9 Pro's ModelName contains "DesignJet Z9": it must never be classified
as a Z9. Model strings only — no captured identification of a real machine.
"""
import pytest

from lib.z9_client import printer_model as pm


@pytest.mark.parametrize("name", [
    "HP DesignJet Z9 24in",
    "HP DesignJet Z9 44in",
    "HP DesignJet Z9+ 24in",
    "HP DesignJet Z9dr 44in",
    "HP DesignJet Z9+dr 44in",
    "  hp designjet   z9 24in  ",
])
def test_z9_family(name):
    assert pm.classify({"ModelName": name}) == pm.FAMILY_Z9
    assert pm.support_level(pm.FAMILY_Z9) == "validated"


@pytest.mark.parametrize("name", [
    "HP DesignJet Z9 Pro 64in PS",
    "HP DesignJet Z9+ Pro 64in",
    "HP DesignJet Z9Pro 64in",
])
def test_z9_pro_family(name):
    assert pm.classify({"ModelName": name}) == pm.FAMILY_Z9_PRO
    assert pm.support_level(pm.FAMILY_Z9_PRO) == "partial"


def test_model_number_is_a_second_witness_for_the_pro():
    # A short ModelName must not let a Pro fall back to the Z9 family.
    ident = {"ModelName": "HP DesignJet Z9 64in", "ModelNumber": "Z9 Pro 64in PS"}
    assert pm.classify(ident) == pm.FAMILY_Z9_PRO


@pytest.mark.parametrize("name", [
    "HP DesignJet Z6 Pro 64in",
    "HP DesignJet Z3200ps 44in",
    "HP DesignJet Z90 44in",      # hypothetical: not a Z9 by prefix
    "HP DesignJet T1600",
])
def test_other_designjet(name):
    assert pm.classify({"ModelName": name}) == pm.FAMILY_OTHER
    assert pm.support_level(pm.FAMILY_OTHER) == "untested"


@pytest.mark.parametrize("ident", [
    {}, None, {"ModelName": ""}, {"ModelName": "Synology DiskStation"},
    # ModelNumber alone is not enough to accept a device.
    {"ModelNumber": "Z9 Pro 64in PS"},
])
def test_not_a_designjet(ident):
    assert pm.classify(ident) is None
    assert pm.support_level(None) is None
