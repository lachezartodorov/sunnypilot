# Volkswagen e-Up A1 integration checkpoint

Date: 2026-08-15  
Vehicle: 2021 Volkswagen e-Up  
Device: comma A1  
Base: sunnypilot `release-tizi`

## Branches

### Lateral-only branch

Use `vw-up-a1-lateral-tizi` for normal driving and future lateral work.

Its code base is commit `5fbcbdcc3` (`Support Volkswagen PQ in safety replay initialization`), followed only by this documentation commit. This is the last verified boundary before the UDS/iBooster investigation began.

It includes:

- e-Up fingerprint/interface support;
- stock-compatible HCA steering states;
- hardened e-Up state parsing and lateral Panda safety;
- the signed Panda firmware built for that lateral safety configuration;
- cruise-main/MADS handling developed during the A1 work;
- removal of the temporary joystick and WASD test infrastructure;
- Volkswagen PQ safety-replay initialization support.

It deliberately excludes:

- OpenPilot longitudinal enablement;
- `ACC_System` transmit exceptions;
- zero-acceleration, deceleration, and hard-stop tools;
- diagnostic passthrough safety modes;
- the signed Panda firmware containing longitudinal probe exceptions;
- UDS discovery/capture utilities added during the longitudinal investigation.

### Diagnostic checkpoint branch

`vw-up-a1-production-tizi` preserves the complete investigation. The final code/firmware checkpoint before this document is `524c957ce`.

Do not use its probe tools casually. Production car-control code still does not enable OpenPilot longitudinal control, but this branch contains debug-only Panda modes capable of transmitting tightly bounded longitudinal test frames when explicitly selected by the corresponding tools.

## Lateral status

Lateral OpenPilot control worked during road testing, including below the original factory lane-assist speed threshold.

Important e-Up-specific findings:

- Stock EPS HCA state is `3` while ready with zero torque.
- Stock EPS HCA state is `5` while actively applying torque.
- Generic PQ active state `7` was not used for the dedicated e-Up path.
- Steering torque remains limited by the VW PQ Panda safety limits.
- The temporary joystick, lag bypasses, and stationary/gear bypass infrastructure were removed before the lateral-only checkpoint.

Remaining lateral caveats:

- Gear reporting should not be treated as authoritative. During later parked diagnostics, `carState.gearShifter` reported `neutral` while the physical selector was in P.
- Cruise-main/MADS behavior worked in testing, but stock cruise engagement and MADS should be rechecked after any future rebase.
- Rebase work must preserve the e-Up HCA state behavior and re-run the VW PQ Panda safety tests.

## Relevant ECU identification

### EPS / J792

- VW software part: `2Q1 909 144 R`
- VW hardware part: `2Q1 909 144 N`
- Software version: `6212`
- Hardware version: `611`
- System: `TRW_G4L_MQBA0`
- ASAM: `EV_SteerAssisZFGen1PQ12PA 002002`
- Earlier steering DTC: `U112100 / DEM_HCA_MSG_ABSENT_FAULT`

### J104 ABS/ESC

- Diagnostic request: `0x713`
- Diagnostic response: `0x77D`
- OBD/Panda bus used for UDS: bus 1
- Identified part: `12E 614 517 F`
- System: TRW EBC 460 ESP
- Pressure-related DID discovered: `0x1816`

### J539 brake booster / iBooster

- Diagnostic request: `0x73B`
- Diagnostic response: `0x7A5`
- OBD/Panda bus used for UDS: bus 1
- Identified part: `12E 909 059 A`
- System: Bosch EBKV
- Pressure-related DID discovered: `0x4E07`

## Longitudinal CAN findings

The standard VW PQ longitudinal request is:

- message: `ACC_System`;
- CAN ID: `0x368`;
- length: 8 bytes;
- expected cadence: 50 Hz;
- checksum: XOR of bytes 1 through 7, stored in byte 0;
- counter: low nibble of byte 1, cycling 0 through 15.

Important fields:

- `ACS_Sta_ADR=1`: ADR active;
- `ACS_StSt_Info=1`;
- `ACS_FreigSollB=1`: requested acceleration released/valid;
- `ACS_Typ_ACC=0`: base ACC;
- `ACS_Typ_ACC=1`: ACC with Follow-to-Stop;
- `ACS_Anhaltewunsch=1`: explicit vehicle-stop request;
- `ACS_Sollbeschl`: requested acceleration.

Passive observation with normal sunnypilot running found no competing stock `0x368` publisher on any Panda bus during an eight-second sample.

## Longitudinal tests and results

All active tests were conducted at very low speed in a controlled area. The tools required explicit confirmations and enforced exact payloads, duration/count ceilings, speed windows, pedal aborts, valid Panda RX checks, and pressure monitoring.

| Test | Key payload/state | Duration | Result |
| --- | --- | --- | --- |
| Inactive request | `f90000fe07fefe00` | 3.0 s | Transmitted; no pressure response |
| Active zero | `ACS_Sta_ADR=1`, `0.00 m/s²` | 0.5 s max | No pressure response |
| Small deceleration | `ACS_Sta_ADR=1`, `-0.20 m/s²` | 0.1 s | No pressure response stationary or rolling |
| Base-ACC hard stop | `ACS_Typ_ACC=0`, `-3.0 m/s²` | 75 frames / 1.480 s | Speed `4.06→5.79 km/h`; pressure stayed about `0.3 bar` |
| Follow-to-Stop hard stop | `ACS_Typ_ACC=1`, stop request, `-3.0 m/s²` | 75 frames / 1.483 s | Speed `4.00→5.94 km/h`; pressure stayed about `0.3 bar` |

