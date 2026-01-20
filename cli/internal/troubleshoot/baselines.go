package troubleshoot

import "fmt"

// GoldenBaseline holds validated production configurations
type GoldenBaseline struct {
	Name        string
	Description string
	Config      map[string]interface{}
	Metrics     map[string]interface{}
	Reference   string
}

// GetGoldenBaselines returns validated production configurations
func GetGoldenBaselines() map[string]*GoldenBaseline {
	return map[string]*GoldenBaseline{
		"openai_realtime": {
			Name:        "OpenAI Realtime",
			Description: "Validated OpenAI Realtime API configuration (Oct 26, 2025)",
			Config: map[string]interface{}{
				"vad": map[string]interface{}{
					"webrtc_aggressiveness":     1, // CRITICAL: Level 0 = too sensitive, causes echo
					"enhanced_enabled":          true,
					"confidence_threshold":      0.6,
					"energy_threshold":          1500,
					"webrtc_start_frames":       2,
					"webrtc_end_silence_frames": 15,
				},
				"barge_in": map[string]interface{}{
					"post_tts_end_protection_ms": 500, // Prevent agent hearing its own audio
				},
				"transport": map[string]interface{}{
					"audiosocket_format": "slin", // PCM16 (slin) for AudioSocket wire
				},
			},
			Metrics: map[string]interface{}{
				"gate_closures":        "1-2 per call", // >50 = flutter problem
				"buffered_chunks":      0,              // Should be 0 with level 1
				"self_interruptions":   0,
				"drift_pct":            "<10%", // Acceptable range
				"provider_bytes_ratio": 1.0,    // Must be 1.0 (no pacing bugs)
			},
			Reference: "Call 1761449250.2163 - Duration: 45.9s, SNR: 64.7 dB",
		},

		"deepgram_standard": {
			Name:        "Deepgram Standard",
			Description: "Validated Deepgram configuration with AudioSocket",
			Config: map[string]interface{}{
				"deepgram": map[string]interface{}{
					"model":           "nova-2-general",
					"language":        "en",
					"encoding":        "mulaw", // Deepgram expects mulaw
					"sample_rate":     8000,    // 8kHz for telephony
					"channels":        1,
					"interim_results": true,
					"vad_events":      true,
					"smart_format":    true,
				},
				"transport": map[string]interface{}{
					"audiosocket_format": "slin",       // AudioSocket wire = slin
					"transcoding":        "slin→mulaw", // Transcode to Deepgram format
				},
			},
			Metrics: map[string]interface{}{
				"provider_bytes_ratio": 1.0, // Must be 1.0
				"drift_pct":            "<10%",
				"underflow_count":      0, // Should be 0
			},
			Reference: "Production baseline - AudioSocket=slin, provider=mulaw@8k",
		},

		"streaming_performance": {
			Name:        "Streaming Performance",
			Description: "Validated streaming playback configuration",
			Config: map[string]interface{}{
				"streaming": map[string]interface{}{
					"min_start_ms":        300,  // 300ms before starting playback
					"low_watermark_ms":    200,  // 200ms jitter buffer
					"provider_grace_ms":   500,  // 500ms grace period
					"jitter_buffer_ms":    100,  // 100ms jitter tolerance
					"fallback_timeout_ms": 5000, // 5s before fallback
				},
			},
			Metrics: map[string]interface{}{
				"drift_pct":            "<10%",                   // Critical threshold
				"underflow_count":      0,                        // Should be 0
				"provider_bytes_ratio": 1.0,                      // Must be 1.0
				"bytes_sent":           ">50000",                 // Reasonable for 10s segment
				"effective_seconds":    "wall_seconds * 0.9-1.1", // Within 10%
			},
			Reference: "Golden baseline metrics from clean audio calls",
		},
	}
}

