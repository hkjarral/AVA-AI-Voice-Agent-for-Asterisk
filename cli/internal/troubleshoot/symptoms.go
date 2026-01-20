package troubleshoot

import (
	"fmt"
	"strings"
)

// SymptomChecker performs symptom-specific analysis
type SymptomChecker struct {
	symptom string
}

// NewSymptomChecker creates a symptom checker
func NewSymptomChecker(symptom string) *SymptomChecker {
	return &SymptomChecker{symptom: symptom}
}

// AnalyzeSymptom performs targeted analysis based on symptom
func (sc *SymptomChecker) AnalyzeSymptom(analysis *Analysis, logData string) {
	switch sc.symptom {
	case "no-audio":
		sc.analyzeNoAudio(analysis, logData)
	case "garbled":
		sc.analyzeGarbled(analysis, logData)
	case "echo":
		sc.analyzeEcho(analysis, logData)
	case "interruption":
		sc.analyzeInterruption(analysis, logData)
	case "one-way":
		sc.analyzeOneWay(analysis, logData)
	}
}

// analyzeNoAudio checks for complete audio failure
func (sc *SymptomChecker) analyzeNoAudio(analysis *Analysis, logData string) {
	analysis.SymptomAnalysis = &SymptomAnalysis{
		Symptom:     "no-audio",
		Description: "Complete silence - no audio in either direction",
		Findings:    []string{},
		RootCauses:  []string{},
		Actions:     []string{},
	}

	lower := strings.ToLower(logData)
	transport := strings.ToLower(strings.TrimSpace(analysis.AudioTransport))

	if transport == "audiosocket" || transport == "" {
		// Check AudioSocket connection
		if !strings.Contains(lower, "\"audiosocket_channel_id\"") && !strings.Contains(lower, "audiosocket channel") {
			analysis.SymptomAnalysis.Findings = append(analysis.SymptomAnalysis.Findings,
				"❌ AudioSocket not detected in logs")
			analysis.SymptomAnalysis.RootCauses = append(analysis.SymptomAnalysis.RootCauses,
				"AudioSocket server not running or not configured")
			analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
				"Check audio_transport: audiosocket and audiosocket section in config/ai-agent.yaml")
			analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
				"Verify port 8090 is listening on the Asterisk side")
		}
	}

	if transport == "externalmedia" || transport == "" {
		// Check ExternalMedia RTP indicators
		if !strings.Contains(lower, "external media") && !strings.Contains(lower, "\"external_media_id\"") {
			analysis.SymptomAnalysis.Findings = append(analysis.SymptomAnalysis.Findings,
				"❌ ExternalMedia RTP not detected in logs")
			analysis.SymptomAnalysis.RootCauses = append(analysis.SymptomAnalysis.RootCauses,
				"ExternalMedia channel not created/attached or RTP not reaching ai_engine")
			analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
				"Check audio_transport: externalmedia and external_media section in config/ai-agent.yaml")
			analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
				"Verify UDP port 18080 reachability (firewall/NAT) between Asterisk and ai_engine")
			analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
				"If behind NAT/VPN: set external_media.advertise_host to a reachable IP")
		}
	}

	// Check for connection errors
	if strings.Contains(lower, "connection refused") || strings.Contains(lower, "connection failed") {
		analysis.SymptomAnalysis.Findings = append(analysis.SymptomAnalysis.Findings,
			"❌ Connection errors detected")
		analysis.SymptomAnalysis.RootCauses = append(analysis.SymptomAnalysis.RootCauses,
			"Network connectivity issue")
		analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
			"Check network configuration")
		analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
			"Verify Asterisk and ai_engine can communicate")
	}

	// Check for media path issues
	if strings.Contains(lower, "media") && strings.Contains(lower, "not found") {
		analysis.SymptomAnalysis.Findings = append(analysis.SymptomAnalysis.Findings,
			"⚠️  Media file issues detected")
		analysis.SymptomAnalysis.RootCauses = append(analysis.SymptomAnalysis.RootCauses,
			"Missing or inaccessible media files")
		analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
			"Check /mnt/asterisk_media/ai-generated directory")
	}
}

