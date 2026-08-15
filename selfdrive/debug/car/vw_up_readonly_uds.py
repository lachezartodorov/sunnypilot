#!/usr/bin/env python3
"""Read-only UDS diagnostics for the Volkswagen e-Up.

This tool intentionally supports only UDS ReadDataByIdentifier (service 0x22).
It does not change diagnostic sessions, request security access, write coding,
clear faults, reset ECUs, or run actuator tests.

Run it directly on a parked comma device with the openpilot service stopped.
"""

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path


MIN_QUERY_INTERVAL = 0.2  # Never send more than five requests per second.
MIN_DIAG_ADDR = 0x700
MAX_DIAG_ADDR = 0x7FE
FUNCTIONAL_ADDR = 0x7DF

IDENTIFICATION_DIDS = {
  0xF187: "vw_spare_part_number",
  0xF189: "software_version",
  0xF191: "hardware_number",
  0xF197: "system_name",
  0xF19E: "odx_file",
}

KNOWN_EUP_PARTS = {
  "12E909059A": "J539 brake booster (Bosch EBKV)",
  "12E614517F": "J104 ABS/ESC (TRW EBC 460 ESP)",
}


@dataclass(frozen=True)
class ReadResult:
  monotonic_time: float
  tx_address: str
  rx_address: str
  bus: int
  did: str
  name: str
  value_hex: str
  value_text: str | None
  known_ecu: str | None


def parse_int(value: str) -> int:
  return int(value, 0)


def validate_address(tx_addr: int, rx_offset: int) -> int:
  if not MIN_DIAG_ADDR <= tx_addr <= MAX_DIAG_ADDR:
    raise ValueError(f"diagnostic address must be 0x{MIN_DIAG_ADDR:X}..0x{MAX_DIAG_ADDR:X}")
  if tx_addr == FUNCTIONAL_ADDR:
    raise ValueError("functional/broadcast address 0x7DF is deliberately forbidden")

  rx_addr = tx_addr + rx_offset
  if not 0 <= rx_addr <= 0x7FF:
    raise ValueError(f"response address 0x{rx_addr:X} is not an 11-bit CAN address")
  return rx_addr


def validate_did(did: int) -> None:
  if not 0 <= did <= 0xFFFF:
    raise ValueError("DID must be 0x0000..0xFFFF")


def decode_text(data: bytes) -> str | None:
  stripped = data.rstrip(b"\x00\xff ")
  if not stripped:
    return None
  try:
    text = stripped.decode("ascii")
  except UnicodeDecodeError:
    return None
  return text if all(c.isprintable() for c in text) else None


def normalize_part_number(value: str | None) -> str | None:
  if value is None:
    return None
  return "".join(value.upper().split())


def identify_known_ecu(did: int, value_text: str | None) -> str | None:
  if did != 0xF187:
    return None
  normalized = normalize_part_number(value_text)
  return KNOWN_EUP_PARTS.get(normalized) if normalized is not None else None


def running_openpilot_processes() -> list[str]:
  """Return safety-critical openpilot processes found in /proc."""
  if not Path("/proc").is_dir():
    return []

  needles = ("system.manager.manager", "manager.py", "pandad", "card.py", "controlsd")
  found = []
  own_pid = os.getpid()
  for entry in Path("/proc").iterdir():
    if not entry.name.isdigit() or int(entry.name) == own_pid:
      continue
    try:
      cmdline = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode(errors="replace")
    except (FileNotFoundError, PermissionError, ProcessLookupError):
      continue
    if any(needle in cmdline for needle in needles):
      found.append(cmdline.strip())
  return sorted(set(found))


def build_result(tx_addr: int, rx_addr: int, bus: int, did: int, data: bytes) -> ReadResult:
  value_text = decode_text(data)
  return ReadResult(
    monotonic_time=time.monotonic(),
    tx_address=f"0x{tx_addr:X}",
    rx_address=f"0x{rx_addr:X}",
    bus=bus,
    did=f"0x{did:04X}",
    name=IDENTIFICATION_DIDS.get(did, "unknown"),
    value_hex=data.hex(),
    value_text=value_text,
    known_ecu=identify_known_ecu(did, value_text),
  )


def emit(result: ReadResult, output_file) -> None:
  line = json.dumps(asdict(result), sort_keys=True)
  print(line, flush=True)
  if output_file is not None:
    output_file.write(line + "\n")
    output_file.flush()


