#!/bin/bash
# connect.sh — connect registered devices to this Mac
# Usage: bash scripts/connect.sh <blueutil> <addr1> <name1> <addr2> <name2> ...

set -uo pipefail

log()  { echo "[macblue] $(date '+%Y-%m-%d %H:%M:%S')  INFO   $*"; }
warn() { echo "[macblue] $(date '+%Y-%m-%d %H:%M:%S')  WARN   $*" >&2; }
err()  { echo "[macblue] $(date '+%Y-%m-%d %H:%M:%S')  ERROR  $*" >&2; }

# ── Parse arguments ──────────────────────────────────────────────────────────

if [[ $# -lt 3 ]]; then
  err "Usage: connect.sh <blueutil> <addr1> <name1> [addr2 name2 ...]"
  exit 1
fi

BLUEUTIL="$1"; shift

if [[ ! -f "$BLUEUTIL" ]]; then
  err "blueutil not found at: $BLUEUTIL"
  exit 1
fi

declare -a ADDRS=()
declare -a NAMES=()

while [[ $# -ge 2 ]]; do
  ADDRS+=("$1")
  NAMES+=("$2")
  shift 2
done

if [[ ${#ADDRS[@]} -eq 0 ]]; then
  err "No devices provided."
  exit 1
fi

# ── Helpers ──────────────────────────────────────────────────────────────────

is_connected() {
  "$BLUEUTIL" --is-connected "$1" 2>/dev/null | grep -q "1"
}

# Run a command with a timeout (macOS doesn't have GNU timeout)
run_with_timeout() {
  local secs="$1"; shift
  "$@" &
  local pid=$!
  ( sleep "$secs" && kill "$pid" 2>/dev/null ) &
  local watchdog=$!
  wait "$pid" 2>/dev/null
  local rc=$?
  kill "$watchdog" 2>/dev/null
  wait "$watchdog" 2>/dev/null
  return $rc
}

connect_device() {
  local addr="$1" name="$2"
  if ! [[ "$addr" =~ ^([0-9a-fA-F]{2}[:\-]){5}[0-9a-fA-F]{2}$ ]]; then
    err "Invalid address for '$name': $addr — skipping"
    return 1
  fi

  # Skip if already connected
  if is_connected "$addr"; then
    log "$name is already connected — skipping."
    return 0
  fi

  log "Connecting $name ($addr)..."
  local max_attempts=4
  for i in $(seq 1 $max_attempts); do
    if run_with_timeout 10 "$BLUEUTIL" --connect "$addr" 2>/dev/null; then
      sleep 1
      if is_connected "$addr"; then
        log "$name connected."
        return 0
      fi
    fi
    warn "  attempt $i/$max_attempts failed, retrying in 2s..."
    sleep 2
  done
  err "Could not connect $name after $max_attempts attempts."
  return 1
}

# ── Main ─────────────────────────────────────────────────────────────────────

failed=0
for i in "${!ADDRS[@]}"; do
  connect_device "${ADDRS[$i]}" "${NAMES[$i]}" || failed=$((failed + 1))
done

if (( failed > 0 )); then
  err "$failed device(s) failed to connect."
  exit 1
fi

log "Done. All devices connected."
