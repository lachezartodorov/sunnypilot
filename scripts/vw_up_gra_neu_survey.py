#!/usr/bin/env python3
"""Survey whether GRA_Neu (0x38A) is present on the live CAN stream.

Stock PQ panda safety lists GRA_Neu as a required RX check. UP may not
send it. Run on device with ignition on / car awake (pandad must be running):

  cd /data/openpilot
  PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 scripts/vw_up_gra_neu_survey.py

Exit 0 if GRA_Neu seen, 1 if not seen within timeout, 2 on setup error.
If not seen, volkswagen_pq.h RX checks need an UP-specific ignore before
MADS lateral can stay canValid.
"""

from __future__ import annotations

import sys
import time

GRA_NEU = 0x38A
TIMEOUT_S = 10.0


def main() -> int:
  try:
    import cereal.messaging as messaging
  except Exception as e:
    print(f"setup error: cannot import cereal.messaging: {e}", file=sys.stderr)
    return 2

  sock = messaging.sub_sock("can", timeout=100)
  print(f"listening for GRA_Neu 0x{GRA_NEU:03X} on cereal 'can' for {TIMEOUT_S:.0f}s...")
  seen: dict[int, int] = {}
  t0 = time.monotonic()
  while time.monotonic() - t0 < TIMEOUT_S:
    msgs = messaging.drain_sock(sock)
    for msg in msgs:
      for c in msg.can:
        if c.address == GRA_NEU:
          seen[int(c.src)] = seen.get(int(c.src), 0) + 1
    time.sleep(0.01)

  if not seen:
    print("RESULT: GRA_Neu NOT seen")
    print("ACTION: make GRA_Neu optional/ignored in opendbc safety volkswagen_pq.h for UP")
    print("NOTE: if the car was off / not connected, re-run with ignition on before changing safety")
    return 1

  for bus, count in sorted(seen.items()):
    print(f"RESULT: GRA_Neu seen on bus {bus}: {count} frames")
  print("ACTION: no PQ safety RX change needed for GRA_Neu")
  return 0


if __name__ == "__main__":
  sys.exit(main())
