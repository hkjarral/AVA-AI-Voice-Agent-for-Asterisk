package troubleshoot

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/fatih/color"
)

var (
	successColor = color.New(color.FgGreen)
	errorColor   = color.New(color.FgRed)
	warningColor = color.New(color.FgYellow)
	infoColor    = color.New(color.FgBlue)
)

// Call represents a call record
type Call struct {
	ID        string
	Timestamp time.Time
	Duration  string
	Status    string
	Channel   string
}

// Runner orchestrates troubleshooting
type Runner struct {
	verbose     bool
	ctx         context.Context
	callID      string
	symptom     string
	interactive bool
	collectOnly bool
	noLLM       bool
	list        bool
	jsonOutput  bool
}

// NewRunner creates a new troubleshoot runner
func NewRunner(callID, symptom string, interactive, collectOnly, noLLM, list, jsonOutput, verbose bool) *Runner {
	return &Runner{
		verbose:     verbose,
		ctx:         context.Background(),
		callID:      callID,
		symptom:     symptom,
		interactive: interactive,
		collectOnly: collectOnly,
		noLLM:       noLLM,
		list:        list,
		jsonOutput:  jsonOutput,
	}
}

// Run executes troubleshooting workflow
func (r *Runner) Run() error {
	// Load .env file for API keys
	LoadEnvFile()

	// List mode
	if r.list {
		return r.listCalls()
	}

	// Determine which call to analyze
	if r.callID == "" || r.callID == "last" {
		calls, err := r.getRecentCalls(10)
		if err != nil {
			return fmt.Errorf("failed to get recent calls: %w", err)
		}
		if len(calls) == 0 {
			if r.jsonOutput {
				_ = r.outputJSON(&RCAReport{
					CallID: r.callID,
					Error:  "no recent calls found (make a test call and re-run)",
				})
				return fmt.Errorf("no recent calls found")
			}
			errorColor.Println("❌ No recent calls found")
			fmt.Println()
			fmt.Println("Tips:")
			fmt.Println("  • Make a test call first")
			fmt.Println("  • Check if ai_engine container is running")
			fmt.Println("  • Verify logs: docker logs ai_engine")
			return fmt.Errorf("no calls to analyze")
		}

		// If --last flag or "last", use most recent
		if r.callID == "last" {
			r.callID = calls[0].ID
			if !r.jsonOutput {
				infoColor.Printf("Analyzing most recent call: %s\n", r.callID)
				fmt.Println()
			}
		} else {
			// No call ID and no --last flag: interactive selection
			selectedID, err := SelectCallInteractive(calls)
			if err != nil {
				return err
			}
			r.callID = selectedID
			if !r.jsonOutput {
				infoColor.Printf("Analyzing call: %s\n", r.callID)
				fmt.Println()
			}
		}
	}

	// Collect logs and data
	logData, err := r.collectCallData()
	if err != nil {
		return fmt.Errorf("failed to collect data: %w", err)
	}

	if r.collectOnly {
		if r.jsonOutput {
			_ = r.outputJSON(&RCAReport{
				CallID: r.callID,
				Error:  "collect-only mode does not produce a report",
			})
			return fmt.Errorf("collect-only mode does not produce a report")
		}
		fmt.Println("Data collection complete.")
		return nil
	}

	// Analyze logs
	analysis := r.analyzeBasic(logData)

	// Extract structured metrics
	metrics := ExtractMetrics(logData)
	analysis.Metrics = metrics

	// Analyze format/sampling alignment
	formatAlignment := AnalyzeFormatAlignment(metrics)
	metrics.FormatAlignment = formatAlignment

	// Compare to golden baselines
	baselineName := detectBaseline(logData)
	if baselineName != "" {
		comparison := CompareToBaseline(metrics, baselineName)
		analysis.BaselineComparison = comparison
		if r.verbose && !r.jsonOutput && comparison != nil {
			infoColor.Printf("  Using baseline: %s\n", comparison.BaselineName)
		}
	}

	// Apply symptom-specific analysis
	if r.symptom != "" {
		checker := NewSymptomChecker(r.symptom)
		checker.AnalyzeSymptom(analysis, logData)
	}

	// LLM analysis
	var llmDiagnosis *LLMDiagnosis
	if !r.noLLM {
		llmAnalyzer, err := NewLLMAnalyzer()
		if err != nil {
			// best-effort; do not fail the report
		} else {
			llmDiagnosis, err = llmAnalyzer.AnalyzeWithLLM(analysis, logData)
			if err != nil {
				// best-effort; do not fail the report
			}
		}
	}

	if r.jsonOutput {
		return r.outputJSON(buildRCAReport(analysis, llmDiagnosis))
	}

	// Human-readable output
	fmt.Println()
	fmt.Println("🔍 Call Troubleshooting & RCA")
	fmt.Println("═══════════════════════════════════════════")
	fmt.Println()
	infoColor.Println("Collecting call data...")
	successColor.Println("✅ Data collected")
	fmt.Println()
	infoColor.Println("Analyzing logs...")
	infoColor.Println("Extracting metrics...")
	infoColor.Println("Analyzing format alignment...")
	infoColor.Println("Comparing to golden baselines...")
	if r.symptom != "" {
		infoColor.Printf("Applying symptom analysis: %s\n", r.symptom)
	}
	if !r.noLLM {
		infoColor.Println("Requesting AI diagnosis...")
	}
	fmt.Println()

	// Show findings
	r.displayFindings(analysis)

	// Show detailed metrics (RCA-level)
	if analysis.Metrics != nil {
		r.displayMetrics(analysis.Metrics)

		// Show overall call quality verdict
		r.displayCallQuality(analysis.Metrics)
	}

	// Show LLM diagnosis
	if llmDiagnosis != nil {
		r.displayLLMDiagnosis(llmDiagnosis)
	}

	// Interactive follow-up
	if r.interactive {
		return r.interactiveSession(analysis)
	}

	return nil
}

