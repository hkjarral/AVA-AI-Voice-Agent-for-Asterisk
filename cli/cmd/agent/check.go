package main

import (
	"errors"
	"os"
	"time"

	"github.com/hkjarral/asterisk-ai-voice-agent/cli/internal/check"
	"github.com/spf13/cobra"
)

var (
	checkJSON bool
	checkFix  bool
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
		if checkFix {
			if checkJSON {
				return errors.New("--fix cannot be combined with --json")
			}
			exitCode, err := runCheckWithFix()
			if err != nil {
				return err
			}
			if exitCode != 0 {
				os.Exit(exitCode)
			}
			return nil
		}

		runner := check.NewRunner(verbose, version, buildTime)
		report, err := runner.Run()

		if report == nil {
			report = &check.Report{
				Version:   version,
				BuildTime: buildTime,
				Timestamp: time.Now(),
				Items: []check.Item{
					{
						Name:    "agent check",
						Status:  check.StatusFail,
						Message: "failed to generate diagnostics report",
						Details: func() string {
							if err != nil {
								return err.Error()
							}
							return "unknown error"
						}(),
					},
				},
			}
		}

		if checkJSON {
			_ = report.OutputJSON(os.Stdout)
		} else {
			report.OutputText(os.Stdout)
		}

		exitCode := 0
		if err != nil || report.FailCount > 0 {
			exitCode = 2
		} else if report.WarnCount > 0 {
			exitCode = 1
		}
		if exitCode != 0 {
			os.Exit(exitCode)
		}
		return nil
	},
}

func init() {
	checkCmd.Flags().BoolVar(&checkJSON, "json", false, "output as JSON (JSON only)")
	checkCmd.Flags().BoolVar(&checkFix, "fix", false, "attempt automatic recovery from recent backups and re-run diagnostics")
	rootCmd.AddCommand(checkCmd)
}
