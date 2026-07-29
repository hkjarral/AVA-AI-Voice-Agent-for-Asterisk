package troubleshoot

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
)

// CallMetrics holds extracted metrics from logs
type CallMetrics struct {
	// Provider bytes tracking
	ProviderSegments   []ProviderSegment
	ProviderBytesTotal int
	EnqueuedBytesTotal int
	WorstEnqueuedRatio float64

	// Streaming performance
	StreamingSummaries     []StreamingSummary
	WorstDriftPct          float64
	UnderflowCount         int
	DriftAssessmentSkipped string

	// VAD/Audio gating
	VADSettings         *VADSettings
	GateClosures        int
	GateFlutterDetected bool

	// Transport/Format (from logs)
	AudioSocketFormat    string
	ProviderInputFormat  string
	ProviderOutputFormat string
	SampleRate           int

	// Format alignment (from config + logs)
	FormatAlignment *FormatAlignment

	// Call timing
	CallDurationSeconds float64

	// Configuration issues
	ConfigErrors []string
}

// FormatAlignment tracks format/sampling configuration and actual behavior
type FormatAlignment struct {
	// From config
	ConfigAudioTransport       string
	ConfigAudioSocketFormat    string
	ConfigProviderInputFormat  string
	ConfigProviderOutputFormat string
	ConfigSampleRate           int

	// From runtime logs
	RuntimeAudioSocketFormat   string
	RuntimeProviderInputFormat string
	RuntimeSampleRate          int

	// Frame size analysis
	ObservedFrameSize int
	ExpectedFrameSize int

	// Alignment issues
	AudioSocketMismatch    bool
	ProviderFormatMismatch bool
	SampleRateMismatch     bool
	FrameSizeMismatch      bool

	// Detailed issues
	Issues []string
}

// ProviderSegment tracks provider bytes per segment
type ProviderSegment struct {
	ProviderBytes int
	EnqueuedBytes int
	Ratio         float64
}

// StreamingSummary holds streaming tuning data
type StreamingSummary struct {
	StreamID         string
	BytesSent        int
	EffectiveSeconds float64
	WallSeconds      float64
	DriftPct         float64
	LowWatermark     int
	MinStart         int
	IsGreeting       bool
}

// VADSettings holds VAD configuration
type VADSettings struct {
	WebRTCAggressiveness int
	ConfidenceThreshold  float64
	EnergyThreshold      int
	EnhancedEnabled      bool
}