// CompareToBaseline compares current metrics to golden baseline
func CompareToBaseline(metrics *CallMetrics, baselineName string) *BaselineComparison {
	baselines := GetGoldenBaselines()
	baseline, exists := baselines[baselineName]
	if !exists {
		return nil
	}

	comparison := &BaselineComparison{
		BaselineName: baseline.Name,
		Deviations:   []Deviation{},
		Compliant:    []string{},
	}

	// Check VAD aggressiveness (OpenAI Realtime)
	if baselineName == "openai_realtime" && metrics.VADSettings != nil {
		expectedAgg := 1
		if metrics.VADSettings.WebRTCAggressiveness != expectedAgg {
			comparison.Deviations = append(comparison.Deviations, Deviation{
				Parameter:     "vad.webrtc_aggressiveness",
				CurrentValue:  fmt.Sprintf("%d", metrics.VADSettings.WebRTCAggressiveness),
				ExpectedValue: fmt.Sprintf("%d", expectedAgg),
				Severity:      "CRITICAL",
				Impact:        "Level 0 = too sensitive, detects echo as speech, causes self-interruption",
				Fix:           "Set webrtc_aggressiveness: 1 in config/ai-agent.yaml vad section",
			})
		} else {
			comparison.Compliant = append(comparison.Compliant, "VAD aggressiveness: 1 ✅")
		}
	}

	// Check gate closures
	if baselineName == "openai_realtime" && metrics.GateClosures > 0 {
		if metrics.GateFlutterDetected {
			comparison.Deviations = append(comparison.Deviations, Deviation{
				Parameter:     "gate_closures",
				CurrentValue:  fmt.Sprintf("%d", metrics.GateClosures),
				ExpectedValue: "1-2 per call",
				Severity:      "CRITICAL",
				Impact:        "Gate flutter (>50 closures) causes echo leakage and self-interruption",
				Fix:           "This is symptom of VAD issue. Fix: webrtc_aggressiveness: 1",
			})
		} else {
			comparison.Compliant = append(comparison.Compliant, fmt.Sprintf("Gate closures: %d (normal) ✅", metrics.GateClosures))
		}
	}

	// Check provider bytes ratio (all baselines)
	if len(metrics.ProviderSegments) > 0 {
		actualRatio := float64(metrics.EnqueuedBytesTotal) / float64(metrics.ProviderBytesTotal)
		if actualRatio < 0.95 || actualRatio > 1.05 {
			comparison.Deviations = append(comparison.Deviations, Deviation{
				Parameter:     "provider_bytes_ratio",
				CurrentValue:  fmt.Sprintf("%.3f", actualRatio),
				ExpectedValue: "1.000",
				Severity:      "CRITICAL",
				Impact:        "Pacing bug - breaks timing, causes garbled/fast/slow audio",
				Fix:           "Check StreamingPlaybackManager provider_bytes tracking in src/core/streaming_playback_manager.py",
			})
		} else {
			comparison.Compliant = append(comparison.Compliant, fmt.Sprintf("Provider bytes ratio: %.3f ✅", actualRatio))
		}
	}

	// Check drift percentage (all baselines)
	if metrics.WorstDriftPct != 0.0 {
		if absFloat(metrics.WorstDriftPct) > 10.0 {
			comparison.Deviations = append(comparison.Deviations, Deviation{
				Parameter:     "drift_pct",
				CurrentValue:  fmt.Sprintf("%.1f%%", metrics.WorstDriftPct),
				ExpectedValue: "<10%",
				Severity:      "HIGH",
				Impact:        "Timing mismatch - audio too fast/slow, playback/recording desync",
				Fix:           "Check streaming.min_start_ms (300), low_watermark_ms (200), jitter_buffer_ms (100)",
			})
		} else {
			comparison.Compliant = append(comparison.Compliant, fmt.Sprintf("Drift: %.1f%% ✅", metrics.WorstDriftPct))
		}
	}

	// Check underflows (streaming baseline)
	if metrics.UnderflowCount > 0 {
		comparison.Deviations = append(comparison.Deviations, Deviation{
			Parameter:     "underflow_count",
			CurrentValue:  fmt.Sprintf("%d", metrics.UnderflowCount),
			ExpectedValue: "0",
			Severity:      "HIGH",
			Impact:        "Jitter buffer starvation - causes stuttering, choppy audio",
			Fix:           "Increase streaming.jitter_buffer_ms from 100 to 150-200ms",
		})
	} else if len(metrics.StreamingSummaries) > 0 {
		comparison.Compliant = append(comparison.Compliant, "No underflows ✅")
	}

	// Check AudioSocket format (only when transport is actually AudioSocket)
	transport := ""
	if metrics.FormatAlignment != nil {
		transport = metrics.FormatAlignment.ConfigAudioTransport
	}
	if transport == "audiosocket" && metrics.AudioSocketFormat != "" {
		if metrics.AudioSocketFormat != "slin" {
			comparison.Deviations = append(comparison.Deviations, Deviation{
				Parameter:     "audiosocket_format",
				CurrentValue:  metrics.AudioSocketFormat,
				ExpectedValue: "slin",
				Severity:      "CRITICAL",
				Impact:        "Format mismatch - causes garbled audio, codec issues",
				Fix:           "Set audiosocket.format: slin in config/ai-agent.yaml AND AudioSocket(slin) in dialplan",
			})
		} else {
			comparison.Compliant = append(comparison.Compliant, "AudioSocket format: slin ✅")
		}
	}

	// Check Deepgram format (if applicable)
	if baselineName == "deepgram_standard" {
		if metrics.ProviderInputFormat != "" && metrics.ProviderInputFormat != "mulaw" {
			comparison.Deviations = append(comparison.Deviations, Deviation{
				Parameter:     "provider_input_format",
				CurrentValue:  metrics.ProviderInputFormat,
				ExpectedValue: "mulaw",
				Severity:      "HIGH",
				Impact:        "Deepgram expects mulaw@8kHz for telephony",
				Fix:           "Set deepgram.encoding: mulaw and sample_rate: 8000 in config",
			})
		}
	}

	return comparison
}

