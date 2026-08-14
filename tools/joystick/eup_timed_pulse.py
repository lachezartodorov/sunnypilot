#!/usr/bin/env python3
import argparse
import threading
import time

from cereal import car, messaging
from opendbc.car.volkswagen.values import CAR
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.tools.joystick.joystickd import EUP_TEST_STEER_SCALE
from openpilot.tools.lib.kbhit import KBHit


MIN_SPEED_KPH = 3.0
MAX_SPEED_KPH = 8.0
PUBLISH_HZ = 10
UNHEALTHY_EVENTS = {
  "selfdrivedLagging", "commIssue", "commIssueAvgFreq", "controlsMismatch",
  "canError", "canBusMissing", "usbError", "processNotRunning",
}


class PulseState:
  def __init__(self):
    self.requested = False
    self.pulse_until = 0.0
    self.quit = False


def read_args():
  parser = argparse.ArgumentParser(description="Gated, timed steering pulse for Volkswagen e-Up")
  parser.add_argument("--torque-percent", type=float, default=10.0,
                      help="percent of the normal 3 Nm steering limit (default: 10, maximum: 80)")
  parser.add_argument("--duration", type=float, default=1.5,
                      help="nonzero joystick pulse duration in seconds (default: 1.5, range: 0.5-2.0)")
  args = parser.parse_args()
  max_percent = EUP_TEST_STEER_SCALE * 100.0
  if not 1.0 <= args.torque_percent <= max_percent:
    parser.error(f"--torque-percent must be between 1 and {max_percent:g}")
  if not 0.5 <= args.duration <= 2.0:
    parser.error("--duration must be between 0.5 and 2.0 seconds")
  return args


def send_joystick(pm, axis):
  msg = messaging.new_message("testJoystick")
  msg.valid = True
  msg.testJoystick.axes = [0.0, axis]
  pm.send("testJoystick", msg)


def publisher(state, steer_axis, duration):
  pm = messaging.PubMaster(["testJoystick"])
  rk = Ratekeeper(PUBLISH_HZ, print_delay_threshold=None)
  last_axis = None

  while not state.quit:
    now = time.monotonic()
    if state.requested:
      state.requested = False
      services = ["carState", "selfdriveStateSP", "pandaStates", "onroadEvents"]
      sm = messaging.SubMaster(services)
      seen = set()
      health_faults = set()
      snapshot_deadline = time.monotonic() + 1.0
      while time.monotonic() < snapshot_deadline and not state.quit:
        sm.update(100)
        seen.update(service for service in services if sm.updated[service])
        if sm.updated["onroadEvents"]:
          health_faults.update(str(event.name) for event in sm["onroadEvents"]
                               if str(event.name) in UNHEALTHY_EVENTS)

      fresh = len(seen) == len(services) and len(sm["pandaStates"]) > 0
      cs = sm["carState"]
      panda = sm["pandaStates"][0] if len(sm["pandaStates"]) else None
      speed_kph = cs.vEgo * 3.6
      gates = {
        "fresh": fresh,
        "health": not health_faults,
        "mads": sm["selfdriveStateSP"].mads.active,
        "drive": str(cs.gearShifter) == "drive",
        "speed": MIN_SPEED_KPH <= speed_kph <= MAX_SPEED_KPH,
        "brake": not cs.brakePressed,
        "steering": not cs.steerFaultTemporary and not cs.steerFaultPermanent,
        "panda": panda is not None and panda.controlsAllowedLateral and not panda.safetyRxChecksInvalid,
      }
      if all(gates.values()):
        now = time.monotonic()
        state.pulse_until = now + duration
        print(f"PULSE START: axis={steer_axis:.4f}, duration={duration}s, speed={speed_kph:.2f} km/h", flush=True)
      else:
        print(f"PULSE REFUSED: speed={speed_kph:.2f} km/h, gates={gates}, "
              f"health_faults={sorted(health_faults)}", flush=True)

    axis = steer_axis if now < state.pulse_until else 0.0
    if axis != last_axis:
      print(f"steer={axis}", flush=True)

    # Stay silent while idle, publish continuously only during the pulse, then
    # send one explicit zero. joystickd also resets input after 0.2 seconds.
    if axis != 0.0 or (last_axis not in (None, 0.0) and axis == 0.0):
      send_joystick(pm, axis)
    last_axis = axis
    rk.keep_time()

  send_joystick(pm, 0.0)


def main():
  args = read_args()
  params = Params()
  if not params.get_bool("IsOffroad"):
    raise SystemExit("Start this tool while offroad")

  cp_raw = params.get("CarParamsPersistent") or params.get("CarParams")
  if cp_raw is None:
    raise SystemExit("CarParams unavailable; connect to the e-Up once before starting this tool")
  CP = messaging.log_from_bytes(cp_raw, car.CarParams)
  if CP.carFingerprint != CAR.VOLKSWAGEN_UP_MK1:
    raise SystemExit(f"This tool only supports {CAR.VOLKSWAGEN_UP_MK1}; detected {CP.carFingerprint}")

  requested_fraction = args.torque_percent / 100.0
  steer_axis = requested_fraction / EUP_TEST_STEER_SCALE
  expected_nm = requested_fraction * 3.0
  print(f"e-Up timed pulse: {args.torque_percent:g}% (~{expected_nm:.2f} Nm), {args.duration:g}s", flush=True)

  state = PulseState()
  params.put_bool("JoystickDebugMode", True, block=True)
  thread = threading.Thread(target=publisher, args=(state, steer_axis, args.duration), daemon=True)
  thread.start()

  print("Timed pulse ready: P=trigger, Q=quit", flush=True)
  keyboard = KBHit()
  try:
    while not state.quit:
      key = keyboard.getch().lower()
      if key == "p":
        state.requested = True
      elif key == "q":
        state.quit = True
  finally:
    state.quit = True
    thread.join(timeout=1.0)
    params.put_bool("JoystickDebugMode", False, block=True)


if __name__ == "__main__":
  main()