// ExtractMetrics parses structured metrics from logs
func ExtractMetrics(logData string) *CallMetrics {
	metrics := &CallMetrics{
		ProviderSegments:   []ProviderSegment{},
		StreamingSummaries: []StreamingSummary{},
		ConfigErrors:       []string{},
		WorstEnqueuedRatio: 1.0,
		WorstDriftPct:      0.0,
	}

	lines := strings.Split(logData, "\n")

	for _, line := range lines {
		// Parse JSON logs OR console structlog logs.
		_, event, fields, ok := parseLogLine(line)
		if !ok {
			continue
		}

		switch event {
		case "PROVIDER SEGMENT BYTES":
			// JSON path still supported; console path uses fields parsing.
			if len(fields) > 0 {
				extractProviderBytesFields(fields, metrics)
			} else {
				var logEntry map[string]interface{}
				if err := json.Unmarshal([]byte(line), &logEntry); err == nil {
					extractProviderBytes(logEntry, metrics)
				}
			}

		case "🎛️ STREAMING TUNING SUMMARY":
			if len(fields) > 0 {
				extractStreamingSummaryFields(fields, metrics)
			} else {
				var logEntry map[string]interface{}
				if err := json.Unmarshal([]byte(line), &logEntry); err == nil {
					extractStreamingSummary(logEntry, metrics)
				}
			}

		case "Transport alignment summary":
			if len(fields) > 0 {
				extractTransportAlignmentFields(fields, metrics)
			} else {
				var logEntry map[string]interface{}
				if err := json.Unmarshal([]byte(line), &logEntry); err == nil {
					extractTransportAlignment(logEntry, metrics)
				}
			}

		case "🎯 WebRTC VAD settings":
			if len(fields) > 0 {
				extractVADSettingsFields(fields, metrics)
			} else {
				var logEntry map[string]interface{}
				if err := json.Unmarshal([]byte(line), &logEntry); err == nil {
					extractVADSettings(logEntry, metrics)
				}
			}

		case "Streaming segment bytes summary v2":
			// Extract underflow count from segment summary
			// Check if this is a greeting segment
			streamID := fields["stream_id"]
			if streamID == "" {
				var logEntry map[string]interface{}
				if err := json.Unmarshal([]byte(line), &logEntry); err == nil {
					if sid, ok := logEntry["stream_id"].(string); ok {
						streamID = sid
					}
				}
			}
			isGreeting := strings.Contains(streamID, "greeting")

			underflows := 0
			if v := fields["underflow_events"]; v != "" {
				underflows = atoiSafe(v)
			} else {
				var logEntry map[string]interface{}
				if err := json.Unmarshal([]byte(line), &logEntry); err == nil {
					if uf, ok := logEntry["underflow_events"].(float64); ok {
						underflows = int(uf)
					}
				}
			}
			if underflows > 0 {
				// Only count underflows from non-greeting segments
				// Greeting segments have underflows during conversation pauses (normal)
				if !isGreeting {
					metrics.UnderflowCount += underflows
				}
			}

		default:
			// Check for other patterns
			if strings.Contains(event, "gate_closure") {
				metrics.GateClosures++
			}

			// Skip Deepgram target_encoding validation warnings (harmless - provider doesn't use that field)
			if strings.Contains(line, "target_encoding") && strings.Contains(line, "error") {
				if !strings.Contains(line, "DeepgramProviderConfig") {
					metrics.ConfigErrors = append(metrics.ConfigErrors, "Configuration error related to target_encoding")
				}
				// Deepgram target_encoding warning is benign - it's a Python validation artifact
			}
		}
	}

	// Detect gate flutter (>50 closures = problem)
	if metrics.GateClosures > 50 {
		metrics.GateFlutterDetected = true
	}

	return metrics
}

// ApplyCallContext removes timing comparisons that are not meaningful for a
// given call shape. Pipeline TTS wall time includes synthesis/queue waits, so it
// cannot be compared directly with audio duration. Extremely short or empty
// segments also produce dramatic percentages from only a few milliseconds.
func (m *CallMetrics) ApplyCallContext(header *RCAHeader) {
	if m == nil {
		return
	}
	m.WorstDriftPct = 0
	if header != nil && strings.TrimSpace(header.PipelineName) != "" {
		m.DriftAssessmentSkipped = "pipeline wall time includes synthesis and queueing"
		return
	}
	for _, sum := range m.StreamingSummaries {
		if sum.IsGreeting || sum.EffectiveSeconds < 0.25 || sum.BytesSent <= 0 {
			continue
		}
		if abs(sum.DriftPct) > abs(m.WorstDriftPct) {
			m.WorstDriftPct = sum.DriftPct
		}
	}
}

// UnderflowRatePct normalizes raw event counts by the estimated number of
// 20 ms telephony frames. A handful of startup underflows in a long call should
// not be labeled the same way as sustained buffer starvation.
func (m *CallMetrics) UnderflowRatePct() float64 {
	if m == nil || m.UnderflowCount <= 0 {
		return 0
	}
	totalFrames := 0
	for _, seg := range m.StreamingSummaries {
		if seg.BytesSent > 0 {
			totalFrames += seg.BytesSent / 160 // ulaw@8k: 160 bytes per 20 ms frame
		}
	}
	if totalFrames <= 0 {
		return 0
	}
	return float64(m.UnderflowCount) / float64(totalFrames) * 100
}

