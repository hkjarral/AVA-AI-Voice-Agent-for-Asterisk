package main

import (
	"fmt"
	"os"

	"github.com/fatih/color"
	"github.com/spf13/cobra"
)

var (
	version   = "5.2.4"   // Overridden at build time via -ldflags
	buildTime = "unknown" // Overridden at build time via -ldflags
	verbose   bool
	noColor   bool
)

func main() {
	if err := rootCmd.Execute(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

var rootCmd = &cobra.Command{
	Use:   "agent",
	Short: "Asterisk AI Voice Agent CLI",
	Long: fmt.Sprintf(`Asterisk AI Voice Agent CLI (%s) - Setup, diagnostics, and RCA

Available commands:
  setup       Interactive setup wizard
  check       Standard diagnostics report
  rca         Post-call root cause analysis
  update      Pull latest code and apply updates
  version     Show version information`,
		version),
	SilenceUsage:  true,
	SilenceErrors: true,
	PersistentPreRun: func(cmd *cobra.Command, args []string) {
		// Auto-disable color when stdout isn't a TTY; allow explicit opt-out as well.
		isTTY := false
		if fi, err := os.Stdout.Stat(); err == nil {
			isTTY = (fi.Mode() & os.ModeCharDevice) != 0
		}
		if noColor || !isTTY {
			color.NoColor = true
		}
	},
}

func init() {
	rootCmd.PersistentFlags().BoolVarP(&verbose, "verbose", "v", false, "verbose output")
	rootCmd.PersistentFlags().BoolVar(&noColor, "no-color", false, "disable color output")
}
