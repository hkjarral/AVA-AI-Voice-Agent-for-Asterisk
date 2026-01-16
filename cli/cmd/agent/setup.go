package main

import (
	"fmt"

	"github.com/hkjarral/asterisk-ai-voice-agent/cli/internal/wizard"
	"github.com/spf13/cobra"
)

var setupCmd = &cobra.Command{
	Use:   "setup",
	Short: "Interactive setup wizard",
	Long: fmt.Sprintf(`Interactive setup wizard for Asterisk AI Voice Agent (%s).

Guides you through configuration and then runs:
  agent check

Notes:
  - Writes .env (secrets) and config/ai-agent.yaml (behavior)
  - Prints the expected Stasis app name and dialplan snippet`,
		version),
	RunE: func(cmd *cobra.Command, args []string) error {
		w, err := wizard.NewWizard()
		if err != nil {
			return fmt.Errorf("failed to initialize wizard: %w", err)
		}
		if err := w.Run(); err != nil {
			return err
		}

		// Run agent check at the end as the standard post-setup validation.
		runner := checkCmd.RunE
		if runner != nil {
			return runner(cmd, args)
		}
		return nil
	},
}

func init() {
	rootCmd.AddCommand(setupCmd)
}
