package troubleshoot

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

// LLMAnalyzer performs AI-powered diagnosis
type LLMAnalyzer struct {
	provider string
	apiKey   string
	model    string
}

// NewLLMAnalyzer creates an LLM analyzer
func NewLLMAnalyzer() (*LLMAnalyzer, error) {
	// Check for LLM configuration in environment
	provider := os.Getenv("TROUBLESHOOT_LLM_PROVIDER")
	if provider == "" {
		// Try to detect from available keys
		if os.Getenv("OPENAI_API_KEY") != "" {
			provider = "openai"
		} else if os.Getenv("ANTHROPIC_API_KEY") != "" {
			provider = "anthropic"
		} else {
			return nil, fmt.Errorf("no LLM provider configured")
		}
	}

	var apiKey, model string
	switch provider {
	case "openai":
		apiKey = os.Getenv("OPENAI_API_KEY")
		model = "gpt-4o-mini" // Fast and cost-effective
	case "anthropic":
		apiKey = os.Getenv("ANTHROPIC_API_KEY")
		model = "claude-3-haiku-20240307" // Fast and cost-effective
	default:
		return nil, fmt.Errorf("unsupported provider: %s", provider)
	}

	if apiKey == "" {
		return nil, fmt.Errorf("no API key found for provider: %s", provider)
	}

	return &LLMAnalyzer{
		provider: provider,
		apiKey:   apiKey,
		model:    model,
	}, nil
}

// AnalyzeWithLLM performs AI-powered analysis
func (llm *LLMAnalyzer) AnalyzeWithLLM(analysis *Analysis, logData string) (*LLMDiagnosis, error) {
	prompt := llm.buildPrompt(analysis, logData)

	var response string
	var err error

	switch llm.provider {
	case "openai":
		response, err = llm.callOpenAI(prompt)
	case "anthropic":
		response, err = llm.callAnthropic(prompt)
	default:
		return nil, fmt.Errorf("unsupported provider: %s", llm.provider)
	}

	if err != nil {
		return nil, err
	}

	return &LLMDiagnosis{
		Provider: llm.provider,
		Model:    llm.model,
		Analysis: response,
	}, nil
}

