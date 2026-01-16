package main

import (
	"os"

	"github.com/hkjarral/asterisk-ai-voice-agent/cli/internal/check"
	"github.com/spf13/cobra"
)

var (
	doctorJSON bool
)

var doctorCmd = &cobra.Command{
	Use:    "doctor",
	Short:  "Alias of `agent check`",
	Hidden: true,
	Long:   "Alias of `agent check` retained for backwards compatibility.",
	RunE: func(cmd *cobra.Command, args []string) error {
		runner := check.NewRunner(verbose, version, buildTime)
		report, err := runner.Run()

		if doctorJSON {
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
	doctorCmd.Flags().BoolVar(&doctorJSON, "json", false, "output as JSON (JSON only)")
	rootCmd.AddCommand(doctorCmd)
}
