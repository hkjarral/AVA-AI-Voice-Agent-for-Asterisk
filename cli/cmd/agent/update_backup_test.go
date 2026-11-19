package main

import (
	"bufio"
	"os"
	"os/exec"
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

func TestConfiguredHealthPortPrecedence(t *testing.T) {
	root := chdirTemp(t)
	t.Setenv("HEALTH_BIND_PORT", "")

	if err := os.MkdirAll(filepath.Join(root, "config"), 0o755); err != nil {
		t.Fatalf("mkdir config: %v", err)
	}
	if err := os.WriteFile(filepath.Join(root, "config", "ai-agent.yaml"), []byte("health:\n  port: 16000\n"), 0o644); err != nil {
		t.Fatalf("write base config: %v", err)
	}
	if got := configuredHealthPort(); got != 16000 {
		t.Fatalf("base YAML port mismatch: got %d want 16000", got)
	}

	if err := os.WriteFile(filepath.Join(root, "config", "ai-agent.local.yaml"), []byte("health:\n  port: 17000\n"), 0o644); err != nil {
		t.Fatalf("write local config: %v", err)
	}
	if got := configuredHealthPort(); got != 17000 {
		t.Fatalf("local YAML port mismatch: got %d want 17000", got)
	}

	if err := os.WriteFile(filepath.Join(root, ".env"), []byte("HEALTH_BIND_PORT=18000\n"), 0o644); err != nil {
		t.Fatalf("write .env: %v", err)
	}
	if got := configuredHealthPort(); got != 18000 {
		t.Fatalf(".env port mismatch: got %d want 18000", got)
	}

	t.Setenv("HEALTH_BIND_PORT", "19000")
	if got := configuredHealthPort(); got != 19000 {
		t.Fatalf("environment port mismatch: got %d want 19000", got)
	}
}

func TestConfiguredHealthPortDefault(t *testing.T) {
	chdirTemp(t)
	t.Setenv("HEALTH_BIND_PORT", "")

	if got := configuredHealthPort(); got != 15000 {
		t.Fatalf("default port mismatch: got %d want 15000", got)
	}
}

// A stopped engine does not imply a quiescent database: keep another writer's
// WAL open and prove that the host backup includes its committed data coherently.
func TestBackupSQLiteHostCopy(t *testing.T) {
	chdirTemp(t)
	backupRoot := t.TempDir()

	rel := filepath.Join("data", "operator", "agents.db")
	if err := os.MkdirAll(filepath.Dir(rel), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("Python with SQLite is required")
	}
	writer := exec.Command(python, "-c", `
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("PRAGMA journal_mode=WAL")
db.execute("CREATE TABLE state(value TEXT)")
db.execute("INSERT INTO state VALUES ('committed in live WAL')")
db.commit()
print("ready", flush=True)
sys.stdin.readline()
db.close()
`, rel)
	input, _ := writer.StdinPipe()
	output, _ := writer.StdoutPipe()
	if err := writer.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { input.Close(); _ = writer.Wait() })
	if ready, err := bufio.NewReader(output).ReadString('\n'); err != nil || ready != "ready\n" {
		t.Fatalf("writer did not open WAL: %q %v", ready, err)
	}

	if err := backupSQLiteHostCopy(rel, backupRoot); err != nil {
		t.Fatalf("host snapshot should succeed, got: %v", err)
	}
	verify := exec.Command(python, "-c", `import sqlite3, sys; db=sqlite3.connect(sys.argv[1]); assert db.execute("PRAGMA quick_check").fetchone() == ("ok",); assert db.execute("SELECT value FROM state").fetchone() == ("committed in live WAL",)`, filepath.Join(backupRoot, rel))
	if out, err := verify.CombinedOutput(); err != nil {
		t.Fatalf("snapshot lost committed WAL data: %v %s", err, out)
	}
	for _, suffix := range []string{"-wal", "-shm"} {
		if _, err := os.Stat(filepath.Join(backupRoot, rel+suffix)); !os.IsNotExist(err) {
			t.Fatalf("snapshot must be standalone, found copied %s", suffix)
		}
	}
}

func TestHostSQLiteSnapshotRejectsCorruptDatabase(t *testing.T) {
	chdirTemp(t)
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("Python with SQLite is required")
	}
	if err := os.WriteFile("broken.db", []byte("not SQLite"), 0600); err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	if err := backupSQLiteHostCopy("broken.db", root); err == nil {
		t.Fatal("corrupt database must stop the update, not produce a successful backup")
	}
	if _, err := os.Stat(filepath.Join(root, "broken.db")); !os.IsNotExist(err) {
		t.Fatal("failed snapshot must not publish a partial backup")
	}
}

func TestRelocatedSQLitePathsRequireExplicitBackup(t *testing.T) {
	chdirTemp(t)
	t.Setenv("AGENTS_DB_PATH", "/app/data/operator/agents.db")
	t.Setenv("CALL_HISTORY_DB_PATH", "data/call_history.db")
	if err := validateSQLiteBackupPaths(); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AGENTS_DB_PATH", "/external/operator.db")
	if err := validateSQLiteBackupPaths(); err == nil {
		t.Fatal("default-path backups must not silently omit an external database")
	}
	t.Setenv("AGENTS_DB_PATH", "")
	if err := os.WriteFile(".env", []byte("AGENTS_DB_PATH=data/custom-agents.db\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := validateSQLiteBackupPaths(); err == nil {
		t.Fatal("dotenv overrides must also require explicit backup mapping")
	}
}

func TestRetryCannotOverwriteOriginalBackupID(t *testing.T) {
	root := chdirTemp(t)
	t.Setenv("AGENTS_DB_PATH", "")
	t.Setenv("CALL_HISTORY_DB_PATH", "")
	originalID := updateBackupID
	t.Cleanup(func() { updateBackupID = originalID })
	updateBackupID = "original-upgrade"
	if err := os.WriteFile(".env", []byte("SYNTHETIC=before\n"), 0600); err != nil {
		t.Fatal(err)
	}
	ctx := recoveryContext(root, "", "")
	if err := createUpdateBackups(ctx); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(".env", []byte("SYNTHETIC=after\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := createUpdateBackups(recoveryContext(root, "", "")); err == nil {
		t.Fatal("a retry must not overwrite the original backup identifier")
	}
	saved, err := os.ReadFile(filepath.Join(ctx.backupDir, ".env"))
	if err != nil || string(saved) != "SYNTHETIC=before\n" {
		t.Fatalf("original backup changed: %s %v", saved, err)
	}
}