func extractProviderBytesFields(fields map[string]string, metrics *CallMetrics) {
	segment := ProviderSegment{}

	segment.ProviderBytes = atoiSafe(fields["provider_bytes"])
	segment.EnqueuedBytes = atoiSafe(fields["enqueued_bytes"])

	if segment.ProviderBytes > 0 {
		metrics.ProviderBytesTotal += segment.ProviderBytes
	}
	if segment.EnqueuedBytes > 0 {
		metrics.EnqueuedBytesTotal += segment.EnqueuedBytes
	}
	if v := fields["enqueued_ratio"]; v != "" {
		segment.Ratio = atofSafe(v)
		deviation := abs(1.0 - segment.Ratio)
		worstDeviation := abs(1.0 - metrics.WorstEnqueuedRatio)
		if deviation > worstDeviation {
			metrics.WorstEnqueuedRatio = segment.Ratio
		}
	}

	metrics.ProviderSegments = append(metrics.ProviderSegments, segment)
}

func extractStreamingSummaryFields(fields map[string]string, metrics *CallMetrics) {
	sum := StreamingSummary{}

	if sid := fields["stream_id"]; sid != "" {
		sum.StreamID = sid
		sum.IsGreeting = strings.Contains(sid, "greeting")
	}

	sum.BytesSent = atoiSafe(fields["bytes_sent"])
	sum.EffectiveSeconds = atofSafe(fields["effective_seconds"])
	sum.WallSeconds = atofSafe(fields["wall_seconds"])
	sum.DriftPct = atofSafe(fields["drift_pct"])
	sum.LowWatermark = atoiSafe(fields["low_watermark"])
	sum.MinStart = atoiSafe(fields["min_start"])

	if sum.DriftPct != 0 && !sum.IsGreeting && sum.EffectiveSeconds >= 0.25 && sum.BytesSent > 0 {
		if abs(sum.DriftPct) > abs(metrics.WorstDriftPct) {
			metrics.WorstDriftPct = sum.DriftPct
		}
	}

	metrics.StreamingSummaries = append(metrics.StreamingSummaries, sum)
}

func extractTransportAlignmentFields(fields map[string]string, metrics *CallMetrics) {
	if v := fields["audiosocket_format"]; v != "" {
		metrics.AudioSocketFormat = v
	}
	if v := fields["provider_input_format"]; v != "" {
		metrics.ProviderInputFormat = v
	}
	if v := fields["provider_output_format"]; v != "" {
		metrics.ProviderOutputFormat = v
	}
	if v := fields["sample_rate"]; v != "" {
		metrics.SampleRate = atoiSafe(v)
	}
}

func extractVADSettingsFields(fields map[string]string, metrics *CallMetrics) {
	if metrics.VADSettings == nil {
		metrics.VADSettings = &VADSettings{}
	}

	// Some sources log "aggressiveness", some log "webrtc_aggressiveness".
	if v := fields["aggressiveness"]; v != "" || fields["webrtc_aggressiveness"] != "" {
		if v == "" {
			v = fields["webrtc_aggressiveness"]
		}
		metrics.VADSettings.WebRTCAggressiveness = atoiSafe(v)
	}
	if v := fields["confidence_threshold"]; v != "" {
		metrics.VADSettings.ConfidenceThreshold = atofSafe(v)
	}
	if v := fields["energy_threshold"]; v != "" {
		metrics.VADSettings.EnergyThreshold = atoiSafe(v)
	}
	if v := strings.ToLower(strings.TrimSpace(fields["enhanced_enabled"])); v != "" {
		metrics.VADSettings.EnhancedEnabled = v == "true" || v == "1" || v == "yes" || v == "on"
	}
}

func atoiSafe(s string) int {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0
	}
	if strings.Contains(s, ".") {
		f, err := strconv.ParseFloat(s, 64)
		if err != nil {
			return 0
		}
		return int(f)
	}
	i, err := strconv.Atoi(s)
	if err != nil {
		return 0
	}
	return i
}

func atofSafe(s string) float64 {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0
	}
	f, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return 0
	}
	return f
}

