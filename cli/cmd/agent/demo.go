package main

import (
	"fmt"

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
		if cmd.Flags().Changed("wav") || cmd.Flags().Changed("loop") || cmd.Flags().Changed("save") {
			return fmt.Errorf("legacy demo audio flags are no longer supported; use `agent check` and a real test call followed by `agent rca`")
		}
		fmt.Println("Note: `agent demo` is a legacy alias; running `agent check`.")
		return checkCmd.RunE(cmd, args)
	},
}

func init() {
	demoCmd.Flags().StringVar(&demoWavFile, "wav", "", "test with custom audio file (WAV format)")
	demoCmd.Flags().IntVar(&demoLoop, "loop", 1, "run N iterations")
	demoCmd.Flags().BoolVar(&demoSave, "save", false, "save generated audio files")

	rootCmd.AddCommand(demoCmd)
}