type RCAReport struct {
	CallID string `json:"call_id"`
	Error  string `json:"error,omitempty"`

	AudioTransport string `json:"audio_transport,omitempty"`

	Pipeline struct {
		HasAudioSocket    bool `json:"has_audiosocket"`
		HasExternalMedia  bool `json:"has_externalmedia"`
		HasTranscription  bool `json:"has_transcription"`
		HasPlayback       bool `json:"has_playback"`
	} `json:"pipeline"`

	Errors   []string `json:"errors,omitempty"`
	Warnings []string `json:"warnings,omitempty"`

	AudioIssues []string `json:"audio_issues,omitempty"`

	Symptom         string           `json:"symptom,omitempty"`
	SymptomAnalysis *SymptomAnalysis `json:"symptom_analysis,omitempty"`

	Metrics            *CallMetrics        `json:"metrics,omitempty"`
	BaselineComparison *BaselineComparison `json:"baseline_comparison,omitempty"`
	LLMDiagnosis       *LLMDiagnosis       `json:"llm_diagnosis,omitempty"`
}

func buildRCAReport(analysis *Analysis, llm *LLMDiagnosis) *RCAReport {
	rep := &RCAReport{
		CallID:       analysis.CallID,
		Errors:       capSlice(analysis.Errors, 20),
		Warnings:     capSlice(analysis.Warnings, 20),
		AudioIssues:  capSlice(analysis.AudioIssues, 50),
		Symptom:      analysis.Symptom,
		Metrics:      analysis.Metrics,
		LLMDiagnosis: llm,
		AudioTransport: analysis.AudioTransport,
	}
	rep.Pipeline.HasAudioSocket = analysis.HasAudioSocket
	rep.Pipeline.HasExternalMedia = analysis.HasExternalMedia
	rep.Pipeline.HasTranscription = analysis.HasTranscription
	rep.Pipeline.HasPlayback = analysis.HasPlayback
	rep.SymptomAnalysis = analysis.SymptomAnalysis
	rep.BaselineComparison = analysis.BaselineComparison
	return rep
}

func capSlice(in []string, n int) []string {
	if len(in) <= n {
		return in
	}
	return in[:n]
}

func (r *Runner) outputJSON(rep *RCAReport) error {
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(rep)
}

// listCalls lists recent calls
func (r *Runner) listCalls() error {
	calls, err := r.getRecentCalls(20)
	if err != nil {
		return err
	}

	if len(calls) == 0 {
		warningColor.Println("No recent calls found")
		return nil
	}

	fmt.Printf("Recent calls (%d):\n\n", len(calls))
	for i, call := range calls {
		age := time.Since(call.Timestamp)
		ageStr := formatDuration(age)
		fmt.Printf("%2d. %s - %s ago", i+1, call.ID, ageStr)
		if call.Duration != "" {
			fmt.Printf(" (duration: %s)", call.Duration)
		}
		fmt.Println()
	}
	fmt.Println()
	fmt.Println("Usage: agent rca --call <id>")
	return nil
}

