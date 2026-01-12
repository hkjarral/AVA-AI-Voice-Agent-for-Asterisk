package main

import (
	"bufio"
	"fmt"
	"os"
	"strings"

	"github.com/hkjarral/asterisk-ai-voice-agent/cli/internal/dialplan"
	"github.com/spf13/cobra"
)

var dialplanCmd = &cobra.Command{
	Use:    "dialplan",
	Short:  "Generate dialplan configuration for AI agent",
	Hidden: true, // v5.0: prefer `agent setup`
	Long: `Generate Asterisk dialplan snippets for the chosen provider.

This command prints the dialplan configuration that you need to add
to your Asterisk extensions_custom.conf file.`,
	RunE: runDialplan,
}

var (
	dialplanProvider string
	dialplanFile     string
)

func init() {
	dialplanCmd.Flags().StringVar(&dialplanProvider, "provider", "", "Provider to generate dialplan for (openai_realtime, deepgram, local_hybrid, google_live)")
	dialplanCmd.Flags().StringVar(&dialplanFile, "file", "/etc/asterisk/extensions_custom.conf", "Target dialplan file location")

	rootCmd.AddCommand(dialplanCmd)
}

func runDialplan(cmd *cobra.Command, args []string) error {
	// If no provider specified, ask user
	if dialplanProvider == "" {
		provider, err := promptProvider()
		if err != nil {
			return err
		}
		dialplanProvider = provider
	}

	// Validate provider
	validProviders := map[string]bool{
		"openai_realtime": true,
		"deepgram":        true,
		"local_hybrid":    true,
		"google_live":     true,
	}

	if !validProviders[dialplanProvider] {
		return fmt.Errorf("invalid provider: %s (must be one of: openai_realtime, deepgram, local_hybrid, google_live)", dialplanProvider)
	}

	// Generate snippet
	snippet := dialplan.GenerateSnippet(dialplanProvider)
	providerName := dialplan.GetProviderDisplayName(dialplanProvider)

	// Print header
	fmt.Println("")
	fmt.Println("╔══════════════════════════════════════════════════════════╗")
	fmt.Printf("║   Dialplan Configuration - %-30s║\n", providerName)
	fmt.Println("╚══════════════════════════════════════════════════════════╝")
	fmt.Println("")

	// Ask for file location
	fmt.Printf("Where should this dialplan be added?\n")
	fmt.Printf("Default location: %s\n\n", dialplanFile)

	reader := bufio.NewReader(os.Stdin)
	fmt.Print("File path [press Enter for default]: ")
	input, _ := reader.ReadString('\n')
	input = strings.TrimSpace(input)

	if input != "" {
		dialplanFile = input
	}

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
	fmt.Println("    Set(AI_PROVIDER=deepgram)       ; Use different provider")
	fmt.Println("    Set(AI_CONTEXT=sales)            ; Use custom greeting/prompt")
	fmt.Println("")
	fmt.Println("For more details, see:")
	fmt.Println("  docs/FreePBX-Integration-Guide.md")
	fmt.Println("")

	return nil
}

func promptProvider() (string, error) {
	fmt.Println("")
	fmt.Println("Select provider for dialplan:")
	fmt.Println("  1) OpenAI Realtime")
	fmt.Println("  2) Deepgram Voice Agent")
	fmt.Println("  3) Local Hybrid")
	fmt.Println("  4) Google Live API")
	fmt.Println("")

	reader := bufio.NewReader(os.Stdin)
	fmt.Print("Your choice [1-4]: ")
	input, _ := reader.ReadString('\n')
	input = strings.TrimSpace(input)

	providers := map[string]string{
		"1": "openai_realtime",
		"2": "deepgram",
		"3": "local_hybrid",
		"4": "google_live",
	}

	if provider, ok := providers[input]; ok {
		return provider, nil
	}

	return "", fmt.Errorf("invalid selection: %s", input)
}
