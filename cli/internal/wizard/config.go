package wizard

import (
	"bufio"
	"fmt"
	"os"
	"sort"
	"strings"

	"gopkg.in/yaml.v3"
)

// Config holds all configuration
type Config struct {
	// .env values
	AsteriskHost     string
	AsteriskUsername string
	AsteriskPassword string
	AudioTransport   string
	AudioSocketHost  string
	AudioSocketPort  string
	OpenAIKey        string
	DeepgramKey      string
	AnthropicKey     string

	// YAML values
	ActivePipeline     string
	DefaultProvider    string
	AvailablePipelines []string
	AvailableProviders []string

	// File paths
	EnvPath  string
	YAMLPath string
}

// LoadConfig reads current configuration from .env and YAML
func LoadConfig() (*Config, error) {
	// Try to find .env - check current dir and parent dir
	envPath := ".env"
	if _, err := os.Stat(envPath); os.IsNotExist(err) {
		envPath = "../.env"
		if _, err := os.Stat(envPath); os.IsNotExist(err) {
			envPath = ".env" // Reset to current for creation
		}
	}

	// Prefer local override file for reading; fall back to base
	yamlPath := "config/ai-agent.local.yaml"
	if _, err := os.Stat(yamlPath); os.IsNotExist(err) {
		yamlPath = "config/ai-agent.yaml"
		if _, err := os.Stat(yamlPath); os.IsNotExist(err) {
			yamlPath = "../config/ai-agent.local.yaml"
			if _, err := os.Stat(yamlPath); os.IsNotExist(err) {
				yamlPath = "../config/ai-agent.yaml"
			}
		}
	}

	cfg := &Config{
		EnvPath:  envPath,
		YAMLPath: yamlPath,
	}

	// Load .env
	if err := cfg.loadEnv(); err != nil {
		return nil, fmt.Errorf("failed to load .env: %w", err)
	}

	// Load YAML
	if err := cfg.loadYAML(); err != nil {
		// YAML might not exist yet, that's okay
		PrintWarning(fmt.Sprintf("Could not load %s: %v", cfg.YAMLPath, err))
	}
	cfg.loadAvailableTargets()

	return cfg, nil
}

// loadAvailableTargets discovers selectable runtime targets from the shipped
// base config plus operator overrides. This keeps the CLI wizard synchronized
// with newly added providers/pipelines without hard-coding a stale menu.
func (c *Config) loadAvailableTargets() {
	pipelines := map[string]bool{}
	providers := map[string]bool{}
	paths := []string{"config/ai-agent.yaml", "config/ai-agent.local.yaml"}
	if strings.HasPrefix(c.YAMLPath, "../") {
		paths = []string{"../config/ai-agent.yaml", "../config/ai-agent.local.yaml"}
	}
	for _, path := range paths {
		data, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		var root map[string]interface{}
		if yaml.Unmarshal(data, &root) != nil {
			continue
		}
		if block, ok := root["pipelines"].(map[string]interface{}); ok {
			for name := range block {
				pipelines[name] = true
			}
		}
		if block, ok := root["providers"].(map[string]interface{}); ok {
			for name, raw := range block {
				cfg, _ := raw.(map[string]interface{})
				kind := strings.ToLower(fmt.Sprint(cfg["type"]))
				caps, _ := cfg["capabilities"].([]interface{})
				capSet := map[string]bool{}
				for _, cap := range caps {
					capSet[strings.ToLower(fmt.Sprint(cap))] = true
				}
				knownFull := name == "openai_realtime" || name == "deepgram" || name == "google_live" || name == "elevenlabs_agent" || name == "local" || strings.HasPrefix(name, "grok")
				if knownFull || kind == "full" || (capSet["stt"] && capSet["llm"] && capSet["tts"]) {
					providers[name] = true
				}
			}
		}
	}
	for name := range pipelines {
		c.AvailablePipelines = append(c.AvailablePipelines, name)
	}
	for name := range providers {
		c.AvailableProviders = append(c.AvailableProviders, name)
	}
	sort.Strings(c.AvailablePipelines)
	sort.Strings(c.AvailableProviders)
}

// loadEnv reads .env file
func (c *Config) loadEnv() error {
	file, err := os.Open(c.EnvPath)
	if err != nil {
		if os.IsNotExist(err) {
			// .env doesn't exist, create from example if available
			if _, err := os.Stat(".env.example"); err == nil {
				return c.createEnvFromExample()
			}
			return fmt.Errorf(".env file not found")
		}
		return err
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())

		// Skip empty lines and comments
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}

		// Parse KEY=VALUE
		parts := strings.SplitN(line, "=", 2)
		if len(parts) != 2 {
			continue
		}

		key := strings.TrimSpace(parts[0])
		value := strings.TrimSpace(parts[1])

		// Remove quotes if present
		value = strings.Trim(value, "\"'")

		switch key {
		case "ASTERISK_HOST":
			c.AsteriskHost = value
		case "ASTERISK_ARI_USERNAME":
			c.AsteriskUsername = value
		case "ASTERISK_ARI_PASSWORD":
			c.AsteriskPassword = value
		case "AUDIO_TRANSPORT":
			c.AudioTransport = value
		case "AUDIOSOCKET_HOST":
			c.AudioSocketHost = value
		case "AUDIOSOCKET_PORT":
			c.AudioSocketPort = value
		case "OPENAI_API_KEY":
			c.OpenAIKey = value
		case "DEEPGRAM_API_KEY":
			c.DeepgramKey = value
		case "ANTHROPIC_API_KEY":
			c.AnthropicKey = value
		}
	}

	return scanner.Err()
}