// getRecentCalls extracts recent calls from logs
func (r *Runner) getRecentCalls(limit int) ([]Call, error) {
	cmd := exec.Command("docker", "logs", "--since", "24h", "ai_engine")
	output, err := cmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("failed to read logs: %w", err)
	}

	// Strip ANSI color codes from log output (console format uses colors)
	// JSON format doesn't have ANSI codes, so this is safe for both
	ansiStripPattern := regexp.MustCompile(`\x1b\[[0-9;]*m`)
	cleanOutput := ansiStripPattern.ReplaceAllString(string(output), "")

	callMap := make(map[string]*Call)
	excludedChannels := make(map[string]bool)

	// First pass: identify AudioSocket channels (internal infrastructure)
	audioSocketPattern := regexp.MustCompile(`"audiosocket_channel_id":\s*"([0-9]+\.[0-9]+)"`)
	externalMediaPattern := regexp.MustCompile(`"external_media_id":\s*"([0-9]+\.[0-9]+)"`)
	pendingExternalMediaPattern := regexp.MustCompile(`"pending_external_media_id":\s*"([0-9]+\.[0-9]+)"`)
	lines := strings.Split(cleanOutput, "\n")

	if r.verbose {
		fmt.Fprintf(os.Stderr, "[DEBUG] Read %d lines from Docker logs\n", len(lines))
	}

	for _, line := range lines {
		matches := audioSocketPattern.FindStringSubmatch(line)
		if len(matches) > 1 {
			excludedChannels[matches[1]] = true
			if r.verbose {
				fmt.Fprintf(os.Stderr, "[DEBUG] Found AudioSocket channel: %s\n", matches[1])
			}
		}
		matches = externalMediaPattern.FindStringSubmatch(line)
		if len(matches) > 1 {
			excludedChannels[matches[1]] = true
			if r.verbose {
				fmt.Fprintf(os.Stderr, "[DEBUG] Found ExternalMedia channel: %s\n", matches[1])
			}
		}
		matches = pendingExternalMediaPattern.FindStringSubmatch(line)
		if len(matches) > 1 {
			excludedChannels[matches[1]] = true
			if r.verbose {
				fmt.Fprintf(os.Stderr, "[DEBUG] Found pending ExternalMedia channel: %s\n", matches[1])
			}
		}
	}

	// Second pass: collect call IDs, excluding AudioSocket channels
	patterns := []*regexp.Regexp{
		regexp.MustCompile(`"call_id":\s*"([0-9]+\.[0-9]+)"`),                     // JSON: "call_id": "1761518880.2191"
		regexp.MustCompile(`(?:call_id|channel_id)[=:][\s]*"?([0-9]+\.[0-9]+)"?`), // call_id= or channel_id=
		regexp.MustCompile(`"caller_channel_id":\s*"([0-9]+\.[0-9]+)"`),           // Explicit caller channel
	}

	matchCount := 0
	for _, line := range lines {
		for _, pattern := range patterns {
			matches := pattern.FindStringSubmatch(line)
			if len(matches) > 1 {
				matchCount++
				callID := matches[1]
				// Skip non-caller channels (AudioSocket / ExternalMedia helper channels)
				if excludedChannels[callID] {
					if r.verbose {
						fmt.Fprintf(os.Stderr, "[DEBUG] Skipping non-caller channel: %s\n", callID)
					}
					continue
				}
				if _, exists := callMap[callID]; !exists {
					callMap[callID] = &Call{
						ID:        callID,
						Timestamp: time.Now(), // Will be refined from log timestamp
					}
					if r.verbose {
						fmt.Fprintf(os.Stderr, "[DEBUG] Found call ID: %s\n", callID)
					}
				}
				break // Found a match, no need to try other patterns
			}
		}
	}

	if r.verbose {
		fmt.Fprintf(os.Stderr, "[DEBUG] Total pattern matches: %d, Unique calls: %d\n", matchCount, len(callMap))
	}

	// Convert to slice and sort by ID (descending, newer first)
	calls := make([]Call, 0, len(callMap))
	for _, call := range callMap {
		calls = append(calls, *call)
	}

	sort.Slice(calls, func(i, j int) bool {
		return calls[i].ID > calls[j].ID
	})

	if len(calls) > limit {
		calls = calls[:limit]
	}

	return calls, nil
}

// collectCallData collects logs for specific call
func (r *Runner) collectCallData() (string, error) {
	cmd := exec.Command("docker", "logs", "--since", "1h", "ai_engine")
	output, err := cmd.CombinedOutput()
	if err != nil {
		return "", err
	}

	// Filter logs for this call ID, including related helper channels (AudioSocket / ExternalMedia).
	// Many ExternalMedia events are emitted on the ExternalMedia channel id, not the caller channel id.
	ansiStripPattern := regexp.MustCompile(`\x1b\[[0-9;]*m`)
	allLogs := ansiStripPattern.ReplaceAllString(string(output), "")
	lines := strings.Split(allLogs, "\n")

	relatedIDs := make(map[string]bool)
	included := make([]string, 0, 1024)
	includedSet := make(map[string]bool)
	channelIDPattern := regexp.MustCompile(`^[0-9]+\.[0-9]+$`)

	addLine := func(line string) {
		if line == "" {
			return
		}
		if includedSet[line] {
			return
		}
		includedSet[line] = true
		included = append(included, line)
	}

	addRelated := func(v any) {
		s, ok := v.(string)
		if !ok {
			return
		}
		s = strings.TrimSpace(s)
		if s == "" || s == r.callID {
			return
		}
		// Channel IDs are usually like 1761518880.2191; keep the filter loose but safe.
		if channelIDPattern.MatchString(s) {
			relatedIDs[s] = true
		}
	}

	// First pass: include lines that reference the caller id; capture related channel ids.
	for _, line := range lines {
		if !strings.Contains(line, r.callID) {
			continue
		}

		addLine(line)

		var entry map[string]any
		if err := json.Unmarshal([]byte(line), &entry); err != nil {
			continue
		}
		addRelated(entry["audiosocket_channel_id"])
		addRelated(entry["external_media_id"])
		addRelated(entry["pending_external_media_id"])
		// Some log lines use "channel_id" to reference the helper channel.
		addRelated(entry["channel_id"])
	}

	// Second pass: include any log lines that reference a related channel id.
	if len(relatedIDs) > 0 {
		for _, line := range lines {
			for id := range relatedIDs {
				if strings.Contains(line, id) {
					addLine(line)
					break
				}
			}
		}
	}

	return strings.Join(included, "\n"), nil
}

