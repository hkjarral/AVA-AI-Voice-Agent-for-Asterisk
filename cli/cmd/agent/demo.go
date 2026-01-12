package main

import (
	"github.com/hkjarral/asterisk-ai-voice-agent/cli/internal/demo"
	"github.com/spf13/cobra"
)

var (
	demoWavFile string
	demoLoop    int
	demoSave    bool
)

var demoCmd = &cobra.Command{
	Use:    "demo",
	Short:  "Audio pipeline validation",
	Hidden: true, // v5.0: prefer `agent check` and `agent rca`
	Long: `Test the complete audio pipeline without making real calls.

Tests:
  - AudioSocket server connectivity
  - Container health and status
  - Configuration validation
  - Provider API connectivity
  - Audio processing pipeline

This helps validate configuration before production use.`,
	RunE: func(cmd *cobra.Command, args []string) error {
		verbose, _ := cmd.Flags().GetBool("verbose")

		runner := demo.NewRunner(verbose)
		return runner.Run()
	},
}

func init() {
	demoCmd.Flags().StringVar(&demoWavFile, "wav", "", "test with custom audio file (WAV format)")
	demoCmd.Flags().IntVar(&demoLoop, "loop", 1, "run N iterations")
	demoCmd.Flags().BoolVar(&demoSave, "save", false, "save generated audio files")

	rootCmd.AddCommand(demoCmd)
}