Exact hard-stop counter-zero payloads:

- Base ACC: `6010814c03289600`
- Follow-to-Stop with stop request: `2810894c43289600`

For both decisive rolling tests:

- all 75 frames passed Panda safety;
- counters and XOR checksums were correct;
- CAN transmit loss and checksum-error counters remained zero;
- Panda RX safety checks remained valid;
- neither J104 nor J539 generated hydraulic pressure;
- the vehicle continued normal creep instead of decelerating.

Conclusion: command magnitude and duration are not the blocker. Repeating stronger or longer `ACC_System` requests is not justified.

## DTC state after the final tests

Read-only UDS `ReadDTCInformation / DTCByStatusMask / ALL` was run without clearing faults, changing sessions, requesting security access, writing coding, or starting actuator tests.

J104:

- raw DTC `0x0001CB`, status `0x08` (`CONFIRMED_DTC`), unchanged from before the active tests;
- raw DTCs `0x0000C3`, `0x000167`, and `0x0001ED`, status `0x10` only;
- no new failed or pending DTC attributable to the commands.

J539:

- entries remained status `0x40` or `0x50` only;
- those statuses mean tests not completed this operation cycle and/or since last clear;
- no failed, pending, or confirmed DTC was produced by the commands.

The raw identifiers are VW diagnostic identifiers; the generic SAE formatter is not a reliable description for them.

## Current conclusion

The Bosch brake booster appears healthy and responds to real pedal input, but it does not directly act on the tested `ACC_System` traffic. J104 also silently ignored both standard base-ACC and Follow-to-Stop requests without recording a new fault.

Most likely remaining explanations:

1. J104 coding or parameterization does not enable ADR/ACC external deceleration requests.
2. J104 requires companion ACC, drivetrain-coordinator, radar/camera, or source-availability messages before accepting `ACC_System`.
3. Source/network arbitration requires the request to arrive from a particular ECU or gateway path even though the frame is physically valid on the observed powertrain CAN.
4. The e-Up uses a different longitudinal-control interface or an EV-specific coordinator path not represented by the generic VW PQ `ACC_System` implementation.
5. The iBooster is a pressure actuator under J104 arbitration, not the primary external longitudinal command endpoint.

## Recommended next steps

Do not perform another stronger actuation probe until the eligibility path is understood.

1. Obtain the correct ODX/ODIS data for J104 `12E 614 517 F` and J539 `12E 909 059 A`.
2. Decode J104 raw DTC `0x0001CB` and read its snapshot/extended records without clearing it.
3. Read and archive J104 coding, adaptations, and parameter-set identification, focusing on ADR/ACC, Front Assist, Follow-to-Stop, and external deceleration authorization.
4. Compare those values with an ACC-equipped PQ12 vehicle using the same or closely related ABS hardware/software.
5. Capture the complete powertrain CAN startup and ACC-engagement sequence from a compatible donor vehicle. Identify companion messages and state transitions immediately before `ACC_System` becomes active.
6. Verify the ECU source and physical CAN path expected by J104, including gateway routing and whether source-address plausibility is enforced indirectly.
7. Determine the EV drivetrain torque-reduction command path. A complete OpenPilot longitudinal implementation will need coordinated propulsion/regen and friction braking, not only iBooster pressure.
8. Only after identifying the missing eligibility condition, add one new bounded test with an exact hypothesis and corresponding Panda unit tests.

## Saved evidence

The local checkpoint workspace contains:

- `vw_up_corrected_stationary_small_decel_20260815_1.jsonl`
- `vw_up_corrected_rolling_small_decel_20260815_1.jsonl`
- `vw_up_rolling_hard_stop_20260815_1.jsonl`
- `vw_up_rolling_fts_hard_stop_20260815_1.jsonl`
- `abs_discovery.jsonl`
- `abs_capture_20260815.jsonl`
- `abs_ranked_20260815.json`
- `ebkv_discovery.jsonl`
- `ebkv_capture.jsonl`
- `ebkv_ranked.json`
- `brake_paired_20260815.jsonl`
- `vw_up_j104_ident.jsonl`
- `vw_up_j539_ident.jsonl`

Original stock-route log referenced at the start of the investigation:

`~/Downloads/e77ab9b8f061c739_00000001--8702f20943--0--rlog.zst`

## Resume checklist

When work resumes:

1. Start from `vw-up-a1-lateral-tizi` for ordinary driving.
2. Confirm the A1 checkout, Panda signature, and zero Panda faults after installation.
3. Revalidate cruise-main/MADS, physical gear versus parsed gear, and lateral engagement in a short controlled drive.
4. Use `vw-up-a1-production-tizi` only when intentionally resuming the diagnostic investigation.
5. Never enable production longitudinal control from the current diagnostic evidence.
