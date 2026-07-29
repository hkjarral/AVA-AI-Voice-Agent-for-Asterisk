package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	"github.com/hkjarral/ava-ai-voice-agent-for-asterisk/cli/internal/check"
	"github.com/spf13/cobra"
)

var (
	checkJSON   bool
	checkFix    bool
	checkLocal  bool
	checkRemote string
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
		// --local or --remote: check local_ai_server components
		if checkLocal || checkRemote != "" {
			return runCheckLocalServer(cmd)
		}

		if checkFix {
			if checkJSON {
				return errors.New("--fix cannot be combined with --json")
			}
			exitCode, err := runCheckWithFix()
			if exitCode != 0 {
				os.Exit(exitCode)
			}
			if err != nil {
				return err
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

// runCheckLocalServer shells out to scripts/check_local_server.py
func runCheckLocalServer(cmd *cobra.Command) error {
	projectRoot, err := findProjectRoot()
	if err != nil {
		return fmt.Errorf("could not find project root: %w", err)
	}

	scriptPath := filepath.Join(projectRoot, "scripts", "check_local_server.py")
	if _, err := os.Stat(scriptPath); os.IsNotExist(err) {
		return fmt.Errorf("script not found: %s\nMake sure you're running from the project directory", scriptPath)
	}

	pyArgs := []string{scriptPath, "--project-root", projectRoot}
	if checkLocal {
		pyArgs = append(pyArgs, "--local")
	} else if checkRemote != "" {
		pyArgs = append(pyArgs, "--remote", checkRemote)
	}
	if checkJSON {
		pyArgs = append(pyArgs, "--json")
	}
	if noColor {
		pyArgs = append(pyArgs, "--no-color")
	}

	pyCmd := exec.Command("python3", pyArgs...)
	pyCmd.Stdout = os.Stdout
	pyCmd.Stderr = os.Stderr
	pyCmd.Dir = projectRoot
	pyCmd.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8")

	if err := pyCmd.Run(); err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			os.Exit(exitErr.ExitCode())
		}
		return fmt.Errorf("local server check failed: %w", err)
	}
	return nil
}

func init() {
	checkCmd.Flags().BoolVar(&checkJSON, "json", false, "output as JSON (JSON only)")
	checkCmd.Flags().BoolVar(&checkFix, "fix", false, "attempt automatic recovery from recent backups and re-run diagnostics")
	checkCmd.Flags().BoolVar(&checkLocal, "local", false, "check local_ai_server on this host (ws://127.0.0.1:8765)")
	checkCmd.Flags().StringVar(&checkRemote, "remote", "", "check remote local_ai_server at IP address")
	rootCmd.AddCommand(checkCmd)
}