// BaselineComparison holds comparison results
type BaselineComparison struct {
	BaselineName string
	Deviations   []Deviation
	Compliant    []string
}

// Deviation represents a deviation from baseline
type Deviation struct {
	Parameter     string
	CurrentValue  string
	ExpectedValue string
	Severity      string // CRITICAL, HIGH, MEDIUM
	Impact        string
	Fix           string
}

func absFloat(x float64) float64 {
	if x < 0 {
		return -x
	}
	return x
}

// FormatComparisonForLLM formats baseline comparison for LLM prompt
func (bc *BaselineComparison) FormatForLLM() string {
	if bc == nil {
		return ""
	}

	var out string
	out += "\n=== GOLDEN BASELINE COMPARISON ===\n"
	out += fmt.Sprintf("Reference: %s\n\n", bc.BaselineName)

	if len(bc.Compliant) > 0 {
		out += "✅ Compliant with Baseline:\n"
		for _, item := range bc.Compliant {
			out += fmt.Sprintf("  %s\n", item)
		}
		out += "\n"
	}

	if len(bc.Deviations) > 0 {
		out += "⚠️  DEVIATIONS FROM GOLDEN BASELINE:\n\n"
		for i, dev := range bc.Deviations {
			out += fmt.Sprintf("%d. [%s] %s\n", i+1, dev.Severity, dev.Parameter)
			out += fmt.Sprintf("   Current: %s\n", dev.CurrentValue)
			out += fmt.Sprintf("   Expected: %s\n", dev.ExpectedValue)
			out += fmt.Sprintf("   Impact: %s\n", dev.Impact)
			out += fmt.Sprintf("   Fix: %s\n", dev.Fix)
			out += "\n"
		}
	}

	return out
}
