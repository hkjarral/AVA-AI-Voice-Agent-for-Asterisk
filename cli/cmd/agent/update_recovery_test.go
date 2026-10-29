package main

import (
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"
)

func recoveryContext(root, base, target string) *updateContext {
	return &updateContext{repoRoot: root, oldSHA: base, newSHA: target,
		servicesToRebuild: map[string]bool{}, servicesToRestart: map[string]bool{},
		skippedServices: map[string]string{}}
}

// Real local Git fast-forward + simulated Docker failure. This exercises the
// run/plan entry points, rather than calling the planner in isolation.
func initRecoveryUpdateRepo(t *testing.T) (string, string, string) {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("fixture uses a POSIX Docker CLI shim")
	}
	initDiscardLocalChangesRepo(t)
	root, _ := os.Getwd()
	runLocalGit(t, "branch", "-M", "main")
	base, _ := gitRevParse("HEAD")
	if err := os.WriteFile("Dockerfile", []byte("FROM scratch\n"), 0600); err != nil {
		t.Fatal(err)
	}
	runLocalGit(t, "add", "Dockerfile")
	runLocalGit(t, "commit", "-m", "requires engine rebuild")
	target, _ := gitRevParse("HEAD")
	// Fetch from another local repo, with no network or real Docker mutations.
	origin := t.TempDir()
	runLocalGit(t, "clone", "--bare", root, origin)
	runLocalGit(t, "remote", "add", "origin", origin)
	runLocalGit(t, "reset", "--hard", base)
	bin := t.TempDir()
	if err := os.WriteFile(filepath.Join(bin, "docker"), []byte("#!/bin/sh\necho 'Docker Compose version v5.5.1'\n"), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("AAVA_UPDATE_FORCE_ACTIVE_CALLS", "true")
	oldRef, oldRemote, oldRebuild, oldPolicy, oldBackup := updateRef, updateRemote, updateRebuild, updateLocalChanges, updateBackupID
	oldSelf, oldCheck, oldPlan, oldJSON := updateSelfUpdate, updateSkipCheck, updatePlan, updatePlanJSON
	t.Cleanup(func() {
		updateRef, updateRemote, updateRebuild, updateLocalChanges, updateBackupID = oldRef, oldRemote, oldRebuild, oldPolicy, oldBackup
		updateSelfUpdate, updateSkipCheck, updatePlan, updatePlanJSON = oldSelf, oldCheck, oldPlan, oldJSON
	})
	updateRef, updateRemote, updateRebuild, updateLocalChanges = "main", "origin", "auto", "abort"
	updateSelfUpdate, updateSkipCheck, updatePlan, updatePlanJSON = false, true, false, false
	return root, base, target
}

func TestUpdateRetriesFailedDockerActionsAtSameCommit(t *testing.T) {
	root, _, target := initRecoveryUpdateRepo(t)
	commands := withDockerActionState(t, "ai_engine\n", map[string]bool{})
	normalRun := runDockerCommandForUpdate
	runDockerCommandForUpdate = func(name string, args ...string) (string, error) {
		out, err := normalRun(name, args...)
		if strings.Join(args, " ") == "compose up -d --build ai_engine" {
			// A fresh recovery record must already exist when Docker starts.
			pending, readErr := readPendingDockerActions(root)
			if readErr != nil || pending == nil || pending.TargetSHA != target {
				t.Fatalf("missing pre-checkout recovery record: %#v, %v", pending, readErr)
			}
			return "", errors.New("synthetic build failure")
		}
		return out, err
	}
	updateBackupID = "first-attempt"
	if err := runUpdate(); err == nil || !strings.Contains(err.Error(), "synthetic build failure") {
		t.Fatalf("expected build failure, got %v", err)
	}
	if head, _ := gitRevParse("HEAD"); head != target {
		t.Fatalf("fixture did not advance Git: %s", head)
	}
	before, err := os.ReadFile(pendingDockerPath(root))
	if err != nil {
		t.Fatal(err)
	}
	// The GUI's JSON preview must show pending work without consuming it.
	updatePlan, updatePlanJSON = true, true
	stdout := os.Stdout
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	os.Stdout = writer
	planErr := runUpdatePlan(recoveryContext(root, target, target))
	os.Stdout = stdout
	writer.Close()
	output, _ := io.ReadAll(reader)
	reader.Close()
	var report updatePlanReport
	if planErr != nil || json.Unmarshal(output, &report) != nil {
		t.Fatalf("invalid recovery preview: %v, %s", planErr, output)
	}
	if report.CodeChanged || !containsString(report.ServicesRebuild, "ai_engine") || len(report.Warnings) == 0 {
		t.Fatalf("preview lost pending actions: %#v", report)
	}
	after, _ := os.ReadFile(pendingDockerPath(root))
	if string(before) != string(after) {
		t.Fatal("preview modified recovery state")
	}
	updatePlan, updatePlanJSON = false, false
	runDockerCommandForUpdate = func(name string, args ...string) (string, error) {
		out, err := normalRun(name, args...)
		if strings.Join(args, " ") == "compose ps --services --status running" {
			// A recreate failure may leave the engine stopped before the retry.
			return "", nil
		}
		return out, err
	}
	*commands = nil
	updateBackupID = "retry"
	if err := runUpdate(); err != nil {
		t.Fatal(err)
	}
	if !commandSeen(*commands, "docker compose up -d --build ai_engine") {
		t.Fatalf("retry skipped rebuild: %v", *commands)
	}
	if _, err := os.Stat(pendingDockerPath(root)); !os.IsNotExist(err) {
		t.Fatalf("successful Docker apply must clear pending state: %v", err)
	}
	*commands = nil
	updateBackupID = "no-op"
	if err := runUpdate(); err != nil {
		t.Fatal(err)
	}
	if len(*commands) != 0 {
		t.Fatalf("completed update must return to a no-op: %v", *commands)
	}
}

