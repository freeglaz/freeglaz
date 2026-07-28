"""Profile refinement part 0: extract_cied_text + parse_cgats_data
must read both measurement-tag dialects — CGATS.17 (HP/Z9 firmware)
AND CTI3 (Argyll/freeglaz, CIED/targ tags).

Targeted regressions:
1. extract_cied_text only recognized the CGATS signature -> failed on CTI3.
2. parse_cgats_data stayed in 'format' state after END_DATA_FORMAT, so the
   NUMBER_OF_SETS that Argyll places between END_DATA_FORMAT and BEGIN_DATA
   overwrote the columns line -> RGB_R/G/B fields lost, spectra lost.

Without these two fixes, the ti3 of a profile already built by colprof cannot
be regenerated from the .icc, which blocks the "rebuild with Argyll" path."""

import pytest

from lib.z9_client.profiling import extract_cied_text, parse_cgats_data


def _wrap_text_tag(body: str) -> bytes:
    return b"text" + b"\x00\x00\x00\x00" + body.encode("ascii") + b"\x00"


CGATS_BODY = (
    "CGATS.17\n"
    "DESCRIPTOR \"firmware\"\n"
    "NUMBER_OF_SETS 2\n"
    "BEGIN_DATA_FORMAT\n"
    "SAMPLE_ID RGB_R RGB_G RGB_B SPECTRAL_400 SPECTRAL_420\n"
    "END_DATA_FORMAT\n"
    "BEGIN_DATA\n"
    "1 0 0 0 0.1 0.2\n"
    "2 255 255 255 0.8 0.9\n"
    "END_DATA\n"
)

CTI3_BODY = (
    "CTI3\n"
    "DESCRIPTOR \"argyll\"\n"
    "NUMBER_OF_FIELDS 6\n"
    "BEGIN_DATA_FORMAT\n"
    "SAMPLE_ID RGB_R RGB_G RGB_B SPEC_400 SPEC_420\n"
    "END_DATA_FORMAT\n"
    "NUMBER_OF_SETS 2\n"
    "BEGIN_DATA\n"
    "1 0.0 0.0 0.0 0.1 0.2\n"
    "2 100.0 100.0 100.0 0.8 0.9\n"
    "END_DATA\n"
)


def test_extract_cied_reads_cgats17_firmware():
    tag = _wrap_text_tag(CGATS_BODY)
    text = extract_cied_text(tag, 0, len(tag))
    assert text.startswith("CGATS.17")
    parsed = parse_cgats_data(text)
    assert len(parsed["data"]) == 2
    assert parsed["format"][:4] == ["SAMPLE_ID", "RGB_R", "RGB_G", "RGB_B"]
    assert parsed["header"]["NUMBER_OF_SETS"] == "2"


def test_extract_cied_reads_cti3_argyll():
    tag = _wrap_text_tag(CTI3_BODY)
    text = extract_cied_text(tag, 0, len(tag))
    assert text.startswith("CTI3")
    parsed = parse_cgats_data(text)
    assert len(parsed["data"]) == 2
    assert parsed["format"][:4] == ["SAMPLE_ID", "RGB_R", "RGB_G", "RGB_B"]


def test_parse_cti3_number_of_sets_after_format_does_not_clobber_columns():
    parsed = parse_cgats_data(CTI3_BODY)
    assert "RGB_R" in parsed["format"]
    assert "RGB_G" in parsed["format"]
    assert "RGB_B" in parsed["format"]
    assert "SPEC_400" in parsed["format"]
    assert parsed["header"]["NUMBER_OF_SETS"] == "2"
    assert parsed["format"] != ["NUMBER_OF_SETS", "2"]


def test_extract_cied_prefers_leading_cti3_over_later_cgats_substring():
    body = (
        "CTI3\n"
        "KEYWORD \"mentionne CGATS ailleurs\"\n"
        "NUMBER_OF_FIELDS 4\n"
        "BEGIN_DATA_FORMAT\n"
        "SAMPLE_ID RGB_R RGB_G RGB_B\n"
        "END_DATA_FORMAT\n"
        "NUMBER_OF_SETS 1\n"
        "BEGIN_DATA\n"
        "1 0.0 0.0 0.0\n"
        "END_DATA\n"
    )
    tag = _wrap_text_tag(body)
    text = extract_cied_text(tag, 0, len(tag))
    assert text.startswith("CTI3")
    assert parse_cgats_data(text)["format"][1] == "RGB_R"


def test_extract_cied_raises_without_known_signature():
    tag = _wrap_text_tag("FOOBAR\nkey val\n")
    with pytest.raises(ValueError, match="CGATS/CTI3"):
        extract_cied_text(tag, 0, len(tag))
