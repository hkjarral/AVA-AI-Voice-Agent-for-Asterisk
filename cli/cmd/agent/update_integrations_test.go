package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestIntegrationBackupCopiesManagedSecretsPrivately(t *testing.T) {
	root := t.TempDir()
	secrets := filepath.Join(root, "secrets", "providers")
	if err := os.MkdirAll(secrets, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(secrets, "synthetic-key"), []byte("synthetic"), 0600); err != nil {
		t.Fatal(err)
	}
	destination := filepath.Join(root, ".agent", "update-backups", "test")
	if err := backupIntegrationState(root, destination); err != nil {
		t.Fatal(err)
	}
	entries, err := filepath.Glob(filepath.Join(destination, "integration-state.*"))
	if err != nil || len(entries) != 1 {
		t.Fatalf("snapshot directories: %v %v", entries, err)
	}
	path := filepath.Join(entries[0], "secrets", "providers", "synthetic-key")
	content, err := os.ReadFile(path)
	if err != nil || string(content) != "synthetic" {
		t.Fatalf("backup mismatch: %v", err)
	}
	info, err := os.Stat(path)
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatalf("backup is not private: %v", err)
	}
}

func TestIntegrationBackupFailureStopsUpdate(t *testing.T) {
	root := t.TempDir()
	t.Setenv("CALENDLY_DB_PATH", "/unmapped/custom-journal.db")
	if err := backupIntegrationState(root, filepath.Join(root, "backup")); err == nil {
		t.Fatal("unmapped state must not produce a successful backup")
	}
}

func TestIntegrationBackupWithoutStateDoesNotNeedPython(t *testing.T) {
	t.Setenv("PATH", t.TempDir())
	t.Setenv("CALENDLY_DB_PATH", "")
	t.Setenv("CALENDLY_TOKEN_PATH", "")
	root := t.TempDir()
	if err := backupIntegrationState(root, filepath.Join(root, "backup")); err != nil {
		t.Fatal(err)
	}
}

func TestIntegrationBackupWithStateRequiresRuntime(t *testing.T) {
	t.Setenv("PATH", t.TempDir())
	t.Setenv("CALENDLY_DB_PATH", "data/custom.db")
	root := t.TempDir()
	if err := backupIntegrationState(root, filepath.Join(root, "backup")); err == nil {
		t.Fatal("state must not be silently skipped without Python")
	}
}

// A disconnected journal can be temporarily absent while erasure remains owed.
func TestIntegrationBackupPreservesStandaloneCalendlyMarker(t *testing.T) {
	t.Setenv("CALENDLY_DB_PATH", "")
	t.Setenv("CALENDLY_TOKEN_PATH", "")
	root := t.TempDir()
	relative := filepath.Join("data", "operator", "calendly.db.configured")
	marker := filepath.Join(root, relative)
	if err := os.MkdirAll(filepath.Dir(marker), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(marker, []byte("calendly-configured-v1\n"), 0600); err != nil {
		t.Fatal(err)
	}
	destination := filepath.Join(root, "backup")
	if err := backupIntegrationState(root, destination); err != nil {
		t.Fatal(err)
	}
	matches, err := filepath.Glob(filepath.Join(destination, "integration-state.*", relative))
	if err != nil || len(matches) != 1 {
		t.Fatalf("marker snapshots: %v %v", matches, err)
	}
	content, err := os.ReadFile(matches[0])
	if err != nil || string(content) != "calendly-configured-v1\n" {
		t.Fatalf("marker backup mismatch: %v", err)
	}
	info, err := os.Stat(matches[0])
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatalf("marker backup is not private: %v", err)
	}
}
