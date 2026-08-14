#!/usr/bin/env python3
import argparse
import time

from cereal import car, messaging
from opendbc.car.volkswagen.values import CAR
from openpilot.common.params import Params
from openpilot.tools.joystick.joystickd import EUP_TEST_STEER_SCALE
from openpilot.tools.lib.kbhit import KBHit


MIN_SPEED_KPH = 3.0
MAX_SPEED_KPH = 20.0
PUBLISH_PERIOD = 0.1
MAX_INPUT_AGE = 0.35
UNHEALTHY_EVENTS = {
  "commIssue", "commIssueAvgFreq", "controlsMismatch", "canError",
  "canBusMissing", "usbError", "processNotRunning",
}
ADVISORY_EVENTS = {"selfdrivedLagging"}
SERVICES = ["carState", "selfdriveStateSP", "pandaStates", "onroadEvents"]


def read_args():
  parser = argparse.ArgumentParser(description="Gated WASD steering control for Volkswagen e-Up")
  parser.add_argument("--torque-percent", type=float, default=10.0,
                      help="percent of the normal 3 Nm steering limit (default: 10, maximum: 80)")
  parser.add_argument("--deadman", type=float, default=0.35,
                      help="seconds without an A/D key repeat before steering returns to zero (default: 0.35)")
  args = parser.parse_args()
  max_percent = EUP_TEST_STEER_SCALE * 100.0
  if not 1.0 <= args.torque_percent <= max_percent:
    parser.error(f"--torque-percent must be between 1 and {max_percent:g}")
  if not 0.2 <= args.deadman <= 0.5:
    parser.error("--deadman must be between 0.2 and 0.5 seconds")
  return args


def send_joystick(pm, axis):
  msg = messaging.new_message("testJoystick")
  msg.valid = True
  msg.testJoystick.axes = [0.0, axis]
  pm.send("testJoystick", msg)


def snapshot_gate():
  sm = messaging.SubMaster(SERVICES)
  seen = set()
  health_faults = set()
  advisories = set()
  deadline = time.monotonic() + 1.0
  while time.monotonic() < deadline:
    sm.update(100)
    seen.update(service for service in SERVICES if sm.updated[service])
    if sm.updated["onroadEvents"]:
      health_faults.update(str(event.name) for event in sm["onroadEvents"]
                           if str(event.name) in UNHEALTHY_EVENTS)
      advisories.update(str(event.name) for event in sm["onroadEvents"]
                        if str(event.name) in ADVISORY_EVENTS)
  return sm, seen, health_faults, advisories


def current_gates(sm, seen, last_update, health_faults):
  now = time.monotonic()
  cs = sm["carState"]
  panda = sm["pandaStates"][0] if len(sm["pandaStates"]) else None
  speed_kph = cs.vEgo * 3.6
  fresh = len(seen) == len(SERVICES) and all(now - last_update[s] <= MAX_INPUT_AGE for s in SERVICES)
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
  return gates, speed_kph


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
  print(f"e-Up WASD steering: {args.torque_percent:g}% (~{expected_nm:.2f} Nm), "
        f"dead-man {args.deadman:g}s", flush=True)
  print("E=arm  A=left  D=right  SPACE=stop/disarm  Q=quit", flush=True)
  print("W/S are disabled: this e-Up configuration has no openpilot longitudinal control.", flush=True)

  pm = messaging.PubMaster(["testJoystick"])
  keyboard = KBHit()
  armed = False
  sm = None
  seen = set()
  last_update = {service: 0.0 for service in SERVICES}
  health_faults = set()
  command_axis = 0.0
  command_until = 0.0
  last_sent_axis = 0.0
  next_publish = 0.0
  warned_longitudinal = False
  lag_warning_shown = False
  quit_requested = False

  params.put_bool("JoystickDebugMode", True, block=True)
  try:
    while not quit_requested:
      now = time.monotonic()
      if keyboard.kbhit():
        key = keyboard.getch().lower()
        if key == "q":
          quit_requested = True
        elif key == " ":
          if last_sent_axis != 0.0:
            send_joystick(pm, 0.0)
          armed = False
          command_axis = 0.0
          last_sent_axis = 0.0
          print("STOPPED / DISARMED", flush=True)
        elif key == "e" and not armed:
          print("Checking one second of fresh vehicle data...", flush=True)
          candidate_sm, candidate_seen, candidate_faults, candidate_advisories = snapshot_gate()
          candidate_last = {service: time.monotonic() for service in SERVICES}
          gates, speed_kph = current_gates(candidate_sm, candidate_seen, candidate_last, candidate_faults)
          if all(gates.values()):
            sm = candidate_sm
            seen = candidate_seen
            last_update = candidate_last
            health_faults = set()
            armed = True
            print(f"ARMED at {speed_kph:.2f} km/h; hold/tap A or D", flush=True)
            if candidate_advisories:
              print(f"ADVISORY (not a disarm): {sorted(candidate_advisories)}", flush=True)
              lag_warning_shown = True
          else:
            print(f"ARM REFUSED: speed={speed_kph:.2f} km/h, gates={gates}, "
                  f"health_faults={sorted(candidate_faults)}", flush=True)
        elif key in ("a", "d"):
          if armed:
            command_axis = -steer_axis if key == "a" else steer_axis
            command_until = now + args.deadman
          else:
            print("Not armed; press E after the car is rolling and MADS is active", flush=True)
        elif key in ("w", "s") and not warned_longitudinal:
          print("W/S disabled: longitudinal commands are not supported", flush=True)
          warned_longitudinal = True

      if armed and sm is not None:
        sm.update(0)
        for service in SERVICES:
          if sm.updated[service]:
            seen.add(service)
            last_update[service] = now
        if sm.updated["onroadEvents"]:
          health_faults = {str(event.name) for event in sm["onroadEvents"]
                           if str(event.name) in UNHEALTHY_EVENTS}
          advisories = {str(event.name) for event in sm["onroadEvents"]
                        if str(event.name) in ADVISORY_EVENTS}
          if advisories and not lag_warning_shown:
            print(f"ADVISORY (not a disarm): {sorted(advisories)}", flush=True)
            lag_warning_shown = True

        gates, speed_kph = current_gates(sm, seen, last_update, health_faults)
        if not all(gates.values()):
          if last_sent_axis != 0.0:
            send_joystick(pm, 0.0)
          armed = False
          command_axis = 0.0
          last_sent_axis = 0.0
          print(f"SAFETY DISARM: speed={speed_kph:.2f} km/h, gates={gates}, "
                f"health_faults={sorted(health_faults)}", flush=True)

      axis = command_axis if armed and now < command_until else 0.0
      if now >= next_publish:
        # Publish at 10 Hz only while commanding, followed by one explicit zero.
        if axis != 0.0 or last_sent_axis != 0.0:
          send_joystick(pm, axis)
        if axis != last_sent_axis:
          print(f"steer={axis:.4f}", flush=True)
        last_sent_axis = axis
        next_publish = now + PUBLISH_PERIOD

      time.sleep(0.01)
  finally:
    send_joystick(pm, 0.0)
    params.put_bool("JoystickDebugMode", False, block=True)


if __name__ == "__main__":
  main()
