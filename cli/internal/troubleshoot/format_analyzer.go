package troubleshoot

import (
	"fmt"
	"os/exec"
	"strings"

	"gopkg.in/yaml.v3"
)

// AnalyzeFormatAlignment checks config vs runtime format/sampling alignment
func AnalyzeFormatAlignment(metrics *CallMetrics) *FormatAlignment {
	alignment := &FormatAlignment{
		Issues: []string{},
	}

	// Load config from server
	config := loadConfigFromServer()
	if config != nil {
		alignment.ConfigAudioTransport = strings.ToLower(strings.TrimSpace(getTopString(config, "audio_transport")))
		alignment.ConfigAudioSocketFormat = getString(config, "audiosocket", "format")
		alignment.ConfigSampleRate = getInt(config, "streaming", "sample_rate")

		// Try to get provider settings (depends on active pipeline)
		// This is a simplified version - production would need pipeline detection
		if providers, ok := config["providers"].(map[string]interface{}); ok {
			if deepgram, ok := providers["deepgram"].(map[string]interface{}); ok {
				alignment.ConfigProviderInputFormat = getStringDirect(deepgram, "input_encoding")
				alignment.ConfigProviderOutputFormat = getStringDirect(deepgram, "output_encoding")
			}
			if openai, ok := providers["openai_realtime"].(map[string]interface{}); ok {
				alignment.ConfigProviderInputFormat = getStringDirect(openai, "input_encoding")
				alignment.ConfigProviderOutputFormat = getStringDirect(openai, "output_encoding")
			}
		}
	}

	// Get runtime values from logs
	alignment.RuntimeAudioSocketFormat = metrics.AudioSocketFormat
	alignment.RuntimeProviderInputFormat = metrics.ProviderInputFormat
	alignment.RuntimeSampleRate = metrics.SampleRate

	// Analyze frame sizes
	analyzeFrameSizes(alignment, metrics)

	// Detect misalignments
	detectMisalignments(alignment)

	return alignment
}

func analyzeFrameSizes(alignment *FormatAlignment, metrics *CallMetrics) {
	// Calculate expected frame size based on format
	if alignment.RuntimeAudioSocketFormat == "slin" || alignment.RuntimeAudioSocketFormat == "slin16" {
		// PCM16 @ 8kHz, 20ms frame = 320 bytes
		alignment.ExpectedFrameSize = 320
	} else if alignment.RuntimeAudioSocketFormat == "ulaw" || alignment.RuntimeAudioSocketFormat == "mulaw" {
		// μ-law @ 8kHz, 20ms frame = 160 bytes
		alignment.ExpectedFrameSize = 160
	}

	// Observe actual frame sizes from provider bytes
	if len(metrics.ProviderSegments) > 0 {
		// Take first segment as sample
		alignment.ObservedFrameSize = metrics.ProviderSegments[0].ProviderBytes / 10 // Rough estimate per frame
	}
}

func detectMisalignments(alignment *FormatAlignment) {
	transport := strings.ToLower(strings.TrimSpace(alignment.ConfigAudioTransport))

	// Check AudioSocket format mismatch
	if transport == "audiosocket" && alignment.ConfigAudioSocketFormat != "" && alignment.RuntimeAudioSocketFormat != "" {
		if alignment.ConfigAudioSocketFormat != alignment.RuntimeAudioSocketFormat {
			alignment.AudioSocketMismatch = true
			alignment.Issues = append(alignment.Issues, fmt.Sprintf(
				"AudioSocket format mismatch: config=%s, runtime=%s",
				alignment.ConfigAudioSocketFormat, alignment.RuntimeAudioSocketFormat))
		}
	}

	// Check provider format mismatch
	if alignment.ConfigProviderInputFormat != "" && alignment.RuntimeProviderInputFormat != "" {
		configNorm := normalizeFormat(alignment.ConfigProviderInputFormat)
		runtimeNorm := normalizeFormat(alignment.RuntimeProviderInputFormat)
		if configNorm != runtimeNorm {
			alignment.ProviderFormatMismatch = true
			alignment.Issues = append(alignment.Issues, fmt.Sprintf(
				"Provider input format mismatch: config=%s, runtime=%s",
				alignment.ConfigProviderInputFormat, alignment.RuntimeProviderInputFormat))
		}
	}

	// Check AudioSocket format is correct (golden baseline)
	if transport == "audiosocket" && alignment.RuntimeAudioSocketFormat != "" && alignment.RuntimeAudioSocketFormat != "slin" {
		alignment.AudioSocketMismatch = true
		alignment.Issues = append(alignment.Issues, fmt.Sprintf(
			"AudioSocket format should be 'slin' (golden baseline), got '%s'",
			alignment.RuntimeAudioSocketFormat))
	}

	// Check frame size alignment
	if alignment.ExpectedFrameSize > 0 && alignment.ObservedFrameSize > 0 {
		// Allow 10% tolerance
		diff := alignment.ExpectedFrameSize - alignment.ObservedFrameSize
		if diff < 0 {
			diff = -diff
		}
		tolerance := alignment.ExpectedFrameSize / 10
		if diff > tolerance {
			alignment.FrameSizeMismatch = true
			alignment.Issues = append(alignment.Issues, fmt.Sprintf(
				"Frame size mismatch: expected ~%d bytes, observed ~%d bytes",
				alignment.ExpectedFrameSize, alignment.ObservedFrameSize))
		}
	}
}

