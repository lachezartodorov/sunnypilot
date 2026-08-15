from openpilot.selfdrive.debug.car.vw_up_inactive_acc_probe import MonitorState
from openpilot.selfdrive.debug.car.vw_up_rolling_small_decel_probe import ready_to_trigger


def test_ready_to_trigger_requires_exact_safe_state():
  state = MonitorState(latest_speed_kph=4.0, latest_pressure_bar=0.3, latest_brake_pressed=False,
                       latest_gas_raw=0, latest_acc_main_on=True)
  assert ready_to_trigger(state)

  for field, value in (
    ("latest_speed_kph", 1.9),
    ("latest_speed_kph", 7.1),
    ("latest_pressure_bar", 1.1),
    ("latest_brake_pressed", True),
    ("latest_gas_raw", 1),
    ("latest_acc_main_on", False),
  ):
    changed = MonitorState(**{**state.__dict__, field: value})
    assert not ready_to_trigger(changed)
