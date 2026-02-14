package main

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/hkjarral/asterisk-ai-voice-agent/cli/internal/check"
	"github.com/hkjarral/asterisk-ai-voice-agent/cli/internal/configmerge"
)

type fixSummary struct {
	repoRoot     string
	prefixBackup string
	sourceBackup string
	restored     []string
	warnings     []string
}

type backupRestoreResult struct {
	restored      int
	coreRestored  bool
	restoredPaths []string
	warnings      []string
}

func runCheckWithFix() (int, error) {
	// 1) Baseline diagnostics first (always show operators what failed before fix).
	runner := check.NewRunner(verbose, version, buildTime)
	before, beforeErr := runner.Run()
	if before == nil {
		before = &check.Report{
			Version:   version,
			BuildTime: buildTime,
			Timestamp: time.Now(),
			Items: []check.Item{
				{
					Name:    "agent check",
					Status:  check.StatusFail,
					Message: "failed to generate diagnostics report",
					Details: func() string {
						if beforeErr != nil {
							return beforeErr.Error()
						}
						return "unknown error"
					}(),
				},
			},
		}
	}
	before.OutputText(os.Stdout)

	noIssues := beforeErr == nil && before.FailCount == 0 && before.WarnCount == 0
	if noIssues {
		fmt.Println("No issues detected. No recovery actions needed.")
		return 0, nil
	}

	fmt.Println("Attempting automatic recovery from recent backups...")
	summary, fixErr := runBackupRecovery()
	if summary != nil {
		printFixSummary(summary)
	}
	if fixErr != nil {
		return 2, fixErr
	}

	// Give services a moment to transition after compose restart/up.
	time.Sleep(2 * time.Second)

	fmt.Println("")
	fmt.Println("Re-running diagnostics after fix...")
	after, afterErr := runner.Run()
	if after == nil {
		return 2, errors.New("post-fix diagnostics failed: report unavailable")
	}
	after.OutputText(os.Stdout)

	if afterErr != nil || after.FailCount > 0 {
		return 2, nil
	}
	if after.WarnCount > 0 {
		return 1, nil
	}
	return 0, nil
}

func runBackupRecovery() (*fixSummary, error) {
	repoRoot, err := resolveRepoRootForFix()
	if err != nil {
		return nil, err
	}
	if err := os.Chdir(repoRoot); err != nil {
		return nil, fmt.Errorf("failed to switch to repo root: %w", err)
	}

	summary := &fixSummary{repoRoot: repoRoot}

	// Safety: snapshot current operator state before touching anything.
	ts := time.Now().UTC().Format("20060102_150405")
	prefixBackup := filepath.Join(repoRoot, ".agent", "check-fix-backups", ts)
	if err := os.MkdirAll(prefixBackup, 0o755); err != nil {
		return summary, fmt.Errorf("failed to create pre-fix backup directory: %w", err)
	}
	summary.prefixBackup = prefixBackup
	for _, rel := range []string{
		".env",
		filepath.Join("config", "ai-agent.yaml"),
		filepath.Join("config", "ai-agent.local.yaml"),
		filepath.Join("config", "users.json"),
		filepath.Join("config", "contexts"),
	} {
		if err := backupPathIfExists(rel, prefixBackup); err != nil {
			return summary, fmt.Errorf("failed to snapshot current state (%s): %w", rel, err)
		}
	}

	restored, source, restoredPaths, warns, err := restoreFromUpdateBackups()
	summary.warnings = append(summary.warnings, warns...)
	if err == nil && restored > 0 {
		summary.sourceBackup = source
		summary.restored = append(summary.restored, restoredPaths...)
	} else {
		// Fallback to Admin UI style per-file *.bak snapshots when update backups are unavailable.
		restored, source, restoredPaths, warns, err = restoreFromFileBackups()
		summary.warnings = append(summary.warnings, warns...)
		if err == nil && restored > 0 {
			summary.sourceBackup = source
			summary.restored = append(summary.restored, restoredPaths...)
		}
	}
	if err != nil {
		return summary, err
	}

	if len(summary.restored) == 0 {
		return summary, errors.New("no restorable backup files found")
	}

	if err := restartCoreServices(); err != nil {
		return summary, err
	}
	return summary, nil
}

func resolveRepoRootForFix() (string, error) {
	root, err := gitShowTopLevel()
	if err == nil && strings.TrimSpace(root) != "" {
		return root, nil
	}
	wd, wdErr := os.Getwd()
	if wdErr != nil {
		return "", fmt.Errorf("unable to resolve repository root: %w", wdErr)
	}
	return wd, nil
}

