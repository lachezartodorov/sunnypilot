import pytest

from openpilot.selfdrive.debug.car.vw_up_readonly_uds import (
  build_result,
  decode_text,
  identify_known_ecu,
  normalize_part_number,
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


@pytest.mark.parametrize((part, expected), [
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
