#!/usr/bin/bash
# optional remote access — never block openpilot boot
# Comma/AGNOS: tailscaled needs root for TUN / netfilter

TS_ROOT="/data/tailscale"
TS_BIN="$TS_ROOT/bin/tailscaled"
TS_CLI="$TS_ROOT/bin/tailscale"
TS_STATE="$TS_ROOT/tailscaled.state"
TS_SOCKET="$TS_ROOT/tailscaled.sock"
TS_LOG="$TS_ROOT/tailscaled.log"

# missing install is fine
if [ ! -x "$TS_BIN" ]; then
  exit 0
fi

# already running
if pgrep -x tailscaled >/dev/null 2>&1; then
  exit 0
fi

# passwordless sudo required; fail quietly if unavailable
if ! sudo -n true >/dev/null 2>&1; then
  exit 0
fi

mkdir -p "$TS_ROOT" || true
sudo -n "$TS_BIN" --state="$TS_STATE" --socket="$TS_SOCKET" >>"$TS_LOG" 2>&1 &

# if previously authenticated, bring the interface up (no-op if already up)
if [ -x "$TS_CLI" ]; then
  sleep 1
  sudo -n "$TS_CLI" --socket="$TS_SOCKET" up >/dev/null 2>&1 || true
fi

exit 0