func extractProviderBytes(entry map[string]interface{}, metrics *CallMetrics) {
	segment := ProviderSegment{}

	if pb, ok := entry["provider_bytes"].(float64); ok {
		segment.ProviderBytes = int(pb)
		metrics.ProviderBytesTotal += int(pb)
	}

	if eb, ok := entry["enqueued_bytes"].(float64); ok {
		segment.EnqueuedBytes = int(eb)
		metrics.EnqueuedBytesTotal += int(eb)
	}

	if ratio, ok := entry["enqueued_ratio"].(float64); ok {
		segment.Ratio = ratio

		// Track worst ratio (furthest from 1.0)
		deviation := abs(1.0 - ratio)
		worstDeviation := abs(1.0 - metrics.WorstEnqueuedRatio)
		if deviation > worstDeviation {
			metrics.WorstEnqueuedRatio = ratio
		}
	}

	metrics.ProviderSegments = append(metrics.ProviderSegments, segment)
}

func extractStreamingSummary(entry map[string]interface{}, metrics *CallMetrics) {
	sum := StreamingSummary{}

	// Extract stream_id to detect greeting segments
	if sid, ok := entry["stream_id"].(string); ok {
		sum.StreamID = sid
		sum.IsGreeting = strings.Contains(sid, "greeting")
	}

	if bs, ok := entry["bytes_sent"].(float64); ok {
		sum.BytesSent = int(bs)
	}

	if es, ok := entry["effective_seconds"].(float64); ok {
		sum.EffectiveSeconds = es
	}

	if ws, ok := entry["wall_seconds"].(float64); ok {
		sum.WallSeconds = ws
	}

	if drift, ok := entry["drift_pct"].(float64); ok {
		sum.DriftPct = drift

		// Track worst drift (but only for non-greeting segments)
		if !sum.IsGreeting {
			if abs(drift) > abs(metrics.WorstDriftPct) {
				metrics.WorstDriftPct = drift
			}
		}
	}

	if lw, ok := entry["low_watermark"].(float64); ok {
		sum.LowWatermark = int(lw)
	}

	if ms, ok := entry["min_start"].(float64); ok {
		sum.MinStart = int(ms)
	}

	metrics.StreamingSummaries = append(metrics.StreamingSummaries, sum)
}

func extractTransportAlignment(entry map[string]interface{}, metrics *CallMetrics) {
	if format, ok := entry["audiosocket_format"].(string); ok {
		metrics.AudioSocketFormat = format
	}

	if format, ok := entry["provider_input_format"].(string); ok {
		metrics.ProviderInputFormat = format
	}

	if format, ok := entry["provider_output_format"].(string); ok {
		metrics.ProviderOutputFormat = format
	}

	if sr, ok := entry["sample_rate"].(float64); ok {
		metrics.SampleRate = int(sr)
	}
}

func extractVADSettings(entry map[string]interface{}, metrics *CallMetrics) {
	if metrics.VADSettings == nil {
		metrics.VADSettings = &VADSettings{}
	}

	if agg, ok := entry["aggressiveness"].(float64); ok {
		metrics.VADSettings.WebRTCAggressiveness = int(agg)
	}
}

func abs(x float64) float64 {
	if x < 0 {
		return -x
	}
	return x
}

