package troubleshoot

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

// SelectCallInteractive prompts user to select a call from list
func SelectCallInteractive(calls []Call) (string, error) {
	if len(calls) == 0 {
		return "", fmt.Errorf("no calls available")
	}

	fmt.Println()
	fmt.Println("Recent calls:")
	fmt.Println()

	for i, call := range calls {
		age := formatDuration(time.Since(call.Timestamp))
		fmt.Printf("  %d) %s (%s ago)\n", i+1, call.ID, age)
	}

	fmt.Println()
	fmt.Printf("Select call [1-%d] or 'q' to quit: ", len(calls))

	reader := bufio.NewReader(os.Stdin)
	input, err := reader.ReadString('\n')
	if err != nil {
		return "", err
	}

	input = strings.TrimSpace(input)

	if input == "q" || input == "Q" {
		return "", fmt.Errorf("cancelled by user")
	}

	choice, err := strconv.Atoi(input)
	if err != nil || choice < 1 || choice > len(calls) {
		return "", fmt.Errorf("invalid selection: %s", input)
	}

	return calls[choice-1].ID, nil
}
