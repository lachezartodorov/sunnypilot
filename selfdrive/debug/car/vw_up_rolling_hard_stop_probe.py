#!/usr/bin/env python3
"""Auto-triggered, bounded Follow-to-Stop -3.0 m/s2 probe for VW e-Up."""

import argparse
import json
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
  sys.path.insert(0, str(REPO_ROOT))

from openpilot.selfdrive.debug.car.vw_up_inactive_acc_probe import (
  ACC_SYSTEM_ADDR,
  BUS,
  MonitorState,
  assert_controls_off,
  process_rx,
  write_record,
)
from openpilot.selfdrive.debug.car.vw_up_readonly_uds import ensure_agnos_python, running_openpilot_processes


SEND_HZ = 50
MAX_DURATION = 1.5
POST_MONITOR_SECONDS = 0.5
ARM_TIMEOUT_SECONDS = 60.0
READY_SECONDS = 0.5
MIN_TRIGGER_SPEED_KPH = 3.0
MAX_TRIGGER_SPEED_KPH = 8.0
MAX_ABORT_SPEED_KPH = 10.0
STOP_SPEED_KPH = 0.5
MIN_STOP_DETECTION_SECONDS = 0.15
MAX_RELEASED_PRESSURE_BAR = 1.0
MAX_PRESSURE_BAR = 80.0
EXPECTED_COUNTER_ZERO_DATA = bytes.fromhex("2810894c43289600")


def build_active_hard_stop_acc_system(packer=None):
  from opendbc.can import CANPacker
  from opendbc.car.volkswagen import pqcan

  packer = CANPacker("vw_pq") if packer is None else packer
  messages = pqcan.create_acc_accel_control(
    packer, BUS, acc_type=1, acc_enabled=True, accel=-3.0, acc_control=1,
    stopping=True, starting=False, esp_hold=False,
  )
  if len(messages) != 1:
    raise RuntimeError(f"expected one ACC_System message, got {len(messages)}")
  address, data, bus = messages[0]
  if address != ACC_SYSTEM_ADDR or bus != BUS:
    raise RuntimeError(f"unexpected hard-stop target: 0x{address:X}/bus {bus}")
  if (data[1] & 0xF0) != 0x10 or data[2:] != EXPECTED_COUNTER_ZERO_DATA[2:]:
    raise RuntimeError(f"hard-stop fields changed: {data.hex()}")
  if data[0] != (data[1] ^ data[2] ^ data[3] ^ data[4] ^ data[5] ^ data[6] ^ data[7]):
    raise RuntimeError(f"hard-stop checksum is invalid: {data.hex()}")
  return messages[0]


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--safe-area-confirmed", action="store_true", required=True)
  parser.add_argument("--driver-ready-confirmed", action="store_true", required=True)
  parser.add_argument("--hard-stop-confirmed", action="store_true", required=True)
  parser.add_argument("--output", type=Path, required=True)
  return parser.parse_args()


def ready_to_trigger(state: MonitorState) -> bool:
  return state.latest_speed_kph is not None and MIN_TRIGGER_SPEED_KPH <= state.latest_speed_kph <= MAX_TRIGGER_SPEED_KPH and \
    state.latest_pressure_bar is not None and state.latest_pressure_bar <= MAX_RELEASED_PRESSURE_BAR and \
    state.latest_brake_pressed is False and state.latest_gas_raw == 0 and state.latest_acc_main_on is True


