import pytest

from openpilot.selfdrive.debug.car.vw_up_inactive_acc_probe import (
  ACC_SYSTEM_ADDR,
  BUS,
  EXPECTED_INACTIVE_DATA,
  build_inactive_acc_system,
  decode_bremse_1_speed_kph,
  decode_bremse_5_pressure_bar,
)


def test_decode_stationary_brake_messages():
  assert decode_bremse_1_speed_kph(bytes.fromhex("0000000000fe001c")) == 0
  assert decode_bremse_5_pressure_bar(bytes.fromhex("00400580080060ad")) == 0.5


def test_decode_brake_messages_rejects_wrong_length():
  with pytest.raises(ValueError):
    decode_bremse_1_speed_kph(b"\x00")
  with pytest.raises(ValueError):
    decode_bremse_5_pressure_bar(b"\x00")


def test_inactive_acc_system_is_frozen():
  assert build_inactive_acc_system() == (ACC_SYSTEM_ADDR, EXPECTED_INACTIVE_DATA, BUS)
  assert EXPECTED_INACTIVE_DATA[0] == EXPECTED_INACTIVE_DATA[1] ^ EXPECTED_INACTIVE_DATA[2] ^ \
    EXPECTED_INACTIVE_DATA[3] ^ EXPECTED_INACTIVE_DATA[4] ^ EXPECTED_INACTIVE_DATA[5] ^ \
    EXPECTED_INACTIVE_DATA[6] ^ EXPECTED_INACTIVE_DATA[7]
