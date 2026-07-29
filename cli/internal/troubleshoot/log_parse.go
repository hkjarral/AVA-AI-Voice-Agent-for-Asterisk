package troubleshoot

import (
	"bytes"
	"encoding/json"
	"regexp"
	"strings"
)

var (
	consoleLevelRe = regexp.MustCompile(`(?i)\[(debug|info|warning|warn|error)\b`)
	consoleEventRe = regexp.MustCompile(`\]\s+([^\[]+?)\s+\[`)
	kvRe           = regexp.MustCompile(`([a-zA-Z_][a-zA-Z0-9_]*)=('[^']*'|"[^"]*"|\S+)`)
)

func stripQuotes(s string) string {
	s = strings.TrimSpace(s)
	if len(s) >= 2 {
		if (s[0] == '\'' && s[len(s)-1] == '\'') || (s[0] == '"' && s[len(s)-1] == '"') {
			return s[1 : len(s)-1]
		}
	}
	return s
}

// parseLogLine attempts to parse both JSON logs and console/structlog logs.
//
// Returns:
// - level: "debug"|"info"|"warning"|"error"|"" (unknown)
// - event: structlog "event" value (or console message)
// - fields: flattened key/value fields as strings (best-effort)
// - ok: true if a parse path succeeded (JSON or console)
func parseLogLine(line string) (level string, event string, fields map[string]string, ok bool) {
	line = strings.TrimSpace(line)
	if line == "" {
		return "", "", nil, false
	}

	// JSON path
	var entry map[string]any
	dec := json.NewDecoder(bytes.NewReader([]byte(line)))
	dec.UseNumber()
	if dec.Decode(&entry) == nil {
		if v, ok := entry["level"].(string); ok {
			level = strings.ToLower(strings.TrimSpace(v))
			if level == "warn" {
				level = "warning"
			}
		}
		if v, ok := entry["event"].(string); ok {
			event = v
		}
		fields = make(map[string]string, 16)
		for k, v := range entry {
			if k == "" || k == "event" || k == "level" {
				continue
			}
			switch t := v.(type) {
			case string:
				fields[k] = t
			case json.Number:
				num := strings.TrimSpace(t.String())
				if strings.Contains(num, ".") && !strings.ContainsAny(num, "eE") {
					num = strings.TrimRight(num, "0")
					num = strings.TrimRight(num, ".")
				}
				fields[k] = num
			case bool:
				if t {
					fields[k] = "true"
				} else {
					fields[k] = "false"
				}
			default:
				// Ignore nested objects; header and metrics should log flat fields.
			}
		}
		return level, event, fields, true
	}

	// Console/structlog path (best-effort)
	level = ""
	if m := consoleLevelRe.FindStringSubmatch(line); len(m) > 1 {
		level = strings.ToLower(m[1])
		if level == "warn" {
			level = "warning"
		}
	}

	event = ""
	if m := consoleEventRe.FindStringSubmatch(line); len(m) > 1 {
		event = strings.TrimSpace(m[1])
	}
	if event == "" {
		// As a fallback, treat entire line as an "event".
		event = line
	}

	fields = make(map[string]string, 16)
	for _, m := range kvRe.FindAllStringSubmatch(line, -1) {
		if len(m) < 3 {
			continue
		}
		k := m[1]
		v := stripQuotes(m[2])
		fields[k] = v
	}
	return level, event, fields, true
}

func jsonNumberString(f float64) string {
	// Minimal float formatting without importing fmt in this helper file.
	// This is only used for debug-ish field conversion; metrics parsing uses strconv.
	b, _ := json.Marshal(f)
	return string(b)
}
