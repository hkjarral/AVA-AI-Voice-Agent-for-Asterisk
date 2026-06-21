package main

import (
	"fmt"

	"github.com/hkjarral/ava-ai-voice-agent-for-asterisk/cli/internal/dialplan"
	"github.com/spf13/cobra"
)

var dialplanCmd = &cobra.Command{
	Use:   "dialplan",
	Short: "Generate dialplan configuration for AI agent",
	Long: `Generate Asterisk dialplan snippets for the chosen provider.

This command prints the dialplan configuration that you need to add
to your Asterisk extensions_custom.conf file.`,
	RunE: runDialplan,
}

var (
	dialplanProvider string
	dialplanAgent    string
	dialplanFile     string
)

func init() {
	dialplanCmd.Flags().StringVar(&dialplanProvider, "provider", "", "Provider to generate dialplan for (openai_realtime, deepgram, local_hybrid, google_live)")
	dialplanCmd.Flags().StringVar(&dialplanAgent, "agent", "default", "Agent slug to select with AI_AGENT")
	dialplanCmd.Flags().StringVar(&dialplanFile, "file", "/etc/asterisk/extensions_custom.conf", "Target dialplan file location")

	rootCmd.AddCommand(dialplanCmd)
}

func runDialplan(cmd *cobra.Command, args []string) error {
	// Generate snippet
	snippet := dialplan.GenerateAgentSnippet(dialplanAgent, dialplanProvider)
	providerName := dialplan.GetProviderDisplayName(dialplanProvider)
	if dialplanProvider == "" {
		providerName = "configured agent provider"
	}

	// Print header
	fmt.Println("")
	fmt.Println("╔══════════════════════════════════════════════════════════╗")
	fmt.Printf("║   Dialplan Configuration - %-30s║\n", providerName)
	fmt.Println("╚══════════════════════════════════════════════════════════╝")
	fmt.Println("")

	// Print instructions
	fmt.Println("")
	fmt.Println("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
	fmt.Println("Add this snippet to:", dialplanFile)
	fmt.Println("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
	fmt.Println("")
	fmt.Println(snippet)
	fmt.Println("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
	fmt.Println("")

	// Print FreePBX instructions
	fmt.Println("FreePBX Setup:")
	fmt.Println("  1. Navigate to: Admin → Config Edit")
	fmt.Println("  2. Click: Asterisk Custom Configuration Files → extensions_custom.conf")
	fmt.Println("  3. Paste the snippet above")
	fmt.Println("  4. Save and Apply Config")
	fmt.Println("")
	fmt.Println("  5. Create Custom Destination:")
	fmt.Println("     Admin → Custom Destination → Add")
	ctx := getContextName(dialplanProvider)
	fmt.Printf("     Target: %s,s,1\n", ctx)
	fmt.Printf("     Description: AI Voice Agent - %s\n", providerName)
	fmt.Println("")
	fmt.Println("  6. Use in IVR/Inbound Route:")
	fmt.Println("     Select your new Custom Destination as call target")
	fmt.Println("")

	// Print context override notes
	fmt.Println("Per-Call Overrides:")
	fmt.Println("  You can override the provider or context per-call using channel variables:")
	fmt.Println("    Set(AI_AGENT=sales)             ; Select an operator-managed agent")
	fmt.Println("    Set(AI_PROVIDER=deepgram)       ; Optional provider/pipeline override")
	fmt.Println("")
	fmt.Println("For more details, see:")
	fmt.Println("  docs/FreePBX-Integration-Guide.md")
	fmt.Println("")

	return nil
}
