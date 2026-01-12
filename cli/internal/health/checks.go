package health

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"

	"gopkg.in/yaml.v3"
)

func (c *Checker) checkDocker() Check {
	// Check if docker command exists
	if _, err := exec.LookPath("docker"); err != nil {
		installCmd := "curl -fsSL https://get.docker.com | sh"
		aavaDocs := docsURL("docs/INSTALLATION.md")
		if c.platform != nil && c.platform.Platform != nil {
			if v := getString(c.platform.Platform, "docker", "install_cmd"); v != "" {
				installCmd = v
			}
			if v := getString(c.platform.Platform, "docker", "aava_docs"); v != "" {
				aavaDocs = docsURL(v)
			}
		}
		return Check{
			Name:        "Docker",
			Status:      StatusFail,
			Message:     "Docker not found",
			Remediation: fmt.Sprintf("Run:\n%s\nDocs: %s", installCmd, aavaDocs),
		}
	}

	// Check if docker daemon is running
	cmd := exec.Command("docker", "ps")
	if err := cmd.Run(); err != nil {
		startCmd := "sudo systemctl start docker"
		rootlessStartCmd := ""
		aavaDocs := docsURL("docs/INSTALLATION.md")
		rootlessDocs := docsURL("docs/CROSS_PLATFORM_PLAN.md")
		if c.platform != nil && c.platform.Platform != nil {
			if v := getString(c.platform.Platform, "docker", "start_cmd"); v != "" {
				startCmd = v
			}
			if v := getString(c.platform.Platform, "docker", "rootless_start_cmd"); v != "" {
				rootlessStartCmd = v
			}
			if v := getString(c.platform.Platform, "docker", "aava_docs"); v != "" {
				aavaDocs = docsURL(v)
			}
			if v := getString(c.platform.Platform, "docker", "rootless_docs"); v != "" {
				rootlessDocs = docsURL(v)
			}
		}

		remediation := fmt.Sprintf("Run: %s\nDocs: %s", startCmd, aavaDocs)
		if rootlessStartCmd != "" {
			remediation = remediation + fmt.Sprintf("\nRootless: %s\nRootless docs: %s", rootlessStartCmd, rootlessDocs)
		}
		return Check{
			Name:        "Docker",
			Status:      StatusFail,
			Message:     "Docker daemon not running",
			Remediation: remediation,
		}
	}

	// Get Docker version
	cmd = exec.Command("docker", "version", "--format", "{{.Server.Version}}")
	output, _ := cmd.Output()
	version := strings.TrimSpace(string(output))

	return Check{
		Name:    "Docker",
		Status:  StatusPass,
		Message: fmt.Sprintf("Docker daemon running (v%s)", version),
	}
}

func (c *Checker) checkContainers() Check {
	// Check if ai_engine container is running (note: underscore not hyphen)
	cmd := exec.Command("docker", "ps", "--format", "{{.Names}}\t{{.Status}}", "--filter", "name=ai_engine")
	output, err := cmd.Output()
	if err != nil {
		return Check{
			Name:        "Containers",
			Status:      StatusFail,
			Message:     "Failed to check container status",
			Details:     err.Error(),
			Remediation: "Run: docker compose ps (docs: " + docsURL("docs/INSTALLATION.md") + ")",
		}
	}

	lines := strings.Split(strings.TrimSpace(string(output)), "\n")
	if len(lines) == 0 || lines[0] == "" {
		return Check{
			Name:        "Containers",
			Status:      StatusFail,
			Message:     "No AI containers running",
			Remediation: "Run: docker compose up -d (docs: " + docsURL("docs/INSTALLATION.md") + ")",
		}
	}

	running := 0
	for _, line := range lines {
		if strings.Contains(line, "Up") {
			running++
		}
	}

	if running == 0 {
		return Check{
			Name:        "Containers",
			Status:      StatusFail,
			Message:     "AI containers not running",
			Remediation: "Run: docker compose up -d (docs: " + docsURL("docs/INSTALLATION.md") + ")",
		}
	}

	return Check{
		Name:    "Containers",
		Status:  StatusPass,
		Message: fmt.Sprintf("%d container(s) running", running),
		Details: string(output),
	}
}

