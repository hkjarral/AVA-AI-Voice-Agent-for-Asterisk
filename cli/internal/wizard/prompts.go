package wizard

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"

	"github.com/fatih/color"
)

var (
	promptColor  = color.New(color.FgCyan, color.Bold)
	successColor = color.New(color.FgGreen)
	errorColor   = color.New(color.FgRed)
	warningColor = color.New(color.FgYellow)
	infoColor    = color.New(color.FgBlue)
)

// PromptText asks for text input with optional default
func PromptText(label string, defaultVal string) string {
	reader := bufio.NewReader(os.Stdin)

	if defaultVal != "" {
		promptColor.Printf("  %s [%s]: ", label, defaultVal)
	} else {
		promptColor.Printf("  %s: ", label)
	}

	input, _ := reader.ReadString('\n')
	input = strings.TrimSpace(input)

	if input == "" && defaultVal != "" {
		return defaultVal
	}

	return input
}

// PromptPassword asks for password input (hidden)
func PromptPassword(label string, hasExisting bool) string {
	reader := bufio.NewReader(os.Stdin)

	if hasExisting {
		promptColor.Printf("  %s [unchanged if blank]: ", label)
	} else {
		promptColor.Printf("  %s: ", label)
	}

	// Note: For production, use terminal.ReadPassword for true hidden input
	// For now, using basic readline (visible for testing)
	input, _ := reader.ReadString('\n')
	input = strings.TrimSpace(input)

	return input
}

// PromptSelect shows numbered options and returns selected index (0-based)
func PromptSelect(label string, options []string, defaultIdx int) int {
	fmt.Println()
	infoColor.Printf("  %s\n", label)

	for i, opt := range options {
		fmt.Printf("  %d) %s\n", i+1, opt)
	}
	fmt.Println()

	reader := bufio.NewReader(os.Stdin)
	promptColor.Printf("  Choice [%d]: ", defaultIdx+1)

	input, _ := reader.ReadString('\n')
	input = strings.TrimSpace(input)

	if input == "" {
		return defaultIdx
	}

	choice, err := strconv.Atoi(input)
	if err != nil || choice < 1 || choice > len(options) {
		warningColor.Printf("  Invalid choice, using default: %d\n", defaultIdx+1)
		return defaultIdx
	}

	return choice - 1
}

// PromptConfirm asks yes/no question
func PromptConfirm(label string, defaultYes bool) bool {
	reader := bufio.NewReader(os.Stdin)

	prompt := "[y/N]"
	if defaultYes {
		prompt = "[Y/n]"
	}

	promptColor.Printf("  %s %s: ", label, prompt)

	input, _ := reader.ReadString('\n')
	input = strings.ToLower(strings.TrimSpace(input))

	if input == "" {
		return defaultYes
	}

	return input == "y" || input == "yes"
}

// PrintSuccess prints success message
func PrintSuccess(msg string) {
	successColor.Printf("  ✅ %s\n", msg)
}

// PrintError prints error message
func PrintError(msg string) {
	errorColor.Printf("  ❌ %s\n", msg)
}

// PrintWarning prints warning message
func PrintWarning(msg string) {
	warningColor.Printf("  ⚠️  %s\n", msg)
}

// PrintInfo prints info message
func PrintInfo(msg string) {
	infoColor.Printf("  ℹ️  %s\n", msg)
}

// PrintStep prints step header
func PrintStep(stepNum int, totalSteps int, title string) {
	fmt.Println()
	fmt.Printf("═══════════════════════════════════════════\n")
	promptColor.Printf("Step %d/%d: %s\n", stepNum, totalSteps, title)
	fmt.Printf("═══════════════════════════════════════════\n")
}
