package main

import (
	"fmt"

	"github.com/spf13/cobra"
)

// quickstart is retained as a compatibility alias. Keeping a second setup
// implementation here caused its provider list and generated configuration to
// drift away from the supported wizard.
var quickstartCmd = &cobra.Command{
	Use:    "quickstart",
	Short:  "Alias of `agent setup`",
	Hidden: true,
	RunE: func(cmd *cobra.Command, args []string) error {
		fmt.Println("Note: `agent quickstart` is a legacy alias; launching `agent setup`.")
		return setupCmd.RunE(cmd, args)
	},
}

func init() {
	rootCmd.AddCommand(quickstartCmd)
}
