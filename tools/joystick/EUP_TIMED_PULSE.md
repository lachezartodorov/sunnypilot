# Volkswagen e-Up timed steering pulse

Start the tool while the device and car are offroad:

```sh
cd /data/openpilot
PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 tools/joystick/eup_timed_pulse.py --torque-percent 10 --duration 1.5
```

The default request is 10% of the normal 3 Nm controller limit. The tool has a
hard 80% ceiling and accepts durations from 0.5 through 2.0 seconds.

After the tool reports that it is ready:

1. Start the car with cruise main off.
2. Select D or B and roll at 3-8 km/h in a clear, closed area.
3. With the brake released, switch cruise main on without pressing Set/Resume.
4. Press `P` in the tool to request one pulse. Press `Q` to quit.

Before every pulse, the tool observes one second of fresh car, MADS, panda, and
event data. It refuses unless the gear, speed, brake, steering, MADS, panda, and
system-health gates all pass. It publishes nothing while idle, sends one zero at
pulse expiry, and disables `JoystickDebugMode` when it exits normally.

At high torque the wheel can move strongly. Keep hands ready, do not fight the
command, and brake or switch cruise main off immediately if needed.

## Keyboard WASD steering

For interactive steering, start the separate keyboard tool while offroad:

```sh
cd /data/openpilot
PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 tools/joystick/eup_wasd_control.py --torque-percent 10
```

After going onroad, select D or B, roll at 3-8 km/h, release the brake, and turn
cruise main on without pressing Set/Resume. Press `E` to run the one-second
health check and arm the controller. Then use:

- `A`: left steering
- `D`: right steering
- `Space`: immediate zero and disarm
- `Q`: send zero and quit

`W` and `S` are deliberately disabled because this e-Up configuration does not
support openpilot longitudinal control. SSH terminals provide key presses but no
portable key-release event, so every A/D press grants only 0.35 seconds of
steering. Holding the key relies on terminal key repeat; releasing it returns to
zero after the dead-man timeout. The controller continuously disarms on a stale
input, unhealthy event, gear/speed/brake change, steering fault, loss of MADS,
or a panda safety failure.
