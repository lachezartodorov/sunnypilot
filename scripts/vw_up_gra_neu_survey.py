#!/usr/bin/env python3
"""Survey whether GRA_Neu (0x38A) is present on bus 0 for VW e-Up.

Stock PQ panda safety lists GRA_Neu as a required RX check. UP may not
send it. Run on device with ignition on / car awake:

  cd /data/openpilot && python scripts/vw_up_gra_neu_survey.py

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
    from panda import Panda
  except Exception as e:
    print(f"setup error: cannot import panda: {e}", file=sys.stderr)
    return 2

  try:
    p = Panda()
  except Exception as e:
    print(f"setup error: cannot open panda: {e}", file=sys.stderr)
    return 2

  print(f"listening for GRA_Neu 0x{GRA_NEU:03X} on all buses for {TIMEOUT_S:.0f}s...")
  seen: dict[int, int] = {}
  t0 = time.monotonic()
  while time.monotonic() - t0 < TIMEOUT_S:
    for addr, _, dat, bus in p.can_recv():
      if addr == GRA_NEU:
        seen[bus] = seen.get(bus, 0) + 1
    time.sleep(0.01)

  if not seen:
    print("RESULT: GRA_Neu NOT seen")
    print("ACTION: make GRA_Neu optional/ignored in opendbc safety volkswagen_pq.h for UP")
    return 1

  for bus, count in sorted(seen.items()):
    print(f"RESULT: GRA_Neu seen on bus {bus}: {count} frames")
  print("ACTION: no PQ safety RX change needed for GRA_Neu")
  return 0


if __name__ == "__main__":
  sys.exit(main())
