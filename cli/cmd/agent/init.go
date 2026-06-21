package main

import (
	"fmt"

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
			return fmt.Errorf("--non-interactive is not implemented; refusing to report a false successful setup")
		}
		if initTemplate != "" {
			return fmt.Errorf("--template is not implemented; use the interactive target selector in `agent setup`")
		}
		fmt.Println("Note: `agent init` is a legacy alias; launching `agent setup`.")
		return setupCmd.RunE(cmd, args)
	},
}

func init() {
	initCmd.Flags().BoolVar(&initNonInteractive, "non-interactive", false, "non-interactive mode (use defaults)")
	initCmd.Flags().StringVar(&initTemplate, "template", "", "config template: local|cloud|hybrid|openai-agent|deepgram-agent")

	rootCmd.AddCommand(initCmd)
}