// Analysis holds analysis results
type Analysis struct {
	CallID             string
	Errors             []string
	Warnings           []string
	AudioIssues        []string
	MetricsMap         map[string]string
	Metrics            *CallMetrics
	BaselineComparison *BaselineComparison
	AudioTransport     string
	HasAudioSocket     bool
	HasExternalMedia   bool
	HasTranscription   bool
	HasPlayback        bool
	Symptom            string
	SymptomAnalysis    *SymptomAnalysis
}

// analyzeBasic performs basic log analysis
func (r *Runner) analyzeBasic(logData string) *Analysis {
	analysis := &Analysis{
		CallID:     r.callID,
		MetricsMap: make(map[string]string),
		Symptom:    r.symptom,
	}

	lines := strings.Split(logData, "\n")

	// Transport detection should be strict enough to avoid false positives from
	// config/alignment logs (e.g., "audiosocket_format" can appear even when
	// ExternalMedia is used).
	hasAudioSocketEvidence := false
	hasExternalMediaEvidence := false

	for _, line := range lines {
		lower := strings.ToLower(line)

		// Check for errors
		if strings.Contains(lower, "error") && !strings.Contains(lower, "0 error") {
			analysis.Errors = append(analysis.Errors, line)
		}

		// Check for warnings
		if strings.Contains(lower, "warning") || strings.Contains(lower, "warn") {
			analysis.Warnings = append(analysis.Warnings, line)
		}

		// Transport indicators (strict)
		if strings.Contains(lower, "\"audiosocket_channel_id\"") ||
			(strings.Contains(lower, "audiosocket") && strings.Contains(lower, "channel") && strings.Contains(lower, "stasis")) ||
			strings.Contains(lower, "audiosocket channel entered stasis") {
			hasAudioSocketEvidence = true
		}
		if strings.Contains(lower, "🎯 external media") ||
			strings.Contains(lower, "externalmedia channel") ||
			strings.Contains(lower, "\"external_media_id\"") ||
			strings.Contains(lower, "\"pending_external_media_id\"") ||
			strings.Contains(lower, "create_external_media_channel") {
			hasExternalMediaEvidence = true
		}

		if strings.Contains(lower, "transcription") || strings.Contains(lower, "transcript") {
			analysis.HasTranscription = true
		}
		if strings.Contains(lower, "playback") || strings.Contains(lower, "playing") {
			analysis.HasPlayback = true
		}

		// Audio quality issues
		if strings.Contains(lower, "underflow") {
			analysis.AudioIssues = append(analysis.AudioIssues, "Jitter buffer underflow detected")
		}
		if strings.Contains(lower, "garbled") || strings.Contains(lower, "distorted") {
			analysis.AudioIssues = append(analysis.AudioIssues, "Audio quality issue detected")
		}
		if strings.Contains(lower, "echo") {
			analysis.AudioIssues = append(analysis.AudioIssues, "Echo detected")
		}
	}

	analysis.HasAudioSocket = hasAudioSocketEvidence
	analysis.HasExternalMedia = hasExternalMediaEvidence
	analysis.AudioTransport = detectTransportBestEffort(logData, hasAudioSocketEvidence, hasExternalMediaEvidence)

	return analysis
}

func detectTransportBestEffort(logData string, hasAudioSocketEvidence, hasExternalMediaEvidence bool) string {
	if hasExternalMediaEvidence && !hasAudioSocketEvidence {
		return "externalmedia"
	}
	if hasAudioSocketEvidence && !hasExternalMediaEvidence {
		return "audiosocket"
	}

	// Ambiguous or unknown from logs: fall back to config.
	if transport := detectTransportFromConfig(); transport != "" {
		return transport
	}
	return ""
}

func detectTransportFromConfig() string {
	cmd := exec.Command("docker", "exec", "ai_engine", "cat", "/app/config/ai-agent.yaml")
	output, err := cmd.CombinedOutput()
	if err != nil {
		return ""
	}

	// Avoid adding a YAML dependency to this file; parse with a tolerant regex
	// that supports optional quotes and trailing comments.
	return detectTransportFromConfigText(string(output))
}

func detectTransportFromConfigText(configText string) string {
	// Go uses RE2 which does not support backreferences, so tolerate optional
	// quoting without enforcing matching quote pairs.
	transportRe := regexp.MustCompile(`(?im)^\s*audio_transport\s*:\s*['"]?(audiosocket|externalmedia)['"]?\s*(?:#.*)?$`)
	matches := transportRe.FindStringSubmatch(configText)
	if len(matches) < 2 {
		return ""
	}
	return strings.ToLower(matches[1])
}