func normalizeFormat(format string) string {
	format = strings.ToLower(format)
	// Normalize various encodings to standard names
	switch format {
	case "mulaw", "ulaw", "pcmu":
		return "mulaw"
	case "alaw", "pcma":
		return "alaw"
	case "linear16", "pcm16", "slin", "slin16":
		return "pcm16"
	case "linear24", "pcm24":
		return "pcm24"
	default:
		return format
	}
}

func loadConfigFromServer() map[string]interface{} {
	// Try to fetch config from Docker container
	cmd := exec.Command("docker", "exec", "ai_engine", "cat", "/app/config/ai-agent.yaml")
	output, err := cmd.CombinedOutput()
	if err != nil {
		return nil
	}

	var config map[string]interface{}
	if err := yaml.Unmarshal(output, &config); err != nil {
		return nil
	}

	return config
}

func getString(config map[string]interface{}, section, key string) string {
	if sec, ok := config[section].(map[string]interface{}); ok {
		if val, ok := sec[key].(string); ok {
			return val
		}
	}
	return ""
}

func getTopString(config map[string]interface{}, key string) string {
	if val, ok := config[key].(string); ok {
		return val
	}
	return ""
}

func getStringDirect(section map[string]interface{}, key string) string {
	if val, ok := section[key].(string); ok {
		return val
	}
	return ""
}

func getInt(config map[string]interface{}, section, key string) int {
	if sec, ok := config[section].(map[string]interface{}); ok {
		if val, ok := sec[key].(int); ok {
			return val
		}
		// Try float64 (YAML numbers default to float64)
		if val, ok := sec[key].(float64); ok {
			return int(val)
		}
	}
	return 0
}

// FormatMetricsForLLM formats format alignment info for LLM
func (fa *FormatAlignment) FormatForLLM() string {
	if fa == nil || len(fa.Issues) == 0 {
		return ""
	}

	var out strings.Builder
	out.WriteString("\n=== FORMAT/SAMPLING ALIGNMENT ===\n\n")

	out.WriteString("Configuration vs Runtime:\n")
	if strings.ToLower(strings.TrimSpace(fa.ConfigAudioTransport)) != "" {
		out.WriteString(fmt.Sprintf("  Transport: %s\n", fa.ConfigAudioTransport))
	}
	if strings.ToLower(strings.TrimSpace(fa.ConfigAudioTransport)) == "audiosocket" && fa.ConfigAudioSocketFormat != "" {
		out.WriteString(fmt.Sprintf("  AudioSocket: config=%s, runtime=%s",
			fa.ConfigAudioSocketFormat, fa.RuntimeAudioSocketFormat))
		if fa.AudioSocketMismatch {
			out.WriteString(" ❌ MISMATCH\n")
		} else {
			out.WriteString(" ✅\n")
		}
	}

	if fa.ConfigProviderInputFormat != "" {
		out.WriteString(fmt.Sprintf("  Provider input: config=%s, runtime=%s",
			fa.ConfigProviderInputFormat, fa.RuntimeProviderInputFormat))
		if fa.ProviderFormatMismatch {
			out.WriteString(" ❌ MISMATCH\n")
		} else {
			out.WriteString(" ✅\n")
		}
	}

	if len(fa.Issues) > 0 {
		out.WriteString("\n⚠️  ALIGNMENT ISSUES:\n")
		for i, issue := range fa.Issues {
			out.WriteString(fmt.Sprintf("%d. %s\n", i+1, issue))
		}
	}

	return out.String()
}