// buildPrompt constructs the LLM prompt
func (llm *LLMAnalyzer) buildPrompt(analysis *Analysis, logData string) string {
	var prompt strings.Builder

	prompt.WriteString("You are an expert in diagnosing Asterisk AI voice agent issues. ")
	prompt.WriteString("Analyze the following call logs and provide a concise diagnosis.\n")
	prompt.WriteString("Be evidence-driven: if the call looks healthy, do NOT invent problems or propose config changes.\n\n")

	prompt.WriteString("Call ID: " + analysis.CallID + "\n\n")

	// Log-derived header snapshot (preferred over guessing).
	if analysis.Header != nil {
		prompt.WriteString("RCA Header (log-derived):\n")
		if analysis.Header.CallerNumber != "" {
			prompt.WriteString(fmt.Sprintf("- Caller: %s\n", analysis.Header.CallerNumber))
		}
		if analysis.Header.CalledNumber != "" {
			prompt.WriteString(fmt.Sprintf("- Called: %s\n", analysis.Header.CalledNumber))
		}
		if analysis.Header.ContextName != "" {
			prompt.WriteString(fmt.Sprintf("- Context: %s\n", analysis.Header.ContextName))
		}
		if analysis.Header.ProviderName != "" {
			prompt.WriteString(fmt.Sprintf("- Provider: %s\n", analysis.Header.ProviderName))
		}
		if analysis.Header.AudioTransport != "" {
			prompt.WriteString(fmt.Sprintf("- Transport: %s\n", analysis.Header.AudioTransport))
		}
		if analysis.Header.TransportProfileEncoding != "" || analysis.Header.TransportProfileSampleRate > 0 {
			prompt.WriteString(fmt.Sprintf("- TransportProfile: %s@%d\n", analysis.Header.TransportProfileEncoding, analysis.Header.TransportProfileSampleRate))
		}
		if analysis.Header.StreamingSampleRate > 0 || analysis.Header.StreamingJitterBufferMs > 0 {
			prompt.WriteString(fmt.Sprintf("- Streaming: sample_rate=%d jitter_buffer_ms=%d\n", analysis.Header.StreamingSampleRate, analysis.Header.StreamingJitterBufferMs))
		}
		prompt.WriteString("\n")
	}

	// Pipeline status
	prompt.WriteString("Pipeline Status:\n")
	if analysis.AudioTransport != "" {
		prompt.WriteString(fmt.Sprintf("- Transport: %s\n", analysis.AudioTransport))
	}
	transport := strings.ToLower(strings.TrimSpace(analysis.AudioTransport))
	if transport == "externalmedia" {
		prompt.WriteString(fmt.Sprintf("- AudioSocket detected: %v (NOT APPLICABLE in ExternalMedia mode)\n", analysis.HasAudioSocket))
		prompt.WriteString(fmt.Sprintf("- ExternalMedia detected: %v\n", analysis.HasExternalMedia))
	} else if transport == "audiosocket" {
		prompt.WriteString(fmt.Sprintf("- AudioSocket detected: %v\n", analysis.HasAudioSocket))
		prompt.WriteString(fmt.Sprintf("- ExternalMedia detected: %v (NOT APPLICABLE in AudioSocket mode)\n", analysis.HasExternalMedia))
	} else {
		prompt.WriteString(fmt.Sprintf("- AudioSocket detected: %v\n", analysis.HasAudioSocket))
		prompt.WriteString(fmt.Sprintf("- ExternalMedia detected: %v\n", analysis.HasExternalMedia))
	}
	prompt.WriteString(fmt.Sprintf("- Transcription: %v\n", analysis.HasTranscription))
	prompt.WriteString(fmt.Sprintf("- Playback: %v\n", analysis.HasPlayback))
	prompt.WriteString("\n")

	// Issues found
	if len(analysis.Errors) > 0 {
		prompt.WriteString(fmt.Sprintf("Errors found: %d\n", len(analysis.Errors)))
		count := 5
		if len(analysis.Errors) < 5 {
			count = len(analysis.Errors)
		}
		for i := 0; i < count; i++ {
			prompt.WriteString(fmt.Sprintf("- %s\n", truncate(analysis.Errors[i], 200)))
		}
		prompt.WriteString("\n")
	}

	if len(analysis.Warnings) > 0 {
		prompt.WriteString(fmt.Sprintf("Warnings found: %d\n", len(analysis.Warnings)))
		count := 5
		if len(analysis.Warnings) < 5 {
			count = len(analysis.Warnings)
		}
		for i := 0; i < count; i++ {
			prompt.WriteString(fmt.Sprintf("- %s\n", truncate(analysis.Warnings[i], 200)))
		}
		prompt.WriteString("\n")
	}

	if len(analysis.AudioIssues) > 0 {
		prompt.WriteString("Audio Issues:\n")
		for _, issue := range analysis.AudioIssues {
			prompt.WriteString(fmt.Sprintf("- %s\n", issue))
		}
		prompt.WriteString("\n")
	}

	// Symptom if specified
	if analysis.Symptom != "" {
		prompt.WriteString(fmt.Sprintf("Reported Symptom: %s\n\n", analysis.Symptom))
	}

	// Extracted metrics (CRITICAL for diagnosis)
	if analysis.Metrics != nil {
		prompt.WriteString(analysis.Metrics.FormatForLLM())
	}

	// Golden baseline comparison (PROVIDES EXACT FIXES)
	if analysis.BaselineComparison != nil {
		prompt.WriteString(analysis.BaselineComparison.FormatForLLM())
		prompt.WriteString("IMPORTANT: Use the exact configuration values from the golden baseline deviations above.\n")
		prompt.WriteString("These are VALIDATED production values that are known to work.\n\n")
	}

	// Format/Sampling alignment (CRITICAL FOR AUDIO QUALITY)
	if analysis.Metrics != nil && analysis.Metrics.FormatAlignment != nil {
		prompt.WriteString(analysis.Metrics.FormatAlignment.FormatForLLM())
		prompt.WriteString("CRITICAL: Format mismatches cause garbled audio, distortion, or complete audio failure.\n")
		if strings.ToLower(strings.TrimSpace(analysis.AudioTransport)) == "audiosocket" {
			prompt.WriteString("Golden baseline: audiosocket.format=slin, provider transcodes as needed.\n\n")
		} else if strings.ToLower(strings.TrimSpace(analysis.AudioTransport)) == "externalmedia" {
			prompt.WriteString("Golden baseline: external_media.codec matches RTP wire codec (typically ulaw@8k); provider transcodes/resamples as needed.\n\n")
		} else {
			prompt.WriteString("Golden baseline depends on transport (audiosocket vs externalmedia); validate transport/profile alignment first.\n\n")
		}
	}

	// Drift is often a sample-rate mismatch; avoid recommending jitter-buffer tweaks unless there are underflows.
	if analysis.Metrics != nil && metricsHasEvidence(analysis.Metrics) {
		prompt.WriteString("\nDRIFT GUIDANCE:\n")
		prompt.WriteString("- If drift is worse than 10% and underflows are 0, suspect a sample-rate mismatch/resampling bug (not jitter buffer tuning).\n")
		prompt.WriteString("- For google_live, Gemini output is typically 24000 Hz; if config assumes 16000 Hz while provider sends 24000 Hz, audio will sound slowed down.\n")
		prompt.WriteString("- Do NOT suggest changing jitter_buffer_ms/min_start_ms/low_watermark_ms unless underflows/jitter evidence is present.\n\n")
	}

	// Sample logs (truncated)
	prompt.WriteString("Sample Log Lines:\n")
	lines := strings.Split(logData, "\n")
	count := 10
	if len(lines) < 10 {
		count = len(lines)
	}
	for i := 0; i < count; i++ {
		if lines[i] != "" {
			prompt.WriteString(truncate(lines[i], 200) + "\n")
		}
	}
	prompt.WriteString("\n")

	prompt.WriteString("Please provide:\n")
	prompt.WriteString("1. Root Cause: Identify the root cause based on golden baseline deviations (if any)\n")
	prompt.WriteString("   - Prioritize CRITICAL severity deviations first\n")
	prompt.WriteString("   - Reference the exact current vs expected values shown above\n")
	prompt.WriteString("   - IMPORTANT: Greeting segments have high drift and underflows during conversation pauses - this is NORMAL\n")
	prompt.WriteString("   - If provider_bytes ratio is 1.0 and drift is only from greeting segments, call is GOOD\n")
	prompt.WriteString("   - If ALL metrics are GOOD (ratio ~1.0, drift <10%, no underflows), state: 'No issues detected - call quality is EXCELLENT'\n")
	prompt.WriteString("   - In ExternalMedia mode, AudioSocket is NOT used; do NOT treat AudioSocket=false as an issue.\n")
	prompt.WriteString("2. Confidence: How confident are you? (High/Medium/Low)\n")
	prompt.WriteString("3. Quick Fix: Provide EXACT configuration changes (or 'N/A' if no issues)\n")
	prompt.WriteString("   - Use the EXACT values from golden baseline (e.g., 'Set webrtc_aggressiveness: 1')\n")
	prompt.WriteString("   - Prefer operator overrides in: config/ai-agent.local.yaml (if present); otherwise config/ai-agent.yaml\n")
	prompt.WriteString("   - Specify the EXACT section (e.g., 'vad:', 'streaming:', 'providers.openai_realtime:')\n")
	prompt.WriteString("   - NEVER suggest config/streaming.yaml or config/deepgram.yaml - these files DO NOT EXIST\n")
	prompt.WriteString("   - Include the EXACT parameter names from the deviations\n")
	prompt.WriteString("4. Prevention: How to prevent this in the future?\n")
	prompt.WriteString("\nCRITICAL FILE STRUCTURE:\n")
	prompt.WriteString("- Base defaults are in config/ai-agent.yaml; operator overrides go in config/ai-agent.local.yaml\n")
	prompt.WriteString("- Sections: streaming:, vad:, providers:, barge_in:, audiosocket:\n")
	prompt.WriteString("\nCOMMON FALSE POSITIVES TO IGNORE:\n")
	prompt.WriteString("- 'DeepgramProviderConfig has no field target_encoding' - This is a benign Python validation warning, NOT a config error\n")
	prompt.WriteString("- Deepgram provider does NOT need target_encoding field (only OpenAI Realtime uses it)\n")
	prompt.WriteString("- Do NOT suggest adding target_encoding to Deepgram config\n")
	prompt.WriteString("\nIMPORTANT: Your fixes MUST use the exact values from the golden baseline comparison.\n")
	prompt.WriteString("Do NOT suggest generic fixes. Use the concrete values provided.\n")
	prompt.WriteString("\nKeep your response concise and actionable (under 400 words).")

	return prompt.String()
}