def main() -> int:
  ensure_agnos_python()
  args = parse_args()
  active = running_openpilot_processes()
  if active:
    details = "\n  ".join(active)
    raise SystemExit(f"Refusing while openpilot processes are running:\n  {details}")

  from opendbc.can import CANPacker
  from opendbc.car.structs import CarParams
  from opendbc.car.volkswagen.values import VolkswagenSafetyFlags
  from panda import Panda

  packer = CANPacker("vw_pq")
  first_address, first_data, _ = build_active_hard_stop_acc_system(packer)
  if first_data != EXPECTED_COUNTER_ZERO_DATA:
    raise RuntimeError(f"counter-zero hard-stop frame changed: {first_data.hex()}")

  safety_param = int(VolkswagenSafetyFlags.PQ_UP | VolkswagenSafetyFlags.LONG_CONTROL |
                     VolkswagenSafetyFlags.PQ_UP_HARD_STOP_PROBE)
  output = args.output.open("x", encoding="utf-8")
  panda = Panda()
  state = MonitorState()
  sent = 0
  initial_tx_blocked = 0
  stopped = False

  try:
    panda.set_safety_mode(CarParams.SafetyModel.volkswagenPq, safety_param)
    panda.send_heartbeat(engaged=False, engaged_mads=False)
    health = panda.health()
    assert_controls_off(health)
    if health["safety_mode"] != CarParams.SafetyModel.volkswagenPq or health["safety_param"] != safety_param:
      raise RuntimeError(f"Panda did not enter expected safety mode/param: {health}")
    initial_tx_blocked = health["safety_tx_blocked"]
    write_record(output, "probe_armed", address=f"0x{first_address:X}", first_value_hex=first_data.hex(),
                 trigger_speed_kph=[MIN_TRIGGER_SPEED_KPH, MAX_TRIGGER_SPEED_KPH], duration=MAX_DURATION,
                 requested_accel_mps2=-3.0, safety_health=health, can_health=panda.can_health(BUS))
    print(json.dumps({"status": "armed", "speed_window_kph": [MIN_TRIGGER_SPEED_KPH, MAX_TRIGGER_SPEED_KPH],
                      "duration": MAX_DURATION, "requested_accel_mps2": -3.0}), flush=True)

    arm_deadline = time.monotonic() + ARM_TIMEOUT_SECONDS
    ready_since = None
    baseline_pressures: list[float] = []
    while time.monotonic() < arm_deadline:
      process_rx(panda, output, state)
      health = panda.health()
      assert_controls_off(health)
      if health["safety_rx_checks_invalid"]:
        raise RuntimeError("Panda RX safety checks became invalid while armed")
      if state.latest_speed_kph is not None and state.latest_speed_kph > MAX_ABORT_SPEED_KPH:
        raise RuntimeError(f"abort while armed: speed rose to {state.latest_speed_kph:.2f} km/h")
      if ready_to_trigger(state):
        if ready_since is None:
          ready_since = time.monotonic()
          baseline_pressures.clear()
        if state.latest_pressure_bar is not None:
          baseline_pressures.append(state.latest_pressure_bar)
        if time.monotonic() - ready_since >= READY_SECONDS:
          break
      else:
        ready_since = None
        baseline_pressures.clear()
      panda.send_heartbeat(engaged=False, engaged_mads=False)
      time.sleep(0.002)
    else:
      raise RuntimeError("arming timeout: no stable released-pedal 3-8 km/h window")

    if not baseline_pressures or state.latest_speed_kph is None:
      raise RuntimeError("missing rolling baseline")
    baseline_pressure_bar = sum(baseline_pressures) / len(baseline_pressures)
    trigger_speed_kph = state.latest_speed_kph
    minimum_speed_kph = trigger_speed_kph
    state.max_pressure_bar = state.latest_pressure_bar
    write_record(output, "probe_triggered", speed_kph=trigger_speed_kph,
                 baseline_pressure_bar=baseline_pressure_bar, gas_raw=state.latest_gas_raw,
                 brake_pressed=state.latest_brake_pressed, acc_main_on=state.latest_acc_main_on)
    print(json.dumps({"status": "triggered", "speed_kph": trigger_speed_kph,
                      "baseline_pressure_bar": baseline_pressure_bar}), flush=True)

    start = time.monotonic()
    next_send = start
    while time.monotonic() - start < MAX_DURATION:
      now = time.monotonic()
      process_rx(panda, output, state)
      if state.latest_speed_kph is None or state.latest_speed_kph > MAX_ABORT_SPEED_KPH:
        raise RuntimeError(f"abort: unsafe speed {state.latest_speed_kph}")
      minimum_speed_kph = min(minimum_speed_kph, state.latest_speed_kph)
      if state.latest_pressure_bar is not None and state.latest_pressure_bar > MAX_PRESSURE_BAR:
        raise RuntimeError(f"abort: brake pressure rose to {state.latest_pressure_bar:.2f} bar")
      if state.latest_brake_pressed or (state.latest_gas_raw is not None and state.latest_gas_raw != 0):
        raise RuntimeError("abort: driver pedal input detected")
      if state.latest_acc_main_on is not True:
        raise RuntimeError("abort: cruise main switched off")
      if now - start >= MIN_STOP_DETECTION_SECONDS and state.latest_speed_kph <= STOP_SPEED_KPH:
        stopped = True
        break
      health = panda.health()
      assert_controls_off(health)
      if health["safety_rx_checks_invalid"]:
        raise RuntimeError("abort: Panda RX safety checks became invalid")
      if now >= next_send:
        address, data, bus = build_active_hard_stop_acc_system(packer)
        panda.can_send(address, data, bus)
        sent += 1
        write_record(output, "tx_active_hard_stop_acc_system", sequence=sent, value_hex=data.hex())
        next_send += 1.0 / SEND_HZ
      panda.send_heartbeat(engaged=False, engaged_mads=False)
      time.sleep(0.001)

    post_deadline = time.monotonic() + POST_MONITOR_SECONDS
    while time.monotonic() < post_deadline:
      process_rx(panda, output, state)
      panda.send_heartbeat(engaged=False, engaged_mads=False)
      time.sleep(0.002)

    final_health = panda.health()
    assert_controls_off(final_health)
    blocked_delta = final_health["safety_tx_blocked"] - initial_tx_blocked
    if blocked_delta:
      raise RuntimeError(f"Panda blocked {blocked_delta} transmit attempts")
    write_record(output, "probe_complete", sent=sent, stopped=stopped, trigger_speed_kph=trigger_speed_kph,
                 minimum_speed_kph=minimum_speed_kph, final_speed_kph=state.latest_speed_kph,
                 baseline_pressure_bar=baseline_pressure_bar, latest_pressure_bar=state.latest_pressure_bar,
                 max_pressure_bar=state.max_pressure_bar, safety_health=final_health, can_health=panda.can_health(BUS))
    print(json.dumps({
      "status": "complete", "sent": sent, "stopped": stopped, "trigger_speed_kph": trigger_speed_kph,
      "minimum_speed_kph": minimum_speed_kph, "final_speed_kph": state.latest_speed_kph,
      "baseline_pressure_bar": baseline_pressure_bar, "latest_pressure_bar": state.latest_pressure_bar,
      "max_pressure_bar": state.max_pressure_bar, "safety_tx_blocked_delta": blocked_delta,
    }, sort_keys=True), flush=True)
    return 0
  except Exception as e:
    write_record(output, "probe_aborted", error=str(e), sent=sent, speed_kph=state.latest_speed_kph,
                 pressure_bar=state.latest_pressure_bar, max_pressure_bar=state.max_pressure_bar)
    raise
  finally:
    passthrough_param = int(VolkswagenSafetyFlags.PQ_UP | VolkswagenSafetyFlags.PQ_UP_DIAG_PASSTHROUGH)
    panda.set_safety_mode(CarParams.SafetyModel.volkswagenPq, passthrough_param)
    panda.send_heartbeat(engaged=False, engaged_mads=False)
    output.close()


if __name__ == "__main__":
  raise SystemExit(main())
