package main

import (
	"os"
	"os/exec"
	"testing"
)

func TestRequestedLocalChangesPolicyValidation(t *testing.T) {
	old := updateLocalChanges
	t.Cleanup(func() { updateLocalChanges = old })

	for _, tc := range []struct {
		value string
		want  localChangesPolicy
	}{
		{"", localChangesAsk},
		{"ask", localChangesAsk},
		{"retain", localChangesRetain},
		{"overwrite", localChangesOverwrite},
		{"abort", localChangesAbort},
		{" RETAIN ", localChangesRetain},
	} {
		updateLocalChanges = tc.value
		got, err := requestedLocalChangesPolicy()
		if err != nil {
			t.Fatalf("requestedLocalChangesPolicy(%q) unexpected error: %v", tc.value, err)
		}
		if got != tc.want {
			t.Fatalf("requestedLocalChangesPolicy(%q) = %q, want %q", tc.value, got, tc.want)
		}
	}
}

func TestRequestedLocalChangesPolicyRejectsUnknownValue(t *testing.T) {
	old := updateLocalChanges
	t.Cleanup(func() { updateLocalChanges = old })

	updateLocalChanges = "maybe"
	if _, err := requestedLocalChangesPolicy(); err == nil {
		t.Fatal("expected invalid local-changes value to fail")
	}
}

func TestResolveLocalChangesPolicyRejectsNoStashWithRetainOrOverwrite(t *testing.T) {
	oldLocalChanges := updateLocalChanges
	oldNoStash := updateNoStash
	t.Cleanup(func() {
		updateLocalChanges = oldLocalChanges
		updateNoStash = oldNoStash
	})

	updateNoStash = true
	for _, policy := range []string{"retain", "overwrite"} {
		updateLocalChanges = policy
		if _, err := resolveLocalChangesPolicy(true, []string{"src/engine.py"}); err == nil {
			t.Fatalf("expected --no-stash with --local-changes=%s to fail", policy)
		}
	}

	updateLocalChanges = "ask"
	got, err := resolveLocalChangesPolicy(true, []string{"src/engine.py"})
	if err != nil {
		t.Fatalf("ask policy with --no-stash should become abort without error: %v", err)
	}
	if got != localChangesAbort {
		t.Fatalf("ask policy with --no-stash = %q, want %q", got, localChangesAbort)
	}
}

func TestResolveLocalChangesPolicyNonInteractiveRequiresExplicitPolicy(t *testing.T) {
	oldLocalChanges := updateLocalChanges
	oldNoStash := updateNoStash
	oldStdin := os.Stdin
	t.Cleanup(func() {
		updateLocalChanges = oldLocalChanges
		updateNoStash = oldNoStash
		os.Stdin = oldStdin
	})

	updateLocalChanges = "ask"
	updateNoStash = false
	stdin, err := os.CreateTemp(t.TempDir(), "stdin")
	if err != nil {
		t.Fatalf("create stdin file: %v", err)
	}
	defer stdin.Close()
	os.Stdin = stdin
	if _, err := resolveLocalChangesPolicy(true, []string{"src/engine.py"}); err == nil {
		t.Fatal("expected dirty non-interactive ask policy to require an explicit policy")
	}
}

func TestGitDiscardLocalChangesKeepsUntrackedByDefault(t *testing.T) {
	initDiscardLocalChangesRepo(t)

	if err := os.WriteFile("tracked.txt", []byte("changed\n"), 0o644); err != nil {
		t.Fatalf("write tracked change: %v", err)
	}
	if err := os.WriteFile("untracked.txt", []byte("keep\n"), 0o644); err != nil {
		t.Fatalf("write untracked file: %v", err)
	}

	if err := gitDiscardLocalChanges(false); err != nil {
		t.Fatalf("gitDiscardLocalChanges(false): %v", err)
	}
	got, err := os.ReadFile("tracked.txt")
	if err != nil {
		t.Fatalf("read tracked file: %v", err)
	}
	if string(got) != "base\n" {
		t.Fatalf("tracked file was not reset: got %q", got)
	}
	if _, err := os.Stat("untracked.txt"); err != nil {
		t.Fatalf("untracked file should remain without includeUntracked: %v", err)
	}
}

func TestGitDiscardLocalChangesCleansUntrackedWhenRequested(t *testing.T) {
	initDiscardLocalChangesRepo(t)

	if err := os.WriteFile("tracked.txt", []byte("changed\n"), 0o644); err != nil {
		t.Fatalf("write tracked change: %v", err)
	}
	if err := os.WriteFile("untracked.txt", []byte("remove\n"), 0o644); err != nil {
		t.Fatalf("write untracked file: %v", err)
	}

	if err := gitDiscardLocalChanges(true); err != nil {
		t.Fatalf("gitDiscardLocalChanges(true): %v", err)
	}
	got, err := os.ReadFile("tracked.txt")
	if err != nil {
		t.Fatalf("read tracked file: %v", err)
	}
	if string(got) != "base\n" {
		t.Fatalf("tracked file was not reset: got %q", got)
	}
	if _, err := os.Stat("untracked.txt"); !os.IsNotExist(err) {
		t.Fatalf("untracked file should be cleaned, stat err: %v", err)
	}
}

func initDiscardLocalChangesRepo(t *testing.T) {
	t.Helper()

	root := chdirTemp(t)
	oldSafeDirectory := gitSafeDirectory
	t.Cleanup(func() { gitSafeDirectory = oldSafeDirectory })

	runLocalGit(t, "init")
	runLocalGit(t, "config", "user.email", "test@example.invalid")
	runLocalGit(t, "config", "user.name", "Test User")
	if err := os.WriteFile("tracked.txt", []byte("base\n"), 0o644); err != nil {
		t.Fatalf("write tracked file: %v", err)
	}
	runLocalGit(t, "add", "tracked.txt")
	runLocalGit(t, "commit", "-m", "init")
	gitSafeDirectory = root
}

func runLocalGit(t *testing.T, args ...string) {
	t.Helper()

	cmd := exec.Command("git", args...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v failed: %v\n%s", args, err, out)
	}
}
