from openpilot.selfdrive.debug.car.vw_up_did_analyze import rank_capture_bytes


def capture(label, sample, value_hex):
  return {
    "event": "capture",
    "label": label,
    "sample": sample,
    "status": "positive",
    "tx_address": "0x73B",
    "did": "0x1234",
    "result": {"value_hex": value_hex},
  }


def test_rank_capture_bytes_prefers_phase_change_over_noise():
  records = [
    capture("released", 0, "0a64"),
    capture("released", 1, "0b64"),
    capture("light", 0, "0a78"),
    capture("light", 1, "0b78"),
    capture("firm", 0, "0aC8"),
    capture("firm", 1, "0bC8"),
  ]
  ranked = rank_capture_bytes(records)
  assert ranked[0]["byte_index"] == 1
  assert ranked[0]["between_phase_range"] == 100
  assert ranked[0]["max_within_phase_range"] == 0
  assert ranked[1]["byte_index"] == 0
  assert ranked[1]["between_phase_range"] == 0


def test_rank_capture_bytes_ignores_changing_payload_lengths():
  records = [capture("released", 0, "00"), capture("firm", 0, "0001")]
  assert rank_capture_bytes(records) == []
