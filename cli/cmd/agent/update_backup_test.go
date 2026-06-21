package main

import (
	"os"
	"path/filepath"
	"testing"
)

// chdirTemp switches the working directory to a fresh temp dir for the duration
// of the test, restoring the previous CWD on cleanup. The backup helpers resolve
// the data DBs and config files relative to CWD, so tests run against an isolated
// repo-root stand-in.
func chdirTemp(t *testing.T) string {
	t.Helper()
	prev, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	dir := t.TempDir()
	if err := os.Chdir(dir); err != nil {
		t.Fatalf("chdir: %v", err)
	}
	t.Cleanup(func() { _ = os.Chdir(prev) })
	return dir
}

// TestBackupSQLiteIfExistsAbsentDBSkips proves a fresh install (no agents.db /
// call_history.db) does not fail the pre-update backup: an absent DB returns nil
// without invoking docker (MED-U3).
func TestBackupSQLiteIfExistsAbsentDBSkips(t *testing.T) {
	chdirTemp(t)
	backupRoot := t.TempDir()

	for _, rel := range []string{
		filepath.Join("data", "operator", "agents.db"),
		filepath.Join("data", "call_history.db"),
	} {
		if err := backupSQLiteIfExists(rel, backupRoot); err != nil {
			t.Fatalf("absent DB %s should skip without error, got: %v", rel, err)
		}
		if _, err := os.Stat(filepath.Join(backupRoot, rel)); !os.IsNotExist(err) {
			t.Fatalf("absent DB %s should not produce a backup artifact", rel)
		}
	}
}

// TestBackupPathIfExists covers the copy mechanism shared by config files and,
// when present, exercises the present/absent branches the DB backup relies on.
func TestBackupPathIfExists(t *testing.T) {
	chdirTemp(t)
	backupRoot := t.TempDir()

	// Absent file: skipped, no error, no artifact.
	if err := backupPathIfExists(filepath.Join("config", "missing.yaml"), backupRoot); err != nil {
		t.Fatalf("absent path should skip without error, got: %v", err)
	}
	if _, err := os.Stat(filepath.Join(backupRoot, "config", "missing.yaml")); !os.IsNotExist(err) {
		t.Fatalf("absent path should not produce a backup artifact")
	}

	// Present file: copied into the backup root preserving its relative path.
	rel := filepath.Join("config", "present.yaml")
	if err := os.MkdirAll(filepath.Dir(rel), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	want := []byte("key: value\n")
	if err := os.WriteFile(rel, want, 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	if err := backupPathIfExists(rel, backupRoot); err != nil {
		t.Fatalf("present path should copy without error, got: %v", err)
	}
	got, err := os.ReadFile(filepath.Join(backupRoot, rel))
	if err != nil {
		t.Fatalf("backup artifact missing: %v", err)
	}
	if string(got) != string(want) {
		t.Fatalf("backup content mismatch: got %q want %q", got, want)
	}
}

// TestBackupSQLiteHostCopy proves the stopped-engine fallback copies the DB and
// any present -wal/-shm sidecars while skipping absent sidecars. This is the path
// taken when ai_engine is not running, so a stopped/unhealthy container no longer
// aborts the update (Finding 1 / Codex P2 + CodeRabbit).
func TestBackupSQLiteHostCopy(t *testing.T) {
	chdirTemp(t)
	backupRoot := t.TempDir()

	rel := filepath.Join("data", "operator", "agents.db")
	if err := os.MkdirAll(filepath.Dir(rel), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	dbContent := []byte("SQLite format 3\x00")
	walContent := []byte("wal-bytes")
	if err := os.WriteFile(rel, dbContent, 0o644); err != nil {
		t.Fatalf("write db: %v", err)
	}
	// Present WAL sidecar should be copied; SHM is absent and must be skipped.
	if err := os.WriteFile(rel+"-wal", walContent, 0o644); err != nil {
		t.Fatalf("write wal: %v", err)
	}

	if err := backupSQLiteHostCopy(rel, backupRoot); err != nil {
		t.Fatalf("host copy should succeed, got: %v", err)
	}

	gotDB, err := os.ReadFile(filepath.Join(backupRoot, rel))
	if err != nil {
		t.Fatalf("db backup missing: %v", err)
	}
	if string(gotDB) != string(dbContent) {
		t.Fatalf("db content mismatch: got %q want %q", gotDB, dbContent)
	}
	gotWAL, err := os.ReadFile(filepath.Join(backupRoot, rel+"-wal"))
	if err != nil {
		t.Fatalf("wal sidecar should be copied: %v", err)
	}
	if string(gotWAL) != string(walContent) {
		t.Fatalf("wal content mismatch: got %q want %q", gotWAL, walContent)
	}
	if _, err := os.Stat(filepath.Join(backupRoot, rel+"-shm")); !os.IsNotExist(err) {
		t.Fatalf("absent shm sidecar should not produce a backup artifact")
	}
}