// displayFindings shows analysis results
func (r *Runner) displayFindings(analysis *Analysis) {
	fmt.Println("═══════════════════════════════════════════")
	fmt.Println("📊 ANALYSIS RESULTS")
	fmt.Println("═══════════════════════════════════════════")
	fmt.Println()

	// Pipeline status
	fmt.Println("Pipeline Status:")
	switch strings.ToLower(strings.TrimSpace(analysis.AudioTransport)) {
	case "audiosocket":
		successColor.Println("  ✅ Transport: AudioSocket")
	case "externalmedia":
		successColor.Println("  ✅ Transport: ExternalMedia RTP")
	default:
		warningColor.Println("  ⚠️  Transport: Unknown")
	}

	if analysis.HasAudioSocket {
		successColor.Println("  ✅ AudioSocket: Detected")
	} else if analysis.AudioTransport == "audiosocket" {
		errorColor.Println("  ❌ AudioSocket: Not detected")
	} else {
		// Not applicable for ExternalMedia calls.
		infoColor.Println("  ℹ️  AudioSocket: Not used")
	}

	if analysis.HasExternalMedia {
		successColor.Println("  ✅ ExternalMedia: Detected")
	} else if analysis.AudioTransport == "externalmedia" {
		errorColor.Println("  ❌ ExternalMedia: Not detected")
	} else {
		infoColor.Println("  ℹ️  ExternalMedia: Not used")
	}

	if analysis.HasTranscription {
		successColor.Println("  ✅ Transcription: Active")
	} else {
		warningColor.Println("  ⚠️  Transcription: Not detected")
	}

	if analysis.HasPlayback {
		successColor.Println("  ✅ Playback: Active")
	} else {
		warningColor.Println("  ⚠️  Playback: Not detected")
	}
	fmt.Println()

	// Audio issues
	if len(analysis.AudioIssues) > 0 {
		errorColor.Printf("Audio Issues Found (%d):\n", len(analysis.AudioIssues))
		for _, issue := range analysis.AudioIssues {
			fmt.Printf("  • %s\n", issue)
		}
		fmt.Println()
	}

	// Errors
	if len(analysis.Errors) > 0 {
		errorColor.Printf("Errors (%d):\n", len(analysis.Errors))
		count := len(analysis.Errors)
		if count > 5 {
			count = 5
		}
		for i := 0; i < count; i++ {
			fmt.Printf("  %d. %s\n", i+1, truncate(analysis.Errors[i], 100))
		}
		if len(analysis.Errors) > 5 {
			fmt.Printf("  ... and %d more\n", len(analysis.Errors)-5)
		}
		fmt.Println()
	}

	// Warnings
	if len(analysis.Warnings) > 0 {
		warningColor.Printf("Warnings (%d):\n", len(analysis.Warnings))
		count := len(analysis.Warnings)
		if count > 3 {
			count = 3
		}
		for i := 0; i < count; i++ {
			fmt.Printf("  %d. %s\n", i+1, truncate(analysis.Warnings[i], 100))
		}
		if len(analysis.Warnings) > 3 {
			fmt.Printf("  ... and %d more\n", len(analysis.Warnings)-3)
		}
		fmt.Println()
	}

	// Symptom-specific analysis
	if analysis.SymptomAnalysis != nil {
		fmt.Println("═══════════════════════════════════════════")
		warningColor.Printf("SYMPTOM ANALYSIS: %s\n", analysis.SymptomAnalysis.Symptom)
		fmt.Println("═══════════════════════════════════════════")
		fmt.Printf("%s\n\n", analysis.SymptomAnalysis.Description)

		if len(analysis.SymptomAnalysis.Findings) > 0 {
			fmt.Println("Findings:")
			for _, finding := range analysis.SymptomAnalysis.Findings {
				fmt.Printf("  %s\n", finding)
			}
			fmt.Println()
		}

		if len(analysis.SymptomAnalysis.RootCauses) > 0 {
			errorColor.Println("Likely Root Causes:")
			for _, cause := range analysis.SymptomAnalysis.RootCauses {
				fmt.Printf("  • %s\n", cause)
			}
			fmt.Println()
		}

		if len(analysis.SymptomAnalysis.Actions) > 0 {
			successColor.Println("Recommended Actions:")
			for i, action := range analysis.SymptomAnalysis.Actions {
				fmt.Printf("  %d. %s\n", i+1, action)
			}
			fmt.Println()
		}
	}

	// Basic recommendations
	r.displayRecommendations(analysis)
}

