"""Smoke tests for lib.z9_client.strategies — colprof presets."""
import os
from unittest.mock import patch

import pytest

from lib.z9_client.strategies import (
    BUILTINS, Strategy, StrategyOps, resolve_flags,
)


EXPECTED_BUILTINS = {
    "default", "faithful", "smooth", "dark-detail",
    "ultra", "compress-90", "xyz-clut",
}

REMOVED_BUILTINS = {
    "darkroom-rec2020", "darkroom-prophoto", "darkroom-adobergb",
}


def test_builtins_loaded():
    assert set(BUILTINS.keys()) == EXPECTED_BUILTINS


def test_darkroom_removed():
    for slug in REMOVED_BUILTINS:
        assert slug not in BUILTINS


def test_builtin_has_required_fields():
    for slug, s in BUILTINS.items():
        assert s.name == slug
        assert s.flags, f"{slug} missing flags"
        assert s.label, f"{slug} missing label"
        assert s.description, f"{slug} missing description"
        assert s.recommended_for, f"{slug} missing recommended_for"
        assert s.tags, f"{slug} missing tags"
        assert s.builtin is True


def test_specific_flags():
    # NO -nc: colprof embeds the .ti3 (targ tag) → self-sufficient profile,
    # reusable as a source (source_profiles). See colprof_strategies_builtin.toml.
    assert BUILTINS["default"].flags == "-v -qh"
    assert BUILTINS["compress-90"].flags == "-v -qh -s 90"
    assert BUILTINS["xyz-clut"].flags == "-v -qh -a x"
    assert BUILTINS["faithful"].flags == "-v -qh -r 0.1"
    # guard: NO builtin carries -nc (process rule)
    assert all("-nc" not in s.flags for s in BUILTINS.values())


def test_strategy_ops_list():
    ops = StrategyOps()
    names = {s.name for s in ops.list_strategies()}
    assert EXPECTED_BUILTINS.issubset(names)


def test_strategy_ops_get():
    ops = StrategyOps()
    s = ops.get_strategy("default")
    assert s is not None
    assert s.label == "Default (high quality)"
    assert ops.get_strategy("unknown-preset") is None


def test_cannot_override_builtin(tmp_path):
    ops = StrategyOps()
    with pytest.raises(ValueError, match="builtin"):
        ops.create_user_strategy("default", "-v -qh", "test")


def test_user_strategy_roundtrip(tmp_path, monkeypatch):
    # Isolation via the CANONICAL FREEGLAZ_STORE_ROOT override: the user TOML now derives from
    # cache.root_dir() (recentralized) → the override moves it correctly (= new correct behavior).
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))

    ops = StrategyOps()
    created = ops.create_user_strategy(
        name="my-preset",
        flags="-v -qh -r 0.3 -nc",
        description="Test",
        label="My preset",
        recommended_for="testing",
        tags=["test"],
    )
    assert created.name == "my-preset"
    assert created.builtin is False

    # Reload (fresh ops instance) - user preset persists
    ops2 = StrategyOps()
    fetched = ops2.get_strategy("my-preset")
    assert fetched is not None
    assert fetched.flags == "-v -qh -r 0.3 -nc"
    assert fetched.label == "My preset"
    assert fetched.recommended_for == "testing"

    ops2.delete_user_strategy("my-preset")
    assert ops2.get_strategy("my-preset") is None


def test_user_strategy_minimal_backcompat(tmp_path, monkeypatch):
    """A user TOML without label/recommended_for does not crash. The TOML is read from
    the FREEGLAZ_STORE_ROOT override (the path derives from cache.root_dir, recentralized)."""
    store = tmp_path / "store"
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(store))

    user_toml = store / "colprof_strategies.toml"
    user_toml.parent.mkdir(parents=True, exist_ok=True)
    user_toml.write_text(
        'schema_version = "1.0"\n'
        '\n[strategy.minimal]\n'
        'flags = "-v -qh"\n',
        encoding='utf-8',
    )

    ops = StrategyOps()
    s = ops.get_strategy("minimal")
    assert s is not None
    assert s.flags == "-v -qh"
    assert s.label == "minimal"  # default = slug
    assert s.recommended_for == ""
    assert s.description == ""


def test_resolve_flags_no_op():
    s = Strategy(name="t", flags="-v -qh -nc")
    assert resolve_flags(s) == "-v -qh -nc"


def test_resolve_flags_argyll_ref():
    s = Strategy(name="t", flags="-v -qh -S {ARGYLL_REF}/Rec2020.icm -nc")
    resolved = resolve_flags(s)
    assert "{ARGYLL_REF}" not in resolved
    assert "Rec2020.icm" in resolved


def test_to_dict_exposes_all_fields():
    """GUI-ready: the serialization contains label, flags, description,
    recommended_for, tags, builtin."""
    s = BUILTINS["default"]
    d = s.to_dict()
    assert "label" in d
    assert "flags" in d
    assert "description" in d
    assert "recommended_for" in d
    assert "tags" in d
    assert "builtin" in d
