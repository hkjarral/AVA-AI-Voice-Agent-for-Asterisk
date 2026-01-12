package main

import (
	"os"

	"github.com/hkjarral/asterisk-ai-voice-agent/cli/internal/troubleshoot"
	"github.com/spf13/cobra"
)

var (
	troubleshootCallID      string
	troubleshootSymptom     string
	troubleshootInteractive bool
	troubleshootCollectOnly bool
	troubleshootNoLLM       bool
	troubleshootList        bool
	troubleshootJSON        bool
)

var troubleshootCmd = &cobra.Command{
	Use:    "troubleshoot",
	Short:  "Post-call analysis and RCA",
	Hidden: true, // v5.0: prefer `agent rca`
	Long: `Analyze call issues and provide root cause analysis.

Usage Examples:
  agent troubleshoot --last              # Analyze most recent call
  agent troubleshoot --list              # List recent calls
  agent troubleshoot --call 1761424308.2043
  agent troubleshoot --last --symptom garbled
  agent troubleshoot --interactive

Symptoms:
  no-audio        Complete silence
  garbled         Distorted/fast/slow audio
  echo            Agent hears itself
  interruption    Self-interruption loop
  one-way         Only one direction works

Requirements:
  - Docker container 'ai_engine' must be running
  - Reads logs from Docker (last 24 hours)
  - No file logging required (uses 'docker logs ai_engine')
  
Features:
  - Automatic log collection from Docker
  - Pattern detection and analysis
  - LLM-powered diagnosis
  - Actionable recommendations`,
	RunE: func(cmd *cobra.Command, args []string) error {
		verbose, _ := cmd.Flags().GetBool("verbose")

		// If --last flag is used, set callID to "last"
		if cmd.Flags().Changed("last") || troubleshootCallID == "" {
			troubleshootCallID = "last"
		}

		runner := troubleshoot.NewRunner(
			troubleshootCallID,
			troubleshootSymptom,
			troubleshootInteractive,
			troubleshootCollectOnly,
			troubleshootNoLLM,
			troubleshootList,
			troubleshootJSON,
			verbose,
		)
		err := runner.Run()
		if troubleshootJSON && err != nil {
			os.Exit(1)
		}
		return err
	},
}

func init() {
	troubleshootCmd.Flags().StringVarP(&troubleshootCallID, "call", "c", "", "analyze specific call ID")
	troubleshootCmd.Flags().BoolVarP(&troubleshootList, "list", "l", false, "list recent calls")
	troubleshootCmd.Flags().Bool("last", false, "analyze most recent call")
	troubleshootCmd.Flags().StringVarP(&troubleshootSymptom, "symptom", "s", "", "symptom: no-audio|garbled|echo|interruption|one-way")
	troubleshootCmd.Flags().BoolVarP(&troubleshootInteractive, "interactive", "i", false, "interactive mode")
	troubleshootCmd.Flags().BoolVar(&troubleshootCollectOnly, "collect-only", false, "only collect logs, no analysis")
	troubleshootCmd.Flags().BoolVar(&troubleshootNoLLM, "no-llm", false, "skip LLM analysis")
	troubleshootCmd.Flags().BoolVar(&troubleshootJSON, "json", false, "output as JSON (JSON only)")

	rootCmd.AddCommand(troubleshootCmd)
}