func (c *Checker) checkCompose() Check {
	// Prefer Docker Compose v2 plugin: docker compose
	cmd := exec.Command("docker", "compose", "version", "--short")
	output, err := cmd.Output()
	if err == nil {
		version := strings.TrimSpace(string(output))
		// Version format: v2.24.1 or 2.24.1 depending on build.
		version = strings.TrimPrefix(version, "v")

		status := StatusPass
		message := fmt.Sprintf("Docker Compose v%s", version)
		remediation := ""

		parts := strings.Split(version, ".")
		if len(parts) >= 2 {
			major, _ := strconv.Atoi(parts[0])
			minor, _ := strconv.Atoi(parts[1])
			if major == 2 && minor > 0 && minor < 20 {
				status = StatusWarn
				upgradeCmd := ""
				aavaDocs := docsURL("docs/INSTALLATION.md")
				if c.platform != nil && c.platform.Platform != nil {
					upgradeCmd = getString(c.platform.Platform, "compose", "upgrade_cmd")
					if upgradeCmd == "" {
						upgradeCmd = getString(c.platform.Platform, "compose", "install_cmd")
					}
					if v := getString(c.platform.Platform, "compose", "aava_docs"); v != "" {
						aavaDocs = docsURL(v)
					}
				}
				if upgradeCmd != "" {
					remediation = fmt.Sprintf("Run:\n%s\nDocs: %s", upgradeCmd, aavaDocs)
				}
				message = fmt.Sprintf("Docker Compose v%s (upgrade recommended: v2.20+)", version)
			}
		}

		return Check{Name: "Compose", Status: status, Message: message, Remediation: remediation}
	}

	// Fall back to docker-compose (v1). If present, treat as unsupported.
	cmd = exec.Command("docker-compose", "version", "--short")
	output, err = cmd.Output()
	if err == nil {
		version := strings.TrimSpace(string(output))
		version = strings.TrimPrefix(version, "v")
		return Check{
			Name:        "Compose",
			Status:      StatusFail,
			Message:     "Docker Compose v1 detected (unsupported)",
			Details:     "docker-compose " + version,
			Remediation: "Install Docker Compose v2 plugin (docs: " + docsURL("docs/INSTALLATION.md") + ")",
		}
	}

	installCmd := "sudo apt-get update && sudo apt-get install -y docker-compose-plugin"
	aavaDocs := docsURL("docs/INSTALLATION.md")
	if c.platform != nil && c.platform.Platform != nil {
		if v := getString(c.platform.Platform, "compose", "install_cmd"); v != "" {
			installCmd = v
		}
		if v := getString(c.platform.Platform, "compose", "aava_docs"); v != "" {
			aavaDocs = docsURL(v)
		}
	}

	return Check{
		Name:        "Compose",
		Status:      StatusFail,
		Message:     "Docker Compose not found",
		Remediation: fmt.Sprintf("Run:\n%s\nDocs: %s", installCmd, aavaDocs),
	}
}

func (c *Checker) checkAsteriskARI() Check {
	// Get ARI credentials from environment
	ariHost := GetEnv("ASTERISK_HOST", c.envMap)
	ariUsername := GetEnv("ASTERISK_ARI_USERNAME", c.envMap)
	ariPassword := GetEnv("ASTERISK_ARI_PASSWORD", c.envMap)

	if ariHost == "" {
		ariHost = "127.0.0.1" // Default
	}

	if ariUsername == "" || ariPassword == "" {
		return Check{
			Name:        "Asterisk ARI",
			Status:      StatusWarn,
			Message:     "ARI credentials not configured",
			Details:     "ASTERISK_ARI_USERNAME or ASTERISK_ARI_PASSWORD not set in .env",
			Remediation: "Set ASTERISK_ARI_USERNAME and ASTERISK_ARI_PASSWORD in .env file",
		}
	}

	// Try to connect to ARI HTTP endpoint
	cmd := exec.Command("curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
		"-u", fmt.Sprintf("%s:%s", ariUsername, ariPassword),
		fmt.Sprintf("http://%s:8088/ari/asterisk/info", ariHost))

	output, err := cmd.Output()
	if err != nil {
		return Check{
			Name:        "Asterisk ARI",
			Status:      StatusWarn,
			Message:     "Cannot connect to ARI",
			Details:     fmt.Sprintf("Host: %s, error: %v", ariHost, err),
			Remediation: "Check if Asterisk is running and ARI is enabled",
		}
	}

	httpCode := strings.TrimSpace(string(output))
	if httpCode == "200" {
		return Check{
			Name:    "Asterisk ARI",
			Status:  StatusPass,
			Message: fmt.Sprintf("ARI accessible at %s:8088", ariHost),
		}
	}

	return Check{
		Name:    "Asterisk ARI",
		Status:  StatusWarn,
		Message: fmt.Sprintf("ARI returned HTTP %s", httpCode),
		Details: fmt.Sprintf("Expected 200, got %s from %s:8088", httpCode, ariHost),
	}
}

