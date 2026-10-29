package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// Contains no config or credentials. Written under the existing update lock,
// before checkout, and removed only after all Docker actions succeed.
type pendingDockerActions struct {
	Version        int      `json:"version"`
	BaseSHA        string   `json:"base_sha"`
	TargetSHA      string   `json:"target_sha"`
	Rebuild        []string `json:"rebuild"`
	Restart        []string `json:"restart"`
	ComposeChanged bool     `json:"compose_changed"`
	Running        []string `json:"running"`
}

func pendingDockerPath(repoRoot string) string {
	return filepath.Join(repoRoot, ".agent", "updates", "pending-docker.json")
}

func readPendingDockerActions(repoRoot string) (*pendingDockerActions, error) {
	path := pendingDockerPath(repoRoot)
	info, err := os.Lstat(path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() || info.Size() > 16384 {
		return nil, fmt.Errorf("invalid pending Docker state at %s; inspect host CLI recovery state", path)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var pending pendingDockerActions
	if err := json.Unmarshal(data, &pending); err != nil {
		return nil, fmt.Errorf("cannot read pending Docker state at %s: %w", path, err)
	}
	if pending.Version != 1 || !validUpdateSHA(pending.BaseSHA) || !validUpdateSHA(pending.TargetSHA) {
		return nil, fmt.Errorf("invalid pending Docker state at %s; inspect host CLI recovery state", path)
	}
	services := append(append(append([]string{}, pending.Rebuild...), pending.Restart...), pending.Running...)
	for _, service := range services {
		if service != "ai_engine" && service != "admin_ui" && service != "local_ai_server" {
			return nil, fmt.Errorf("unknown service in pending Docker state at %s", path)
		}
	}
	return &pending, nil
}

func validUpdateSHA(sha string) bool {
	if len(sha) != 40 && len(sha) != 64 {
		return false
	}
	for _, char := range sha {
		if !(char >= '0' && char <= '9' || char >= 'a' && char <= 'f') {
			return false
		}
	}
	return true
}

func planDockerActions(ctx *updateContext) error {
	// Explicit --rebuild=all applies even when HEAD already equals the target.
	decideDockerActions(ctx)
	pending, err := readPendingDockerActions(ctx.repoRoot)
	if err != nil {
		return err
	}
	if pending != nil {
		ctx.pendingDocker = true
		// A manual branch switch/reset can make the saved actions unrelated to
		// this checkout. Never silently consume that recovery record.
		if ctx.oldSHA != pending.BaseSHA && ctx.oldSHA != pending.TargetSHA {
			return fmt.Errorf("pending Docker actions belong to another checkout revision; inspect %s and complete host CLI recovery before updating", pendingDockerPath(ctx.repoRoot))
		}
		if rebuildMode(strings.ToLower(strings.TrimSpace(updateRebuild))) == rebuildNone && len(pending.Rebuild) > 0 {
			return errors.New("pending Docker rebuilds cannot be completed with --rebuild=none; retry with --rebuild=auto or all")
		}
		if !updateIncludeUI && (containsString(pending.Rebuild, "admin_ui") || containsString(pending.Restart, "admin_ui")) {
			return errors.New("pending Admin UI actions require --include-ui=true to complete the interrupted update")
		}
		for _, service := range pending.Rebuild {
			ctx.servicesToRebuild[service] = true
		}
		for _, service := range pending.Restart {
			ctx.servicesToRestart[service] = true
		}
		ctx.composeChanged = ctx.composeChanged || pending.ComposeChanged
		ctx.runningBeforeUpdate = map[string]bool{}
		for _, service := range pending.Running {
			ctx.runningBeforeUpdate[service] = true
		}
	}
	for service := range ctx.servicesToRebuild {
		delete(ctx.servicesToRestart, service)
	}
	applyServiceFilters(ctx)
	applyOptionalServiceFilters(ctx)
	return nil
}

func savePendingDockerActions(ctx *updateContext) error {
	if !updateHasDockerChanges(ctx) {
		return nil
	}
	pending := pendingDockerActions{
		Version: 1, BaseSHA: ctx.oldSHA, TargetSHA: ctx.newSHA,
		Rebuild: sortedKeys(ctx.servicesToRebuild), Restart: sortedKeys(ctx.servicesToRestart),
		ComposeChanged: ctx.composeChanged,
		Running:        sortedKeys(ctx.runningBeforeUpdate),
	}
	data, err := json.MarshalIndent(pending, "", "  ")
	if err != nil {
		return err
	}
	path := pendingDockerPath(ctx.repoRoot)
	file, err := os.CreateTemp(filepath.Dir(path), ".pending-docker-*")
	if err != nil {
		return fmt.Errorf("cannot journal Docker actions before checkout: %w", err)
	}
	defer os.Remove(file.Name())
	_, writeErr := file.Write(data)
	if writeErr == nil {
		writeErr = file.Sync()
	}
	closeErr := file.Close()
	if writeErr != nil {
		return writeErr
	}
	if closeErr != nil {
		return closeErr
	}
	if err := os.Rename(file.Name(), path); err != nil {
		return fmt.Errorf("cannot journal Docker actions before checkout: %w", err)
	}
	return nil
}

func captureRunningDockerServices(ctx *updateContext) error {
	if ctx.runningBeforeUpdate == nil {
		ctx.runningBeforeUpdate = map[string]bool{}
	}
	out, err := runDockerCommandForUpdate("docker", "compose", "ps", "--services", "--status", "running")
	if err != nil {
		out, err = runDockerCommandForUpdate("docker", "compose", "ps", "--services")
	}
	if err != nil {
		return fmt.Errorf("cannot snapshot running services before checkout; Docker actions were not applied: %w", err)
	}
	for _, service := range strings.Fields(out) {
		if service == "ai_engine" || service == "admin_ui" || service == "local_ai_server" {
			ctx.runningBeforeUpdate[service] = true
		}
	}
	return nil
}

func clearPendingDockerActions(repoRoot string) error {
	err := os.Remove(pendingDockerPath(repoRoot))
	if err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("Docker actions completed but pending state could not be removed: %w", err)
	}
	return nil
}

func hostNetworkEntitlementError(output string, err error) bool {
	if err == nil {
		return false
	}
	// Verbose command execution streams diagnostics and returns only the exit
	// status in err; non-verbose execution also embeds the output in err.
	diagnostics := output + "\n" + err.Error()
	return strings.Contains(diagnostics, "additional privileges requested") &&
		strings.Contains(diagnostics, "--allow=network.host")
}

var runDockerBakeForUpdate = func(definition string) (string, error) {
	return runCmdWithInput(strings.NewReader(definition), "docker", "buildx", "bake",
		"--file", "-", "--allow=network.host", "--load")
}

var exportDockerBakeForUpdate = func(services []string) (string, error) {
	args := append([]string{"compose", "build", "--print"}, services...)
	cmd := exec.Command("docker", args...)
	var diagnostics bytes.Buffer
	cmd.Stderr = &diagnostics
	// Keep warnings on stderr out of the machine-readable Bake definition.
	output, err := cmd.Output()
	if err != nil {
		return "", fmt.Errorf("%w: %s", err, strings.TrimSpace(diagnostics.String()))
	}
	return string(output), nil
}

func buildWithHostNetworkGrant(services []string) error {
	printUpdateInfo("Compose did not grant host-network build access; retrying Bake with --allow=network.host")
	definition, err := exportDockerBakeForUpdate(services)
	if err != nil {
		return fmt.Errorf("cannot export Compose build for host-network recovery (check docker compose/buildx versions): %w", err)
	}
	// --print must produce a Bake document, not a warning or partial output.
	if !json.Valid([]byte(definition)) {
		return errors.New("Compose build --print did not produce valid JSON; check docker compose/buildx versions")
	}
	if _, err := runDockerBakeForUpdate(definition); err != nil {
		return fmt.Errorf("Bake host-network build failed; pending Docker actions are retained for retry (check docker compose/buildx versions): %w", err)
	}
	return nil
}