// createEnvFromExample creates .env from .env.example
func (c *Config) createEnvFromExample() error {
	input, err := os.ReadFile(".env.example")
	if err != nil {
		return err
	}

	err = os.WriteFile(c.EnvPath, input, 0600)
	if err != nil {
		return err
	}

	PrintSuccess("Created .env from .env.example")
	return c.loadEnv()
}

// loadYAML reads config/ai-agent.yaml
func (c *Config) loadYAML() error {
	base := "config/ai-agent.yaml"
	local := "config/ai-agent.local.yaml"
	if strings.HasPrefix(c.YAMLPath, "../") {
		base, local = "../config/ai-agent.yaml", "../config/ai-agent.local.yaml"
	}
	loaded := false
	for _, path := range []string{base, local} {
		data, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		var yamlData map[string]interface{}
		if err := yaml.Unmarshal(data, &yamlData); err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		loaded = true
		if val, exists := yamlData["active_pipeline"]; exists {
			c.ActivePipeline, _ = val.(string) // explicit null clears the base
		}
		if val, ok := yamlData["default_provider"].(string); ok {
			c.DefaultProvider = val
		}
	}
	if !loaded {
		return fmt.Errorf("no ai-agent YAML configuration found")
	}
	return nil
}

// SaveEnv updates .env file in-place
func (c *Config) SaveEnv() error {
	// Read existing .env
	lines := []string{}

	file, err := os.Open(c.EnvPath)
	if err == nil {
		scanner := bufio.NewScanner(file)
		for scanner.Scan() {
			lines = append(lines, scanner.Text())
		}
		file.Close()
	}

	// Update values
	updates := map[string]string{
		"ASTERISK_HOST":         c.AsteriskHost,
		"ASTERISK_ARI_USERNAME": c.AsteriskUsername,
		"ASTERISK_ARI_PASSWORD": c.AsteriskPassword,
		"AUDIO_TRANSPORT":       c.AudioTransport,
		"AUDIOSOCKET_HOST":      c.AudioSocketHost,
		"AUDIOSOCKET_PORT":      c.AudioSocketPort,
		"OPENAI_API_KEY":        c.OpenAIKey,
		"DEEPGRAM_API_KEY":      c.DeepgramKey,
		"ANTHROPIC_API_KEY":     c.AnthropicKey,
	}

	// Apply updates
	for key, value := range updates {
		if value == "" {
			continue // Skip empty values
		}

		found := false
		for i, line := range lines {
			trimmed := strings.TrimSpace(line)
			if strings.HasPrefix(trimmed, key+"=") || strings.HasPrefix(trimmed, "#"+key+"=") {
				lines[i] = fmt.Sprintf("%s=%s", key, value)
				found = true
				break
			}
		}

		if !found {
			// Append new key
			lines = append(lines, fmt.Sprintf("%s=%s", key, value))
		}
	}

	// Write back
	content := strings.Join(lines, "\n") + "\n"
	if err := os.WriteFile(c.EnvPath, []byte(content), 0600); err != nil {
		return err
	}
	return os.Chmod(c.EnvPath, 0600)
}

// SaveYAML updates config/ai-agent.local.yaml (operator override file)
func (c *Config) SaveYAML(template string) error {
	_ = template // Kept for backwards compatibility with existing call sites.

	// Write only local overrides to avoid freezing base defaults in the operator file.
	localPath := "config/ai-agent.local.yaml"
	if _, err := os.Stat("config"); os.IsNotExist(err) {
		localPath = "../config/ai-agent.local.yaml"
	}

	yamlData := map[string]interface{}{}
	if input, err := os.ReadFile(localPath); err == nil {
		var existing map[string]interface{}
		if err := yaml.Unmarshal(input, &existing); err == nil && existing != nil {
			yamlData = existing
		}
	}

	// Always write active_pipeline, including null when switching from a
	// pipeline to a full-agent provider. Leaving the previous override behind
	// silently routed calls through the wrong engine path.
	if c.ActivePipeline != "" {
		yamlData["active_pipeline"] = c.ActivePipeline
	} else {
		yamlData["active_pipeline"] = nil
	}
	if c.DefaultProvider != "" {
		yamlData["default_provider"] = c.DefaultProvider
	}

	// Write back
	output, err := yaml.Marshal(yamlData)
	if err != nil {
		return err
	}

	return os.WriteFile(localPath, output, 0644)
}

// GetMaskedKey returns masked version of API key for display
func GetMaskedKey(key string) string {
	if key == "" {
		return "(not set)"
	}
	if len(key) < 8 {
		return "****"
	}
	return "**..." + key[len(key)-3:]
}
