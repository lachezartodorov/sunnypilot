#!/usr/bin/env python3
"""Rank changing bytes in labeled vw_up_readonly_uds.py capture logs."""

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path


def load_capture_records(paths: list[Path]) -> list[dict]:
  records = []
  for path in paths:
    for line in path.read_text(encoding="utf-8").splitlines():
      try:
        record = json.loads(line)
      except json.JSONDecodeError:
        continue
      if record.get("event") == "capture" and record.get("status") == "positive" and record.get("result"):
        records.append(record)
  return records


def rank_capture_bytes(records: list[dict]) -> list[dict]:
  grouped: dict[tuple[str, str], dict[str, list[bytes]]] = defaultdict(lambda: defaultdict(list))
  for record in records:
    key = (record["tx_address"], record["did"])
    grouped[key][record["label"]].append(bytes.fromhex(record["result"]["value_hex"]))

  ranked = []
  for (tx_address, did), phases in grouped.items():
    lengths = {len(value) for values in phases.values() for value in values}
    if len(lengths) != 1:
      continue
    payload_length = lengths.pop()
    for byte_index in range(payload_length):
      medians = {}
      within_ranges = {}
      unique_values = {}
      for label, values in phases.items():
        byte_values = [value[byte_index] for value in values]
        medians[label] = statistics.median(byte_values)
        within_ranges[label] = max(byte_values) - min(byte_values)
        unique_values[label] = sorted(set(byte_values))
      between_range = max(medians.values()) - min(medians.values())
      within_noise = max(within_ranges.values())
      ranked.append({
        "tx_address": tx_address,
        "did": did,
        "byte_index": byte_index,
        "score": round(between_range / (1 + within_noise), 4),
        "between_phase_range": between_range,
        "max_within_phase_range": within_noise,
        "phase_medians": medians,
        "phase_unique_values": unique_values,
        "samples_per_phase": {label: len(values) for label, values in phases.items()},
      })
  return sorted(ranked, key=lambda item: (item["score"], item["between_phase_range"]), reverse=True)


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("capture", nargs="+", type=Path, help="capture JSONL file(s)")
  parser.add_argument("--top", type=int, default=50)
  parser.add_argument("--output", type=Path, help="optional JSON output path")
  args = parser.parse_args()

  ranked = rank_capture_bytes(load_capture_records(args.capture))[:args.top]
  rendered = json.dumps(ranked, indent=2, sort_keys=True)
  print(rendered)
  if args.output is not None:
    args.output.write_text(rendered + "\n", encoding="utf-8")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
