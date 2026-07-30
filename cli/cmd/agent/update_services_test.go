package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func updateTestContext() *updateContext {
	return &updateContext{
		servicesToRebuild: map[string]bool{"local_ai_server": true},
		servicesToRestart: map[string]bool{},
		skippedServices:   map[string]string{},
	}
}

func withLocalAIInstallDetector(t *testing.T, installed, known bool) {
	t.Helper()
	original := detectLocalAIInstallationForUpdate
	detectLocalAIInstallationForUpdate = func() (bool, bool) { return installed, known }
	t.Cleanup(func() { detectLocalAIInstallationForUpdate = original })
}

func withUpdateRebuildMode(t *testing.T, mode rebuildMode) {
	t.Helper()
	original := updateRebuild
	updateRebuild = string(mode)
	t.Cleanup(func() { updateRebuild = original })
}

func withAIEngineRunningForActiveRouteRead(t *testing.T, running bool) {
	t.Helper()
	original := aiEngineRunningForActiveRouteRead
	aiEngineRunningForActiveRouteRead = func() bool { return running }
	t.Cleanup(func() { aiEngineRunningForActiveRouteRead = original })
}

func withDockerActionState(t *testing.T, running string, existing map[string]bool) *[]string {
	t.Helper()
	commands := []string{}
	originalRun := runDockerCommandForUpdate
	originalExisting := existingComposeServicesForUpdate
	originalIncludeUI := updateIncludeUI
	originalForceRecreate := updateForceRecreate
	runDockerCommandForUpdate = func(name string, args ...string) (string, error) {
		command := strings.Join(append([]string{name}, args...), " ")
		commands = append(commands, command)
		if command == "docker compose ps --services --status running" {
			return running, nil
		}
		return "", nil
	}
	existingComposeServicesForUpdate = func() (map[string]bool, bool) {
		return existing, true
	}
	updateIncludeUI = false
	updateForceRecreate = false
	t.Cleanup(func() {
		runDockerCommandForUpdate = originalRun
		existingComposeServicesForUpdate = originalExisting
		updateIncludeUI = originalIncludeUI
		updateForceRecreate = originalForceRecreate
	})
	return &commands
}

func commandSeen(commands []string, want string) bool {
	for _, command := range commands {
		if command == want {
			return true
		}
	}
	return false
}

func commandContainingSeen(commands []string, fragments ...string) bool {
	for _, command := range commands {
		matches := true
		for _, fragment := range fragments {
			if !strings.Contains(command, fragment) {
				matches = false
				break
			}
		}
		if matches {
			return true
		}
	}
	return false
}

func TestOptionalLocalAIChangesSkippedWhenNotInstalledOrSelected(t *testing.T) {
	withLocalAIInstallDetector(t, false, true)
	withUpdateRebuildMode(t, rebuildAuto)

	ctx := updateTestContext()
	applyOptionalServiceFilters(ctx)

	if ctx.servicesToRebuild["local_ai_server"] {
		t.Fatal("automatic update must not rebuild an uninstalled, unselected local_ai_server")
	}
	if got := ctx.skippedServices["local_ai_server"]; got != "rebuild (not installed or selected)" {
		t.Fatalf("unexpected skip reason: %q", got)
	}
}

func TestOptionalLocalAIChangesKeptForStoppedInstalledContainer(t *testing.T) {
	withLocalAIInstallDetector(t, true, true)
	withUpdateRebuildMode(t, rebuildAuto)

	ctx := updateTestContext()
	applyOptionalServiceFilters(ctx)

	if !ctx.servicesToRebuild["local_ai_server"] {
		t.Fatal("a stopped but existing local_ai_server must remain in the rebuild plan")
	}
}

func TestOptionalLocalAIChangesKeptWhenDetectionIsUnavailable(t *testing.T) {
	withLocalAIInstallDetector(t, false, false)
	withUpdateRebuildMode(t, rebuildAuto)

	ctx := updateTestContext()
	applyOptionalServiceFilters(ctx)

	if !ctx.servicesToRebuild["local_ai_server"] {
		t.Fatal("unknown installation state must fail conservatively")
	}
}

func TestRebuildAllStillIncludesLocalAI(t *testing.T) {
	withLocalAIInstallDetector(t, false, true)
	withUpdateRebuildMode(t, rebuildAll)

	ctx := updateTestContext()
	applyOptionalServiceFilters(ctx)

	if !ctx.servicesToRebuild["local_ai_server"] {
		t.Fatal("explicit --rebuild=all must include local_ai_server")
	}
}

