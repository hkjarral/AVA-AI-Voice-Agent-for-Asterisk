package config

import (
	"fmt"
	"os"

	"gopkg.in/yaml.v3"
)

// ValidationResult holds validation results
type ValidationResult struct {
	Passed   []string
	Warnings []string
	Errors   []string
}

// Validator validates configuration files
type Validator struct {
	configPath string
	config     map[string]interface{}
}

// NewValidator creates a new config validator
func NewValidator(configPath string) *Validator {
	return &Validator{
		configPath: configPath,
	}
}

// Validate validates the configuration file
func (v *Validator) Validate() (*ValidationResult, error) {
	result := &ValidationResult{
		Passed:   []string{},
		Warnings: []string{},
		Errors:   []string{},
	}

	// Load YAML
	data, err := os.ReadFile(v.configPath)
	if err != nil {
		return nil, fmt.Errorf("failed to read config file: %w", err)
	}

	// Parse YAML
	if err := yaml.Unmarshal(data, &v.config); err != nil {
		return nil, fmt.Errorf("invalid YAML syntax: %w", err)
	}

	result.Passed = append(result.Passed, "YAML syntax valid")

	// Validate structure
	v.validateStructure(result)
	v.validateProviders(result)
	v.validateSampleRates(result)
	v.validateTransport(result)
	v.validateBargeIn(result)

	return result, nil
}

// validateStructure checks required top-level fields
func (v *Validator) validateStructure(result *ValidationResult) {
	required := []string{"default_provider", "providers"}

	for _, field := range required {
		if _, ok := v.config[field]; ok {
			result.Passed = append(result.Passed, fmt.Sprintf("Required field '%s' present", field))
		} else {
			result.Errors = append(result.Errors, fmt.Sprintf("Missing required field: %s", field))
		}
	}

	// Contexts are optional in v7 because operator-managed agents live in
	// agents.db. Their absence is not a configuration warning.
}

// validateProviders checks provider configurations
func (v *Validator) validateProviders(result *ValidationResult) {
	providers, ok := v.config["providers"].(map[string]interface{})
	if !ok {
		result.Errors = append(result.Errors, "Invalid 'providers' structure")
		return
	}

	hasEnabled := false

	for name, config := range providers {
		providerConfig, ok := config.(map[string]interface{})
		if !ok {
			result.Errors = append(result.Errors, fmt.Sprintf("Invalid config for provider: %s", name))
			continue
		}

		// Check enabled flag
		enabled, ok := providerConfig["enabled"].(bool)
		if ok && enabled {
			hasEnabled = true
			result.Passed = append(result.Passed, fmt.Sprintf("Provider '%s' enabled", name))
		}

		// Check required provider-specific fields
		v.validateProviderConfig(name, providerConfig, result)
	}

	if !hasEnabled {
		result.Warnings = append(result.Warnings, "No providers are enabled")
	}

	// default_provider may point to either a full provider or a modular pipeline.
	defaultProvider, ok := v.config["default_provider"].(string)
	if ok {
		if providerConfig, exists := providers[defaultProvider]; exists {
			if pc, ok := providerConfig.(map[string]interface{}); ok {
				if enabled, ok := pc["enabled"].(bool); ok && enabled {
					result.Passed = append(result.Passed, fmt.Sprintf("Default provider '%s' is enabled", defaultProvider))
				} else {
					result.Warnings = append(result.Warnings, fmt.Sprintf("Default provider '%s' is not enabled", defaultProvider))
				}
			}
		} else if pipelines, ok := v.config["pipelines"].(map[string]interface{}); ok {
			if _, exists := pipelines[defaultProvider]; exists {
				result.Passed = append(result.Passed, fmt.Sprintf("Default target '%s' is a configured pipeline", defaultProvider))
			} else {
				result.Errors = append(result.Errors, fmt.Sprintf("Default target '%s' not found in providers or pipelines", defaultProvider))
			}
		} else {
			result.Errors = append(result.Errors, fmt.Sprintf("Default target '%s' not found in providers or pipelines", defaultProvider))
		}
	}
}

