#!/usr/bin/env bash
set -euo pipefail

# Capture a focused log window for ai-engine and local-ai-server.
# Usage: scripts/capture_call_window.sh [duration_seconds]
# Default duration is 75 seconds. Creates a capture directory with per-service logs.

DUR="${1:-75}"

# Prefer docker compose if available; fallback to docker-compose
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  DC=(docker compose -p asterisk-ai-voice-agent)
else
  DC=(docker-compose -p asterisk-ai-voice-agent)
fi

# Basic sanity checks
if ! command -v docker >/dev/null 2>&1; then
  echo "[capture] Docker not found on PATH. Please run on the server with Docker running." >&2
  exit 1
fi

# Service names must match docker-compose.yml
SERVICES=(ai_engine local_ai_server)

TS=$(date +%Y%m%d_%H%M%S)
OUT_DIR="logs/call_captures/call_${TS}"
mkdir -p "$OUT_DIR"

echo "[capture] Writing to $OUT_DIR for ${DUR}s"

# Health snapshot if reachable (non-fatal)
if command -v curl >/dev/null 2>&1; then
  (curl -sS http://127.0.0.1:15000/health || true) | sed 's/^/[health] /' >"$OUT_DIR/health_prefetch.json" 2>/dev/null || true
fi

# Use timeout if available (Linux). If not, try gtimeout (macOS coreutils). Otherwise, just follow once.
TIMEOUT_BIN=""
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_BIN="timeout"
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_BIN="gtimeout"
fi

pids=()
for svc in "${SERVICES[@]}"; do
  log_file="$OUT_DIR/${svc}.log"
  echo "[capture] Following $svc logs -> $log_file"
  if [[ -n "$TIMEOUT_BIN" ]]; then
    ("$TIMEOUT_BIN" "${DUR}s" "${DC[@]}" logs -f "$svc" --no-color --timestamps 2>&1 | tee "$log_file") &
  else
    echo "[capture] WARNING: timeout/gtimeout not found; press Ctrl-C after ~${DUR}s" >&2
    ("${DC[@]}" logs -f "$svc" --no-color --timestamps 2>&1 | tee "$log_file") &
  fi
  pids+=("$!")
done

# Optional: capture Asterisk tail if running locally (non-fatal)
ASTERISK_LOG="/var/log/asterisk/full"
if [[ -r "$ASTERISK_LOG" ]]; then
  echo "[capture] Detected $ASTERISK_LOG; capturing tail during window"
  if [[ -n "$TIMEOUT_BIN" ]]; then
    ("$TIMEOUT_BIN" "${DUR}s" tail -n 300 -F "$ASTERISK_LOG" 2>&1 | tee "$OUT_DIR/asterisk.log") &
  else
    (tail -n 300 -F "$ASTERISK_LOG" 2>&1 | tee "$OUT_DIR/asterisk.log") &
  fi
  pids+=("$!")
fi

# Wait for all background followers
for pid in "${pids[@]}"; do
  wait "$pid" || true
done

# Post-capture health snapshot
if command -v curl >/dev/null 2>&1; then
  (curl -sS http://127.0.0.1:15000/health || true) | sed 's/^/[health] /' >"$OUT_DIR/health_post.json" 2>/dev/null || true
fi

echo "[capture] Done. Files written to: $OUT_DIR"
echo "$OUT_DIR" >"$OUT_DIR/.path"

