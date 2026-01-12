package main

import (
	"fmt"

	"github.com/hkjarral/asterisk-ai-voice-agent/cli/internal/wizard"
	"github.com/spf13/cobra"
)

var (
	initNonInteractive bool
	initTemplate       string
)

var initCmd = &cobra.Command{
	Use:    "init",
	Short:  "Interactive setup wizard",
	Hidden: true, // v5.0: prefer `agent setup`
	Long: `Interactive setup wizard for Asterisk AI Voice Agent.

Guides you through configuration:
  - Asterisk ARI credentials
  - Audio transport (AudioSocket/ExternalMedia)
  - AI provider selection (OpenAI, Deepgram, Anthropic, etc.)
  - Pipeline configuration
  - Configuration validation

This can be run multiple times to reconfigure the system.`,
	RunE: func(cmd *cobra.Command, args []string) error {
		if initNonInteractive {
			fmt.Println("⚠️  Non-interactive mode not yet implemented")
			fmt.Println("For now, run without --non-interactive flag")
			return nil
		}

		// Create and run wizard
		w, err := wizard.NewWizard()
		if err != nil {
			return fmt.Errorf("failed to initialize wizard: %w", err)
		}

		if err := w.Run(); err != nil {
			return err
		}
		return checkCmd.RunE(cmd, args)
	},
}

func init() {
	initCmd.Flags().BoolVar(&initNonInteractive, "non-interactive", false, "non-interactive mode (use defaults)")
	initCmd.Flags().StringVar(&initTemplate, "template", "", "config template: local|cloud|hybrid|openai-agent|deepgram-agent")

	rootCmd.AddCommand(initCmd)
}