// validateProviderConfig validates provider-specific configuration
func (v *Validator) validateProviderConfig(provider string, config map[string]interface{}, result *ValidationResult) {
	switch provider {
	case "openai_realtime":
		if model, ok := config["model"].(string); ok {
			if model == "gpt-realtime" || model == "gpt-realtime-mini" {
				result.Passed = append(result.Passed, fmt.Sprintf("OpenAI model '%s' valid", model))
			} else {
				result.Warnings = append(result.Warnings, fmt.Sprintf("OpenAI model '%s' may be outdated", model))
			}
		}

	case "deepgram":
		if model, ok := config["model"].(string); ok {
			validModels := map[string]bool{
				"nova-3":           true,
				"flux-general-en":  true,
				"nova-2":           true,
				"nova-2-general":   true,
				"nova-2-phonecall": true,
				"nova":             true,
			}
			if validModels[model] {
				result.Passed = append(result.Passed, fmt.Sprintf("Deepgram model '%s' valid", model))
			} else {
				result.Warnings = append(result.Warnings, fmt.Sprintf("Deepgram model '%s' may be invalid", model))
			}
		}

	case "google_live":
		// Accept either legacy 'model' or current 'llm_model' naming.
		if model, ok := config["model"].(string); ok {
			if model == "models/gemini-2.0-flash-exp" {
				result.Passed = append(result.Passed, "Google model valid")
			} else {
				result.Warnings = append(result.Warnings, fmt.Sprintf("Google model '%s' may be outdated", model))
			}
		} else if llmModel, ok := config["llm_model"].(string); ok {
			if llmModel != "" {
				result.Passed = append(result.Passed, fmt.Sprintf("Google llm_model '%s' configured", llmModel))
			}
		}
	}
}

// validateSampleRates checks sample rate alignment
func (v *Validator) validateSampleRates(result *ValidationResult) {
	providers, ok := v.config["providers"].(map[string]interface{})
	if !ok {
		return
	}

	for name, config := range providers {
		providerConfig, ok := config.(map[string]interface{})
		if !ok {
			continue
		}

		enabled, _ := providerConfig["enabled"].(bool)
		if !enabled {
			continue
		}

		// Input/output rates commonly differ for realtime providers and are
		// resampled intentionally. Validate that configured values are positive;
		// equality is not a correctness requirement.
		inputRate, hasInput := providerConfig["provider_input_sample_rate_hz"].(int)
		if !hasInput {
			inputRate, hasInput = providerConfig["input_sample_rate_hz"].(int)
		}
		outputRate, hasOutput := providerConfig["provider_output_sample_rate_hz"].(int)
		if !hasOutput {
			outputRate, hasOutput = providerConfig["output_sample_rate_hz"].(int)
		}

		if hasInput && inputRate <= 0 {
			result.Errors = append(result.Errors, fmt.Sprintf("Provider '%s': input sample rate must be positive", name))
		}
		if hasOutput && outputRate <= 0 {
			result.Errors = append(result.Errors, fmt.Sprintf("Provider '%s': output sample rate must be positive", name))
		}
		if hasInput || hasOutput {
			result.Passed = append(result.Passed,
				fmt.Sprintf("Provider '%s': sample rates configured (input=%d output=%d)", name, inputRate, outputRate))
		}
	}
}

// validateTransport checks transport configuration
func (v *Validator) validateTransport(result *ValidationResult) {
	if transport, ok := v.config["audio_transport"].(string); ok {
		validTransports := map[string]bool{
			"audiosocket":   true,
			"externalmedia": true,
		}

		if validTransports[transport] {
			result.Passed = append(result.Passed, fmt.Sprintf("Audio transport '%s' valid", transport))
		} else {
			result.Errors = append(result.Errors, fmt.Sprintf("Invalid audio transport: %s (must be 'audiosocket' or 'externalmedia')", transport))
		}
	} else {
		result.Warnings = append(result.Warnings, "No audio_transport specified (will use default)")
	}
}

// validateBargeIn checks barge-in configuration
func (v *Validator) validateBargeIn(result *ValidationResult) {
	bargeIn, ok := v.config["barge_in"].(map[string]interface{})
	if !ok {
		result.Warnings = append(result.Warnings, "No barge_in configuration found")
		return
	}

	if enabled, ok := bargeIn["enabled"].(bool); ok {
		if enabled {
			result.Passed = append(result.Passed, "Barge-in enabled")

			// Check for protection settings
			if protection, ok := bargeIn["post_tts_end_protection_ms"].(int); ok {
				if protection >= 100 && protection <= 500 {
					result.Passed = append(result.Passed, fmt.Sprintf("Barge-in protection: %d ms", protection))
				} else {
					result.Warnings = append(result.Warnings, fmt.Sprintf("Barge-in protection %d ms outside recommended range (100-500)", protection))
				}
			}
		} else {
			result.Passed = append(result.Passed, "Barge-in disabled")
		}
	}
}

// AutoFix attempts to fix common issues
func (v *Validator) AutoFix(result *ValidationResult) (int, error) {
	fixed := 0

	// For now, auto-fix is limited - most issues require manual intervention
	// This is a placeholder for future enhancement

	return fixed, nil
}
