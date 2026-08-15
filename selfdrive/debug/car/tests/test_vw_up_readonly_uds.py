import argparse

import pytest

from openpilot.selfdrive.debug.car.vw_up_readonly_uds import (
  build_result,
  decode_text,
  expand_did_ranges,
  identify_known_ecu,
  load_attempted_dids,
  load_positive_dids,
  normalize_part_number,
  numeric_views,
  parse_did_range,
  parse_did_target,
  validate_address,
  validate_did,
)


def test_validate_physical_address():
  assert validate_address(0x713, 0x6A) == 0x77D
  with pytest.raises(ValueError, match="broadcast"):
    validate_address(0x7DF, 0x08)
  with pytest.raises(ValueError, match="11-bit"):
    validate_address(0x796, 0x6A)
  with pytest.raises(ValueError, match="diagnostic address"):
    validate_address(0x368, 0x6A)


def test_validate_did():
  validate_did(0x0000)
  validate_did(0xFFFF)
  with pytest.raises(ValueError):
    validate_did(-1)
  with pytest.raises(ValueError):
    validate_did(0x10000)


def test_ascii_decoding_and_normalization():
  assert decode_text(b"12E 909 059 A\x00\xff") == "12E 909 059 A"
  assert decode_text(b"\x00\xff ") is None
  assert decode_text(b"\x80") is None
  assert normalize_part_number("12e 909 059 a") == "12E909059A"


def test_numeric_views():
  assert numeric_views(b"\x01\x00") == {
    "unsigned_be": 256,
    "signed_be": 256,
    "unsigned_le": 1,
    "signed_le": 1,
  }
  assert numeric_views(b"abc") == {}


def test_did_ranges_and_resume(tmp_path):
  assert parse_did_range("0x028d:0x028f") == (0x028D, 0x028F)
  assert expand_did_ranges([(3, 5), (1, 3)]) == [1, 2, 3, 4, 5]
  output = tmp_path / "discovery.jsonl"
  output.write_text(
    '{"event":"did_attempt","did":"0x028D"}\n'
    '{"event":"something_else","did":"0x028E"}\n'
    'not-json\n'
  )
  assert load_attempted_dids(output) == {0x028D}
  assert load_positive_dids(output) == set()

  output.write_text(
    output.read_text()
    + '{"event":"did_attempt","did":"0x4E06","status":"positive"}\n'
    + '{"event":"did_attempt","did":"0x4E07","status":"negative"}\n'
  )
  assert load_positive_dids(output) == {0x4E06}


def test_did_target():
  assert parse_did_target("0x73b:0x4e03") == (0x73B, 0x4E03)
  with pytest.raises(argparse.ArgumentTypeError):
    parse_did_target("0x73b")
  with pytest.raises(argparse.ArgumentTypeError):
    parse_did_target("0x73b:0x10000")


@pytest.mark.parametrize("part,expected", [
  ("12E 909 059 A", "J539 brake booster (Bosch EBKV)"),
  ("12E614517F", "J104 ABS/ESC (TRW EBC 460 ESP)"),
  ("unknown", None),
])
def test_known_eup_ecu_matching(part, expected):
  assert identify_known_ecu(0xF187, part) == expected
  assert identify_known_ecu(0xF189, part) is None


def test_json_result_fields():
  result = build_result(0x713, 0x77D, 1, 0xF187, b"12E614517F")
  assert result.tx_address == "0x713"
  assert result.rx_address == "0x77D"
  assert result.did == "0xF187"
  assert result.known_ecu == "J104 ABS/ESC (TRW EBC 460 ESP)"