func (c *Checker) checkAudioSocket() Check {
	// Check if port 8090 is listening (typical AudioSocket port)
	cmd := exec.Command("sh", "-c", "netstat -tuln 2>/dev/null | grep :8090 || ss -tuln 2>/dev/null | grep :8090")
	if err := cmd.Run(); err != nil {
		return Check{
			Name:    "AudioSocket",
			Status:  StatusWarn,
			Message: "AudioSocket port 8090 not detected",
			Details: "This is normal when idle (no active calls)",
		}
	}

	return Check{
		Name:    "AudioSocket",
		Status:  StatusPass,
		Message: "AudioSocket port 8090 listening",
	}
}

func (c *Checker) checkConfiguration() Check {
	// Look for config file in common locations
	configPaths := []string{
		"config/ai-agent.yaml",
		"/app/config/ai-agent.yaml",
		"../config/ai-agent.yaml",
	}

	var configPath string
	for _, path := range configPaths {
		if _, err := os.Stat(path); err == nil {
			configPath = path
			break
		}
	}

	if configPath == "" {
		return Check{
			Name:        "Configuration",
			Status:      StatusFail,
			Message:     "config/ai-agent.yaml not found",
			Remediation: "Run: agent setup",
		}
	}

	// Check if file is readable
	raw, err := os.ReadFile(configPath)
	if err != nil {
		return Check{
			Name:        "Configuration",
			Status:      StatusFail,
			Message:     "Cannot read config file",
			Details:     err.Error(),
			Remediation: "Check file permissions",
		}
	}

	absPath, _ := filepath.Abs(configPath)

	// Parse YAML to catch obvious issues early.
	var root map[string]interface{}
	if err := yaml.Unmarshal(raw, &root); err != nil {
		return Check{
			Name:        "Configuration",
			Status:      StatusFail,
			Message:     "Invalid YAML in config/ai-agent.yaml",
			Details:     err.Error(),
			Remediation: "Fix YAML syntax (see docs/Configuration-Reference.md) and re-run: agent check",
		}
	}

	// Minimal schema checks. Credentials are injected from .env at runtime, so we warn
	// when blocks are missing rather than failing hard.
	status := StatusPass
	details := []string{absPath}
	remediation := []string{}

	providersRaw, providersOK := root["providers"].(map[string]interface{})
	if !providersOK {
		return Check{
			Name:        "Configuration",
			Status:      StatusFail,
			Message:     "Missing required 'providers' block in ai-agent.yaml",
			Details:     absPath,
			Remediation: "Add 'providers:' to config/ai-agent.yaml (see docs/Configuration-Reference.md)",
		}
	}
	if len(providersRaw) == 0 {
		return Check{
			Name:        "Configuration",
			Status:      StatusFail,
			Message:     "No providers configured in ai-agent.yaml",
			Details:     absPath,
			Remediation: "Add at least one provider under 'providers:' (see docs/Configuration-Reference.md)",
		}
	}

	if _, ok := root["default_provider"].(string); !ok {
		status = StatusWarn
		details = append(details, "default_provider is missing or not a string (engine may fall back to defaults)")
		remediation = append(remediation, "Set default_provider in config/ai-agent.yaml")
	} else {
		// If default_provider is set, ensure it references an existing provider key.
		if dp, _ := root["default_provider"].(string); dp != "" {
			if _, ok := providersRaw[dp]; !ok {
				return Check{
					Name:        "Configuration",
					Status:      StatusFail,
					Message:     fmt.Sprintf("default_provider references missing provider: %s", dp),
					Details:     absPath,
					Remediation: "Fix default_provider or add the provider under 'providers:' (see docs/Configuration-Reference.md)",
				}
			}
		}
	}

	if _, ok := root["asterisk"].(map[string]interface{}); !ok {
		status = StatusWarn
		details = append(details, "asterisk block missing (credentials are injected from .env at runtime)")
		remediation = append(remediation, "Ensure .env has ASTERISK_ARI_USERNAME and ASTERISK_ARI_PASSWORD (see docs/INSTALLATION.md)")
	}

	if _, ok := root["llm"].(map[string]interface{}); !ok {
		status = StatusWarn
		details = append(details, "llm block missing (defaults/env may be used; behavior may be unexpected)")
		remediation = append(remediation, "Add llm.initial_greeting and llm.prompt to config/ai-agent.yaml")
	}

	// Pipelines: if active_pipeline is set, it must exist under pipelines (unless explicitly disabled).
	if ap, ok := root["active_pipeline"].(string); ok {
		ap = strings.TrimSpace(ap)
		if ap != "" {
			if pipes, ok := root["pipelines"].(map[string]interface{}); ok {
				if _, exists := pipes[ap]; !exists {
					return Check{
						Name:        "Configuration",
						Status:      StatusFail,
						Message:     fmt.Sprintf("active_pipeline references missing pipeline: %s", ap),
						Details:     absPath,
						Remediation: "Fix active_pipeline or add the pipeline under 'pipelines:' (see docs/Configuration-Reference.md)",
					}
				}
			} else {
				return Check{
					Name:        "Configuration",
					Status:      StatusFail,
					Message:     "active_pipeline is set but pipelines block is missing",
					Details:     absPath,
					Remediation: "Add 'pipelines:' or clear active_pipeline (see docs/Configuration-Reference.md)",
				}
			}
		}
	}

	// Contexts: if a context explicitly sets provider, ensure it exists.
	if ctxs, ok := root["contexts"].(map[string]interface{}); ok {
		for name, rawCtx := range ctxs {
			ctx, ok := rawCtx.(map[string]interface{})
			if !ok {
				continue
			}
			if p, ok := ctx["provider"].(string); ok && strings.TrimSpace(p) != "" {
				if _, ok := providersRaw[p]; !ok {
					status = StatusWarn
					details = append(details, fmt.Sprintf("context %q references missing provider %q", name, p))
					remediation = append(remediation, "Fix context provider or add provider under 'providers:'")
				}
			}
		}
	}

	message := "Configuration file found"
	if status != StatusPass {
		message = "Configuration found with warnings"
	}

	var remediationStr string
	if len(remediation) > 0 {
		remediationStr = strings.Join(remediation, " | ")
	}

	return Check{
		Name:        "Configuration",
		Status:      status,
		Message:     message,
		Details:     strings.Join(details, "\n"),
		Remediation: remediationStr,
	}
}