// displayRecommendations shows basic recommendations
func (r *Runner) displayRecommendations(analysis *Analysis) {
	fmt.Println("Recommendations:")

	transport := strings.ToLower(strings.TrimSpace(analysis.AudioTransport))
	if transport == "audiosocket" {
		if !analysis.HasAudioSocket {
			fmt.Println("  • Check if AudioSocket is configured correctly")
			fmt.Println("  • Verify AudioSocket port is reachable from Asterisk")
		}
	} else if transport == "externalmedia" {
		if !analysis.HasExternalMedia {
			fmt.Println("  • Check if ExternalMedia RTP is configured correctly")
			fmt.Println("  • Verify UDP 18080 reachability (firewall/NAT)")
		}
	} else {
		if !analysis.HasAudioSocket && !analysis.HasExternalMedia {
			fmt.Println("  • Check which transport you're using (audiosocket vs externalmedia)")
			fmt.Println("  • Confirm config/ai-agent.yaml has a valid audio_transport value")
		}
	}

	if len(analysis.AudioIssues) > 0 {
		fmt.Println("  • Run: agent check (for detailed diagnostics)")
		fmt.Println("  • Check jitter_buffer_ms settings")
		fmt.Println("  • Verify network stability")
	}

	if len(analysis.Errors) > 10 {
		fmt.Println("  • High error count - check container logs")
		fmt.Println("  • Run: docker logs ai_engine | grep ERROR")
	}

	fmt.Println()
}

// displayMetrics shows RCA-level metrics
func (r *Runner) displayMetrics(metrics *CallMetrics) {
	fmt.Println("═══════════════════════════════════════════")
	fmt.Println("📈 DETAILED METRICS (RCA-Level)")
	fmt.Println("═══════════════════════════════════════════")
	fmt.Println()

	// Provider bytes tracking
	if len(metrics.ProviderSegments) > 0 {
		successColor.Println("Provider Bytes Tracking:")
		fmt.Printf("  Segments: %d\n", len(metrics.ProviderSegments))
		fmt.Printf("  Total provider bytes: %s\n", formatBytes(metrics.ProviderBytesTotal))
		fmt.Printf("  Total enqueued bytes: %s\n", formatBytes(metrics.EnqueuedBytesTotal))

		if metrics.ProviderBytesTotal > 0 {
			actualRatio := float64(metrics.EnqueuedBytesTotal) / float64(metrics.ProviderBytesTotal)
			if actualRatio >= 0.99 && actualRatio <= 1.01 {
				successColor.Printf("  Ratio: %.3f ✅ PERFECT\n", actualRatio)
			} else if actualRatio >= 0.95 && actualRatio <= 1.05 {
				warningColor.Printf("  Ratio: %.3f ⚠️  ACCEPTABLE\n", actualRatio)
			} else {
				errorColor.Printf("  Ratio: %.3f ❌ CRITICAL (should be 1.0)\n", actualRatio)
				fmt.Println("  Impact: Pacing bug - causes garbled/fast/slow audio")
			}
		}
		fmt.Println()
	}

	// Streaming performance
	if len(metrics.StreamingSummaries) > 0 {
		successColor.Println("Streaming Performance:")

		// Separate greeting and conversation segments
		var greetingSegment *StreamingSummary
		conversationSegments := []StreamingSummary{}

		for _, seg := range metrics.StreamingSummaries {
			if seg.IsGreeting {
				greetingSegment = &seg
			} else {
				conversationSegments = append(conversationSegments, seg)
			}
		}

		// Show segment summary
		fmt.Printf("  Segments: %d", len(metrics.StreamingSummaries))
		if greetingSegment != nil {
			fmt.Printf(" (1 greeting, %d conversation)", len(conversationSegments))
		}
		fmt.Println()

		// Drift analysis (excluding greeting)
		if metrics.WorstDriftPct == 0.0 && greetingSegment != nil {
			// Only greeting segment exists, show its drift as informational
			warningColor.Printf("  Greeting drift: %.1f%% (expected - includes conversation pauses)\n", greetingSegment.DriftPct)
			successColor.Println("  Conversation drift: N/A (no separate segments)")
		} else if absFloat(metrics.WorstDriftPct) <= 5.0 {
			successColor.Printf("  Drift: %.1f%% ✅ EXCELLENT\n", metrics.WorstDriftPct)
		} else if absFloat(metrics.WorstDriftPct) <= 10.0 {
			warningColor.Printf("  Drift: %.1f%% ⚠️  ACCEPTABLE\n", metrics.WorstDriftPct)
		} else {
			errorColor.Printf("  Drift: %.1f%% ❌ CRITICAL (should be <10%%)\n", metrics.WorstDriftPct)
			fmt.Println("  Impact: Timing mismatch - audio too fast/slow")
		}

		// Underflow analysis
		if metrics.UnderflowCount > 0 {
			// Calculate underflow rate
			totalFrames := 0
			for _, seg := range metrics.StreamingSummaries {
				totalFrames += seg.BytesSent / 320 // 320 bytes per 20ms frame
			}
			underflowRate := float64(metrics.UnderflowCount) / float64(totalFrames) * 100

			if underflowRate < 1.0 {
				warningColor.Printf("  Underflows: %d (%.1f%% of frames - acceptable)\n", metrics.UnderflowCount, underflowRate)
			} else if underflowRate < 5.0 {
				warningColor.Printf("  Underflows: %d (%.1f%% of frames - minor impact)\n", metrics.UnderflowCount, underflowRate)
			} else {
				errorColor.Printf("  Underflows: %d (%.1f%% of frames - significant) ❌\n", metrics.UnderflowCount, underflowRate)
				fmt.Println("  Impact: Jitter buffer starvation - choppy audio")
			}
		} else {
			successColor.Println("  Underflows: 0 ✅ NONE")
		}
		fmt.Println()
	}

	// VAD settings
	if metrics.VADSettings != nil {
		successColor.Println("VAD Configuration:")
		if metrics.VADSettings.WebRTCAggressiveness == 1 {
			successColor.Printf("  WebRTC Aggressiveness: %d ✅ OPTIMAL\n", metrics.VADSettings.WebRTCAggressiveness)
		} else if metrics.VADSettings.WebRTCAggressiveness == 0 {
			errorColor.Printf("  WebRTC Aggressiveness: %d ❌ TOO SENSITIVE\n", metrics.VADSettings.WebRTCAggressiveness)
			fmt.Println("  Impact: Detects echo as speech - causes self-interruption")
		} else {
			warningColor.Printf("  WebRTC Aggressiveness: %d\n", metrics.VADSettings.WebRTCAggressiveness)
		}
		fmt.Println()
	}

	// Audio gating
	if metrics.GateClosures > 0 {
		successColor.Println("Audio Gating:")
		if metrics.GateFlutterDetected {
			errorColor.Printf("  Gate closures: %d ❌ FLUTTER DETECTED\n", metrics.GateClosures)
			fmt.Println("  Impact: Echo leakage causing self-interruption")
		} else if metrics.GateClosures <= 5 {
			successColor.Printf("  Gate closures: %d ✅ NORMAL\n", metrics.GateClosures)
		} else {
			warningColor.Printf("  Gate closures: %d ⚠️  ELEVATED\n", metrics.GateClosures)
		}
		fmt.Println()
	}

	// Transport/Format
	if metrics.AudioSocketFormat != "" || metrics.ProviderInputFormat != "" {
		transport := ""
		if metrics.FormatAlignment != nil {
			transport = strings.ToLower(strings.TrimSpace(metrics.FormatAlignment.ConfigAudioTransport))
		}
		successColor.Println("Transport Configuration:")
		if transport != "" {
			fmt.Printf("  Transport: %s\n", transport)
		}
		if transport == "audiosocket" && metrics.AudioSocketFormat != "" {
			if metrics.AudioSocketFormat == "slin" {
				successColor.Printf("  AudioSocket format: %s ✅ CORRECT\n", metrics.AudioSocketFormat)
			} else {
				errorColor.Printf("  AudioSocket format: %s ❌ WRONG (should be slin)\n", metrics.AudioSocketFormat)
			}
		}
		if metrics.ProviderInputFormat != "" {
			fmt.Printf("  Provider input: %s\n", metrics.ProviderInputFormat)
		}
		if metrics.ProviderOutputFormat != "" {
			fmt.Printf("  Provider output: %s\n", metrics.ProviderOutputFormat)
		}
		if metrics.SampleRate > 0 {
			fmt.Printf("  Sample rate: %d Hz\n", metrics.SampleRate)
		}
		fmt.Println()
	}

	// Format Alignment Analysis
	if metrics.FormatAlignment != nil && len(metrics.FormatAlignment.Issues) > 0 {
		errorColor.Println("⚠️  FORMAT/SAMPLING ALIGNMENT ISSUES:")
		for i, issue := range metrics.FormatAlignment.Issues {
			fmt.Printf("  %d. %s\n", i+1, issue)
		}
		fmt.Println()
		fmt.Println("Impact: Format mismatches cause garbled audio, distortion, or no audio")
		fmt.Println("Action: Verify config/ai-agent.yaml format settings match golden baseline")
		fmt.Println()
	}
}

