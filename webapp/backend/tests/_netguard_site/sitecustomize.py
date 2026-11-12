"""Loaded automatically by Python subprocesses started from the test suite (the conftest
prepends this directory to PYTHONPATH): installs the same network guard as in-process,
so a CLI run by a test cannot reach a printer either. Attempts are appended to the file
named by FREEGLAZ_NETGUARD_LOG, which the conftest checks after each test."""
import importlib.util as _ilu
import os as _os

_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "netguard.py")
_spec = _ilu.spec_from_file_location("_freeglaz_test_netguard", _path)
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_mod.install()