// analyzeGarbled checks for audio quality issues
func (sc *SymptomChecker) analyzeGarbled(analysis *Analysis, logData string) {
	analysis.SymptomAnalysis = &SymptomAnalysis{
		Symptom:     "garbled",
		Description: "Distorted, fast, slow, or choppy audio",
		Findings:    []string{},
		RootCauses:  []string{},
		Actions:     []string{},
	}

	lower := strings.ToLower(logData)
	transport := strings.ToLower(strings.TrimSpace(analysis.AudioTransport))

	// Check for underflows
	if strings.Contains(lower, "underflow") {
		count := strings.Count(lower, "underflow")
		analysis.SymptomAnalysis.Findings = append(analysis.SymptomAnalysis.Findings,
			fmt.Sprintf("❌ Jitter buffer underflows detected (%d occurrences)", count))
		analysis.SymptomAnalysis.RootCauses = append(analysis.SymptomAnalysis.RootCauses,
			"Audio pacing mismatch - playback too fast for buffer")
		analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
			"Increase jitter_buffer_ms in streaming config (try 100ms)")
		analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
			"Check provider_bytes calculation accuracy")
	}

	// Check for format issues
	if strings.Contains(lower, "format") && (strings.Contains(lower, "mismatch") || strings.Contains(lower, "error")) {
		analysis.SymptomAnalysis.Findings = append(analysis.SymptomAnalysis.Findings,
			"⚠️  Audio format issues detected")
		analysis.SymptomAnalysis.RootCauses = append(analysis.SymptomAnalysis.RootCauses,
			"Audio codec mismatch between components")
		if transport == "externalmedia" {
			analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
				"Verify external_media.codec matches RTP wire codec (typically ulaw@8k for telephony)")
			analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
				"Verify external_media.format/sample_rate alignment with provider expectations (avoid unnecessary resampling)")
		} else {
			analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
				"Verify audiosocket.format matches Asterisk dialplan (slin recommended)")
		}
		analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
			"Check transcoding configuration")
	}

	// Check for normalizer issues
	if !strings.Contains(lower, "normalizer") {
		analysis.SymptomAnalysis.Findings = append(analysis.SymptomAnalysis.Findings,
			"⚠️  Normalizer not active in logs")
		analysis.SymptomAnalysis.RootCauses = append(analysis.SymptomAnalysis.RootCauses,
			"Audio normalization not applying")
		analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
			"Check normalizer configuration and logging")
	}

	// Check sample rate
	if strings.Contains(lower, "sample rate") || strings.Contains(lower, "sample_rate") {
		analysis.SymptomAnalysis.Findings = append(analysis.SymptomAnalysis.Findings,
			"⚠️  Sample rate configuration detected")
		analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
			"Verify sample rate consistency across transport ↔ provider")
	}
}

// analyzeEcho checks for echo and self-hearing issues
func (sc *SymptomChecker) analyzeEcho(analysis *Analysis, logData string) {
	analysis.SymptomAnalysis = &SymptomAnalysis{
		Symptom:     "echo",
		Description: "Agent hears its own output, causing confusion",
		Findings:    []string{},
		RootCauses:  []string{},
		Actions:     []string{},
	}

	lower := strings.ToLower(logData)

	// Check for VAD issues
	if strings.Contains(lower, "vad") || strings.Contains(lower, "voice activity") {
		analysis.SymptomAnalysis.Findings = append(analysis.SymptomAnalysis.Findings,
			"⚠️  VAD configuration detected")
		analysis.SymptomAnalysis.RootCauses = append(analysis.SymptomAnalysis.RootCauses,
			"VAD may be too sensitive, detecting echo as speech")
		analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
			"For OpenAI Realtime: Set webrtc_aggressiveness: 1")
		analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
			"Check confidence_threshold (try 0.6 or higher)")
	}

	// Check for audio gate issues
	if strings.Contains(lower, "gate") || strings.Contains(lower, "gating") {
		analysis.SymptomAnalysis.Findings = append(analysis.SymptomAnalysis.Findings,
			"⚠️  Audio gating activity detected")
		analysis.SymptomAnalysis.RootCauses = append(analysis.SymptomAnalysis.RootCauses,
			"Audio gate may be opening/closing rapidly")
		analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
			"Check post_tts_end_protection_ms setting")
		analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
			"Verify gate isn't fluttering (50+ closures = issue)")
	}

	// Check for echo cancellation
	if strings.Contains(lower, "echo") {
		count := strings.Count(lower, "echo")
		analysis.SymptomAnalysis.Findings = append(analysis.SymptomAnalysis.Findings,
			fmt.Sprintf("❌ Echo mentions in logs (%d occurrences)", count))
		analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
			"Let provider handle echo cancellation (OpenAI has built-in)")
		analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
			"Reduce local VAD sensitivity")
	}
}

