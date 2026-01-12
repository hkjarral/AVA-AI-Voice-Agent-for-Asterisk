package main

import (
	"bufio"
	"fmt"
	"io"
	"net/http"
	"os"
	"regexp"
	"strings"
	"time"

	"github.com/hkjarral/asterisk-ai-voice-agent/cli/internal/dialplan"
	"github.com/hkjarral/asterisk-ai-voice-agent/cli/internal/validator"
	"github.com/spf13/cobra"
	"gopkg.in/yaml.v3"
)

var quickstartCmd = &cobra.Command{
	Use:    "quickstart",
	Short:  "Interactive setup wizard for first-time users",
	Hidden: true, // v5.0: prefer `agent setup`
	Long: `Interactive wizard that guides you through:
  1. Provider selection
  2. API key validation
  3. ARI connection test
  4. Configuration generation
  5. Docker startup
  6. Dialplan snippet generation
  7. Optional health check

This command is designed for first-time users to get up and running quickly.`,
	RunE: runQuickstart,
}

func init() {
	rootCmd.AddCommand(quickstartCmd)
}

func runQuickstart(cmd *cobra.Command, args []string) error {
	fmt.Println("")
	fmt.Println("╔══════════════════════════════════════════════════════════╗")
	fmt.Println("║   Asterisk AI Voice Agent - Quickstart Wizard           ║")
	fmt.Println("╚══════════════════════════════════════════════════════════╝")
	fmt.Println("")
	fmt.Println("This wizard will help you:")
	fmt.Println("  • Select and configure your AI provider")
	fmt.Println("  • Validate API keys")
	fmt.Println("  • Test Asterisk ARI connection")
	fmt.Println("  • Generate dialplan configuration")
	fmt.Println("  • Start Docker containers")
	fmt.Println("")

	reader := bufio.NewReader(os.Stdin)

	// Step 1: Provider Selection
	fmt.Println("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
	fmt.Println("Step 1: Provider Selection")
	fmt.Println("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
	fmt.Println("")
	fmt.Println("Available providers:")
	fmt.Println("  1) OpenAI Realtime    - Full-duplex, natural conversations (requires API key)")
	fmt.Println("  2) Deepgram           - Fast, accurate transcription (requires API key)")
	fmt.Println("  3) Google Live API    - Multimodal capabilities (requires API key)")
	fmt.Println("  4) Local Hybrid       - Runs entirely on-premise (no API key needed)")
	fmt.Println("")

	fmt.Print("Select provider [1-4]: ")
	choice, _ := reader.ReadString('\n')
	choice = strings.TrimSpace(choice)

	provider := ""
	needsAPIKey := true

	switch choice {
	case "1":
		provider = "openai_realtime"
		fmt.Println("✓ Selected: OpenAI Realtime")
	case "2":
		provider = "deepgram"
		fmt.Println("✓ Selected: Deepgram")
	case "3":
		provider = "google_live"
		fmt.Println("✓ Selected: Google Live API")
	case "4":
		provider = "local_hybrid"
		needsAPIKey = false
		fmt.Println("✓ Selected: Local Hybrid (no API key required)")
	default:
		return fmt.Errorf("invalid selection: %s", choice)
	}

	fmt.Println("")

	// Step 2: API Key Validation (if needed)
	var apiKey string
	if needsAPIKey {
		fmt.Println("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
		fmt.Println("Step 2: API Key Validation")
		fmt.Println("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
		fmt.Println("")

		switch provider {
		case "openai_realtime":
			fmt.Println("Get your API key from: https://platform.openai.com/api-keys")
		case "deepgram":
			fmt.Println("Get your API key from: https://console.deepgram.com/")
		case "google_live":
			fmt.Println("Get your API key from: https://console.cloud.google.com/")
		}

		fmt.Println("")
		fmt.Print("Enter API key: ")
		apiKey, _ = reader.ReadString('\n')
		apiKey = strings.TrimSpace(apiKey)

		if apiKey == "" {
			return fmt.Errorf("API key cannot be empty")
		}

		// Validate API key
		fmt.Print("Validating API key... ")
		if err := validator.ValidateAPIKey(provider, apiKey); err != nil {
			fmt.Println("❌")
			fmt.Println("")
			fmt.Printf("API key validation failed: %v\n", err)
			fmt.Println("")
			fmt.Println("Please check:")
			fmt.Println("  • API key is correct")
			fmt.Println("  • Internet connection is working")
			fmt.Println("  • Provider service is accessible")
			fmt.Println("")
			fmt.Println("Re-run 'agent setup' to try again")
			return fmt.Errorf("API key validation failed")
		}

		fmt.Println("✓")
		fmt.Println("")
	}

	// Step 3: ARI Connection
	fmt.Println("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
	fmt.Println("Step 3: Asterisk ARI Connection")
	fmt.Println("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
	fmt.Println("")

	fmt.Print("Asterisk host [localhost]: ")
	asteriskHost, _ := reader.ReadString('\n')
	asteriskHost = strings.TrimSpace(asteriskHost)
	if asteriskHost == "" {
		asteriskHost = "localhost"
	}

	fmt.Print("ARI username [asterisk]: ")
	ariUser, _ := reader.ReadString('\n')
	ariUser = strings.TrimSpace(ariUser)
	if ariUser == "" {
		ariUser = "asterisk"
	}

	fmt.Print("ARI password: ")
	ariPassword, _ := reader.ReadString('\n')
	ariPassword = strings.TrimSpace(ariPassword)

	fmt.Println("")
	fmt.Printf("Testing ARI connection to %s...\n", asteriskHost)

	if err := validateARIConnection(asteriskHost, ariUser, ariPassword); err != nil {
		fmt.Printf("❌ Connection failed: %v\n", err)
		fmt.Println("")
		fmt.Println("Troubleshooting:")
		fmt.Println("  • Check if Asterisk is running")
		fmt.Println("  • Verify ARI is enabled in /etc/asterisk/ari.conf")
		fmt.Println("  • Check firewall settings")
		fmt.Println("")
		fmt.Print("Continue anyway? [y/N]: ")
		cont, _ := reader.ReadString('\n')
		if strings.TrimSpace(strings.ToLower(cont)) != "y" {
			return fmt.Errorf("ARI connection check failed")
		}
	} else {
		fmt.Println("✓ ARI connection successful")
	}
	fmt.Println("")

	// Step 4: Generate Configuration
	fmt.Println("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
	fmt.Println("Step 4: Configuration")
	fmt.Println("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
	fmt.Println("")

	fmt.Println("Configuration will be saved to:")
	fmt.Println("  • .env (credentials)")
	fmt.Println("  • config/ai-agent.yaml (AI settings)")
	fmt.Println("")

	fmt.Print("Continue? [Y/n]: ")
	confirm, _ := reader.ReadString('\n')
	confirm = strings.TrimSpace(confirm)

	if strings.ToLower(confirm) == "n" {
		fmt.Println("Quickstart cancelled")
		return nil
	}

	// Save configuration
	fmt.Print("Writing configuration... ")
	if err := writeConfiguration(provider, apiKey, asteriskHost, ariUser, ariPassword); err != nil {
		fmt.Println("❌")
		return fmt.Errorf("failed to write configuration: %w", err)
	}
	fmt.Println("✓")
	fmt.Println("")

	// Step 5: Dialplan Generation
	fmt.Println("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
	fmt.Println("Step 5: Dialplan Configuration")
	fmt.Println("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
	fmt.Println("")

	snippet := dialplan.GenerateSnippet(provider)
	providerName := dialplan.GetProviderDisplayName(provider)

	fmt.Printf("Add this dialplan snippet to /etc/asterisk/extensions_custom.conf:\n")
	fmt.Println("")
	fmt.Println("────────────────────────────────────────────────────────────")
	fmt.Println(snippet)
	fmt.Println("────────────────────────────────────────────────────────────")
	fmt.Println("")
	fmt.Println("FreePBX users:")
	fmt.Println("  1. Admin → Config Edit → extensions_custom.conf")
	fmt.Println("  2. Paste snippet above")
	fmt.Println("  3. Admin → Custom Destination → Add")
	contextName := getContextName(provider)
	fmt.Printf("     Target: %s,s,1\n", contextName)
	fmt.Printf("     Description: AI Voice Agent - %s\n", providerName)
	fmt.Println("")

	// Step 6: Summary
	fmt.Println("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
	fmt.Println("Setup Complete!")
	fmt.Println("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
	fmt.Println("")
	fmt.Println("Next steps:")
	fmt.Println("  1. Add dialplan snippet to Asterisk (see above)")
	fmt.Println("  2. Start Docker containers:")
	fmt.Println("     docker compose up -d")
	fmt.Println("  3. Check health:")
	fmt.Println("     agent check")
	fmt.Println("  4. Make a test call!")
	fmt.Println("")
	fmt.Println("For detailed setup instructions, see:")
	fmt.Println("  docs/CLI_TOOLS_GUIDE.md")
	fmt.Println("  docs/FreePBX-Integration-Guide.md")
	fmt.Println("")

	return nil
}

func validateARIConnection(host, user, password string) error {
	if host == "" {
		host = "localhost"
	}

	// Use curl-like behavior to check ARI endpoint
	url := fmt.Sprintf("http://%s:8088/ari/asterisk/info", host)
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}

	req.SetBasicAuth(user, password)

	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("connection error: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == 401 {
		return fmt.Errorf("authentication failed (check username/password)")
	}

	if resp.StatusCode != 200 {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("ARI returned status %d: %s", resp.StatusCode, string(body))
	}

	return nil
}

func writeConfiguration(provider, apiKey, ariHost, ariUser, ariPassword string) error {
	// 1. Read .env.example
	content, err := os.ReadFile(".env.example")
	if err != nil {
		// Fallback if .env.example missing
		return writeMinimalEnv(provider, apiKey, ariHost, ariUser, ariPassword)
	}

	envStr := string(content)

	// Helper to replace value for a key
	replaceKey := func(key, value string) {
		// Pattern matches KEY=... until end of line
		re := regexp.MustCompile(fmt.Sprintf(`(?m)^#?\s*%s=.*$`, key))
		if re.MatchString(envStr) {
			envStr = re.ReplaceAllString(envStr, fmt.Sprintf("%s=%s", key, value))
		} else {
			// If not found, append it
			envStr += fmt.Sprintf("\n%s=%s", key, value)
		}
	}

	// Replace Asterisk settings
	replaceKey("ASTERISK_HOST", ariHost)
	replaceKey("ASTERISK_ARI_USERNAME", ariUser)
	replaceKey("ASTERISK_ARI_PASSWORD", ariPassword)

	// Replace API keys based on provider
	switch provider {
	case "openai_realtime":
		replaceKey("OPENAI_API_KEY", apiKey)
	case "deepgram":
		replaceKey("DEEPGRAM_API_KEY", apiKey)
	case "google_live":
		replaceKey("GOOGLE_API_KEY", apiKey)
	}

	// Write .env (restrict access; contains secrets)
	if err := os.WriteFile(".env", []byte(envStr), 0600); err != nil {
		return fmt.Errorf("failed to write .env: %w", err)
	}

	// 2. Update ai-agent.yaml
	return updateYAMLConfig(provider)
}

func writeMinimalEnv(provider, apiKey, ariHost, ariUser, ariPassword string) error {
	envContent := fmt.Sprintf(`# Asterisk AI Voice Agent Configuration
# Generated by agent quickstart

ASTERISK_HOST=%s
ASTERISK_ARI_USERNAME=%s
ASTERISK_ARI_PASSWORD=%s

`, ariHost, ariUser, ariPassword)

	switch provider {
	case "openai_realtime":
		envContent += fmt.Sprintf("OPENAI_API_KEY=%s\n", apiKey)
	case "deepgram":
		envContent += fmt.Sprintf("DEEPGRAM_API_KEY=%s\n", apiKey)
	case "google_live":
		envContent += fmt.Sprintf("GOOGLE_API_KEY=%s\n", apiKey)
	}

	return os.WriteFile(".env", []byte(envContent), 0600)
}

func updateYAMLConfig(activeProvider string) error {
	data, err := os.ReadFile("config/ai-agent.yaml")
	if err != nil {
		return fmt.Errorf("failed to read config/ai-agent.yaml: %w", err)
	}

	var root yaml.Node
	if err := yaml.Unmarshal(data, &root); err != nil {
		return fmt.Errorf("failed to parse config: %w", err)
	}

	// Helper to traverse and update value
	updateKey := func(path []string, value string) bool {
		node := &root
		if len(node.Content) > 0 {
			node = node.Content[0] // Access document root
		}

		for _, key := range path[:len(path)-1] {
			found := false
			for i := 0; i < len(node.Content); i += 2 {
				if node.Content[i].Value == key {
					node = node.Content[i+1]
					found = true
					break
				}
			}
			if !found {
				return false
			}
		}

		targetKey := path[len(path)-1]
		for i := 0; i < len(node.Content); i += 2 {
			if node.Content[i].Value == targetKey {
				node.Content[i+1].Value = value
				if value == "true" || value == "false" {
					node.Content[i+1].Tag = "!!bool"
				} else {
					node.Content[i+1].Tag = "!!str"
				}
				return true
			}
		}
		return false
	}

	// Update default_provider
	updateKey([]string{"default_provider"}, activeProvider)

	// Update enabled flags for all providers
	providers := []string{"openai_realtime", "deepgram", "google_live", "local"}
	for _, p := range providers {
		enabled := "false"
		if p == activeProvider || (activeProvider == "local_hybrid" && p == "local") {
			enabled = "true"
		}
		updateKey([]string{"providers", p, "enabled"}, enabled)
	}

	// Special handling for local_hybrid pipeline
	if activeProvider == "local_hybrid" {
		updateKey([]string{"active_pipeline"}, "local_hybrid")
		updateKey([]string{"default_provider"}, "local_hybrid")
	}

	// Write back
	f, err := os.Create("config/ai-agent.yaml")
	if err != nil {
		return fmt.Errorf("failed to open config for writing: %w", err)
	}
	defer f.Close()

	encoder := yaml.NewEncoder(f)
	encoder.SetIndent(2)
	return encoder.Encode(&root)
}