func (c *Checker) checkProviderKeys() Check {
	// Check for common provider API keys in environment or .env file
	keys := map[string]string{
		"OPENAI_API_KEY":    "OpenAI",
		"DEEPGRAM_API_KEY":  "Deepgram",
		"ANTHROPIC_API_KEY": "Anthropic",
	}

	found := []string{}
	missing := []string{}

	for env, name := range keys {
		// Check both OS env and .env file
		if val := GetEnv(env, c.envMap); val != "" {
			found = append(found, name)
		} else {
			missing = append(missing, name)
		}
	}

	if len(found) == 0 {
		return Check{
			Name:        "Provider Keys",
			Status:      StatusFail,
			Message:     "No provider API keys found",
			Remediation: "Set API keys in .env file",
		}
	}

	status := StatusPass
	if len(missing) > 0 {
		status = StatusInfo
	}

	return Check{
		Name:    "Provider Keys",
		Status:  status,
		Message: fmt.Sprintf("%d provider(s) configured", len(found)),
		Details: fmt.Sprintf("Found: %s", strings.Join(found, ", ")),
	}
}

func (c *Checker) checkAudioPipeline() Check {
	// Check if we can find recent audio pipeline logs (note: ai_engine with underscore)
	cmd := exec.Command("docker", "logs", "--tail", "100", "ai_engine")
	output, err := cmd.Output()

	if err != nil {
		return Check{
			Name:    "Audio Pipeline",
			Status:  StatusWarn,
			Message: "Cannot check audio pipeline logs",
			Details: err.Error(),
		}
	}

	logs := string(output)

	// Look for key indicators
	indicators := map[string]string{
		"StreamingPlaybackManager initialized": "Streaming manager active",
		"AudioSocket server listening":         "AudioSocket ready",
		"VAD":                                  "VAD configured",
	}

	found := []string{}
	for pattern, desc := range indicators {
		if strings.Contains(logs, pattern) {
			found = append(found, desc)
		}
	}

	if len(found) == 0 {
		return Check{
			Name:    "Audio Pipeline",
			Status:  StatusWarn,
			Message: "No audio pipeline indicators in logs",
			Details: "This may be normal if engine just started",
		}
	}

	return Check{
		Name:    "Audio Pipeline",
		Status:  StatusPass,
		Message: fmt.Sprintf("%d component(s) detected", len(found)),
		Details: strings.Join(found, ", "),
	}
}

