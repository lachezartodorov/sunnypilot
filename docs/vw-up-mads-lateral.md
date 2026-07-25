# VW e-Up MADS lateral (stock EPS)

Branch: `vw-up-tici`  
Platform: `VOLKSWAGEN_UP_MK1`

## Goal

MADS **lateral-only** on stock EPS. No openpilot ACC write. Accept stock ~6 minute HCA timer.

## Engagement UX

Stalk cruise buttons (`GRA_Neu`) are not mapped for UP yet. Engage via stock CC + MADS main-cruise edge:

1. Enable params (once on device):
   - `Mads` = true
   - `MadsMainCruiseAllowed` = true
   - `MadsUnifiedEngagementMode` = false
2. Drive, then set **stock cruise control** (`Motor_2.GRA_Status` → 1 or 2).
3. That sets `cruiseState.available/enabled`, which:
   - opens panda `controls_allowed` (stock PQ safety uses the same GRA_Status)
   - fires MADS `lkasEnable` when main cruise becomes available
4. MADS lateral sends `HCA_1` torque; EPS should go READY → ACTIVE.

## Software notes

- `dashcamOnly` is off only for `VOLKSWAGEN_UP_MK1` (other PQ stay dashcam-only).
- Cruise state comes from `Motor_2` (no ACC radar).
- Wheel speeds from `Bremse_3` when present; `vEgo` always from `Bremse_1`.
- Follow-up: [pq-flasher](https://github.com/pd0wm/pq-flasher) if the 6‑minute timer is unacceptable.

## GRA_Neu survey

```bash
cd /data/openpilot
PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 scripts/vw_up_gra_neu_survey.py
```

Run with **ignition on in the car**. Do not change `volkswagen_pq.h` based on a bench/off-car result.

- If GRA_Neu is seen: no safety RX change.
- If not seen with car awake: make `0x38A` optional/ignored in PQ safety RX for UP, then rebuild/flash panda as required.

Bench result (device not in car): NOT seen — expected; safety unchanged pending in-car run.

## Device params (already set on this C3)

- `Mads=1`
- `MadsMainCruiseAllowed=1`
- `MadsUnifiedEngagementMode=0`

## First-drive checklist

1. Fingerprint `VOLKSWAGEN_UP_MK1`
2. Re-run GRA_Neu survey with ignition on; apply safety change only if still missing
3. `Lenkhilfe_2.LH2_Sta_HCA` reaches READY (not stuck DISABLED/FAULT)
4. Set stock CC; confirm no panda block of `HCA_1`
5. MADS on; confirm `latActive` and non-zero `HCA_1` torque
6. EPS ACTIVE and wheel response
7. Note 6‑minute timer behavior
