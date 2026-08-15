#!/usr/bin/env python3
"""Stationary, bounded active-state/zero-acceleration probe for VW e-Up."""

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
  BASELINE_SECONDS,
  MAX_STATIONARY_SPEED_KPH,
  MonitorState,
  assert_controls_off,
  process_rx,
  write_record,
)
from openpilot.selfdrive.debug.car.vw_up_readonly_uds import ensure_agnos_python, running_openpilot_processes


SEND_HZ = 50
MAX_DURATION = 0.5
MAX_PRESSURE_RISE_BAR = 0.5
MAX_RELEASED_BASELINE_BAR = 1.0
EXPECTED_COUNTER_ZERO_DATA = bytes.fromhex("ae3081a405289600")


def build_active_zero_acc_system(packer=None):
  from opendbc.can import CANPacker
  from opendbc.car.volkswagen import pqcan

  packer = CANPacker("vw_pq") if packer is None else packer
  messages = pqcan.create_acc_accel_control(
    packer, BUS, acc_type=0, acc_enabled=True, accel=0.0, acc_control=3,
    stopping=False, starting=False, esp_hold=False,
  )
  if len(messages) != 1:
    raise RuntimeError(f"expected one ACC_System message, got {len(messages)}")
  address, data, bus = messages[0]
  if address != ACC_SYSTEM_ADDR or bus != BUS:
    raise RuntimeError(f"unexpected active-zero target: 0x{address:X}/bus {bus}")
  if (data[1] & 0xF0) != 0x30 or data[2:] != EXPECTED_COUNTER_ZERO_DATA[2:]:
    raise RuntimeError(f"active-zero fields changed: {data.hex()}")
  if data[0] != (data[1] ^ data[2] ^ data[3] ^ data[4] ^ data[5] ^ data[6] ^ data[7]):
    raise RuntimeError(f"active-zero checksum is invalid: {data.hex()}")
  return messages[0]


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--parked-confirmed", action="store_true", required=True)
  parser.add_argument("--safe-area-confirmed", action="store_true", required=True)
  parser.add_argument("--active-zero-confirmed", action="store_true", required=True)
  parser.add_argument("--duration", type=float, default=MAX_DURATION)
  parser.add_argument("--output", type=Path, required=True)
  return parser.parse_args()


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
  first_address, first_data, first_bus = build_active_zero_acc_system(packer)
  if first_data != EXPECTED_COUNTER_ZERO_DATA:
    raise RuntimeError(f"counter-zero active frame changed: {first_data.hex()}")

  safety_param = int(VolkswagenSafetyFlags.PQ_UP | VolkswagenSafetyFlags.LONG_CONTROL |
                     VolkswagenSafetyFlags.PQ_UP_ZERO_ACCEL_PROBE)
  output = args.output.open("x", encoding="utf-8")
  panda = Panda()
  state = MonitorState()
  baseline_pressures: list[float] = []
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
    write_record(output, "probe_start", duration=args.duration, address=f"0x{first_address:X}",
                 first_value_hex=first_data.hex(), safety_health=health, can_health=panda.can_health(BUS))

    baseline_deadline = time.monotonic() + BASELINE_SECONDS
    while time.monotonic() < baseline_deadline:
      process_rx(panda, output, state)
      if state.latest_pressure_bar is not None:
        baseline_pressures.append(state.latest_pressure_bar)
      panda.send_heartbeat(engaged=False, engaged_mads=False)
      time.sleep(0.01)

    if state.bremse_1_count < 50 or state.bremse_5_count < 20:
      raise RuntimeError(
        f"insufficient brake CAN baseline: Bremse_1={state.bremse_1_count}, Bremse_5={state.bremse_5_count}"
      )
    if state.max_speed_kph > MAX_STATIONARY_SPEED_KPH:
      raise RuntimeError(f"vehicle is not stationary: observed {state.max_speed_kph:.2f} km/h")
    baseline_pressure_bar = sum(baseline_pressures) / len(baseline_pressures)
    if baseline_pressure_bar > MAX_RELEASED_BASELINE_BAR:
      raise RuntimeError(f"service brake does not appear released: baseline {baseline_pressure_bar:.2f} bar")
    health = panda.health()
    assert_controls_off(health)
    if health["safety_rx_checks_invalid"]:
      raise RuntimeError("Panda RX safety checks are invalid; refusing to transmit")
    write_record(output, "baseline_complete", pressure_bar=baseline_pressure_bar,
                 bremse_1_count=state.bremse_1_count, bremse_5_count=state.bremse_5_count,
                 safety_health=health, can_health=panda.can_health(BUS))

    start = time.monotonic()
    next_send = start
    while time.monotonic() - start < args.duration:
      now = time.monotonic()
      process_rx(panda, output, state)
      if state.max_speed_kph > MAX_STATIONARY_SPEED_KPH:
        raise RuntimeError(f"abort: vehicle speed rose to {state.max_speed_kph:.2f} km/h")
      if state.latest_pressure_bar is not None and state.latest_pressure_bar > baseline_pressure_bar + MAX_PRESSURE_RISE_BAR:
        raise RuntimeError(
          f"abort: brake pressure rose from {baseline_pressure_bar:.2f} to {state.latest_pressure_bar:.2f} bar"
        )
      health = panda.health()
      assert_controls_off(health)
      if now >= next_send:
        address, data, bus = build_active_zero_acc_system(packer)
        panda.can_send(address, data, bus)
        sent += 1
        write_record(output, "tx_active_zero_acc_system", sequence=sent, value_hex=data.hex())
        next_send += 1.0 / SEND_HZ
      panda.send_heartbeat(engaged=False, engaged_mads=False)
      time.sleep(0.001)

    process_rx(panda, output, state)
    final_health = panda.health()
    assert_controls_off(final_health)
    blocked_delta = final_health["safety_tx_blocked"] - initial_tx_blocked
    if blocked_delta:
      raise RuntimeError(f"Panda blocked {blocked_delta} transmit attempts")
    write_record(output, "probe_complete", sent=sent, max_speed_kph=state.max_speed_kph,
                 baseline_pressure_bar=baseline_pressure_bar, latest_pressure_bar=state.latest_pressure_bar,
                 safety_health=final_health, can_health=panda.can_health(BUS))
    print(json.dumps({
      "status": "complete", "sent": sent, "max_speed_kph": state.max_speed_kph,
      "baseline_pressure_bar": baseline_pressure_bar, "latest_pressure_bar": state.latest_pressure_bar,
      "safety_tx_blocked_delta": blocked_delta,
    }, sort_keys=True))
    return 0
  except Exception as e:
    write_record(output, "probe_aborted", error=str(e), sent=sent)
    raise
  finally:
    panda.set_safety_mode(CarParams.SafetyModel.noOutput)
    panda.send_heartbeat(engaged=False, engaged_mads=False)
    output.close()


if __name__ == "__main__":
  raise SystemExit(main())