func (c *Checker) checkNetwork() Check {
	// Check Docker network and ARI connectivity
	cmd := exec.Command("docker", "network", "ls", "--format", "{{.Name}}")
	output, err := cmd.Output()

	if err != nil {
		return Check{
			Name:    "Network",
			Status:  StatusWarn,
			Message: "Cannot check Docker networks",
			Details: err.Error(),
		}
	}

	networks := strings.Split(strings.TrimSpace(string(output)), "\n")

	// Check if using bridge, host, or custom network
	ariHost := GetEnv("ASTERISK_HOST", c.envMap)
	if ariHost == "" {
		ariHost = "127.0.0.1"
	}

	var networkMode string
	if ariHost == "127.0.0.1" || ariHost == "localhost" {
		networkMode = "host network (localhost)"
	} else if strings.Contains(ariHost, ".") {
		networkMode = fmt.Sprintf("remote host (%s)", ariHost)
	} else {
		networkMode = fmt.Sprintf("container name (%s)", ariHost)
	}

	return Check{
		Name:    "Network",
		Status:  StatusPass,
		Message: fmt.Sprintf("Using %s", networkMode),
		Details: fmt.Sprintf("Networks available: %d", len(networks)),
	}
}

func (c *Checker) checkMediaDirectory() Check {
	// Check common media directory locations
	dirs := []string{
		"/mnt/asterisk_media/ai-generated",
		"/var/spool/asterisk/monitor",
		"./media",
	}

	for _, dir := range dirs {
		if stat, err := os.Stat(dir); err == nil && stat.IsDir() {
			// Check if writable
			testFile := filepath.Join(dir, ".agent_test")
			if err := os.WriteFile(testFile, []byte("test"), 0644); err == nil {
				os.Remove(testFile)
				return Check{
					Name:    "Media Directory",
					Status:  StatusPass,
					Message: "Media directory accessible and writable",
					Details: dir,
				}
			}
		}
	}

	return Check{
		Name:    "Media Directory",
		Status:  StatusWarn,
		Message: "Media directory not found or not writable",
		Details: "Checked: " + strings.Join(dirs, ", "),
	}
}

func (c *Checker) checkLogs() Check {
	// Check for recent errors in ai_engine logs (note: underscore)
	cmd := exec.Command("docker", "logs", "--tail", "100", "ai_engine")
	output, err := cmd.Output()

	if err != nil {
		return Check{
			Name:    "Logs",
			Status:  StatusWarn,
			Message: "Cannot read container logs",
			Details: err.Error(),
		}
	}

	logs := string(output)

	// Count errors and warnings
	errorCount := strings.Count(strings.ToUpper(logs), "ERROR")
	warnCount := strings.Count(strings.ToUpper(logs), "WARN")

	if errorCount > 10 {
		return Check{
			Name:        "Logs",
			Status:      StatusFail,
			Message:     fmt.Sprintf("%d errors in last 100 lines", errorCount),
			Details:     "Check logs: docker logs ai_engine",
			Remediation: "Run: agent rca",
		}
	}

	if errorCount > 0 || warnCount > 5 {
		return Check{
			Name:    "Logs",
			Status:  StatusWarn,
			Message: fmt.Sprintf("%d errors, %d warnings in last 100 lines", errorCount, warnCount),
			Details: "May indicate recent issues",
		}
	}

	return Check{
		Name:    "Logs",
		Status:  StatusPass,
		Message: "No critical errors in recent logs",
	}
}

func (c *Checker) checkRecentCalls() Check {
	// Try to find recent call info from logs (note: ai_engine with underscore)
	cmd := exec.Command("docker", "logs", "--tail", "500", "ai_engine")
	output, err := cmd.Output()

	if err != nil {
		return Check{
			Name:    "Recent Calls",
			Status:  StatusInfo,
			Message: "Cannot check recent calls",
			Details: err.Error(),
		}
	}

	logs := string(output)

	// Look for call indicators
	callIndicators := []string{
		"call_id",
		"Stasis start",
		"Channel answered",
	}

	found := false
	for _, indicator := range callIndicators {
		if strings.Contains(logs, indicator) {
			found = true
			break
		}
	}

	if !found {
		return Check{
			Name:    "Recent Calls",
			Status:  StatusInfo,
			Message: "No recent calls detected in logs",
			Details: "This is normal if no calls have been placed recently",
		}
	}

	return Check{
		Name:    "Recent Calls",
		Status:  StatusInfo,
		Message: "Recent call activity detected",
		Details: "See logs for details",
	}
}
