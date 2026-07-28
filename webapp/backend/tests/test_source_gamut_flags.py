"""Unit tests — resolve_gamut_aliases_in_flags (config).

Makes -s/-S source-gamut aliases in free-form colprof flags resolve to the
bundled assets/ ICC (like the CLI), while leaving the percentage form alone.
"""
import pytest

from lib.z9_client.config import (
    resolve_gamut_aliases_in_flags, _BUNDLE_ASSETS)


def test_uppercase_S_alias_resolves_to_assets():
    out = resolve_gamut_aliases_in_flags(["-v", "-qh", "-S", "AdobeRGB"])
    resolved = out[out.index("-S") + 1]
    assert resolved == str(_BUNDLE_ASSETS / "ClayRGB-elle-V2-g22.icc")
    assert out[:3] == ["-v", "-qh", "-S"]          # rest untouched


def test_lowercase_s_alias_resolves_too():
    # -s also takes a source profile → alias must resolve there as well.
    out = resolve_gamut_aliases_in_flags(["-s", "Rec2020"])
    assert out[1] == str(_BUNDLE_ASSETS / "Rec2020-elle-V2-rec709.icc")


def test_percentage_form_left_untouched():
    # -s 90 / -S 2.0 = compression/expansion percentage, NOT a profile.
    assert resolve_gamut_aliases_in_flags(["-v", "-qh", "-s", "90"]) == \
        ["-v", "-qh", "-s", "90"]
    assert resolve_gamut_aliases_in_flags(["-S", "2.0"]) == ["-S", "2.0"]


def test_absolute_path_passthrough(tmp_path):
    icc = tmp_path / "custom.icc"
    icc.write_bytes(b"\0")
    out = resolve_gamut_aliases_in_flags(["-S", str(icc)])
    assert out[1] == str(icc.resolve())


def test_no_gamut_flag_is_noop():
    flags = ["-v", "-qh", "-r", "0.5", "-nc"]
    assert resolve_gamut_aliases_in_flags(flags) == flags


def test_unknown_alias_raises():
    with pytest.raises(FileNotFoundError):
        resolve_gamut_aliases_in_flags(["-S", "NotAProfile"])


def test_dangling_flag_no_value_is_ignored():
    # -S at the very end (no value) must not IndexError.
    assert resolve_gamut_aliases_in_flags(["-v", "-qh", "-S"]) == ["-v", "-qh", "-S"]
