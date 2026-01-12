package main

import (
	"os"

	"github.com/hkjarral/asterisk-ai-voice-agent/cli/internal/check"
	"github.com/spf13/cobra"
)

var (
	checkJSON bool
)

var checkCmd = &cobra.Command{
	Use:   "check",
	Short: "Standard diagnostics report",
	Long: `Run the standard diagnostics report for Asterisk AI Voice Agent.

This is the recommended first step when troubleshooting. It prints a shareable report
to stdout. Use --json for JSON-only output.

Probes:
  - Docker + Compose
  - ai_engine container status, network mode, mounts
  - In-container checks via: docker exec ai_engine python -
  - ARI reachability and app registration (container-side only)
  - Transport compatibility + advertise host alignment
  - Best-effort internet/DNS reachability (no external containers)

Exit codes:
  0 - PASS (no warnings)
  1 - WARN (non-critical issues)
  2 - FAIL (critical issues)`,
	RunE: func(cmd *cobra.Command, args []string) error {
		runner := check.NewRunner(verbose, version, buildTime)
		report, err := runner.Run()

		if checkJSON {
			_ = report.OutputJSON(os.Stdout)
		} else {
			report.OutputText(os.Stdout)
		}

		if report.FailCount > 0 {
			os.Exit(2)
		}
		if report.WarnCount > 0 {
			os.Exit(1)
		}
		return err
	},
}

func init() {
	checkCmd.Flags().BoolVar(&checkJSON, "json", false, "output as JSON (JSON only)")
	rootCmd.AddCommand(checkCmd)
}
