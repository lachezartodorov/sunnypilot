from opendbc.can import CANPacker

from openpilot.selfdrive.debug.car.vw_up_inactive_acc_probe import ACC_SYSTEM_ADDR, BUS, MonitorState
from openpilot.selfdrive.debug.car.vw_up_rolling_hard_stop_probe import (
  EXPECTED_COUNTER_ZERO_DATA,
  build_active_hard_stop_acc_system,
  ready_to_trigger,
)


def test_active_hard_stop_acc_system_is_frozen():
  assert build_active_hard_stop_acc_system() == (ACC_SYSTEM_ADDR, EXPECTED_COUNTER_ZERO_DATA, BUS)


def test_active_hard_stop_counter_and_checksum_advance():
  packer = CANPacker("vw_pq")
  frames = [build_active_hard_stop_acc_system(packer)[1] for _ in range(16)]
  assert [(frame[1] & 0x0F) for frame in frames] == list(range(16))
  for frame in frames:
    assert frame[0] == frame[1] ^ frame[2] ^ frame[3] ^ frame[4] ^ frame[5] ^ frame[6] ^ frame[7]


def test_hard_stop_trigger_requires_all_guard_conditions():
  state = MonitorState(latest_speed_kph=5.0, latest_pressure_bar=0.3, latest_brake_pressed=False,
                       latest_gas_raw=0, latest_acc_main_on=True)
  assert ready_to_trigger(state)
  state.latest_speed_kph = 8.1
  assert not ready_to_trigger(state)
  state.latest_speed_kph = 5.0
  state.latest_brake_pressed = True
  assert not ready_to_trigger(state)
