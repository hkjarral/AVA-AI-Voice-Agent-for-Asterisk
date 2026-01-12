package main

import (
	"fmt"
	"os"

	"github.com/hkjarral/asterisk-ai-voice-agent/cli/internal/config"
	"github.com/spf13/cobra"
)

var configCmd = &cobra.Command{
	Use:    "config",
	Short:  "Configuration validation and management",
	Hidden: true, // v5.0: prefer `agent check`
	Long: `Validate and manage AI agent configuration files.

Validates config/ai-agent.yaml for:
- YAML syntax errors
- Required fields present
- Provider configurations
- Sample rate alignment
- Transport compatibility`,
}

var validateCmd = &cobra.Command{
	Use:   "validate",
	Short: "Validate configuration file",
	Long: `Validate config/ai-agent.yaml for syntax and configuration errors.

Exit codes:
  0 - Configuration is valid
  1 - Warnings found (non-critical)
  2 - Errors found (critical)`,
	RunE: runValidate,
}

var (
	configFile   string
	configFix    bool
	configStrict bool
)

func init() {
	validateCmd.Flags().StringVar(&configFile, "file", "config/ai-agent.yaml", "Path to configuration file")
	validateCmd.Flags().BoolVar(&configFix, "fix", false, "Attempt to auto-fix issues")
	validateCmd.Flags().BoolVar(&configStrict, "strict", false, "Treat warnings as errors")

	configCmd.AddCommand(validateCmd)
	rootCmd.AddCommand(configCmd)
}

func runValidate(cmd *cobra.Command, args []string) error {
	fmt.Println("")
	fmt.Printf("Validating %s...\n", configFile)
	fmt.Println("")

	// Load and validate
	validator := config.NewValidator(configFile)
	result, err := validator.Validate()

	if err != nil {
		fmt.Printf("❌ Failed to load configuration: %v\n", err)
		return fmt.Errorf("validation failed")
	}

	// Print results
	printValidationResult(result)

	// Handle auto-fix
	if configFix && (len(result.Errors) > 0 || len(result.Warnings) > 0) {
		fmt.Println("")
		fmt.Println("Attempting auto-fix...")

		fixed, err := validator.AutoFix(result)
		if err != nil {
			fmt.Printf("❌ Auto-fix failed: %v\n", err)
			return err
		}

		if fixed > 0 {
			fmt.Printf("✓ Fixed %d issue(s)\n", fixed)
			fmt.Println("")
			fmt.Println("Re-validating...")

			// Re-validate
			result, err = validator.Validate()
			if err != nil {
				return err
			}

			printValidationResult(result)
		} else {
			fmt.Println("⚠️  No issues could be auto-fixed")
			fmt.Println("   Manual intervention required")
		}
	}

	// Determine exit code
	exitCode := 0

	if len(result.Errors) > 0 {
		exitCode = 2
	} else if len(result.Warnings) > 0 {
		if configStrict {
			exitCode = 2
		} else {
			exitCode = 1
		}
	}

	if exitCode != 0 {
		os.Exit(exitCode)
	}

	return nil
}

func printValidationResult(result *config.ValidationResult) {
	// Print passes
	for _, check := range result.Passed {
		fmt.Printf("✓ %s\n", check)
	}

	// Print warnings
	for _, warning := range result.Warnings {
		fmt.Printf("⚠️  %s\n", warning)
	}

	// Print errors
	for _, err := range result.Errors {
		fmt.Printf("❌ %s\n", err)
	}

	// Summary
	fmt.Println("")
	fmt.Printf("Summary: %d passed, %d warning(s), %d error(s)\n",
		len(result.Passed),
		len(result.Warnings),
		len(result.Errors))

	if len(result.Errors) > 0 {
		fmt.Println("")
		fmt.Println("❌ Configuration has errors")
		if !configFix {
			fmt.Println("   Run with --fix to attempt auto-fix")
		}
	} else if len(result.Warnings) > 0 {
		fmt.Println("")
		fmt.Println("⚠️  Configuration has warnings")
		if configStrict {
			fmt.Println("   (treated as errors in strict mode)")
		}
	} else {
		fmt.Println("")
		fmt.Println("✅ Configuration is valid")
	}
}