// analyzeInterruption checks for self-interruption loops
func (sc *SymptomChecker) analyzeInterruption(analysis *Analysis, logData string) {
	analysis.SymptomAnalysis = &SymptomAnalysis{
		Symptom:     "interruption",
		Description: "Agent interrupts itself mid-sentence",
		Findings:    []string{},
		RootCauses:  []string{},
		Actions:     []string{},
	}

	lower := strings.ToLower(logData)

	// Check for interruption events
	if strings.Contains(lower, "interrupt") {
		count := strings.Count(lower, "interrupt")
		analysis.SymptomAnalysis.Findings = append(analysis.SymptomAnalysis.Findings,
			fmt.Sprintf("❌ Interruptions detected (%d occurrences)", count))
		analysis.SymptomAnalysis.RootCauses = append(analysis.SymptomAnalysis.RootCauses,
			"Agent hearing its own audio output")
	}

	// Related to echo issues
	analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
		"This is typically an echo/VAD issue")
	analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
		"See 'echo' symptom analysis for details")
	analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
		"Adjust VAD aggressiveness and post-TTS protection")
}

// analyzeOneWay checks for uni-directional audio
func (sc *SymptomChecker) analyzeOneWay(analysis *Analysis, logData string) {
	analysis.SymptomAnalysis = &SymptomAnalysis{
		Symptom:     "one-way",
		Description: "Audio works in only one direction",
		Findings:    []string{},
		RootCauses:  []string{},
		Actions:     []string{},
	}

	lower := strings.ToLower(logData)

	// Check transcription (caller → agent)
	hasTranscription := strings.Contains(lower, "transcription") || strings.Contains(lower, "transcript")
	if !hasTranscription {
		analysis.SymptomAnalysis.Findings = append(analysis.SymptomAnalysis.Findings,
			"❌ No transcription detected (caller → agent broken)")
		analysis.SymptomAnalysis.RootCauses = append(analysis.SymptomAnalysis.RootCauses,
			"STT provider not receiving audio or not working")
		analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
			"Check STT provider API key and connectivity")
	}

	// Check playback (agent → caller)
	hasPlayback := strings.Contains(lower, "playback") || strings.Contains(lower, "playing")
	if !hasPlayback {
		analysis.SymptomAnalysis.Findings = append(analysis.SymptomAnalysis.Findings,
			"❌ No playback detected (agent → caller broken)")
		analysis.SymptomAnalysis.RootCauses = append(analysis.SymptomAnalysis.RootCauses,
			"TTS provider or playback system not working")
		analysis.SymptomAnalysis.Actions = append(analysis.SymptomAnalysis.Actions,
			"Check TTS provider API key and connectivity")
	}

	if hasTranscription && !hasPlayback {
		analysis.SymptomAnalysis.Findings = append(analysis.SymptomAnalysis.Findings,
			"ℹ️  Caller can be heard but agent cannot be heard")
	} else if !hasTranscription && hasPlayback {
		analysis.SymptomAnalysis.Findings = append(analysis.SymptomAnalysis.Findings,
			"ℹ️  Agent can be heard but caller cannot be heard")
	}
}

// SymptomAnalysis holds symptom-specific findings
type SymptomAnalysis struct {
	Symptom     string
	Description string
	Findings    []string
	RootCauses  []string
	Actions     []string
}
