package wizard

import (
	"bufio"
	"fmt"
	"os"
	"sort"
	"strings"

	"gopkg.in/yaml.v3"
)

// PipelineComponents holds the adapter names for each stage of a pipeline.
type PipelineComponents struct {
	STT string
	LLM string
	TTS string
}

// adapterEnvKey maps pipeline adapter names to the env var they require.
// Local/offline adapters (local_stt, local_llm, local_tts, native_llm,
// ollama_llm) are intentionally absent — they need no cloud API key.
// CAMB_API_KEY is the env var used by the cambai_tts adapter (verified
// against config/ai-agent.golden-cambai.yaml).
var adapterEnvKey = map[string]string{
	"openai_stt":    "OPENAI_API_KEY",
	"openai_llm":    "OPENAI_API_KEY",
	"openai_tts":    "OPENAI_API_KEY",
	"deepgram_stt":  "DEEPGRAM_API_KEY",
	"deepgram_tts":  "DEEPGRAM_API_KEY",
	"elevenlabs_tts": "ELEVENLABS_API_KEY",
	"telnyx_llm":    "TELNYX_API_KEY",
	"groq_stt":      "GROQ_API_KEY",
	"groq_llm":      "GROQ_API_KEY",
	"groq_tts":      "GROQ_API_KEY",
	"cambai_tts":    "CAMB_API_KEY",
}

// providerEnvKey maps full-agent DefaultProvider values to their env vars.
// elevenlabs_agent requires both the API key and the agent ID.
// Custom grok* instances (e.g. acme_grok) are intentionally absent: per the
// YAML conventions they authenticate via api_key_file, not an env var.
var providerEnvKey = map[string][]string{
	"openai_realtime":  {"OPENAI_API_KEY"},
	"deepgram":         {"DEEPGRAM_API_KEY"},
	"google_live":      {"GOOGLE_API_KEY"},
	"grok":             {"XAI_API_KEY"},
	"elevenlabs_agent": {"ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID"},
}

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
	// Keys holds additional provider API keys keyed by env-var name.
	Keys map[string]string

	// YAML values
	ActivePipeline     string
	DefaultProvider    string
	AvailablePipelines []string
	AvailableProviders []string
	// Pipelines maps each pipeline name to its adapter components.
	Pipelines map[string]PipelineComponents

	// File paths
	EnvPath  string
	YAMLPath string
}

// extraEnvKeys is the list of provider API key env vars beyond the three
// named fields (OPENAI_API_KEY, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY).
var extraEnvKeys = []string{
	"ELEVENLABS_API_KEY",
	"ELEVENLABS_AGENT_ID",
	"TELNYX_API_KEY",
	"GROQ_API_KEY",
	"GOOGLE_API_KEY",
	"XAI_API_KEY",
	"CAMB_API_KEY",
}

// GetKey returns the current value for the given env-var name, routing
// the three legacy named fields to their struct fields.
func (c *Config) GetKey(envVar string) string {
	switch envVar {
	case "OPENAI_API_KEY":
		return c.OpenAIKey
	case "DEEPGRAM_API_KEY":
		return c.DeepgramKey
	case "ANTHROPIC_API_KEY":
		return c.AnthropicKey
	default:
		return c.Keys[envVar]
	}
}

// SetKey stores a value for the given env-var name, routing the three
// legacy named fields to their struct fields and everything else to Keys.
func (c *Config) SetKey(envVar, value string) {
	switch envVar {
	case "OPENAI_API_KEY":
		c.OpenAIKey = value
	case "DEEPGRAM_API_KEY":
		c.DeepgramKey = value
	case "ANTHROPIC_API_KEY":
		c.AnthropicKey = value
	default:
		if c.Keys == nil {
			c.Keys = make(map[string]string)
		}
		c.Keys[envVar] = value
	}
}

// RequiredEnvKeys derives the set of cloud API key env vars needed by the
// currently selected pipeline or full-agent provider. It is pure (no I/O)
// and returns a sorted, deduplicated slice. When all components are local,
// the slice is empty.
func (c *Config) RequiredEnvKeys() []string {
	seen := map[string]bool{}

	// Collect keys from the pipeline's component adapters (if set).
	if c.ActivePipeline != "" {
		if comps, ok := c.Pipelines[c.ActivePipeline]; ok {
			for _, adapter := range []string{comps.STT, comps.LLM, comps.TTS} {
				if envVar, mapped := adapterEnvKey[adapter]; mapped {
					seen[envVar] = true
				}
			}
		}
	}
	// Collect keys from the full-agent provider independently — configs such as
	// ai-agent.golden-google-live.yaml set BOTH active_pipeline AND
	// default_provider, so we must union both sources rather than else-if.
	if c.DefaultProvider != "" {
		if envVars, mapped := providerEnvKey[c.DefaultProvider]; mapped {
			for _, ev := range envVars {
				seen[ev] = true
			}
		}
	}

	keys := make([]string, 0, len(seen))
	for k := range seen {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
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
		EnvPath:   envPath,
		YAMLPath:  yamlPath,
		Keys:      make(map[string]string),
		Pipelines: make(map[string]PipelineComponents),
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
	if c.Pipelines == nil {
		c.Pipelines = make(map[string]PipelineComponents)
	}
	for i, path := range paths {
		data, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		var root map[string]interface{}
		if yaml.Unmarshal(data, &root) != nil {
			continue
		}
		if block, ok := root["pipelines"].(map[string]interface{}); ok {
			for name, raw := range block {
				pipelines[name] = true
				entry, _ := raw.(map[string]interface{})
				stt, _ := entry["stt"].(string)
				llm, _ := entry["llm"].(string)
				tts, _ := entry["tts"].(string)
				if i == 0 {
					// Base file: whole-assignment is correct — nothing to preserve yet.
					c.Pipelines[name] = PipelineComponents{STT: stt, LLM: llm, TTS: tts}
				} else {
					// Local override file: merge field-by-field so that a partial
					// override (e.g. only tts) preserves the base values for the
					// fields the local file omits. A pipeline that exists only in
					// the local file is still added in full.
					existing := c.Pipelines[name]
					if stt != "" {
						existing.STT = stt
					}
					if llm != "" {
						existing.LLM = llm
					}
					if tts != "" {
						existing.TTS = tts
					}
					c.Pipelines[name] = existing
				}
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
		default:
			// Load any extra provider key env vars we know about.
			for _, extra := range extraEnvKeys {
				if key == extra {
					if c.Keys == nil {
						c.Keys = make(map[string]string)
					}
					c.Keys[key] = value
					break
				}
			}
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
	for k, v := range c.Keys {
		if v != "" {
			updates[k] = v
		}
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
