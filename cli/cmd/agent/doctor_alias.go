package main

import (
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
		checkJSON = doctorJSON
		return checkCmd.RunE(cmd, args)
	},
}

func init() {
	doctorCmd.Flags().BoolVar(&doctorJSON, "json", false, "output as JSON (JSON only)")
	rootCmd.AddCommand(doctorCmd)
}
