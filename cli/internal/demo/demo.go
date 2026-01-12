package demo

import (
	"context"
	"fmt"
	"net"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/fatih/color"
)

var (
	successColor = color.New(color.FgGreen)
	errorColor   = color.New(color.FgRed)
	warningColor = color.New(color.FgYellow)
	infoColor    = color.New(color.FgBlue)
)

// Runner orchestrates demo tests
type Runner struct {
	verbose bool
	ctx     context.Context
}

// NewRunner creates a new demo runner
func NewRunner(verbose bool) *Runner {
	return &Runner{
		verbose: verbose,
		ctx:     context.Background(),
	}
}

// Run executes all demo tests
func (r *Runner) Run() error {
	fmt.Println()
	fmt.Println("🎤 Audio Pipeline Demo")
	fmt.Println("═══════════════════════════════════════════")
	fmt.Println()
	infoColor.Println("Testing audio pipeline components...")
	fmt.Println()
	
	tests := []struct {
		name string
		fn   func() error
	}{
		{"Docker Daemon", r.testDocker},
		{"Container Status", r.testContainerHealth},
		{"AudioSocket Server", r.testAudioSocket},
		{"Configuration Files", r.testConfiguration},
		{"Provider Keys", r.testProviders},
		{"Log Health", r.testLogs},
	}
	
	passed := 0
	failed := 0
	warnings := 0
	
	for i, test := range tests {
		fmt.Printf("[%d/%d] Testing %s...\n", i+1, len(tests), test.name)
		
		if err := test.fn(); err != nil {
			if strings.Contains(err.Error(), "warning:") {
				warningColor.Printf("  ⚠️  WARN: %v\n", err)
				warnings++
				passed++ // Count as passed with warning
			} else {
				errorColor.Printf("  ❌ FAIL: %v\n", err)
				failed++
			}
		} else {
			successColor.Printf("  ✅ PASS\n")
			passed++
		}
		fmt.Println()
	}
	
	// Summary
	fmt.Println("═══════════════════════════════════════════")
	fmt.Printf("📊 DEMO SUMMARY\n")
	fmt.Println("═══════════════════════════════════════════")
	fmt.Println()
	
	successColor.Printf("✅ Passed: %d\n", passed)
	if warnings > 0 {
		warningColor.Printf("⚠️  Warnings: %d\n", warnings)
	}
	if failed > 0 {
		errorColor.Printf("❌ Failed: %d\n", failed)
	}
	fmt.Println()
	
	if failed == 0 {
		if warnings > 0 {
			warningColor.Println("⚠️  System operational with warnings")
			fmt.Println("    Review warnings before production use")
		} else {
			successColor.Println("🎉 System is ready for production calls!")
		}
	} else {
		errorColor.Println("❌ System has failures")
		fmt.Println("    Fix critical issues before production use")
	}
	fmt.Println()
	
	fmt.Println("Next steps:")
	fmt.Println("  • agent check      (standard diagnostics report)")
	fmt.Println("  • Make a test call")
	fmt.Println()
	
	if failed > 0 {
		return fmt.Errorf("%d tests failed", failed)
	}
	
	return nil
}

// testDocker checks if Docker daemon is running
func (r *Runner) testDocker() error {
	if r.verbose {
		infoColor.Printf("  → Checking Docker daemon...\n")
	}
	
	cmd := exec.Command("docker", "info")
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("Docker daemon not running")
	}
	
	if r.verbose {
		infoColor.Printf("  → Docker is running\n")
	}
	return nil
}

// testContainerHealth checks if ai-engine container is running
func (r *Runner) testContainerHealth() error {
	if r.verbose {
		infoColor.Printf("  → Checking ai_engine container...\n")
	}
	
	cmd := exec.Command("docker", "ps", "--format", "{{.Names}}\t{{.Status}}", "--filter", "name=ai_engine")
	output, err := cmd.Output()
	if err != nil {
		return fmt.Errorf("failed to check container status")
	}
	
	status := strings.TrimSpace(string(output))
	if status == "" {
		return fmt.Errorf("ai_engine container not found")
	}
	
	if !strings.Contains(status, "Up") {
		return fmt.Errorf("ai_engine container not running: %s", status)
	}
	
	if r.verbose {
		infoColor.Printf("  → Container status: %s\n", status)
	}
	return nil
}

