package main

import (
	"fmt"

	"github.com/hkjarral/ava-ai-voice-agent-for-asterisk/cli/internal/wizard"
	"github.com/spf13/cobra"
)

var setupListTargets bool

var setupCmd = &cobra.Command{
	Use:   "setup",
	Short: "Interactive setup wizard",
	Long: fmt.Sprintf(`Interactive setup wizard for Asterisk AI Voice Agent (%s).

Guides you through configuration and then runs:
  agent check

Notes:
  - Writes .env (secrets) and config/ai-agent.local.yaml (operator overrides)
  - Prints the expected Stasis app name and dialplan snippet`,
		version),
	RunE: func(cmd *cobra.Command, args []string) error {
		if setupListTargets {
			cfg, err := wizard.LoadConfig()
			if err != nil {
				return err
			}
			fmt.Printf("Current provider: %s\n", cfg.DefaultProvider)
			fmt.Printf("Current pipeline: %s\n", cfg.ActivePipeline)
			fmt.Println("Available pipelines:")
			for _, name := range cfg.AvailablePipelines {
				fmt.Println("  " + name)
			}
			fmt.Println("Available full-agent providers:")
			for _, name := range cfg.AvailableProviders {
				fmt.Println("  " + name)
			}
			return nil
		}
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
	setupCmd.Flags().BoolVar(&setupListTargets, "list-targets", false, "list configured providers and pipelines without making changes")
	rootCmd.AddCommand(setupCmd)
}
