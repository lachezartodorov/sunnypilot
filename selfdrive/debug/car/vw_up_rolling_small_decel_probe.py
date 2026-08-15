#!/usr/bin/env python3
"""Auto-triggered rolling -0.20 m/s2 ACC_System probe for VW e-Up."""

import argparse
import json
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
  sys.path.insert(0, str(REPO_ROOT))

from openpilot.selfdrive.debug.car.vw_up_inactive_acc_probe import (
  BUS,
  MonitorState,
  assert_controls_off,
  process_rx,
  write_record,
)
from openpilot.selfdrive.debug.car.vw_up_readonly_uds import ensure_agnos_python, running_openpilot_processes
from openpilot.selfdrive.debug.car.vw_up_small_decel_probe import MAX_DURATION, build_active_small_decel_acc_system


SEND_HZ = 50
DEFAULT_PROBE_DURATION = 0.1
POST_MONITOR_SECONDS = 0.5
ARM_TIMEOUT_SECONDS = 60.0
READY_SECONDS = 0.5
MIN_TRIGGER_SPEED_KPH = 2.0
MAX_TRIGGER_SPEED_KPH = 7.0
MAX_ABORT_SPEED_KPH = 10.0
MAX_RELEASED_PRESSURE_BAR = 1.0
MAX_PRESSURE_RISE_BAR = 3.0


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--safe-area-confirmed", action="store_true", required=True)
  parser.add_argument("--driver-ready-confirmed", action="store_true", required=True)
  parser.add_argument("--rolling-small-decel-confirmed", action="store_true", required=True)
  parser.add_argument("--duration", type=float, default=DEFAULT_PROBE_DURATION)
  parser.add_argument("--output", type=Path, required=True)
  return parser.parse_args()


def ready_to_trigger(state: MonitorState) -> bool:
  return state.latest_speed_kph is not None and MIN_TRIGGER_SPEED_KPH <= state.latest_speed_kph <= MAX_TRIGGER_SPEED_KPH and \
    state.latest_pressure_bar is not None and state.latest_pressure_bar <= MAX_RELEASED_PRESSURE_BAR and \
    state.latest_brake_pressed is False and state.latest_gas_raw == 0 and state.latest_acc_main_on is True


def main() -> int:
  ensure_agnos_python()
  args = parse_args()
  if args.duration <= 0 or args.duration > MAX_DURATION:
    raise SystemExit(f"--duration must be greater than zero and no more than {MAX_DURATION:.1f} seconds")
  active = running_openpilot_processes()
  if active:
    details = "\n  ".join(active)
    raise SystemExit(f"Refusing while openpilot processes are running:\n  {details}")

  from opendbc.can import CANPacker
  from opendbc.car.structs import CarParams
  from opendbc.car.volkswagen.values import VolkswagenSafetyFlags
  from panda import Panda

  packer = CANPacker("vw_pq")
  safety_param = int(VolkswagenSafetyFlags.PQ_UP | VolkswagenSafetyFlags.LONG_CONTROL |
                     VolkswagenSafetyFlags.PQ_UP_SMALL_DECEL_PROBE)
  output = args.output.open("x", encoding="utf-8")
  panda = Panda()
  state = MonitorState()
  sent = 0
  initial_tx_blocked = 0

  try:
    panda.set_safety_mode(CarParams.SafetyModel.volkswagenPq, safety_param)
    panda.send_heartbeat(engaged=False, engaged_mads=False)
    health = panda.health()
    assert_controls_off(health)
    if health["safety_mode"] != CarParams.SafetyModel.volkswagenPq or health["safety_param"] != safety_param:
      raise RuntimeError(f"Panda did not enter expected safety mode/param: {health}")
    initial_tx_blocked = health["safety_tx_blocked"]
    write_record(output, "probe_armed", trigger_speed_kph=[MIN_TRIGGER_SPEED_KPH, MAX_TRIGGER_SPEED_KPH],
                 duration=args.duration, safety_health=health, can_health=panda.can_health(BUS))
    print(json.dumps({"status": "armed", "speed_window_kph": [MIN_TRIGGER_SPEED_KPH, MAX_TRIGGER_SPEED_KPH],
                      "duration": args.duration}), flush=True)

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
      raise RuntimeError("arming timeout: no stable released-pedal 2-7 km/h window")

    if not baseline_pressures or state.latest_speed_kph is None:
      raise RuntimeError("missing rolling baseline")
    baseline_pressure_bar = sum(baseline_pressures) / len(baseline_pressures)
    trigger_speed_kph = state.latest_speed_kph
    state.max_pressure_bar = state.latest_pressure_bar
    write_record(output, "probe_triggered", speed_kph=trigger_speed_kph,
                 baseline_pressure_bar=baseline_pressure_bar, gas_raw=state.latest_gas_raw,
                 brake_pressed=state.latest_brake_pressed, acc_main_on=state.latest_acc_main_on)
    print(json.dumps({"status": "triggered", "speed_kph": trigger_speed_kph,
                      "baseline_pressure_bar": baseline_pressure_bar}), flush=True)

    start = time.monotonic()
    next_send = start
    while time.monotonic() - start < args.duration:
      now = time.monotonic()
      process_rx(panda, output, state)
      if state.latest_speed_kph is None or state.latest_speed_kph > MAX_ABORT_SPEED_KPH:
        raise RuntimeError(f"abort: unsafe speed {state.latest_speed_kph}")
      if state.latest_pressure_bar is not None and state.latest_pressure_bar > baseline_pressure_bar + MAX_PRESSURE_RISE_BAR:
        raise RuntimeError(
          f"abort: brake pressure rose from {baseline_pressure_bar:.2f} to {state.latest_pressure_bar:.2f} bar"
        )
      if state.latest_brake_pressed or (state.latest_gas_raw is not None and state.latest_gas_raw != 0):
        raise RuntimeError("abort: driver pedal input detected")
      if now >= next_send:
        address, data, bus = build_active_small_decel_acc_system(packer)
        panda.can_send(address, data, bus)
        sent += 1
        write_record(output, "tx_active_small_decel_acc_system", sequence=sent, value_hex=data.hex())
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
    write_record(output, "probe_complete", sent=sent, trigger_speed_kph=trigger_speed_kph,
                 final_speed_kph=state.latest_speed_kph, baseline_pressure_bar=baseline_pressure_bar,
                 latest_pressure_bar=state.latest_pressure_bar, max_pressure_bar=state.max_pressure_bar,
                 safety_health=final_health, can_health=panda.can_health(BUS))
    print(json.dumps({
      "status": "complete", "sent": sent, "trigger_speed_kph": trigger_speed_kph,
      "final_speed_kph": state.latest_speed_kph, "baseline_pressure_bar": baseline_pressure_bar,
      "latest_pressure_bar": state.latest_pressure_bar, "max_pressure_bar": state.max_pressure_bar,
      "safety_tx_blocked_delta": blocked_delta,
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