func TestDockerActionsBuildStoppedInstalledLocalAIWithoutStartingIt(t *testing.T) {
	commands := withDockerActionState(t, "ai_engine\n", map[string]bool{"local_ai_server": true})
	ctx := updateTestContext()

	if err := applyDockerActions(ctx); err != nil {
		t.Fatalf("applyDockerActions: %v", err)
	}

	if !commandSeen(*commands, "docker compose build local_ai_server") {
		t.Fatalf("stopped Local AI image was not built: %#v", *commands)
	}
	if commandContainingSeen(*commands, "compose up", "local_ai_server") {
		t.Fatalf("stopped Local AI must not be started: %#v", *commands)
	}
}

func TestDockerActionsRebuildRunningLocalAIWithComposeUp(t *testing.T) {
	commands := withDockerActionState(t, "local_ai_server\n", map[string]bool{"local_ai_server": true})
	ctx := updateTestContext()

	if err := applyDockerActions(ctx); err != nil {
		t.Fatalf("applyDockerActions: %v", err)
	}

	if !commandSeen(*commands, "docker compose up -d --build local_ai_server") {
		t.Fatalf("running Local AI was not rebuilt in place: %#v", *commands)
	}
	if commandSeen(*commands, "docker compose build local_ai_server") {
		t.Fatalf("running Local AI must not use the stopped-service build-only path: %#v", *commands)
	}
}

func TestDockerActionsStartSelectedLocalAIWithoutExistingContainer(t *testing.T) {
	commands := withDockerActionState(t, "ai_engine\n", map[string]bool{})
	ctx := updateTestContext()

	if err := applyDockerActions(ctx); err != nil {
		t.Fatalf("applyDockerActions: %v", err)
	}

	if !commandSeen(*commands, "docker compose up -d --build local_ai_server") {
		t.Fatalf("selected Local AI without a container must be started: %#v", *commands)
	}
}

func TestRebuildAllDoesNotStartStoppedInstalledLocalAI(t *testing.T) {
	withUpdateRebuildMode(t, rebuildAll)
	commands := withDockerActionState(t, "ai_engine\n", map[string]bool{"local_ai_server": true})
	ctx := updateTestContext()

	if err := applyDockerActions(ctx); err != nil {
		t.Fatalf("applyDockerActions: %v", err)
	}

	if !commandSeen(*commands, "docker compose build local_ai_server") {
		t.Fatalf("rebuild-all must still refresh the stopped Local AI image: %#v", *commands)
	}
	if commandContainingSeen(*commands, "compose up", "local_ai_server") {
		t.Fatalf("rebuild-all must preserve stopped Local AI state: %#v", *commands)
	}
}

func TestDockerActionsDoNothingForSkippedUnusedLocalAI(t *testing.T) {
	withLocalAIInstallDetector(t, false, true)
	withUpdateRebuildMode(t, rebuildAuto)
	commands := withDockerActionState(t, "ai_engine\n", map[string]bool{})
	ctx := updateTestContext()
	applyOptionalServiceFilters(ctx)

	if err := applyDockerActions(ctx); err != nil {
		t.Fatalf("applyDockerActions: %v", err)
	}

	if len(*commands) != 0 {
		t.Fatalf("unused Local AI should not invoke Docker: %#v", *commands)
	}
}

func TestActiveRouteDetectsLocalFullAgent(t *testing.T) {
	cfg := map[string]any{
		"default_provider": "local",
		"providers": map[string]any{
			"local": map[string]any{"type": "full"},
		},
	}
	if !activeRouteUsesLocalAI(cfg) {
		t.Fatal("local full-agent route should require local_ai_server")
	}
}

func TestActiveRouteDetectsLocalPipelineComponent(t *testing.T) {
	cfg := map[string]any{
		"default_provider": "hosted_pipeline",
		"pipelines": map[string]any{
			"hosted_pipeline": map[string]any{
				"stt": "customer_local_stt",
				"llm": "hosted_llm",
				"tts": "hosted_tts",
			},
		},
		"providers": map[string]any{
			"customer_local_stt": map[string]any{"type": "local"},
			"hosted_llm":         map[string]any{"type": "openai"},
			"hosted_tts":         map[string]any{"type": "elevenlabs"},
		},
	}
	if !activeRouteUsesLocalAI(cfg) {
		t.Fatal("pipeline with a local component should require local_ai_server")
	}
}

