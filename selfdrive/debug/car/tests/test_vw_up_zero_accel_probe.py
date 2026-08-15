from openpilot.selfdrive.debug.car.vw_up_zero_accel_probe import (
  ACC_SYSTEM_ADDR,
  BUS,
  EXPECTED_COUNTER_ZERO_DATA,
  build_active_zero_acc_system,
)


def test_active_zero_acc_system_is_frozen():
  assert build_active_zero_acc_system() == (ACC_SYSTEM_ADDR, EXPECTED_COUNTER_ZERO_DATA, BUS)
  assert EXPECTED_COUNTER_ZERO_DATA[0] == EXPECTED_COUNTER_ZERO_DATA[1] ^ EXPECTED_COUNTER_ZERO_DATA[2] ^ \
    EXPECTED_COUNTER_ZERO_DATA[3] ^ EXPECTED_COUNTER_ZERO_DATA[4] ^ EXPECTED_COUNTER_ZERO_DATA[5] ^ \
    EXPECTED_COUNTER_ZERO_DATA[6] ^ EXPECTED_COUNTER_ZERO_DATA[7]


def test_active_zero_counter_and_checksum_advance():
  from opendbc.can import CANPacker

  packer = CANPacker("vw_pq")
  frames = [build_active_zero_acc_system(packer)[1] for _ in range(16)]
  assert [(frame[1] & 0x0F) for frame in frames] == list(range(16))
  assert all(frame[0] == frame[1] ^ frame[2] ^ frame[3] ^ frame[4] ^ frame[5] ^ frame[6] ^ frame[7]
             for frame in frames)