// displayCallQuality shows overall call quality verdict
func (r *Runner) displayCallQuality(metrics *CallMetrics) {
	fmt.Println("═══════════════════════════════════════════")
	fmt.Println("🎯 OVERALL CALL QUALITY")
	fmt.Println("═══════════════════════════════════════════")
	fmt.Println()

	// Calculate score based on metrics
	issues := []string{}
	score := 100.0

	// Check provider bytes ratio
	if len(metrics.ProviderSegments) > 0 && metrics.ProviderBytesTotal > 0 {
		actualRatio := float64(metrics.EnqueuedBytesTotal) / float64(metrics.ProviderBytesTotal)
		if actualRatio < 0.95 || actualRatio > 1.05 {
			issues = append(issues, "Provider bytes pacing issue")
			score -= 30.0
		}
	}

	// Check drift (excluding greeting segments)
	if absFloat(metrics.WorstDriftPct) > 10.0 {
		issues = append(issues, fmt.Sprintf("High drift (%.1f%%)", metrics.WorstDriftPct))
		score -= 25.0
	}

	// Check underflows (with rate-based severity)
	if metrics.UnderflowCount > 0 && len(metrics.StreamingSummaries) > 0 {
		totalFrames := 0
		for _, seg := range metrics.StreamingSummaries {
			totalFrames += seg.BytesSent / 320
		}
		underflowRate := float64(metrics.UnderflowCount) / float64(totalFrames) * 100

		if underflowRate >= 5.0 {
			// Significant underflows
			issues = append(issues, fmt.Sprintf("%d underflows (%.1f%% rate - significant)", metrics.UnderflowCount, underflowRate))
			score -= 20.0
		} else if underflowRate >= 1.0 {
			// Minor underflows
			issues = append(issues, fmt.Sprintf("%d underflows (%.1f%% rate - minor)", metrics.UnderflowCount, underflowRate))
			score -= 5.0
		}
		// < 1% underflow rate is considered acceptable, no score deduction
	}

	// Check gate flutter
	if metrics.GateFlutterDetected {
		issues = append(issues, "Gate flutter detected")
		score -= 20.0
	}

	// Check VAD issues
	if metrics.VADSettings != nil && metrics.VADSettings.WebRTCAggressiveness == 0 {
		issues = append(issues, "VAD too sensitive")
		score -= 15.0
	}

	// Check format alignment issues (CRITICAL)
	if metrics.FormatAlignment != nil {
		if metrics.FormatAlignment.AudioSocketMismatch {
			issues = append(issues, "AudioSocket format mismatch")
			score -= 30.0 // Critical - causes garbled audio
		}
		if metrics.FormatAlignment.ProviderFormatMismatch {
			issues = append(issues, "Provider format mismatch")
			score -= 25.0
		}
		if metrics.FormatAlignment.FrameSizeMismatch {
			issues = append(issues, "Frame size mismatch")
			score -= 20.0
		}
	}

	// Determine verdict
	if score >= 90 {
		successColor.Println("Verdict: ✅ EXCELLENT - No significant issues detected")
	} else if score >= 70 {
		warningColor.Println("Verdict: ⚠️  FAIR - Minor issues detected")
	} else if score >= 50 {
		warningColor.Println("Verdict: ⚠️  POOR - Multiple issues affecting quality")
	} else {
		errorColor.Println("Verdict: ❌ CRITICAL - Severe issues detected")
	}

	fmt.Printf("Quality Score: %.0f/100\n", score)

	if len(issues) > 0 {
		fmt.Println("\nIssues Detected:")
		for _, issue := range issues {
			fmt.Printf("  • %s\n", issue)
		}
	} else {
		fmt.Println("\n✅ All metrics within acceptable thresholds")
		fmt.Println("✅ Provider bytes ratio: ~1.0")
		fmt.Println("✅ Drift: <10%")
		fmt.Println("✅ No underflows")
		fmt.Println("✅ Clean audio expected")
	}

	fmt.Println()
}

