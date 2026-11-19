package main

import (
	_ "embed"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

//go:embed integration_backup.py
var integrationBackupScript string

func backupIntegrationState(repoRoot, backupRoot string) error {
	present, err := integrationStatePresent(repoRoot)
	if err != nil {
		return err
	}
	if !present {
		return nil
	}
	if _, err := exec.LookPath("python3"); err != nil {
		return fmt.Errorf("Python 3.7+ with SQLite is required for integration snapshots; upgrade the updater image or use host recovery: %w", err)
	}
	if err := os.MkdirAll(backupRoot, 0700); err != nil {
		return err
	}
	destination, err := os.MkdirTemp(backupRoot, "integration-state.")
	if err != nil {
		return err
	}
	cmd := exec.Command("python3", "-c", integrationBackupScript, repoRoot, destination)
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("integration snapshot failed; retain Calendly state and managed secrets with a private manual backup before updating: %w", err)
	}
	printUpdateInfo("Private integration snapshot: %s", destination)
	return nil
}

func integrationStatePresent(root string) (bool, error) {
	for _, key := range []string{"CALENDLY_DB_PATH", "CALENDLY_TOKEN_PATH"} {
		if strings.TrimSpace(os.Getenv(key)) != "" {
			return true, nil
		}
	}
	for _, rel := range []string{"secrets", "data/operator/calendly.db", "data/operator/calendly.db.configured"} {
		path := filepath.Join(root, rel)
		info, err := os.Lstat(path)
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return false, err
		}
		if rel != "secrets" || !info.IsDir() {
			return true, nil
		}
		entries, err := os.ReadDir(path)
		if err != nil {
			return false, err
		}
		if len(entries) != 0 {
			return true, nil
		}
	}
	environment, err := os.ReadFile(filepath.Join(root, ".env"))
	if err != nil && !os.IsNotExist(err) {
		return false, err
	}
	for _, line := range strings.Split(string(environment), "\n") {
		line = strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(line), "export "))
		key, _, found := strings.Cut(line, "=")
		if found && (strings.TrimSpace(key) == "CALENDLY_DB_PATH" || strings.TrimSpace(key) == "CALENDLY_TOKEN_PATH") {
			return true, nil
		}
	}
	return false, nil
}