// callOpenAI makes OpenAI API request
func (llm *LLMAnalyzer) callOpenAI(prompt string) (string, error) {
	url := "https://api.openai.com/v1/chat/completions"

	requestBody := map[string]interface{}{
		"model": llm.model,
		"messages": []map[string]string{
			{
				"role":    "user",
				"content": prompt,
			},
		},
		"max_tokens":  800,
		"temperature": 0.3,
	}

	jsonData, err := json.Marshal(requestBody)
	if err != nil {
		return "", err
	}

	req, err := http.NewRequest("POST", url, bytes.NewBuffer(jsonData))
	if err != nil {
		return "", err
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+llm.apiKey)

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return "", fmt.Errorf("OpenAI request failed: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}

	if resp.StatusCode != 200 {
		return "", fmt.Errorf("OpenAI API error %d: %s", resp.StatusCode, string(body))
	}

	var result map[string]interface{}
	if err := json.Unmarshal(body, &result); err != nil {
		return "", err
	}

	choices, ok := result["choices"].([]interface{})
	if !ok || len(choices) == 0 {
		return "", fmt.Errorf("no response from OpenAI")
	}

	message, ok := choices[0].(map[string]interface{})["message"].(map[string]interface{})
	if !ok {
		return "", fmt.Errorf("invalid response format")
	}

	content, ok := message["content"].(string)
	if !ok {
		return "", fmt.Errorf("no content in response")
	}

	return content, nil
}

// callAnthropic makes Anthropic API request
func (llm *LLMAnalyzer) callAnthropic(prompt string) (string, error) {
	url := "https://api.anthropic.com/v1/messages"

	requestBody := map[string]interface{}{
		"model": llm.model,
		"messages": []map[string]string{
			{
				"role":    "user",
				"content": prompt,
			},
		},
		"max_tokens": 800,
	}

	jsonData, err := json.Marshal(requestBody)
	if err != nil {
		return "", err
	}

	req, err := http.NewRequest("POST", url, bytes.NewBuffer(jsonData))
	if err != nil {
		return "", err
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("x-api-key", llm.apiKey)
	req.Header.Set("anthropic-version", "2023-06-01")

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return "", fmt.Errorf("Anthropic request failed: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}

	if resp.StatusCode != 200 {
		return "", fmt.Errorf("Anthropic API error %d: %s", resp.StatusCode, string(body))
	}

	var result map[string]interface{}
	if err := json.Unmarshal(body, &result); err != nil {
		return "", err
	}

	content, ok := result["content"].([]interface{})
	if !ok || len(content) == 0 {
		return "", fmt.Errorf("no content in response")
	}

	text, ok := content[0].(map[string]interface{})["text"].(string)
	if !ok {
		return "", fmt.Errorf("invalid response format")
	}

	return text, nil
}

// LLMDiagnosis holds LLM analysis results
type LLMDiagnosis struct {
	Provider string `json:"provider"`
	Model    string `json:"model"`
	Analysis string `json:"analysis"`
}
