#!/usr/bin/env bash
set -euo pipefail

# Summarize key evidence from a capture directory created by capture_call_window.sh
# Usage: scripts/summarize_call_capture.sh [capture_dir]

CAPTURE_DIR="${1:-}"
if [[ -z "$CAPTURE_DIR" ]]; then
  # pick latest
  CAPTURE_DIR=$(ls -1dt logs/call_captures/call_* 2>/dev/null | head -n1 || true)
fi

if [[ -z "$CAPTURE_DIR" || ! -d "$CAPTURE_DIR" ]]; then
  echo "[summarize] No capture directory found. Provide a path or run capture first." >&2
  exit 1
fi

AI_ENGINE_LOG="$CAPTURE_DIR/ai_engine.log"
LOCAL_LOG="$CAPTURE_DIR/local_ai_server.log"
AST_LOG="$CAPTURE_DIR/asterisk.log"
OUT_SUMMARY="$CAPTURE_DIR/summary.txt"

echo "[summarize] Source: $CAPTURE_DIR" | tee "$OUT_SUMMARY"
echo "" | tee -a "$OUT_SUMMARY"

summ() {
  local label="$1"; shift
  echo "- $label: $*" | tee -a "$OUT_SUMMARY"
}

grep_safe_first() {
  local pat="$1"; local file="$2"
  if [[ -r "$file" ]]; then
    grep -F "$pat" "$file" | head -n1 || true
  fi
}

grep_safe_any() {
  local pat="$1"; local file="$2"
  if [[ -r "$file" ]]; then
    if grep -Fq "$pat" "$file"; then
      echo yes
    else
      echo no
    fi
  else
    echo n/a
  fi
}

count_safe() {
  local pat="$1"; local file="$2"
  if [[ -r "$file" ]]; then
    grep -F "$pat" "$file" | wc -l | tr -d ' '
  else
    echo 0
  fi
}

echo "AI-Engine" | tee -a "$OUT_SUMMARY"
summ "listening"     "$(grep_safe_first 'AudioSocket server listening' "$AI_ENGINE_LOG" | sed 's/^/present: /')"
summ "accept"        "$(grep_safe_first 'AudioSocket connection accepted' "$AI_ENGINE_LOG" | sed 's/^/present: /')"
summ "bind"          "$(grep_safe_first 'AudioSocket connection bound to channel' "$AI_ENGINE_LOG" | sed 's/^/present: /')"
summ "input_mode"    "$(grep_safe_first 'Set provider upstream input mode' "$AI_ENGINE_LOG" | sed 's/^/present: /')"
summ "inbound_chunks" "count=$(count_safe 'AudioSocket inbound chunk' "$AI_ENGINE_LOG")"

echo "" | tee -a "$OUT_SUMMARY"
echo "Local AI Server" | tee -a "$OUT_SUMMARY"
if [[ -r "$LOCAL_LOG" ]]; then
  TRANS_LINE=$(grep -F "Transcript:" "$LOCAL_LOG" | head -n1 || true)
  if [[ -n "$TRANS_LINE" ]]; then
    summ "transcript_first" "$(echo "$TRANS_LINE" | sed 's/^/present: /')"
  else
    summ "transcript_first" "none found in window"
  fi
  summ "ws_batches"    "count=$(count_safe 'WS batch send' "$AI_ENGINE_LOG") (provider-side debug)"
else
  summ "transcript_first" "n/a (no local_ai_server log)"
fi

echo "" | tee -a "$OUT_SUMMARY"
echo "Asterisk" | tee -a "$OUT_SUMMARY"
if [[ -r "$AST_LOG" ]]; then
  summ "local_originate" "present=$(grep_safe_any 'ai-agent-media-fork' "$AST_LOG")"
  summ "audiosocket_cmd" "present=$(grep_safe_any 'AudioSocket(' "$AST_LOG")"
  summ "no_type_err"     "absent=$( [[ $(count_safe 'Failed to read type header from AudioSocket' "$AST_LOG") -eq 0 ]] && echo yes || echo no )"
  summ "no_forward_err"  "absent=$( [[ $(count_safe 'Failed to forward frame' "$AST_LOG") -eq 0 ]] && echo yes || echo no )"
else
  summ "asterisk_tail"   "n/a (not captured on this host)"
fi

echo "" | tee -a "$OUT_SUMMARY"
echo "Notes" | tee -a "$OUT_SUMMARY"
summ "health_prefetch" "$( [[ -r "$CAPTURE_DIR/health_prefetch.json" ]] && echo captured || echo none )"
summ "health_post"     "$( [[ -r "$CAPTURE_DIR/health_post.json" ]] && echo captured || echo none )"

echo "" | tee -a "$OUT_SUMMARY"
echo "[summarize] Summary written to $OUT_SUMMARY"

