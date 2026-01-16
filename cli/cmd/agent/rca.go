package main

import (
	"os"

	"github.com/hkjarral/asterisk-ai-voice-agent/cli/internal/troubleshoot"
	"github.com/spf13/cobra"
)

var (
	rcaCallID string
	rcaJSON   bool
)

var rcaCmd = &cobra.Command{
	Use:   "rca",
	Short: "Post-call root cause analysis",
	Long: `Analyze the most recent call (or a specific call ID) and print an RCA report.

This is the recommended post-call troubleshooting command.`,
	RunE: func(cmd *cobra.Command, args []string) error {
		callID := rcaCallID
		if callID == "" {
			callID = "last"
		}

		runner := troubleshoot.NewRunner(
			callID,
			"",    // symptom
			false, // interactive
			false, // collectOnly
			false, // noLLM (best-effort; prints nothing extra in --json mode)
			false, // list
			rcaJSON,
			verbose,
		)
		err := runner.Run()
		if rcaJSON && err != nil {
			os.Exit(1)
		}
		return err
	},
}

func init() {
	rcaCmd.Flags().StringVar(&rcaCallID, "call", "", "analyze specific call ID (default: last)")
	rcaCmd.Flags().BoolVar(&rcaJSON, "json", false, "output as JSON (JSON only)")
	rootCmd.AddCommand(rcaCmd)
}
