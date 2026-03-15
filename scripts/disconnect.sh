#!/bin/bash
# disconnect.sh — disconnect devices so the other Mac can connect them
# Usage: bash scripts/disconnect.sh <blueutil> <addr1> <name1> <addr2> <name2> ...

set -uo pipefail

log()  { echo "[macblue] $(date '+%Y-%m-%d %H:%M:%S')  INFO   $*"; }
warn() { echo "[macblue] $(date '+%Y-%m-%d %H:%M:%S')  WARN   $*" >&2; }
err()  { echo "[macblue] $(date '+%Y-%m-%d %H:%M:%S')  ERROR  $*" >&2; }

# ── Parse arguments ──────────────────────────────────────────────────────────

if [[ $# -lt 3 ]]; then
  err "Usage: disconnect.sh <blueutil> <addr1> <name1> [addr2 name2 ...]"
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

# ── Main ─────────────────────────────────────────────────────────────────────

failed=0
for i in "${!ADDRS[@]}"; do
  addr="${ADDRS[$i]}"
  name="${NAMES[$i]}"
  if ! [[ "$addr" =~ ^([0-9a-fA-F]{2}[:\-]){5}[0-9a-fA-F]{2}$ ]]; then
    err "Invalid address for '$name': $addr — skipping"
    failed=$((failed + 1))
    continue
  fi

  if ! is_connected "$addr"; then
    log "$name is already disconnected — skipping."
    continue
  fi

  log "Disconnecting $name ($addr)..."
  # Disconnect repeatedly — devices try to auto-reconnect
  for attempt in 1 2 3; do
    run_with_timeout 10 "$BLUEUTIL" --disconnect "$addr" 2>/dev/null
    sleep 1
    if ! is_connected "$addr"; then
      log "$name disconnected."
      break
    fi
    warn "  $name reconnected, pushing away again ($attempt/3)..."
  done

  if is_connected "$addr"; then
    warn "$name still connected — the other Mac may need a retry."
  fi
done

if (( failed > 0 )); then
  err "$failed device(s) skipped due to invalid addresses."
  exit 1
fi

log "Done. All devices disconnected."
