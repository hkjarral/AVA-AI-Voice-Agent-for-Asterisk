package wizard

import (
	"fmt"
	"strings"
)

// Wizard orchestrates the interactive configuration
type Wizard struct {
	config     *Config
	hasChanges bool
	totalSteps int
}

// NewWizard creates a new wizard instance
func NewWizard() (*Wizard, error) {
	cfg, err := LoadConfig()
	if err != nil {
		return nil, err
	}

	return &Wizard{
		config:     cfg,
		hasChanges: false,
		totalSteps: 6,
	}, nil
}

// Run executes the wizard
func (w *Wizard) Run() error {
	// Header
	fmt.Println()
	fmt.Println("🚀 Asterisk AI Voice Agent - Setup Wizard")
	fmt.Println("══════════════════════════════════════════")
	fmt.Println()

	PrintInfo("Reading current configuration...")
	PrintSuccess(fmt.Sprintf("Loaded %s", w.config.EnvPath))
	if w.config.ActivePipeline != "" {
		PrintSuccess(fmt.Sprintf("Loaded %s (pipeline: %s)", w.config.YAMLPath, w.config.ActivePipeline))
	} else if w.config.DefaultProvider != "" {
		PrintSuccess(fmt.Sprintf("Loaded %s (provider: %s)", w.config.YAMLPath, w.config.DefaultProvider))
	}

	// Step 1: Mode selection
	if err := w.stepModeSelection(); err != nil {
		return err
	}

	// Step 2: Asterisk configuration
	if err := w.stepAsteriskConfig(); err != nil {
		return err
	}

	// Step 3: Audio transport
	if err := w.stepAudioTransport(); err != nil {
		return err
	}

	// Step 4: Pipeline/Provider selection
	if err := w.stepPipelineSelection(); err != nil {
		return err
	}

	// Step 5: API keys
	if err := w.stepAPIKeys(); err != nil {
		return err
	}

	// Step 6: Review and apply
	if err := w.stepReviewAndApply(); err != nil {
		return err
	}

	return nil
}

// stepModeSelection handles Step 1: Mode Selection
func (w *Wizard) stepModeSelection() error {
	PrintStep(1, w.totalSteps, "Mode Selection")

	currentMode := "Unknown"
	if w.config.ActivePipeline != "" {
		currentMode = fmt.Sprintf("Pipeline mode (%s)", w.config.ActivePipeline)
	} else if w.config.DefaultProvider != "" {
		currentMode = fmt.Sprintf("Monolithic mode (%s)", w.config.DefaultProvider)
	}

	PrintInfo("Current: " + currentMode)
	fmt.Println()

	options := []string{"Keep current configuration"}
	targets := []string{"keep:"}
	for _, name := range w.config.AvailablePipelines {
		options = append(options, "Pipeline: "+name)
		targets = append(targets, "pipeline:"+name)
	}
	for _, name := range w.config.AvailableProviders {
		options = append(options, "Full agent: "+name)
		targets = append(targets, "provider:"+name)
	}

	choice := PromptSelect("Select mode:", options, 0)

	if choice <= 0 || choice >= len(targets) {
		PrintInfo("Keeping current configuration")
		return nil
	}
	kind, name, _ := strings.Cut(targets[choice], ":")
	if kind == "pipeline" {
		w.config.ActivePipeline = name
		w.config.DefaultProvider = name
	} else {
		w.config.ActivePipeline = ""
		w.config.DefaultProvider = name
	}
	w.hasChanges = true

	return nil
}

// stepAsteriskConfig handles Step 2: Asterisk Configuration
func (w *Wizard) stepAsteriskConfig() error {
	PrintStep(2, w.totalSteps, "Asterisk Configuration")

	if w.config.AsteriskHost != "" {
		PrintInfo(fmt.Sprintf("Current: %s:8088 (user: %s)",
			w.config.AsteriskHost, w.config.AsteriskUsername))
	}
	fmt.Println()

	// Prompts
	newHost := PromptText("Asterisk Host", w.config.AsteriskHost)
	if newHost != w.config.AsteriskHost {
		w.config.AsteriskHost = newHost
		w.hasChanges = true
	}

	newUser := PromptText("ARI Username", w.config.AsteriskUsername)
	if newUser != w.config.AsteriskUsername {
		w.config.AsteriskUsername = newUser
		w.hasChanges = true
	}

	newPass := PromptPassword("ARI Password", w.config.AsteriskPassword != "")
	if newPass != "" && newPass != w.config.AsteriskPassword {
		w.config.AsteriskPassword = newPass
		w.hasChanges = true
	}

	// Test connectivity
	fmt.Println()
	PrintInfo("Testing ARI connection...")
	if err := TestARIConnectivity(w.config.AsteriskHost,
		w.config.AsteriskUsername, w.config.AsteriskPassword); err != nil {
		PrintWarning(fmt.Sprintf("ARI test failed: %v", err))
		if !PromptConfirm("Continue anyway?", false) {
			return fmt.Errorf("ARI connectivity required")
		}
	} else {
		PrintSuccess(fmt.Sprintf("ARI accessible at %s:8088", w.config.AsteriskHost))
	}

	return nil
}