// FormatMetricsForLLM formats metrics into human-readable text for LLM prompt
func (m *CallMetrics) FormatForLLM() string {
	var out strings.Builder

	out.WriteString("=== CALL METRICS ===\n\n")

	// Provider bytes analysis
	if len(m.ProviderSegments) > 0 {
		out.WriteString("Provider Bytes Tracking:\n")
		out.WriteString(fmt.Sprintf("  Total segments: %d\n", len(m.ProviderSegments)))
		out.WriteString(fmt.Sprintf("  Total provider bytes: %d\n", m.ProviderBytesTotal))
		out.WriteString(fmt.Sprintf("  Total enqueued bytes: %d\n", m.EnqueuedBytesTotal))

		if m.ProviderBytesTotal > 0 {
			actualRatio := float64(m.EnqueuedBytesTotal) / float64(m.ProviderBytesTotal)
			out.WriteString(fmt.Sprintf("  Overall ratio: %.3f\n", actualRatio))

			if actualRatio < 0.95 || actualRatio > 1.05 {
				out.WriteString(fmt.Sprintf("  ⚠️  ISSUE: Ratio should be ~1.0, got %.3f\n", actualRatio))
			}
		}

		if m.WorstEnqueuedRatio < 0.95 || m.WorstEnqueuedRatio > 1.05 {
			out.WriteString(fmt.Sprintf("  ⚠️  Worst segment ratio: %.3f\n", m.WorstEnqueuedRatio))
		}
		out.WriteString("\n")
	}

	// Streaming performance
	if len(m.StreamingSummaries) > 0 {
		out.WriteString("Streaming Performance:\n")
		out.WriteString(fmt.Sprintf("  Streaming segments: %d\n", len(m.StreamingSummaries)))

		if m.DriftAssessmentSkipped != "" {
			out.WriteString(fmt.Sprintf("  Drift assessment skipped: %s\n", m.DriftAssessmentSkipped))
		} else if abs(m.WorstDriftPct) > 10.0 {
			out.WriteString(fmt.Sprintf("  ⚠️  ISSUE: Worst drift: %.1f%% (should be <10%%)\n", m.WorstDriftPct))
		} else {
			out.WriteString(fmt.Sprintf("  Drift: %.1f%%\n", m.WorstDriftPct))
		}

		if m.UnderflowCount > 0 {
			out.WriteString(fmt.Sprintf("  Underflows: %d (%.2f%% of estimated frames)\n", m.UnderflowCount, m.UnderflowRatePct()))
		}
		out.WriteString("\n")
	}

	// VAD settings
	if m.VADSettings != nil {
		out.WriteString("VAD Configuration:\n")
		out.WriteString(fmt.Sprintf("  WebRTC Aggressiveness: %d\n", m.VADSettings.WebRTCAggressiveness))

		if m.VADSettings.WebRTCAggressiveness == 0 {
			out.WriteString("  ⚠️  ISSUE: Level 0 is too sensitive, causes echo detection\n")
			out.WriteString("  Recommendation: Set to 1 for OpenAI Realtime\n")
		}
		out.WriteString("\n")
	}

	// Audio gating
	if m.GateClosures > 0 {
		out.WriteString(fmt.Sprintf("Audio Gate Closures: %d\n", m.GateClosures))

		if m.GateFlutterDetected {
			out.WriteString("  ⚠️  CRITICAL: Gate flutter detected (>50 closures)\n")
			out.WriteString("  This causes echo leakage and self-interruption\n")
		}
		out.WriteString("\n")
	}

	// Transport/Format
	if m.AudioSocketFormat != "" || m.ProviderInputFormat != "" {
		transport := ""
		if m.FormatAlignment != nil {
			transport = strings.ToLower(strings.TrimSpace(m.FormatAlignment.ConfigAudioTransport))
		}
		out.WriteString("Transport Configuration:\n")
		if transport != "" {
			out.WriteString(fmt.Sprintf("  Transport: %s\n", transport))
		}
		if transport == "audiosocket" && m.AudioSocketFormat != "" {
			out.WriteString(fmt.Sprintf("  AudioSocket format: %s\n", m.AudioSocketFormat))
		}
		if m.ProviderInputFormat != "" {
			out.WriteString(fmt.Sprintf("  Provider input format: %s\n", m.ProviderInputFormat))
		}
		if m.ProviderOutputFormat != "" {
			out.WriteString(fmt.Sprintf("  Provider output format: %s\n", m.ProviderOutputFormat))
		}
		if m.SampleRate > 0 {
			out.WriteString(fmt.Sprintf("  Sample rate: %d Hz\n", m.SampleRate))
		}

		// Check for mismatches
		if transport == "audiosocket" && m.AudioSocketFormat != "" && m.AudioSocketFormat != "slin" {
			out.WriteString("  ⚠️  ISSUE: AudioSocket should use 'slin' format\n")
		}
		out.WriteString("\n")
	}

	// Config errors
	if len(m.ConfigErrors) > 0 {
		out.WriteString("Configuration Errors:\n")
		for _, err := range m.ConfigErrors {
			out.WriteString(fmt.Sprintf("  • %s\n", err))
		}
		out.WriteString("\n")
	}

	return out.String()
}