func restoreFromUpdateBackups() (int, string, []string, []string, error) {
	backupRoot := filepath.Join(".agent", "update-backups")
	entries, err := os.ReadDir(backupRoot)
	if err != nil {
		if os.IsNotExist(err) {
			return 0, "", nil, nil, errors.New("no update backup directories found")
		}
		return 0, "", nil, nil, fmt.Errorf("failed to read update backup root: %w", err)
	}

	type dirInfo struct {
		path string
		mt   time.Time
	}
	dirs := make([]dirInfo, 0, len(entries))
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		full := filepath.Join(backupRoot, e.Name())
		info, statErr := os.Stat(full)
		if statErr != nil {
			continue
		}
		dirs = append(dirs, dirInfo{path: full, mt: info.ModTime()})
	}
	if len(dirs) == 0 {
		return 0, "", nil, nil, errors.New("no update backup directories found")
	}
	sort.Slice(dirs, func(i, j int) bool { return dirs[i].mt.After(dirs[j].mt) })

	var warnings []string
	restoreBase := shouldRestoreBaseConfig()
	for _, candidate := range dirs {
		result := restoreFromSingleBackupDir(candidate.path, restoreBase)
		warnings = append(warnings, result.warnings...)
		if result.restored == 0 {
			continue
		}
		if result.coreRestored {
			return result.restored, candidate.path, result.restoredPaths, warnings, nil
		}
	}
	return 0, "", nil, warnings, errors.New("no usable update backup directory found")
}

func restoreFromSingleBackupDir(backupDir string, restoreBase bool) backupRestoreResult {
	result := backupRestoreResult{}

	needEnv := !fileValid(".env", validateEnvBackup)
	needLocal := !fileValid(filepath.Join("config", "ai-agent.local.yaml"), validateYAMLMappingBackup)
	needBase := restoreBase && !fileValid(filepath.Join("config", "ai-agent.yaml"), validateYAMLMappingBackup)

	backupEnvOK := backupFileValid(backupDir, ".env", validateEnvBackup)
	backupLocalOK := backupFileValid(backupDir, filepath.Join("config", "ai-agent.local.yaml"), validateYAMLMappingBackup)
	backupBaseOK := backupFileValid(backupDir, filepath.Join("config", "ai-agent.yaml"), validateYAMLMappingBackup)

	envOkAfter := !needEnv || backupEnvOK
	localOkAfter := !needLocal || backupLocalOK
	baseOkAfter := !needBase || backupBaseOK

	// Avoid partial writes: only restore from this candidate when it can produce a viable core config.
	if !envOkAfter || !(localOkAfter || baseOkAfter) {
		return result
	}

	restoreFile := func(rel string, validate func(string) error, allow bool) {
		if !allow {
			return
		}
		src := filepath.Join(backupDir, rel)
		if _, err := os.Stat(src); err != nil {
			return
		}
		if validate != nil {
			if err := validate(src); err != nil {
				result.warnings = append(result.warnings, fmt.Sprintf("Skipped %s from %s: %v", rel, backupDir, err))
				return
			}
		}
		if err := copyFile(src, rel); err != nil {
			result.warnings = append(result.warnings, fmt.Sprintf("Failed to restore %s from %s: %v", rel, backupDir, err))
			return
		}
		result.restored++
		result.restoredPaths = append(result.restoredPaths, rel)
	}

	restoreFile(".env", validateEnvBackup, needEnv)
	restoreFile(filepath.Join("config", "ai-agent.local.yaml"), validateYAMLMappingBackup, needLocal)
	restoreFile(filepath.Join("config", "ai-agent.yaml"), validateYAMLMappingBackup, needBase)
	restoreFile(filepath.Join("config", "users.json"), nil, !fileExists(filepath.Join("config", "users.json")))

	srcCtx := filepath.Join(backupDir, "config", "contexts")
	if info, err := os.Stat(srcCtx); err == nil && info.IsDir() {
		dstCtx := filepath.Join("config", "contexts")
		restoreContextsAtomic(srcCtx, dstCtx, &result)
	}

	result.coreRestored = fileValid(".env", validateEnvBackup) &&
		(fileValid(filepath.Join("config", "ai-agent.local.yaml"), validateYAMLMappingBackup) ||
			fileValid(filepath.Join("config", "ai-agent.yaml"), validateYAMLMappingBackup))
	return result
}

