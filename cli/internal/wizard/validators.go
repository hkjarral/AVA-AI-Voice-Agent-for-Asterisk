package wizard

import (
	"fmt"
	"io"
	"net/http"
	"os/exec"
	"strings"
	"time"
)

// TestARIConnectivity tests Asterisk ARI connection
func TestARIConnectivity(host, username, password string) error {
	url := fmt.Sprintf("http://%s:8088/ari/asterisk/info", host)

	client := &http.Client{Timeout: 5 * time.Second}
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return err
	}

	req.SetBasicAuth(username, password)

	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("connection failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		return fmt.Errorf("HTTP %d (expected 200)", resp.StatusCode)
	}

	return nil
}

// TestAudioSocketPort checks if AudioSocket port is listening
func TestAudioSocketPort(port string) error {
	cmd := exec.Command("sh", "-c",
		fmt.Sprintf("netstat -tuln 2>/dev/null | grep :%s || ss -tuln 2>/dev/null | grep :%s", port, port))

	if err := cmd.Run(); err != nil {
		return fmt.Errorf("port %s not listening", port)
	}

	return nil
}

// TestOpenAIKey validates OpenAI API key
func TestOpenAIKey(apiKey string) error {
	if apiKey == "" {
		return fmt.Errorf("API key is empty")
	}

	url := "https://api.openai.com/v1/models"

	client := &http.Client{Timeout: 10 * time.Second}
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return err
	}

	req.Header.Set("Authorization", "Bearer "+apiKey)

	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("connection failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == 401 {
		return fmt.Errorf("invalid API key")
	}

	if resp.StatusCode != 200 {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(body))
	}

	return nil
}

// TestDeepgramKey validates Deepgram API key
func TestDeepgramKey(apiKey string) error {
	if apiKey == "" {
		return fmt.Errorf("API key is empty")
	}

	url := "https://api.deepgram.com/v1/projects"

	client := &http.Client{Timeout: 10 * time.Second}
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return err
	}

	req.Header.Set("Authorization", "Token "+apiKey)

	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("connection failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == 401 {
		return fmt.Errorf("invalid API key")
	}

	if resp.StatusCode != 200 {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(body))
	}

	return nil
}

// TestAnthropicKey validates Anthropic API key
func TestAnthropicKey(apiKey string) error {
	if apiKey == "" {
		return fmt.Errorf("API key is empty")
	}

	// Anthropic doesn't have a simple validation endpoint
	// Just check format for now
	if !strings.HasPrefix(apiKey, "sk-ant-") {
		return fmt.Errorf("invalid format (should start with sk-ant-)")
	}

	return nil
}

// TestDockerRunning checks if Docker daemon is running
func TestDockerRunning() error {
	cmd := exec.Command("docker", "info")
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("Docker daemon not running")
	}
	return nil
}

// TestContainerExists checks if a container exists
func TestContainerExists(name string) bool {
	cmd := exec.Command("docker", "ps", "-a", "--format", "{{.Names}}", "--filter", "name="+name)
	output, err := cmd.Output()
	if err != nil {
		return false
	}

	containers := strings.Split(strings.TrimSpace(string(output)), "\n")
	for _, c := range containers {
		if c == name {
			return true
		}
	}

	return false
}
