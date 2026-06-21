package wizard

import (
	"fmt"
	"os/exec"
	"strings"
)

// RebuildContainers rebuilds and recreates containers
func RebuildContainers(pipeline string) error {
	// Determine which containers to rebuild based on pipeline
	containers := []string{"ai_engine"}

	// Add local-ai-server if using local models
	if strings.Contains(pipeline, "local") {
		if TestContainerExists("local_ai_server") {
			containers = append(containers, "local_ai_server")
		}
	}

	PrintInfo("Rebuilding containers: " + strings.Join(containers, ", "))

	for _, container := range containers {
		// Build
		PrintInfo(fmt.Sprintf("Building %s...", container))
		buildCmd := exec.Command("docker", "compose", "-p", "asterisk-ai-voice-agent", "build", container)
		if output, err := buildCmd.CombinedOutput(); err != nil {
			return fmt.Errorf("build failed for %s: %w\n%s", container, err, string(output))
		}

		// Force recreate
		PrintInfo(fmt.Sprintf("Recreating %s...", container))
		upCmd := exec.Command("docker", "compose", "-p", "asterisk-ai-voice-agent", "up", "-d", "--force-recreate", container)
		if output, err := upCmd.CombinedOutput(); err != nil {
			return fmt.Errorf("recreate failed for %s: %w\n%s", container, err, string(output))
		}
	}

	PrintSuccess("Containers rebuilt successfully")
	return nil
}

// GetContainerStatus checks if container is running
func GetContainerStatus(name string) (bool, error) {
	cmd := exec.Command("docker", "ps", "--format", "{{.Names}}\t{{.Status}}", "--filter", "name="+name)
	output, err := cmd.Output()
	if err != nil {
		return false, err
	}

	status := strings.TrimSpace(string(output))
	if status == "" {
		return false, nil
	}

	// Container exists and is running if output is not empty
	return strings.Contains(status, "Up"), nil
}