func restoreFromFileBackups() (int, string, []string, []string, error) {
	var warnings []string
	restored := 0
	var restoredPaths []string
	sources := map[string]bool{}

	needEnv := !fileValid(".env", validateEnvBackup)
	needLocal := !fileValid(filepath.Join("config", "ai-agent.local.yaml"), validateYAMLMappingBackup)
	restoreBase := shouldRestoreBaseConfig()
	needBase := restoreBase && !fileValid(filepath.Join("config", "ai-agent.yaml"), validateYAMLMappingBackup)
	needUsers := !fileExists(filepath.Join("config", "users.json"))

	findLatestValidated := func(rel string, pattern string, validate func(string) error) string {
		src, err := latestBackupMatch(pattern)
		if err != nil {
			warnings = append(warnings, err.Error())
			return ""
		}
		if src == "" {
			return ""
		}
		if validate != nil {
			if err := validate(src); err != nil {
				warnings = append(warnings, fmt.Sprintf("Skipped %s from %s: %v", rel, src, err))
				return ""
			}
		}
		return src
	}

	envSrc := ""
	if needEnv {
		envSrc = findLatestValidated(".env", ".env.bak.*", validateEnvBackup)
	}
	localSrc := ""
	if needLocal {
		localSrc = findLatestValidated(filepath.Join("config", "ai-agent.local.yaml"), filepath.Join("config", "ai-agent.local.yaml.bak.*"), validateYAMLMappingBackup)
	}
	baseSrc := ""
	if needBase {
		baseSrc = findLatestValidated(filepath.Join("config", "ai-agent.yaml"), filepath.Join("config", "ai-agent.yaml.bak.*"), validateYAMLMappingBackup)
	}
	usersSrc := ""
	if needUsers {
		usersSrc = findLatestValidated(filepath.Join("config", "users.json"), filepath.Join("config", "users.json.bak.*"), nil)
	}

	envOkAfter := !needEnv || envSrc != ""
	localOkAfter := !needLocal || localSrc != ""
	baseOkAfter := !needBase || baseSrc != ""
	if !envOkAfter || !(localOkAfter || baseOkAfter) {
		return 0, "", nil, warnings, errors.New("no usable backup files found (missing core files)")
	}

	restoreFromSrc := func(src string, rel string) {
		if src == "" {
			return
		}
		if err := copyFile(src, rel); err != nil {
			warnings = append(warnings, fmt.Sprintf("Failed to restore %s from %s: %v", rel, src, err))
			return
		}
		restored++
		restoredPaths = append(restoredPaths, rel)
		sources[filepath.Dir(src)] = true
	}

	restoreFromSrc(envSrc, ".env")
	restoreFromSrc(localSrc, filepath.Join("config", "ai-agent.local.yaml"))
	restoreFromSrc(baseSrc, filepath.Join("config", "ai-agent.yaml"))
	restoreFromSrc(usersSrc, filepath.Join("config", "users.json"))

	if restored == 0 {
		return 0, "", nil, warnings, errors.New("no usable backup files found")
	}
	if !(fileValid(".env", validateEnvBackup) &&
		(fileValid(filepath.Join("config", "ai-agent.local.yaml"), validateYAMLMappingBackup) ||
			fileValid(filepath.Join("config", "ai-agent.yaml"), validateYAMLMappingBackup))) {
		return 0, "", nil, warnings, errors.New("no usable backup files found (missing core files)")
	}
	sourceList := sortedKeys(sources)
	return restored, strings.Join(sourceList, ", "), restoredPaths, warnings, nil
}

func fileExists(rel string) bool {
	_, err := os.Stat(rel)
	return err == nil
}

func fileValid(rel string, validate func(string) error) bool {
	if validate == nil {
		return fileExists(rel)
	}
	if _, err := os.Stat(rel); err != nil {
		return false
	}
	return validate(rel) == nil
}

func backupFileValid(backupDir string, rel string, validate func(string) error) bool {
	src := filepath.Join(backupDir, rel)
	if _, err := os.Stat(src); err != nil {
		return false
	}
	if validate == nil {
		return true
	}
	return validate(src) == nil
}

func restoreContextsAtomic(srcCtx string, dstCtx string, result *backupRestoreResult) {
	tmpCtx := filepath.Join("config", fmt.Sprintf(".contexts.restore.tmp.%d", time.Now().UnixNano()))
	backupCtx := filepath.Join("config", fmt.Sprintf("contexts.pre_restore.%d", time.Now().UnixNano()))

	if err := copyDir(srcCtx, tmpCtx); err != nil {
		result.warnings = append(result.warnings, fmt.Sprintf("Failed to stage config/contexts restore from %s: %v", srcCtx, err))
		_ = os.RemoveAll(tmpCtx)
		return
	}

	if info, err := os.Stat(dstCtx); err == nil && info.IsDir() {
		if err := os.Rename(dstCtx, backupCtx); err != nil {
			result.warnings = append(result.warnings, fmt.Sprintf("Failed to backup existing config/contexts before restore: %v", err))
			_ = os.RemoveAll(tmpCtx)
			return
		}
		result.warnings = append(result.warnings, fmt.Sprintf("Moved existing config/contexts to %s", backupCtx))
	}

	if err := os.Rename(tmpCtx, dstCtx); err != nil {
		result.warnings = append(result.warnings, fmt.Sprintf("Failed to activate restored config/contexts: %v", err))
		if info, err2 := os.Stat(backupCtx); err2 == nil && info.IsDir() {
			_ = os.Rename(backupCtx, dstCtx)
		}
		_ = os.RemoveAll(tmpCtx)
		return
	}

	result.restored++
	result.restoredPaths = append(result.restoredPaths, filepath.Join("config", "contexts"))
	result.warnings = append(result.warnings, fmt.Sprintf("Restored config/contexts from %s", srcCtx))
}

