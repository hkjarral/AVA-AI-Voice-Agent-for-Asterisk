package troubleshoot

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
)

// CallHistorySummary is the canonical persisted result for a call. RCA remains
// log-driven for audio diagnostics, but this record supplies facts that logs do
// not reliably preserve (duration, outcome, turn latency, and routing source).
type CallHistorySummary struct {
	CallID                   string  `json:"call_id"`
	ProviderName             string  `json:"provider_name,omitempty"`
	PipelineName             string  `json:"pipeline_name,omitempty"`
	ContextName              string  `json:"context_name,omitempty"`
	Outcome                  string  `json:"outcome,omitempty"`
	DurationSeconds          float64 `json:"duration_seconds,omitempty"`
	StartTime                string  `json:"start_time,omitempty"`
	EndTime                  string  `json:"end_time,omitempty"`
	ErrorMessage             string  `json:"error_message,omitempty"`
	AverageTurnLatencyMS     float64 `json:"avg_turn_latency_ms,omitempty"`
	MaximumTurnLatencyMS     float64 `json:"max_turn_latency_ms,omitempty"`
	TotalTurns               int     `json:"total_turns,omitempty"`
	BargeInCount             int     `json:"barge_in_count,omitempty"`
	RoutingMethod            string  `json:"routing_method,omitempty"`
	CodecAlignmentOK         *bool   `json:"codec_alignment_ok,omitempty"`
	ConversationHistoryBytes int     `json:"conversation_history_bytes,omitempty"`
}

// loadCallHistorySummary queries inside ai_engine so relative/overridden DB
// paths resolve exactly as they do at runtime. It is intentionally best-effort:
// old installations may not have Call History enabled or may have an older
// schema, and log-only RCA should continue to work in those cases.
func loadCallHistorySummary(callID string) (*CallHistorySummary, error) {
	const script = `
import json, os, sqlite3, sys
p = os.environ.get("CALL_HISTORY_DB_PATH", "/app/data/call_history.db")
c = sqlite3.connect(p)
c.row_factory = sqlite3.Row
cols = {r[1] for r in c.execute("PRAGMA table_info(call_records)")}
if "call_id" not in cols:
    print("null")
    raise SystemExit(0)
order_by = "start_time DESC" if "start_time" in cols else "rowid DESC"
r = c.execute("SELECT * FROM call_records WHERE call_id=? ORDER BY " + order_by + " LIMIT 1", (sys.argv[1],)).fetchone()
if r is None:
    print("null")
    raise SystemExit(0)
d = dict(r)
keys = ("call_id", "provider_name", "pipeline_name", "context_name", "outcome",
        "duration_seconds", "start_time", "end_time", "error_message",
        "avg_turn_latency_ms", "max_turn_latency_ms", "total_turns",
        "barge_in_count", "routing_method", "codec_alignment_ok")
out = {k: d.get(k) for k in keys if k in d and d.get(k) is not None}
out["conversation_history_bytes"] = len(d.get("conversation_history") or "")
if "codec_alignment_ok" in out:
    out["codec_alignment_ok"] = bool(out["codec_alignment_ok"])
print(json.dumps(out, separators=(",", ":")))
`
	cmd := exec.Command("docker", "exec", "ai_engine", "python3", "-c", script, callID)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("call history query failed: %w (%s)", err, strings.TrimSpace(string(out)))
	}
	trimmed := strings.TrimSpace(string(out))
	if trimmed == "" || trimmed == "null" {
		return nil, nil
	}
	var summary CallHistorySummary
	if err := json.Unmarshal([]byte(trimmed), &summary); err != nil {
		return nil, fmt.Errorf("invalid call history response: %w", err)
	}
	return &summary, nil
}