func TestUpdateRebuildAllAtSameCommit(t *testing.T) {
	_, _, target := initRecoveryUpdateRepo(t)
	runLocalGit(t, "reset", "--hard", target)
	commands := withDockerActionState(t, "ai_engine\n", map[string]bool{})
	updateRebuild, updateBackupID = "all", "same-head-rebuild"
	if err := runUpdate(); err != nil {
		t.Fatal(err)
	}
	if !commandSeen(*commands, "docker compose up -d --build ai_engine") {
		t.Fatalf("explicit rebuild-all ignored: %v", *commands)
	}
}

func TestPendingSameCommitStillGuardsActiveCalls(t *testing.T) {
	root, _, target := initRecoveryUpdateRepo(t)
	runLocalGit(t, "reset", "--hard", target)
	commands := withDockerActionState(t, "ai_engine\n", map[string]bool{})
	if err := os.MkdirAll(filepath.Dir(pendingDockerPath(root)), 0700); err != nil {
		t.Fatal(err)
	}
	ctx := recoveryContext(root, target, target)
	ctx.servicesToRebuild["ai_engine"] = true
	if err := savePendingDockerActions(ctx); err != nil {
		t.Fatal(err)
	}
	bin := t.TempDir()
	if err := os.WriteFile(filepath.Join(bin, "docker"), []byte("#!/bin/sh\nif [ \"$1\" = exec ]; then echo '{\"active_calls\":2}'; else echo compose; fi\n"), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("AAVA_UPDATE_FORCE_ACTIVE_CALLS", "false")
	updateBackupID = "active-calls"
	if err := runUpdate(); err == nil || !strings.Contains(err.Error(), "2 active call(s)") {
		t.Fatalf("recovery bypassed active-call guard: %v", err)
	}
	if commandContainingSeen(*commands, "compose up") {
		t.Fatalf("active-call guard allowed recreation: %v", *commands)
	}
	if pending, err := readPendingDockerActions(root); err != nil || pending == nil {
		t.Fatal("active-call refusal must retain recovery state")
	}
}

func TestRunningSnapshotFailureStopsBeforeCheckout(t *testing.T) {
	root, base, _ := initRecoveryUpdateRepo(t)
	commands := withDockerActionState(t, "ai_engine\n", map[string]bool{})
	originalRun := runDockerCommandForUpdate
	runDockerCommandForUpdate = func(name string, args ...string) (string, error) {
		out, err := originalRun(name, args...)
		if strings.HasPrefix(strings.Join(args, " "), "compose ps") {
			return "", errors.New("daemon unavailable")
		}
		return out, err
	}
	updateBackupID = "snapshot-failure"
	if err := runUpdate(); err == nil || !strings.Contains(err.Error(), "cannot snapshot running services") {
		t.Fatalf("snapshot failure was ignored: %v", err)
	}
	if head, _ := gitRevParse("HEAD"); head != base {
		t.Fatal("checkout advanced without a running-state snapshot")
	}
	if _, err := os.Stat(pendingDockerPath(root)); !os.IsNotExist(err) {
		t.Fatal("snapshot failure must not write a partial recovery record")
	}
	if commandContainingSeen(*commands, "compose up") {
		t.Fatalf("snapshot failure must not recreate containers: %v", *commands)
	}
}

func TestRecoveryRestoresPreviouslyRunningDeletedLocalAI(t *testing.T) {
	root := t.TempDir()
	base, target := strings.Repeat("a", 40), strings.Repeat("b", 40)
	if err := os.MkdirAll(filepath.Dir(pendingDockerPath(root)), 0700); err != nil {
		t.Fatal(err)
	}
	ctx := recoveryContext(root, base, target)
	ctx.servicesToRebuild["local_ai_server"] = true
	ctx.runningBeforeUpdate = map[string]bool{"local_ai_server": true}
	if err := savePendingDockerActions(ctx); err != nil {
		t.Fatal(err)
	}
	commands := withDockerActionState(t, "", map[string]bool{})
	withLocalAIUpdateState(t, localAIUpdateState{existingKnown: true, selectionKnown: true})
	withUpdateRebuildMode(t, rebuildAuto)
	ctx = recoveryContext(root, target, target)
	if err := planDockerActions(ctx); err != nil {
		t.Fatal(err)
	}
	if err := applyDockerActions(ctx); err != nil {
		t.Fatal(err)
	}
	if !commandSeen(*commands, "docker compose up -d --build local_ai_server") {
		t.Fatalf("recovery dropped previously running Local AI: %v", *commands)
	}
}

func TestBakeRecoveryUsesCleanStdoutAndExplicitGrant(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("fixture uses a POSIX Docker CLI shim")
	}
	bin := t.TempDir()
	inputPath, argsPath := filepath.Join(bin, "input.json"), filepath.Join(bin, "args.txt")
	t.Setenv("AAVA_TEST_BAKE_INPUT", inputPath)
	t.Setenv("AAVA_TEST_BAKE_ARGS", argsPath)
	script := `#!/bin/sh
if [ "$1 $2 $3" = "compose build --print" ]; then
  echo 'warning from Compose' >&2
  echo '{"target":{"ai_engine":{"network":"host","tags":["test-only:latest"]}},"group":{"default":{"targets":["ai_engine"]}}}'
elif [ "$1 $2" = "buildx bake" ]; then
  printf '%s\n' "$@" > "$AAVA_TEST_BAKE_ARGS"
  cat > "$AAVA_TEST_BAKE_INPUT"
else
  exit 1
fi
`
	if err := os.WriteFile(filepath.Join(bin, "docker"), []byte(script), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	if err := buildWithHostNetworkGrant([]string{"ai_engine"}); err != nil {
		t.Fatal(err)
	}
	input, _ := os.ReadFile(inputPath)
	if !json.Valid(input) || strings.Contains(string(input), "warning") || !strings.Contains(string(input), "test-only:latest") {
		t.Fatalf("stdin lost machine-readable Compose definition: %s", input)
	}
	args, _ := os.ReadFile(argsPath)
	if string(args) != "buildx\nbake\n--file\n-\n--allow=network.host\n--load\n" {
		t.Fatalf("unexpected Bake permissions/output: %s", args)
	}
}

func TestPendingDockerStateRejectsUnsafeRecovery(t *testing.T) {
	base, target := strings.Repeat("a", 40), strings.Repeat("b", 40)
	for _, scenario := range []string{"different-head", "excluded-ui", "rebuild-none", "invalid-json", "symlink"} {
		t.Run(scenario, func(t *testing.T) {
			root := t.TempDir()
			if err := os.MkdirAll(filepath.Dir(pendingDockerPath(root)), 0700); err != nil {
				t.Fatal(err)
			}
			ctx := recoveryContext(root, base, target)
			ctx.servicesToRebuild["admin_ui"] = true
			if err := savePendingDockerActions(ctx); err != nil {
				t.Fatal(err)
			}
			commands := withDockerActionState(t, "ai_engine\n", map[string]bool{})
			withUpdateRebuildMode(t, rebuildAuto)
			updateIncludeUI = true
			ctx = recoveryContext(root, target, target)
			switch scenario {
			case "different-head":
				ctx.oldSHA = strings.Repeat("c", 40)
			case "excluded-ui":
				updateIncludeUI = false
			case "rebuild-none":
				updateRebuild = "none"
			case "invalid-json":
				os.WriteFile(pendingDockerPath(root), []byte("{"), 0600)
			case "symlink":
				os.Remove(pendingDockerPath(root))
				if err := os.Symlink(filepath.Join(root, "outside"), pendingDockerPath(root)); err != nil {
					t.Skip(err)
				}
			}
			if err := planDockerActions(ctx); err == nil {
				t.Fatal("unsafe/incomplete recovery was accepted")
			}
			if len(*commands) != 0 {
				t.Fatalf("invalid recovery must fail before Docker: %v", *commands)
			}
			if _, err := os.Lstat(pendingDockerPath(root)); err != nil {
				t.Fatal("invalid recovery must preserve the record")
			}
		})
	}
}

func TestHostNetworkBuildFallback(t *testing.T) {
	for _, scenario := range []string{"running", "verbose-output", "stopped-local", "unrelated-error", "bake-failure", "export-failure", "invalid-export", "recreate-failure"} {
		t.Run(scenario, func(t *testing.T) {
			commands := withDockerActionState(t, "ai_engine\n", map[string]bool{"local_ai_server": true})
			ctx := recoveryContext(t.TempDir(), "", "")
			service := "ai_engine"
			if scenario == "stopped-local" {
				service = "local_ai_server"
			}
			ctx.servicesToRebuild[service] = true
			originalRun := runDockerCommandForUpdate
			buildErr := errors.New(`additional privileges requested: pass "--allow=network.host" to grant requested privileges`)
			buildOutput := buildErr.Error()
			if scenario == "unrelated-error" {
				buildErr = errors.New("package download failed")
				buildOutput = buildErr.Error()
			}
			if scenario == "verbose-output" {
				buildErr = errors.New("exit status 1")
			}
			runDockerCommandForUpdate = func(name string, args ...string) (string, error) {
				out, err := originalRun(name, args...)
				command := strings.Join(args, " ")
				if command == "compose up -d --build ai_engine" || command == "compose build local_ai_server" {
					return buildOutput, buildErr
				}
				if scenario == "recreate-failure" && command == "compose up -d --no-build --pull never ai_engine" {
					return "", errors.New("recreate failed")
				}
				return out, err
			}
			originalExport, originalBake := exportDockerBakeForUpdate, runDockerBakeForUpdate
			t.Cleanup(func() { exportDockerBakeForUpdate, runDockerBakeForUpdate = originalExport, originalBake })
			exported, baked := false, false
			definition := `{"group":{"default":{"targets":["selected"]}},"target":{"selected":{"network":"host","tags":["custom-image:latest"]}}}`
			exportDockerBakeForUpdate = func(services []string) (string, error) {
				exported = true
				if !reflect.DeepEqual(services, []string{service}) {
					t.Fatalf("export widened service scope: %v", services)
				}
				if scenario == "export-failure" {
					return "", errors.New("unsupported --print")
				}
				if scenario == "invalid-export" {
					return "truncated JSON", nil
				}
				return definition, nil
			}
			runDockerBakeForUpdate = func(input string) (string, error) {
				baked = true
				if input != definition {
					t.Fatal("Bake definition was changed")
				}
				if scenario == "bake-failure" {
					return "", errors.New("builder unavailable")
				}
				return "", nil
			}
			err := applyDockerActions(ctx)
			wantSuccess := scenario == "running" || scenario == "verbose-output" || scenario == "stopped-local"
			if (err == nil) != wantSuccess {
				t.Fatalf("unexpected apply result: %v", err)
			}
			if scenario == "unrelated-error" && (exported || baked) {
				t.Fatal("unrelated build failure must not grant host-network access")
			}
			up := commandSeen(*commands, "docker compose up -d --no-build --pull never ai_engine")
			if up != (scenario == "running" || scenario == "verbose-output" || scenario == "recreate-failure") {
				t.Fatalf("unexpected recreation after Bake: %v", *commands)
			}
			if scenario == "stopped-local" && commandContainingSeen(*commands, "compose up", service) {
				t.Fatalf("fallback started stopped Local AI: %v", *commands)
			}
		})
	}
}