func TestHostedActiveRouteDoesNotInstallLocalAI(t *testing.T) {
	cfg := map[string]any{
		"default_provider": "deepgram",
		"active_pipeline":  nil,
		"providers": map[string]any{
			"deepgram":  map[string]any{"type": "full"},
			"local_stt": map[string]any{"type": "local", "enabled": true},
		},
	}
	if activeRouteUsesLocalAI(cfg) {
		t.Fatal("merely declaring an unused local provider must not install local_ai_server")
	}
}

func TestAgentOnlyLocalRouteRequiresLocalAI(t *testing.T) {
	pipelineJSON := `{"pipeline":"agent_local_pipeline"}`
	routes := []activeAgentRoute{
		{Provider: "hosted_profile", ExtraJSON: &pipelineJSON},
	}
	cfg := map[string]any{
		"default_provider": "deepgram",
		"pipelines": map[string]any{
			"agent_local_pipeline": map[string]any{
				"stt": "local_stt",
				"llm": "hosted_llm",
				"tts": "hosted_tts",
			},
		},
		"providers": map[string]any{
			"local_stt":  map[string]any{"type": "local"},
			"hosted_llm": map[string]any{"type": "openai"},
			"hosted_tts": map[string]any{"type": "elevenlabs"},
		},
	}

	usesLocal, known := activeAgentRoutesUseLocalAI(routes, cfg)
	if !known || !usesLocal {
		t.Fatal("an active Agent's local pipeline must require local_ai_server")
	}
}

func TestInvalidActiveAgentExtraJSONKeepsLocalAIConservatively(t *testing.T) {
	invalidJSON := `{"pipeline":`
	routes := []activeAgentRoute{{Provider: "deepgram", ExtraJSON: &invalidJSON}}
	cfg := map[string]any{
		"providers": map[string]any{
			"deepgram": map[string]any{"type": "full"},
		},
	}

	usesLocal, known := activeAgentRoutesUseLocalAI(routes, cfg)
	if known || usesLocal {
		t.Fatal("invalid active Agent JSON must make installation state unknown")
	}
}

func TestReadActiveAgentRoutesUsesReadOnlySQLiteSubprocess(t *testing.T) {
	withAIEngineRunningForActiveRouteRead(t, false)
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("python3 is required for the updater's SQLite route reader")
	}
	root := chdirTemp(t)
	dbPath := filepath.Join(root, "data", "operator", "agents.db")
	if err := os.MkdirAll(filepath.Dir(dbPath), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	createScript := `
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("CREATE TABLE agents (provider TEXT, extra_json TEXT, is_active INTEGER)")
db.execute("INSERT INTO agents VALUES (?, ?, 1)", ("deepgram", '{"pipeline":"local_only"}'))
db.execute("INSERT INTO agents VALUES (?, ?, 0)", ("local", None))
db.commit(); db.close()
`
	if out, err := exec.Command(python, "-c", createScript, dbPath).CombinedOutput(); err != nil {
		t.Fatalf("create sqlite fixture: %v (%s)", err, out)
	}

	routes, known := readActiveAgentRoutes()
	if !known {
		t.Fatal("valid agents.db should be readable")
	}
	if len(routes) != 1 || routes[0].Provider != "deepgram" || routes[0].ExtraJSON == nil {
		t.Fatalf("unexpected active routes: %#v", routes)
	}
}

func TestActiveAgentRouteReaderPrefersRunningEngineOverMissingHostDB(t *testing.T) {
	withAIEngineRunningForActiveRouteRead(t, true)

	dbPath := filepath.Join(t.TempDir(), "missing", "agents.db")
	cmd, known := activeAgentRouteReadCommand(dbPath, "route-reader-script")
	if !known || cmd == nil {
		t.Fatal("a running engine must be queried even when the host-relative database is absent")
	}
	want := []string{"docker", "exec", "ai_engine", "python3", "-c", "route-reader-script"}
	if len(cmd.Args) != len(want) {
		t.Fatalf("unexpected command args: %#v", cmd.Args)
	}
	for i := range want {
		if cmd.Args[i] != want[i] {
			t.Fatalf("unexpected command args: %#v", cmd.Args)
		}
	}
}
