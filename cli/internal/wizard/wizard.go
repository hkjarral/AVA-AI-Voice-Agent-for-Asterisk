package wizard

import (
	"fmt"
)

// Wizard orchestrates the interactive configuration
type Wizard struct {
	config      *Config
	hasChanges  bool
	totalSteps  int
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
	
	options := []string{
		"Keep current configuration",
		"Pipeline: cloud_openai (Deepgram STT → OpenAI LLM/TTS)",
		"Pipeline: local_only (Local STT/LLM/TTS)",
		"Pipeline: hybrid_deepgram_openai",
		"Monolithic: OpenAI Realtime",
		"Monolithic: Deepgram Voice Agent",
	}
	
	choice := PromptSelect("Select mode:", options, 0)
	
	// Apply selection
	switch choice {
	case 0:
		// Keep current
		PrintInfo("Keeping current configuration")
	case 1:
		w.config.ActivePipeline = "cloud_openai"
		w.config.DefaultProvider = ""
		w.hasChanges = true
	case 2:
		w.config.ActivePipeline = "local_only"
		w.config.DefaultProvider = ""
		w.hasChanges = true
	case 3:
		w.config.ActivePipeline = "hybrid_deepgram_openai"
		w.config.DefaultProvider = ""
		w.hasChanges = true
	case 4:
		w.config.ActivePipeline = ""
		w.config.DefaultProvider = "openai_realtime"
		w.hasChanges = true
	case 5:
		w.config.ActivePipeline = ""
		w.config.DefaultProvider = "deepgram"
		w.hasChanges = true
	}
	
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
		"AudioSocket (recommended)",
		"ExternalMedia (RTP/WebRTC)",
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

// stepAPIKeys handles Step 5: API Keys & Validation
func (w *Wizard) stepAPIKeys() error {
	PrintStep(5, w.totalSteps, "API Keys & Validation")
	
	// Determine which keys are needed based on pipeline/provider
	needsOpenAI := w.config.ActivePipeline == "cloud_openai" || 
		w.config.ActivePipeline == "hybrid_deepgram_openai" ||
		w.config.DefaultProvider == "openai_realtime"
	
	needsDeepgram := w.config.ActivePipeline == "cloud_openai" ||
		w.config.ActivePipeline == "hybrid_deepgram_openai" ||
		w.config.DefaultProvider == "deepgram"
	
	// OpenAI
	if needsOpenAI {
		fmt.Println()
		PrintInfo(fmt.Sprintf("OpenAI API Key: %s", GetMaskedKey(w.config.OpenAIKey)))
		newKey := PromptText("OpenAI API Key (leave blank to keep)", "")
		if newKey != "" {
			PrintInfo("Testing OpenAI API key...")
			if err := TestOpenAIKey(newKey); err != nil {
				PrintError(fmt.Sprintf("OpenAI test failed: %v", err))
				if PromptConfirm("Retry?", true) {
					return w.stepAPIKeys() // Retry this step
				}
				if !PromptConfirm("Continue with invalid key?", false) {
					return fmt.Errorf("valid OpenAI key required")
				}
			} else {
				PrintSuccess("OpenAI API key valid")
				w.config.OpenAIKey = newKey
				w.hasChanges = true
			}
		}
	}
	
	// Deepgram
	if needsDeepgram {
		fmt.Println()
		PrintInfo(fmt.Sprintf("Deepgram API Key: %s", GetMaskedKey(w.config.DeepgramKey)))
		newKey := PromptText("Deepgram API Key (leave blank to keep)", "")
		if newKey != "" {
			PrintInfo("Testing Deepgram API key...")
			if err := TestDeepgramKey(newKey); err != nil {
				PrintError(fmt.Sprintf("Deepgram test failed: %v", err))
				if PromptConfirm("Retry?", true) {
					return w.stepAPIKeys() // Retry
				}
				if !PromptConfirm("Continue with invalid key?", false) {
					return fmt.Errorf("valid Deepgram key required")
				}
			} else {
				PrintSuccess("Deepgram API key valid")
				w.config.DeepgramKey = newKey
				w.hasChanges = true
			}
		}
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