def read_did(panda, tx_addr: int, rx_offset: int, bus: int, did: int, timeout: float) -> ReadResult:
  # Imports stay here so validation and unit tests don't require Panda hardware.
  from opendbc.car.uds import UdsClient

  rx_addr = validate_address(tx_addr, rx_offset)
  validate_did(did)
  client = UdsClient(panda, tx_addr, rx_addr, bus, timeout=timeout)
  data = client.read_data_by_identifier(did)
  return build_result(tx_addr, rx_addr, bus, did, data)


def add_common_args(parser: argparse.ArgumentParser) -> None:
  parser.add_argument("--bus", type=int, default=1, choices=(0, 1, 2), help="Panda CAN bus (default: 1/OBD)")
  parser.add_argument("--rx-offset", type=parse_int, default=0x6A,
                      help="physical response offset (default: 0x6A for VW PQ)")
  parser.add_argument("--timeout", type=float, default=0.25, help="response timeout in seconds")
  parser.add_argument("--interval", type=float, default=MIN_QUERY_INTERVAL,
                      help="delay between requests; minimum 0.2 seconds")
  parser.add_argument("--output", type=Path, help="optional JSONL output path")


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--parked-confirmed", action="store_true", required=True,
                      help="confirm the car is parked, secured, and nobody is relying on openpilot")
  subparsers = parser.add_subparsers(dest="command", required=True)

  scan = subparsers.add_parser("scan", help="find ECUs using only DID 0xF187")
  add_common_args(scan)
  scan.add_argument("--start", type=parse_int, default=0x700)
  scan.add_argument("--end", type=parse_int, default=0x795,
                    help="inclusive; 0x795 is the highest address valid with offset 0x6A")

  identify = subparsers.add_parser("identify", help="read standard identification DIDs from one ECU")
  add_common_args(identify)
  identify.add_argument("address", type=parse_int)

  read = subparsers.add_parser("read", help="read one or more explicit DIDs from one ECU")
  add_common_args(read)
  read.add_argument("address", type=parse_int)
  read.add_argument("did", type=parse_int, nargs="+")
  return parser.parse_args()


def main() -> int:
  args = parse_args()
  if args.interval < MIN_QUERY_INTERVAL:
    raise SystemExit(f"--interval must be at least {MIN_QUERY_INTERVAL:.1f} seconds")
  if args.timeout <= 0 or args.timeout > 2:
    raise SystemExit("--timeout must be greater than zero and no more than two seconds")

  active = running_openpilot_processes()
  if active:
    details = "\n  ".join(active)
    raise SystemExit(
      "Refusing to take control of Panda while openpilot processes are running.\n"
      "Stop the comma service first (on AGNOS: sudo systemctl stop comma), then retry.\n"
      f"Detected:\n  {details}"
    )

  if args.command == "scan":
    if args.start > args.end:
      raise SystemExit("--start must not be greater than --end")
    addresses = list(range(args.start, args.end + 1))
    dids = (0xF187,)
  elif args.command == "identify":
    addresses = (args.address,)
    dids = tuple(IDENTIFICATION_DIDS)
  else:
    addresses = (args.address,)
    dids = tuple(args.did)

  try:
    for address in addresses:
      validate_address(address, args.rx_offset)
    for did in dids:
      validate_did(did)
  except ValueError as e:
    raise SystemExit(str(e)) from e

  from opendbc.car.structs import CarParams
  from opendbc.car.uds import MessageTimeoutError, NegativeResponseError
  from panda import Panda

  output_file = args.output.open("a", encoding="utf-8") if args.output is not None else None
  panda = Panda()
  try:
    # param=0 routes Panda bus 1 to the OBD-II pins. ELM327 safety permits only
    # ISO-15765 diagnostic CAN address ranges; this program further limits the
    # payload to UDS ReadDataByIdentifier inside read_did().
    panda.set_safety_mode(CarParams.SafetyModel.elm327, 0)
    for address_index, address in enumerate(addresses):
      if args.command == "scan" and address_index % 16 == 0:
        print(f"Scanning 0x{address:X} ({address_index + 1}/{len(addresses)})", file=sys.stderr, flush=True)
      for did in dids:
        try:
          result = read_did(panda, address, args.rx_offset, args.bus, did, args.timeout)
        except (MessageTimeoutError, NegativeResponseError):
          pass
        else:
          emit(result, output_file)
        time.sleep(args.interval)
  finally:
    # Never leave broad diagnostic transmit safety selected after the tool exits.
    panda.set_safety_mode(CarParams.SafetyModel.noOutput)
    if output_file is not None:
      output_file.close()
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