// displayLLMDiagnosis shows AI-powered diagnosis
func (r *Runner) displayLLMDiagnosis(diagnosis *LLMDiagnosis) {
	fmt.Println("═══════════════════════════════════════════")
	infoColor.Printf("🤖 AI DIAGNOSIS (%s - %s)\n", diagnosis.Provider, diagnosis.Model)
	fmt.Println("═══════════════════════════════════════════")
	fmt.Println()
	fmt.Println(diagnosis.Analysis)
	fmt.Println()
}

// formatBytes formats byte count with commas
func formatBytes(bytes int) string {
	if bytes < 1000 {
		return fmt.Sprintf("%d bytes", bytes)
	} else if bytes < 1000000 {
		return fmt.Sprintf("%.1f KB", float64(bytes)/1000)
	} else {
		return fmt.Sprintf("%.2f MB", float64(bytes)/1000000)
	}
}

// interactiveSession runs interactive troubleshooting
func (r *Runner) interactiveSession(analysis *Analysis) error {
	fmt.Println("═══════════════════════════════════════════")
	fmt.Println("Interactive Mode")
	fmt.Println("═══════════════════════════════════════════")
	fmt.Println()
	fmt.Println("Coming soon: Interactive Q&A for deeper diagnosis")
	return nil
}

// detectBaseline determines which golden baseline to use
func detectBaseline(logData string) string {
	lower := strings.ToLower(logData)

	// Check for OpenAI Realtime
	if strings.Contains(lower, "openai") && strings.Contains(lower, "realtime") {
		return "openai_realtime"
	}

	// Check for Deepgram
	if strings.Contains(lower, "deepgram") {
		return "deepgram_standard"
	}

	// Default to streaming performance baseline
	if strings.Contains(lower, "streaming tuning") {
		return "streaming_performance"
	}

	return "streaming_performance" // Default baseline
}

// Helper functions
func formatDuration(d time.Duration) string {
	if d < time.Minute {
		return fmt.Sprintf("%ds", int(d.Seconds()))
	}
	if d < time.Hour {
		return fmt.Sprintf("%dm", int(d.Minutes()))
	}
	return fmt.Sprintf("%dh", int(d.Hours()))
}

func truncate(s string, maxLen int) string {
	if len(s) <= maxLen {
		return s
	}
	return s[:maxLen-3] + "..."
}