// stepAudioTransport handles Step 3: Audio Transport
func (w *Wizard) stepAudioTransport() error {
	PrintStep(3, w.totalSteps, "Audio Transport")

	if w.config.AudioTransport != "" {
		PrintInfo(fmt.Sprintf("Current: %s", w.config.AudioTransport))
		if w.config.AudioTransport == "audiosocket" && w.config.AudioSocketPort != "" {
			PrintInfo(fmt.Sprintf("AudioSocket port: %s", w.config.AudioSocketPort))
		}
	}
	fmt.Println()

	options := []string{
		"AudioSocket (TCP media)",
		"ExternalMedia (RTP; current default for new installs)",
	}

	defaultIdx := 0
	if w.config.AudioTransport == "externalmedia" {
		defaultIdx = 1
	}

	choice := PromptSelect("Select transport:", options, defaultIdx)

	newTransport := "audiosocket"
	if choice == 1 {
		newTransport = "externalmedia"
	}

	if newTransport != w.config.AudioTransport {
		w.config.AudioTransport = newTransport
		w.hasChanges = true
	}

	// AudioSocket specific
	if newTransport == "audiosocket" {
		fmt.Println()
		newPort := PromptText("AudioSocket Port", w.config.AudioSocketPort)
		if newPort == "" {
			newPort = "8090"
		}
		if newPort != w.config.AudioSocketPort {
			w.config.AudioSocketPort = newPort
			w.hasChanges = true
		}

		// Test port
		fmt.Println()
		PrintInfo(fmt.Sprintf("Testing AudioSocket port %s...", newPort))
		if err := TestAudioSocketPort(newPort); err != nil {
			PrintWarning(fmt.Sprintf("Port test failed: %v", err))
			PrintInfo("Port may not be listening yet (container not started)")
		} else {
			PrintSuccess(fmt.Sprintf("Port %s is listening", newPort))
		}
	}

	return nil
}

// stepPipelineSelection handles Step 4: Pipeline/Provider Selection
func (w *Wizard) stepPipelineSelection() error {
	PrintStep(4, w.totalSteps, "Pipeline Configuration")

	if w.config.ActivePipeline != "" {
		PrintInfo(fmt.Sprintf("Selected pipeline: %s", w.config.ActivePipeline))
	} else if w.config.DefaultProvider != "" {
		PrintInfo(fmt.Sprintf("Selected provider: %s", w.config.DefaultProvider))
	}

	// Configuration already selected in Step 1
	// This step just confirms and shows what it means

	return nil
}

// keySpec describes how to display and optionally validate a credential.
// Label is the full human label (e.g. "OpenAI API Key", "ElevenLabs Agent ID") —
// not every required credential is an API key, so the label is spelled out in full.
type keySpec struct {
	Label    string
	Validate func(string) error // nil means store without network validation
}

// keySpecs maps env-var names to their display/validation specs.
var keySpecs = map[string]keySpec{
	"OPENAI_API_KEY":      {Label: "OpenAI API Key", Validate: TestOpenAIKey},
	"DEEPGRAM_API_KEY":    {Label: "Deepgram API Key", Validate: TestDeepgramKey},
	"ANTHROPIC_API_KEY":   {Label: "Anthropic API Key", Validate: TestAnthropicKey},
	"ELEVENLABS_API_KEY":  {Label: "ElevenLabs API Key", Validate: nil},
	"ELEVENLABS_AGENT_ID": {Label: "ElevenLabs Agent ID", Validate: nil},
	"TELNYX_API_KEY":      {Label: "Telnyx API Key", Validate: nil},
	"GROQ_API_KEY":        {Label: "Groq API Key", Validate: nil},
	"GOOGLE_API_KEY":      {Label: "Google API Key", Validate: nil},
	"XAI_API_KEY":         {Label: "xAI (Grok) API Key", Validate: nil},
	"CAMB_API_KEY":        {Label: "Camb.ai API Key", Validate: nil},
}

