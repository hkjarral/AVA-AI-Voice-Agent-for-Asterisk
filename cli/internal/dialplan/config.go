package dialplan

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"gopkg.in/yaml.v3"
)

// ConfigDirectory finds the same project-relative config paths as the wizard.
func ConfigDirectory() (string, error) {
	for _, dir := range []string{"config", "../config"} {
		for _, name := range []string{"ai-agent.local.yaml", "ai-agent.yaml"} {
			_, err := os.Stat(filepath.Join(dir, name))
			if err == nil {
				return dir, nil
			}
			if !os.IsNotExist(err) {
				return "", err
			}
		}
	}
	return "", fmt.Errorf("configuration not found; run from the project directory or set --app-name explicitly")
}

// ConfiguredAppName reads the effective app name without creating .env or changing settings.
func ConfiguredAppName(dir string) (string, error) {
	const defaultApp = "asterisk-ai-voice-agent"
	app := defaultApp
	for _, name := range []string{"ai-agent.yaml", "ai-agent.local.yaml"} {
		data, err := os.ReadFile(filepath.Join(dir, name))
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return "", err
		}
		var cfg struct {
			Asterisk struct {
				AppName *string `yaml:"app_name"`
			} `yaml:"asterisk"`
		}
		if err := yaml.Unmarshal(data, &cfg); err != nil {
			return "", err
		}
		if cfg.Asterisk.AppName != nil {
			app = strings.TrimSpace(*cfg.Asterisk.AppName)
			if app == "" {
				app = defaultApp
			}
		}
	}
	if err := ValidateIdentifiers("", "", app); err != nil {
		return "", err
	}
	return app, nil
}

// ValidateIdentifiers prevents CLI arguments/configuration from injecting dialplan commands.
func ValidateIdentifiers(agent, provider, app string) error {
	valid := regexp.MustCompile(`^[A-Za-z0-9_.-]+$`)
	for label, value := range map[string]string{"Agent": agent, "provider": provider, "Stasis app": app} {
		if value == "" && label != "Stasis app" {
			continue
		}
		if !valid.MatchString(strings.TrimSpace(value)) {
			return fmt.Errorf("invalid %s identifier", label)
		}
	}
	return nil
}
