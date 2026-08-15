#!/usr/bin/env python3
"""Stationary, inactive-only ACC_System acceptance probe for the VW e-Up.

This sends only the standard Volkswagen PQ inactive acceleration message. It
does not contain an acceleration, deceleration, stop, or brake request. Panda
safety independently permits only that inactive value because this probe has
no route to longitudinal controls_allowed.
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path


# Make direct invocation work before importing another openpilot module.
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
  sys.path.insert(0, str(REPO_ROOT))

from openpilot.selfdrive.debug.car.vw_up_readonly_uds import ensure_agnos_python, running_openpilot_processes


ACC_SYSTEM_ADDR = 0x368
BREMSE_1_ADDR = 0x1A0
BREMSE_5_ADDR = 0x4A8
BUS = 0
SEND_HZ = 50
MAX_DURATION = 3.0
BASELINE_SECONDS = 2.0
MAX_STATIONARY_SPEED_KPH = 0.1
MAX_PRESSURE_RISE_BAR = 1.0
EXPECTED_INACTIVE_DATA = bytes.fromhex("f90000fe07fefe00")


@dataclass
class MonitorState:
  bremse_1_count: int = 0
  bremse_5_count: int = 0
  max_speed_kph: float = 0.0
  latest_pressure_bar: float | None = None


def decode_bremse_1_speed_kph(data: bytes) -> float:
  if len(data) != 8:
    raise ValueError("Bremse_1 must be eight bytes")
  raw = ((data[2] & 0xFE) >> 1) | (data[3] << 7)
  return raw * 0.01


def decode_bremse_5_pressure_bar(data: bytes) -> float:
  if len(data) != 8:
    raise ValueError("Bremse_5 must be eight bytes")
  raw = data[2] | ((data[3] & 0x0F) << 8)
  return raw * 0.1


def build_inactive_acc_system():
  from opendbc.can import CANPacker
  from opendbc.car.volkswagen import pqcan

  packer = CANPacker("vw_pq")
  messages = pqcan.create_acc_accel_control(
    packer, BUS, acc_type=0, acc_enabled=False, accel=0.0, acc_control=0,
    stopping=False, starting=False, esp_hold=False,
  )
  if len(messages) != 1:
    raise RuntimeError(f"expected one ACC_System message, got {len(messages)}")
  address, data, bus = messages[0]
  if (address, data, bus) != (ACC_SYSTEM_ADDR, EXPECTED_INACTIVE_DATA, BUS):
    raise RuntimeError(
      f"inactive ACC_System changed: got 0x{address:X}/{data.hex()}/bus {bus}, "
      f"expected 0x{ACC_SYSTEM_ADDR:X}/{EXPECTED_INACTIVE_DATA.hex()}/bus {BUS}"
    )
  if data[0] != (data[1] ^ data[2] ^ data[3] ^ data[4] ^ data[5] ^ data[6] ^ data[7]):
    raise RuntimeError("inactive ACC_System XOR checksum is invalid")
  return messages[0]


def write_record(output, event: str, **values) -> None:
  record = {"event": event, "monotonic_time": time.monotonic(), **values}
  output.write(json.dumps(record, sort_keys=True) + "\n")
  output.flush()


def process_rx(panda, output, state: MonitorState) -> None:
  for address, data, src in panda.can_recv():
    if src != BUS or address not in (BREMSE_1_ADDR, BREMSE_5_ADDR):
      continue
    if address == BREMSE_1_ADDR:
      speed_kph = decode_bremse_1_speed_kph(data)
      state.bremse_1_count += 1
      state.max_speed_kph = max(state.max_speed_kph, speed_kph)
      write_record(output, "rx_bremse_1", speed_kph=speed_kph, value_hex=data.hex())
    else:
      pressure_bar = decode_bremse_5_pressure_bar(data)
      state.bremse_5_count += 1
      state.latest_pressure_bar = pressure_bar
      write_record(output, "rx_bremse_5", pressure_bar=pressure_bar, value_hex=data.hex())


def assert_controls_off(health: dict) -> None:
  if health["controls_allowed"] or health["controls_allowed_longitudinal"]:
    raise RuntimeError("Panda unexpectedly reports longitudinal controls allowed")


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--parked-confirmed", action="store_true", required=True)
  parser.add_argument("--inactive-only-confirmed", action="store_true", required=True)
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

  from opendbc.car.structs import CarParams
  from opendbc.car.volkswagen.values import VolkswagenSafetyFlags
  from panda import Panda

  address, data, bus = build_inactive_acc_system()
  safety_param = int(VolkswagenSafetyFlags.PQ_UP | VolkswagenSafetyFlags.LONG_CONTROL)
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
    write_record(output, "probe_start", duration=args.duration, address=f"0x{address:X}",
                 value_hex=data.hex(), safety_health=health)

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
    if not baseline_pressures:
      raise RuntimeError("no Bremse_5 pressure baseline")
    baseline_pressure_bar = sum(baseline_pressures) / len(baseline_pressures)
    health = panda.health()
    assert_controls_off(health)
    if health["safety_rx_checks_invalid"]:
      raise RuntimeError("Panda RX safety checks are invalid; refusing to transmit")
    write_record(output, "baseline_complete", pressure_bar=baseline_pressure_bar,
                 bremse_1_count=state.bremse_1_count, bremse_5_count=state.bremse_5_count,
                 safety_health=health)

    start = time.monotonic()
    next_send = start
    next_health = start
    while time.monotonic() - start < args.duration:
      now = time.monotonic()
      process_rx(panda, output, state)
      if state.max_speed_kph > MAX_STATIONARY_SPEED_KPH:
        raise RuntimeError(f"abort: vehicle speed rose to {state.max_speed_kph:.2f} km/h")
      if state.latest_pressure_bar is not None and state.latest_pressure_bar > baseline_pressure_bar + MAX_PRESSURE_RISE_BAR:
        raise RuntimeError(
          f"abort: brake pressure rose from {baseline_pressure_bar:.2f} to {state.latest_pressure_bar:.2f} bar"
        )
      if now >= next_health:
        health = panda.health()
        assert_controls_off(health)
        write_record(output, "safety_health", safety_health=health)
        next_health += 0.1
      if now >= next_send:
        panda.can_send(address, data, bus)
        sent += 1
        write_record(output, "tx_inactive_acc_system", sequence=sent, value_hex=data.hex())
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
                 safety_health=final_health)
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