// stepAPIKeys handles Step 5: API Keys & Validation
func (w *Wizard) stepAPIKeys() error {
	PrintStep(5, w.totalSteps, "API Keys & Validation")

	required := w.config.RequiredEnvKeys()
	if len(required) == 0 {
		fmt.Println()
		PrintInfo("No cloud API keys required for this pipeline.")
		return nil
	}

	for _, envVar := range required {
		spec, known := keySpecs[envVar]
		if !known {
			spec = keySpec{Label: envVar, Validate: nil}
		}

		fmt.Println()
		PrintInfo(fmt.Sprintf("%s: %s", spec.Label, GetMaskedKey(w.config.GetKey(envVar))))
		newKey := PromptText(spec.Label+" (leave blank to keep)", "")
		if newKey == "" {
			continue
		}

		if spec.Validate != nil {
			PrintInfo(fmt.Sprintf("Testing %s...", spec.Label))
			if err := spec.Validate(newKey); err != nil {
				PrintError(fmt.Sprintf("%s test failed: %v", spec.Label, err))
				if PromptConfirm("Retry?", true) {
					return w.stepAPIKeys()
				}
				if !PromptConfirm("Continue with invalid value?", false) {
					return fmt.Errorf("valid %s required", spec.Label)
				}
			} else {
				PrintSuccess(fmt.Sprintf("%s valid", spec.Label))
			}
		}

		w.config.SetKey(envVar, newKey)
		w.hasChanges = true
	}

	return nil
}

// stepReviewAndApply handles Step 6: Review & Apply Changes
func (w *Wizard) stepReviewAndApply() error {
	PrintStep(6, w.totalSteps, "Review & Apply Changes")

	if !w.hasChanges {
		PrintInfo("No changes detected")
		return nil
	}

	// Show changes
	fmt.Println()
	PrintInfo("Configuration changes:")
	fmt.Println("  • .env file will be updated")
	if w.config.ActivePipeline != "" {
		fmt.Printf("  • Pipeline: %s\n", w.config.ActivePipeline)
	}
	if w.config.DefaultProvider != "" {
		fmt.Printf("  • Provider: %s\n", w.config.DefaultProvider)
	}
	fmt.Println()

	// Confirm
	if !PromptConfirm("Apply changes?", true) {
		PrintInfo("Changes cancelled")
		return nil
	}

	// Save .env
	fmt.Println()
	PrintInfo("Saving .env...")
	if err := w.config.SaveEnv(); err != nil {
		return fmt.Errorf("failed to save .env: %w", err)
	}
	PrintSuccess("Updated .env")

	// Save YAML if pipeline changed
	if w.config.ActivePipeline != "" || w.config.DefaultProvider != "" {
		PrintInfo("Updating config/ai-agent.yaml...")
		template := "config/ai-agent.example.yaml"
		if err := w.config.SaveYAML(template); err != nil {
			PrintWarning(fmt.Sprintf("Failed to update YAML: %v", err))
		} else {
			PrintSuccess("Updated config/ai-agent.yaml")
		}
	}

	// Rebuild containers
	fmt.Println()
	if PromptConfirm("Rebuild ai_engine container?", true) {
		PrintInfo("Checking Docker...")
		if err := TestDockerRunning(); err != nil {
			PrintWarning("Docker not running, skipping rebuild")
		} else {
			pipeline := w.config.ActivePipeline
			if pipeline == "" {
				pipeline = w.config.DefaultProvider
			}
			if err := RebuildContainers(pipeline); err != nil {
				PrintError(fmt.Sprintf("Rebuild failed: %v", err))
				PrintInfo("Run manually: docker compose -p asterisk-ai-voice-agent up -d --force-recreate ai_engine")
			}
		}
	}

	// Next steps
	fmt.Println()
	fmt.Println("═══════════════════════════════════════════")
	PrintSuccess("Configuration complete!")
	fmt.Println("═══════════════════════════════════════════")
	fmt.Println()
	fmt.Println("Next steps:")
	fmt.Println("  • agent check      (verify health)")
	fmt.Println("  • Make a test call")
	fmt.Println("  • agent rca        (analyze the most recent call)")
	fmt.Println()

	return nil
}