func latestBackupMatch(pattern string) (string, error) {
	matches, err := filepath.Glob(pattern)
	if err != nil {
		return "", fmt.Errorf("invalid backup glob pattern %s: %w", pattern, err)
	}
	if len(matches) == 0 {
		return "", nil
	}
	type matchInfo struct {
		path string
		mt   time.Time
	}
	infos := make([]matchInfo, 0, len(matches))
	for _, m := range matches {
		st, statErr := os.Stat(m)
		if statErr != nil {
			continue
		}
		infos = append(infos, matchInfo{path: m, mt: st.ModTime()})
	}
	if len(infos) == 0 {
		return "", nil
	}
	sort.Slice(infos, func(i, j int) bool { return infos[i].mt.After(infos[j].mt) })
	return infos[0].path, nil
}

func validateYAMLMappingBackup(path string) error {
	if hasConflictMarkers(path) {
		return errors.New("contains git conflict markers")
	}
	if _, err := configmerge.ReadYAMLFile(path); err != nil {
		return fmt.Errorf("invalid YAML mapping: %w", err)
	}
	return nil
}

func validateEnvBackup(path string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	content := string(data)
	if strings.TrimSpace(content) == "" {
		return errors.New("empty env file")
	}
	// Keep this lightweight: for recovery we only require core ARI keys.
	if !strings.Contains(content, "ASTERISK_HOST=") || !strings.Contains(content, "ASTERISK_ARI_USERNAME=") {
		return errors.New("missing core ARI keys")
	}
	return nil
}

func hasConflictMarkers(path string) bool {
	data, err := os.ReadFile(path)
	if err != nil {
		return false
	}
	hasOpen := false
	hasSep := false
	hasClose := false
	for _, line := range strings.Split(string(data), "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "<<<<<<<") {
			hasOpen = true
		} else if strings.HasPrefix(trimmed, "=======") {
			hasSep = true
		} else if strings.HasPrefix(trimmed, ">>>>>>>") {
			hasClose = true
		}

		// Only signal a conflict when the marker pattern is plausibly present.
		// A standalone "=======" line can occur legitimately (e.g., separators in YAML),
		// but Git conflicts require marker combinations.
		if hasOpen && (hasSep || hasClose) {
			return true
		}
		if hasSep && hasClose {
			return true
		}
	}
	return false
}

func shouldRestoreBaseConfig() bool {
	base := filepath.Join("config", "ai-agent.yaml")
	if _, err := os.Stat(base); err != nil {
		return true
	}
	if hasConflictMarkers(base) {
		return true
	}
	if _, err := configmerge.ReadYAMLFile(base); err != nil {
		return true
	}
	return false
}

func restartCoreServices() error {
	if _, err := runCmd("docker", "compose", "version"); err != nil {
		return fmt.Errorf("docker compose unavailable: %w", err)
	}

	if _, err := runCmd("docker", "compose", "up", "-d", "--no-build", "ai_engine", "admin_ui"); err == nil {
		return nil
	}

	// Fallback path: restart each service and attempt up if restart fails.
	for _, svc := range []string{"ai_engine", "admin_ui"} {
		if _, err := runCmd("docker", "compose", "restart", svc); err != nil {
			if _, err2 := runCmd("docker", "compose", "up", "-d", "--no-build", svc); err2 != nil {
				return fmt.Errorf("failed to restart %s (restart error: %v; up error: %w)", svc, err, err2)
			}
		}
	}
	return nil
}

func printFixSummary(summary *fixSummary) {
	fmt.Println("")
	fmt.Println("Recovery summary")
	fmt.Printf("  Repo root: %s\n", summary.repoRoot)
	if summary.prefixBackup != "" {
		fmt.Printf("  Pre-fix snapshot: %s\n", summary.prefixBackup)
	}
	if summary.sourceBackup != "" {
		fmt.Printf("  Restored from: %s\n", summary.sourceBackup)
	}
	if len(summary.restored) > 0 {
		fmt.Printf("  Restored paths: %s\n", strings.Join(summary.restored, ", "))
	}
	for _, w := range summary.warnings {
		fmt.Printf("  Warning: %s\n", w)
	}
}
