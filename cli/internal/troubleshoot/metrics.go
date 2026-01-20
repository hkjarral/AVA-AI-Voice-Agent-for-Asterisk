package troubleshoot

import (
	"encoding/json"
	"fmt"
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
	StreamingSummaries []StreamingSummary
	WorstDriftPct      float64
	UnderflowCount     int

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
	ConfigAudioTransport      string
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
		// Parse JSON logs
		var logEntry map[string]interface{}
		if err := json.Unmarshal([]byte(line), &logEntry); err != nil {
			continue // Not JSON, skip
		}

		event, _ := logEntry["event"].(string)

		switch event {
		case "PROVIDER SEGMENT BYTES":
			extractProviderBytes(logEntry, metrics)

		case "🎛️ STREAMING TUNING SUMMARY":
			extractStreamingSummary(logEntry, metrics)

		case "Transport alignment summary":
			extractTransportAlignment(logEntry, metrics)

		case "🎯 WebRTC VAD settings":
			extractVADSettings(logEntry, metrics)

		case "Streaming segment bytes summary v2":
			// Extract underflow count from segment summary
			// Check if this is a greeting segment
			streamID, _ := logEntry["stream_id"].(string)
			isGreeting := strings.Contains(streamID, "greeting")

			if uf, ok := logEntry["underflow_events"].(float64); ok {
				// Only count underflows from non-greeting segments
				// Greeting segments have underflows during conversation pauses (normal)
				if !isGreeting {
					metrics.UnderflowCount += int(uf)
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

		if abs(m.WorstDriftPct) > 10.0 {
			out.WriteString(fmt.Sprintf("  ⚠️  ISSUE: Worst drift: %.1f%% (should be <10%%)\n", m.WorstDriftPct))
		} else {
			out.WriteString(fmt.Sprintf("  Drift: %.1f%%\n", m.WorstDriftPct))
		}

		if m.UnderflowCount > 0 {
			out.WriteString(fmt.Sprintf("  ⚠️  ISSUE: %d underflow events detected\n", m.UnderflowCount))
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