// testAudioSocket checks if AudioSocket server is listening
func (r *Runner) testAudioSocket() error {
	if r.verbose {
		infoColor.Printf("  → Checking AudioSocket on port 8090...\n")
	}
	
	conn, err := net.DialTimeout("tcp", "127.0.0.1:8090", 2*time.Second)
	if err != nil {
		return fmt.Errorf("AudioSocket not listening on port 8090")
	}
	defer conn.Close()
	
	if r.verbose {
		infoColor.Printf("  → AudioSocket server is listening\n")
	}
	return nil
}

// testConfiguration validates config files exist
func (r *Runner) testConfiguration() error {
	if r.verbose {
		infoColor.Printf("  → Validating configuration files...\n")
	}
	
	// Check for .env in current or parent directory
	envPath := ".env"
	if _, err := os.Stat(envPath); os.IsNotExist(err) {
		envPath = "../.env"
		if _, err := os.Stat(envPath); os.IsNotExist(err) {
			return fmt.Errorf(".env file not found")
		}
	}
	
	// Check for YAML config
	yamlPath := "config/ai-agent.yaml"
	if _, err := os.Stat(yamlPath); os.IsNotExist(err) {
		yamlPath = "../config/ai-agent.yaml"
		if _, err := os.Stat(yamlPath); os.IsNotExist(err) {
			return fmt.Errorf("config/ai-agent.yaml not found")
		}
	}
	
	if r.verbose {
		infoColor.Printf("  → Found %s\n", envPath)
		infoColor.Printf("  → Found %s\n", yamlPath)
	}
	
	return nil
}

// testProviders checks provider API keys are configured
func (r *Runner) testProviders() error {
	if r.verbose {
		infoColor.Printf("  → Checking provider API keys...\n")
	}
	
	// Load .env file manually to check for keys
	envMap := r.loadEnvFile()
	
	// Check for common provider keys in environment or .env
	hasOpenAI := r.getEnvKey("OPENAI_API_KEY", envMap) != ""
	hasDeepgram := r.getEnvKey("DEEPGRAM_API_KEY", envMap) != ""
	hasAnthropic := r.getEnvKey("ANTHROPIC_API_KEY", envMap) != ""
	
	providerCount := 0
	if hasOpenAI {
		providerCount++
		if r.verbose {
			infoColor.Printf("  → OpenAI API key configured\n")
		}
	}
	if hasDeepgram {
		providerCount++
		if r.verbose {
			infoColor.Printf("  → Deepgram API key configured\n")
		}
	}
	if hasAnthropic {
		providerCount++
		if r.verbose {
			infoColor.Printf("  → Anthropic API key configured\n")
		}
	}
	
	if providerCount == 0 {
		return fmt.Errorf("warning: no provider API keys configured")
	}
	
	return nil
}

// loadEnvFile loads .env file and returns map of keys
func (r *Runner) loadEnvFile() map[string]string {
	envMap := make(map[string]string)
	
	// Try to find .env
	envPath := ".env"
	if _, err := os.Stat(envPath); os.IsNotExist(err) {
		envPath = "../.env"
		if _, err := os.Stat(envPath); os.IsNotExist(err) {
			return envMap
		}
	}
	
	data, err := os.ReadFile(envPath)
	if err != nil {
		return envMap
	}
	
	lines := strings.Split(string(data), "\n")
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		
		parts := strings.SplitN(line, "=", 2)
		if len(parts) == 2 {
			key := strings.TrimSpace(parts[0])
			value := strings.TrimSpace(parts[1])
			value = strings.Trim(value, "\"'")
			envMap[key] = value
		}
	}
	
	return envMap
}

// getEnvKey gets value from OS environment or .env map
func (r *Runner) getEnvKey(key string, envMap map[string]string) string {
	// Check OS environment first
	if val := os.Getenv(key); val != "" {
		return val
	}
	// Fallback to .env map
	return envMap[key]
}

// testLogs checks for recent errors in container logs
func (r *Runner) testLogs() error {
	if r.verbose {
		infoColor.Printf("  → Checking recent container logs...\n")
	}
	
	cmd := exec.Command("docker", "logs", "--since", "5m", "ai_engine")
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("warning: could not read container logs")
	}
	
	logStr := string(output)
	
	// Check for critical errors
	if strings.Contains(logStr, "CRITICAL") || strings.Contains(logStr, "FATAL") {
		return fmt.Errorf("critical errors detected in logs")
	}
	
	// Check for warnings
	errorCount := strings.Count(logStr, "ERROR")
	if errorCount > 10 {
		return fmt.Errorf("warning: %d errors in recent logs", errorCount)
	}
	
	if r.verbose && errorCount > 0 {
		infoColor.Printf("  → %d errors in last 5 minutes (acceptable)\n", errorCount)
	}
	
	return nil
}
