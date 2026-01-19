package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/hkjarral/asterisk-ai-voice-agent/cli/internal/check"
	"github.com/spf13/cobra"
)

type rebuildMode string

const (
	rebuildAuto rebuildMode = "auto"
	rebuildNone rebuildMode = "none"
	rebuildAll  rebuildMode = "all"
)

var (
	updateRemote        string
	updateRef           string
	updateNoStash       bool
	updateStashUntracked bool
	updateRebuild       string
	updateForceRecreate bool
	updateSkipCheck     bool
	updateSelfUpdate    bool
	gitSafeDirectory    string
)

var updateCmd = &cobra.Command{
	Use:   "update",
	Short: "Pull latest code and apply updates",
	Long: `Update Asterisk AI Voice Agent to the latest code and apply changes safely.

This command:
  - Backs up operator-owned config (.env, config/ai-agent.yaml, config/contexts/)
  - Safely fast-forwards to origin/main (no forced merges by default)
  - Preserves local tracked changes using git stash (optional)
  - Rebuilds/restarts only the containers impacted by the change set
  - Verifies success by running agent check (optional)

Safety notes:
  - No hard resets are performed.
  - Fast-forward only: if your branch has diverged, the update stops with guidance.`,
	RunE: func(cmd *cobra.Command, args []string) error {
		return runUpdate()
	},
}

func init() {
	updateCmd.Flags().StringVar(&updateRemote, "remote", "origin", "git remote name")
	updateCmd.Flags().StringVar(&updateRef, "ref", "main", "git ref/branch to update to (e.g., main)")
	updateCmd.Flags().BoolVar(&updateNoStash, "no-stash", false, "abort if repo has local changes instead of stashing")
	updateCmd.Flags().BoolVar(&updateStashUntracked, "stash-untracked", false, "include untracked files when stashing (does not include ignored files)")
	updateCmd.Flags().StringVar(&updateRebuild, "rebuild", string(rebuildAuto), "rebuild mode: auto|none|all")
	updateCmd.Flags().BoolVar(&updateForceRecreate, "force-recreate", false, "force recreate containers during docker compose up")
	updateCmd.Flags().BoolVar(&updateSkipCheck, "skip-check", false, "skip running agent check after update")
	updateCmd.Flags().BoolVar(&updateSelfUpdate, "self-update", true, "auto-update the agent CLI binary if a newer release is available")
	rootCmd.AddCommand(updateCmd)
}

type updateContext struct {
	repoRoot string
	oldSHA   string
	newSHA   string
	backupDir string
	stashed  bool
	stashRef string

	changedFiles []string

	servicesToRebuild map[string]bool
	servicesToRestart map[string]bool
	composeChanged    bool
}

func runUpdate() error {
	printUpdateStep("Preparing update")
	if updateSelfUpdate {
		maybeSelfUpdateAndReexec()
	}

	repoRoot, err := gitShowTopLevel()
	if err != nil {
		return err
	}
	if err := os.Chdir(repoRoot); err != nil {
		return fmt.Errorf("failed to chdir to repo root: %w", err)
	}

	ctx := &updateContext{
		repoRoot:          repoRoot,
		servicesToRebuild: map[string]bool{},
		servicesToRestart: map[string]bool{},
	}

	ctx.oldSHA, err = gitRevParse("HEAD")
	if err != nil {
		return err
	}

	printUpdateStep("Creating backups")
	if err := createUpdateBackups(ctx); err != nil {
		return err
	}

	printUpdateStep("Checking working tree")
	dirty, err := gitIsDirty(updateStashUntracked)
	if err != nil {
		return err
	}
	if dirty {
		if updateNoStash {
			return errors.New("working tree has local changes; re-run without --no-stash or commit your changes first")
		}
		printUpdateInfo("Working tree is dirty; stashing changes")
		if err := gitStash(ctx, updateStashUntracked); err != nil {
			return err
		}
	}

	printUpdateStep(fmt.Sprintf("Fetching %s/%s", updateRemote, updateRef))
	if err := gitFetch(updateRemote, updateRef); err != nil {
		return err
	}
	ctx.newSHA, err = gitRevParse(fmt.Sprintf("%s/%s", updateRemote, updateRef))
	if err != nil {
		return err
	}

	if ctx.newSHA == ctx.oldSHA {
		printUpdateInfo("Already up to date (%s)", shortSHA(ctx.oldSHA))
		if ctx.stashed {
			printUpdateStep("Restoring stashed changes")
			if err := gitStashPop(ctx); err != nil {
				return err
			}
		}
		if updateSkipCheck {
			printUpdateSummary(ctx, "", 0, 0)
			return nil
		}

		printUpdateStep("Running agent check")
		report, status, warnCount, failCount, err := runPostUpdateCheck()
		printPostUpdateCheck(report, warnCount, failCount)
		printUpdateSummary(ctx, status, warnCount, failCount)
		if err != nil {
			return err
		}
		if failCount > 0 {
			return errors.New("post-update check reported failures")
		}
		return nil
	}

	printUpdateStep("Fast-forwarding code")
	if err := gitMergeFastForward(fmt.Sprintf("%s/%s", updateRemote, updateRef)); err != nil {
		return err
	}

	if ctx.stashed {
		printUpdateStep("Restoring stashed changes")
		if err := gitStashPop(ctx); err != nil {
			return err
		}
	}

	ctx.changedFiles, err = gitDiffNames(ctx.oldSHA, ctx.newSHA)
	if err != nil {
		return err
	}
	decideDockerActions(ctx)

	printUpdateStep("Applying Docker changes")
	printDockerActionsPlanned(ctx)
	if err := applyDockerActions(ctx); err != nil {
		return err
	}

	if updateSkipCheck {
		printUpdateSummary(ctx, "", 0, 0)
		return nil
	}

	printUpdateStep("Running agent check")
	report, status, warnCount, failCount, err := runPostUpdateCheck()
	printPostUpdateCheck(report, warnCount, failCount)
	printUpdateSummary(ctx, status, warnCount, failCount)
	if err != nil {
		return err
	}
	if failCount > 0 {
		return errors.New("post-update check reported failures")
	}
	return nil
}

func maybeSelfUpdateAndReexec() {
	// Avoid infinite loops if we successfully replaced ourselves and re-exec'd.
	if os.Getenv("AAVA_AGENT_SKIP_SELF_UPDATE") == "1" {
		return
	}
	if runtime.GOOS == "windows" {
		// Windows in-place replacement is unreliable (binary-in-use); fall back to the installer hint.
		printSelfUpdateHint()
		return
	}

	current := strings.TrimSpace(version)
	if !strings.HasPrefix(strings.ToLower(current), "v") {
		// dev builds: best-effort hint only
		printSelfUpdateHint()
		return
	}

	latest, err := fetchLatestReleaseTag(context.Background(), "hkjarral/Asterisk-AI-Voice-Agent")
	if err != nil || latest == "" {
		return
	}
	if compareSemver(current, latest) >= 0 {
		return
	}

	exePath, err := os.Executable()
	if err != nil || exePath == "" {
		printSelfUpdateHint()
		return
	}
	if resolved, err := filepath.EvalSymlinks(exePath); err == nil && resolved != "" {
		exePath = resolved
	}

	binName, ok := releaseBinaryName(runtime.GOOS, runtime.GOARCH)
	if !ok {
		printSelfUpdateHint()
		return
	}

	if err := selfUpdateFromGitHubRelease(latest, binName, exePath); err != nil {
		printSelfUpdateHint()
		return
	}

	// Re-exec into the updated binary so the rest of `agent update` runs the newest logic.
	env := append(os.Environ(), "AAVA_AGENT_SKIP_SELF_UPDATE=1")
	args := append([]string{exePath}, os.Args[1:]...)
	_ = syscall.Exec(exePath, args, env)
}

func releaseBinaryName(goos string, goarch string) (string, bool) {
	switch goos {
	case "linux":
		switch goarch {
		case "amd64":
			return "agent-linux-amd64", true
		case "arm64":
			return "agent-linux-arm64", true
		}
	case "darwin":
		switch goarch {
		case "amd64":
			return "agent-darwin-amd64", true
		case "arm64":
			return "agent-darwin-arm64", true
		}
	case "windows":
		if goarch == "amd64" {
			return "agent-windows-amd64.exe", true
		}
	}
	return "", false
}

func selfUpdateFromGitHubRelease(tag string, binName string, installPath string) error {
	installDir := filepath.Dir(installPath)
	if installDir == "" {
		return errors.New("invalid install path")
	}
	if err := ensureWritableDir(installDir); err != nil {
		return err
	}

	base := fmt.Sprintf("https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/download/%s", tag)
	binURL := base + "/" + binName
	sumsURL := base + "/SHA256SUMS"

	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
	defer cancel()

	sums, err := httpGetBytes(ctx, sumsURL)
	if err != nil {
		return err
	}
	expected, err := parseSHA256SUMS(sums, binName)
	if err != nil {
		return err
	}

	payload, err := httpGetBytes(ctx, binURL)
	if err != nil {
		return err
	}
	actual := fmt.Sprintf("%x", sha256.Sum256(payload))
	if !strings.EqualFold(actual, expected) {
		return fmt.Errorf("checksum mismatch for %s", binName)
	}

	// Backup existing binary (best-effort).
	if _, err := os.Stat(installPath); err == nil {
		bak := filepath.Join(installDir, "agent.bak."+time.Now().UTC().Format("20060102_150405"))
		_ = copyFile(installPath, bak)
	}

	tmp := filepath.Join(installDir, ".agent.new."+strconv.Itoa(os.Getpid()))
	if err := os.WriteFile(tmp, payload, 0o755); err != nil {
		return err
	}
	_ = os.Chmod(tmp, 0o755)

	if err := os.Rename(tmp, installPath); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return nil
}

func httpGetBytes(ctx context.Context, url string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "aava-agent-cli")
	client := &http.Client{Timeout: 25 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("GET %s failed: %s", url, resp.Status)
	}
	return io.ReadAll(resp.Body)
}

func parseSHA256SUMS(sums []byte, filename string) (string, error) {
	for _, line := range strings.Split(string(sums), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		parts := strings.Fields(line)
		if len(parts) < 2 {
			continue
		}
		hash := strings.TrimSpace(parts[0])
		name := strings.TrimSpace(parts[1])
		if name == filename {
			if len(hash) != 64 {
				return "", fmt.Errorf("invalid sha256 length for %s", filename)
			}
			return hash, nil
		}
	}
	return "", fmt.Errorf("checksum for %s not found in SHA256SUMS", filename)
}

func ensureWritableDir(dir string) error {
	testPath := filepath.Join(dir, ".agent.write-test."+strconv.Itoa(os.Getpid()))
	if err := os.WriteFile(testPath, []byte("x"), 0o600); err != nil {
		return err
	}
	_ = os.Remove(testPath)
	return nil
}

func printSelfUpdateHint() {
	latest, err := fetchLatestReleaseTag(context.Background(), "hkjarral/Asterisk-AI-Voice-Agent")
	if err != nil || latest == "" {
		return
	}
	current := strings.TrimSpace(version)
	if !strings.HasPrefix(strings.ToLower(current), "v") {
		// dev builds or unknown formats are best-effort only.
		return
	}
	if compareSemver(current, latest) >= 0 {
		return
	}
	fmt.Printf("Notice: a newer agent CLI is available (%s -> %s). Update with:\n", current, latest)
	fmt.Printf("  curl -sSL https://raw.githubusercontent.com/hkjarral/Asterisk-AI-Voice-Agent/main/scripts/install-cli.sh | bash\n")
}

func fetchLatestReleaseTag(ctx context.Context, repo string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 4*time.Second)
	defer cancel()

	url := fmt.Sprintf("https://api.github.com/repos/%s/releases/latest", repo)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("User-Agent", "aava-agent-cli")

	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return "", fmt.Errorf("unexpected status %s", resp.Status)
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}
	var payload struct {
		TagName string `json:"tag_name"`
	}
	if err := json.Unmarshal(body, &payload); err != nil {
		return "", err
	}
	tag := strings.TrimSpace(payload.TagName)
	if tag == "" {
		return "", errors.New("missing tag_name in response")
	}
	return tag, nil
}

func compareSemver(a string, b string) int {
	amaj, amin, apat, okA := parseSemver(a)
	bmaj, bmin, bpat, okB := parseSemver(b)
	if !okA || !okB {
		return 0
	}
	if amaj != bmaj {
		if amaj < bmaj {
			return -1
		}
		return 1
	}
	if amin != bmin {
		if amin < bmin {
			return -1
		}
		return 1
	}
	if apat != bpat {
		if apat < bpat {
			return -1
		}
		return 1
	}
	return 0
}

func parseSemver(v string) (major int, minor int, patch int, ok bool) {
	v = strings.TrimSpace(v)
	v = strings.TrimPrefix(strings.ToLower(v), "v")
	if v == "" {
		return 0, 0, 0, false
	}
	if i := strings.IndexByte(v, '-'); i >= 0 {
		v = v[:i]
	}
	parts := strings.Split(v, ".")
	if len(parts) < 3 {
		return 0, 0, 0, false
	}
	maj, err := strconv.Atoi(parts[0])
	if err != nil {
		return 0, 0, 0, false
	}
	min, err := strconv.Atoi(parts[1])
	if err != nil {
		return 0, 0, 0, false
	}
	pat, err := strconv.Atoi(parts[2])
	if err != nil {
		return 0, 0, 0, false
	}
	return maj, min, pat, true
}

func createUpdateBackups(ctx *updateContext) error {
	timestamp := time.Now().UTC().Format("20060102_150405")
	backupDir := filepath.Join(ctx.repoRoot, ".agent", "update-backups", timestamp)
	if err := os.MkdirAll(backupDir, 0o755); err != nil {
		return fmt.Errorf("failed to create backup directory: %w", err)
	}
	ctx.backupDir = backupDir

	paths := []string{
		".env",
		filepath.Join("config", "ai-agent.yaml"),
		filepath.Join("config", "contexts"),
	}

	for _, rel := range paths {
		if err := backupPathIfExists(rel, backupDir); err != nil {
			return err
		}
	}
	return nil
}

func backupPathIfExists(relPath string, backupRoot string) error {
	info, err := os.Stat(relPath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return fmt.Errorf("failed to stat %s: %w", relPath, err)
	}
	dst := filepath.Join(backupRoot, relPath)
	if info.IsDir() {
		return copyDir(relPath, dst)
	}
	return copyFile(relPath, dst)
}

func copyFile(src string, dst string) error {
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		return fmt.Errorf("failed to create backup dir for %s: %w", dst, err)
	}
	in, err := os.Open(src)
	if err != nil {
		return fmt.Errorf("failed to open %s: %w", src, err)
	}
	defer in.Close()

	out, err := os.Create(dst)
	if err != nil {
		return fmt.Errorf("failed to create %s: %w", dst, err)
	}
	defer func() {
		_ = out.Close()
	}()
	if _, err := io.Copy(out, in); err != nil {
		return fmt.Errorf("failed to copy %s -> %s: %w", src, dst, err)
	}
	if err := out.Sync(); err != nil {
		return fmt.Errorf("failed to sync %s: %w", dst, err)
	}
	return nil
}

func copyDir(srcDir string, dstDir string) error {
	return filepath.WalkDir(srcDir, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(srcDir, path)
		if err != nil {
			return err
		}
		dstPath := filepath.Join(dstDir, rel)
		if entry.IsDir() {
			return os.MkdirAll(dstPath, 0o755)
		}
		if entry.Type()&os.ModeSymlink != 0 {
			// Skip symlinks in backups; they are uncommon here and can point outside the repo.
			return nil
		}
		return copyFile(path, dstPath)
	})
}

func gitShowTopLevel() (string, error) {
	if _, err := exec.LookPath("git"); err != nil {
		return "", errors.New("git not found in PATH")
	}

	// Work around Git's "dubious ownership" guardrail by setting safe.directory
	// to the detected repo root (if we can find it without invoking git).
	if gitSafeDirectory == "" {
		if candidate, err := findGitRootFromCWD(); err == nil && candidate != "" {
			gitSafeDirectory = candidate
		}
	}

	out, err := runGitCmd("rev-parse", "--show-toplevel")
	if err != nil {
		// If we're hitting Git's safe.directory guardrail, print a human-friendly message
		// that explains the cause and the exact one-time fix.
		msg := err.Error()
		if strings.Contains(msg, "detected dubious ownership") && strings.Contains(msg, "safe.directory") {
			return "", fmt.Errorf(
				"git safety check blocked this repo (detected 'dubious ownership').\n"+
					"This happens when the repo directory is owned by a different user (common with Docker/UID-mapped setups).\n\n"+
					"Fix (one-time):\n"+
					"  git config --global --add safe.directory %s\n",
				bestEffortCWD(),
			)
		}
		return "", fmt.Errorf("not a git repository (or git not installed): %w", err)
	}
	top := strings.TrimSpace(out)
	if top == "" {
		return "", errors.New("git rev-parse returned empty repo root")
	}
	if abs, err := filepath.Abs(top); err == nil {
		top = abs
	}
	gitSafeDirectory = top
	return top, nil
}

func gitRevParse(ref string) (string, error) {
	out, err := runGitCmd("rev-parse", ref)
	if err != nil {
		return "", fmt.Errorf("git rev-parse %s failed: %w", ref, err)
	}
	return strings.TrimSpace(out), nil
}

func gitIsDirty(includeUntracked bool) (bool, error) {
	args := []string{"status", "--porcelain"}
	// Default behavior: ignore untracked files so operator backup artifacts (e.g., *.bak, .preflight-ok)
	// don't force a stash attempt on every update run. Use --stash-untracked to include them.
	if includeUntracked {
		args = append(args, "--untracked-files=all")
	} else {
		args = append(args, "--untracked-files=no")
	}
	out, err := runGitCmd(args...)
	if err != nil {
		return false, fmt.Errorf("git status failed: %w", err)
	}
	return strings.TrimSpace(out) != "", nil
}

func gitStash(ctx *updateContext, includeUntracked bool) error {
	msg := "agent update " + time.Now().UTC().Format(time.RFC3339)
	var err error
	var out string

	if includeUntracked {
		out, err = runGitCmd("stash", "save", "-u", msg)
	} else {
		out, err = runGitCmd("stash", "save", msg)
	}
	if err != nil {
		return fmt.Errorf("git stash failed: %w", err)
	}

	// If there was nothing to stash, git prints a message and does not create an entry.
	if strings.Contains(out, "No local changes") {
		return nil
	}

	ctx.stashed = true
	ctx.stashRef = ""
	ref, refErr := runGitCmd("stash", "list", "-1")
	if refErr == nil {
		ctx.stashRef = strings.TrimSpace(ref)
	}
	return nil
}

func gitStashPop(ctx *updateContext) error {
	_, err := runGitCmd("stash", "pop")
	if err != nil {
		// On conflict, git typically returns non-zero and leaves the stash in place.
		return fmt.Errorf("git stash pop failed (possible conflicts). Your stash is likely preserved; run `git stash list` and resolve conflicts: %w", err)
	}
	return nil
}

func gitFetch(remote string, ref string) error {
	_, err := runGitCmd("fetch", remote, ref)
	if err != nil {
		return fmt.Errorf("git fetch %s %s failed: %w", remote, ref, err)
	}
	return nil
}

func gitMergeFastForward(remoteRef string) error {
	_, err := runGitCmd("merge", "--ff-only", remoteRef)
	if err != nil {
		return fmt.Errorf("git merge --ff-only %s failed (branch likely diverged or local conflicts). Fix manually and retry: %w", remoteRef, err)
	}
	return nil
}

func gitDiffNames(oldSHA string, newSHA string) ([]string, error) {
	out, err := runGitCmd("diff", "--name-only", oldSHA+".."+newSHA)
	if err != nil {
		return nil, fmt.Errorf("git diff failed: %w", err)
	}
	lines := []string{}
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		lines = append(lines, line)
	}
	sort.Strings(lines)
	return lines, nil
}

func decideDockerActions(ctx *updateContext) {
	mode := rebuildMode(strings.ToLower(strings.TrimSpace(updateRebuild)))
	if mode != rebuildAuto && mode != rebuildNone && mode != rebuildAll {
		mode = rebuildAuto
	}

	for _, f := range ctx.changedFiles {
		if strings.HasPrefix(f, "docker-compose") && (strings.HasSuffix(f, ".yml") || strings.HasSuffix(f, ".yaml")) {
			ctx.composeChanged = true
		}
	}

	if mode == rebuildNone {
		// Conservative: restart ai_engine if code/config changed.
		for _, f := range ctx.changedFiles {
			if strings.HasPrefix(f, "src/") || f == "main.py" || strings.HasPrefix(f, "config/") || strings.HasPrefix(f, "scripts/") {
				ctx.servicesToRestart["ai_engine"] = true
			}
		}
		return
	}

	if mode == rebuildAll {
		ctx.servicesToRebuild["ai_engine"] = true
		ctx.servicesToRebuild["admin_ui"] = true
		ctx.servicesToRebuild["local_ai_server"] = true
		return
	}

	// auto
	for _, f := range ctx.changedFiles {
		switch {
		case strings.HasPrefix(f, "admin_ui/"):
			ctx.servicesToRebuild["admin_ui"] = true
		case strings.HasPrefix(f, "local_ai_server/"):
			ctx.servicesToRebuild["local_ai_server"] = true
		case f == "Dockerfile" || f == "requirements.txt":
			ctx.servicesToRebuild["ai_engine"] = true
		case strings.HasPrefix(f, "src/") || f == "main.py" || strings.HasPrefix(f, "config/") || strings.HasPrefix(f, "scripts/"):
			ctx.servicesToRestart["ai_engine"] = true
		}
	}

	// If we rebuild, restart is implied.
	for svc := range ctx.servicesToRebuild {
		delete(ctx.servicesToRestart, svc)
	}
}

func applyDockerActions(ctx *updateContext) error {
	if len(ctx.servicesToRebuild) == 0 && len(ctx.servicesToRestart) == 0 && !ctx.composeChanged {
		return nil
	}

	if _, err := runCmd("docker", "compose", "version"); err != nil {
		return fmt.Errorf("docker compose is required but not available: %w", err)
	}

	if ctx.composeChanged {
		// Avoid implicit builds when Compose files change (some deployments use pull_policy: build).
		// The rebuild/restart logic below will handle builds explicitly when needed.
		args := []string{"compose", "up", "-d", "--remove-orphans", "--no-build"}
		if updateForceRecreate {
			args = append(args, "--force-recreate")
		}
		if _, err := runCmd("docker", args...); err != nil {
			return fmt.Errorf("docker compose up (remove-orphans) failed: %w", err)
		}
	}

	rebuildServices := sortedKeys(ctx.servicesToRebuild)
	restartServices := sortedKeys(ctx.servicesToRestart)

	if len(rebuildServices) > 0 {
		args := []string{"compose", "up", "-d", "--build"}
		if updateForceRecreate {
			args = append(args, "--force-recreate")
		}
		args = append(args, rebuildServices...)
		if _, err := runCmd("docker", args...); err != nil {
			return fmt.Errorf("docker compose up --build failed: %w", err)
		}
	}

	for _, svc := range restartServices {
		if _, err := runCmd("docker", "compose", "restart", svc); err != nil {
			// Fallback: start/recreate service if restart fails.
			if _, err2 := runCmd("docker", "compose", "up", "-d", "--no-build", svc); err2 != nil {
				return fmt.Errorf("failed to restart %s (restart error: %v; up error: %w)", svc, err, err2)
			}
		}
	}

	return nil
}

func runPostUpdateCheck() (report *check.Report, status string, warnCount int, failCount int, err error) {
	runner := check.NewRunner(verbose, version, buildTime)
	report, runErr := runner.Run()
	if report == nil {
		return nil, "FAIL", 0, 1, fmt.Errorf("agent check failed: %w", runErr)
	}
	warnCount = report.WarnCount
	failCount = report.FailCount
	if runErr != nil || failCount > 0 {
		return report, "FAIL", warnCount, failCount, runErr
	}
	if warnCount > 0 {
		return report, "WARN", warnCount, 0, nil
	}
	return report, "PASS", 0, 0, nil
}

func printUpdateSummary(ctx *updateContext, checkStatus string, warnCount int, failCount int) {
	if strings.TrimSpace(ctx.oldSHA) == strings.TrimSpace(ctx.newSHA) {
		fmt.Printf("Up to date: %s\n", shortSHA(ctx.oldSHA))
	} else {
		fmt.Printf("Updated: %s -> %s\n", shortSHA(ctx.oldSHA), shortSHA(ctx.newSHA))
	}
	if ctx.backupDir != "" {
		fmt.Printf("Backups: %s\n", ctx.backupDir)
	}
	if ctx.stashed {
		if ctx.stashRef != "" {
			fmt.Printf("Stash: %s\n", ctx.stashRef)
		} else {
			fmt.Printf("Stash: created\n")
		}
	}
	if len(ctx.servicesToRebuild) > 0 {
		fmt.Printf("Rebuilt: %s\n", strings.Join(sortedKeys(ctx.servicesToRebuild), ", "))
	}
	if len(ctx.servicesToRestart) > 0 {
		fmt.Printf("Restarted: %s\n", strings.Join(sortedKeys(ctx.servicesToRestart), ", "))
	}
	if ctx.composeChanged {
		fmt.Printf("Compose: applied changes\n")
	}
	if checkStatus != "" {
		fmt.Printf("Check: %s (warn=%d fail=%d)\n", checkStatus, warnCount, failCount)
	}
}

func printUpdateStep(title string) {
	fmt.Printf("\n==> %s\n", title)
}

func printUpdateInfo(format string, args ...any) {
	fmt.Printf(" - "+format+"\n", args...)
}

func printDockerActionsPlanned(ctx *updateContext) {
	if len(ctx.servicesToRebuild) == 0 && len(ctx.servicesToRestart) == 0 && !ctx.composeChanged {
		printUpdateInfo("No container rebuild/restart required")
		return
	}
	if ctx.composeChanged {
		printUpdateInfo("Compose files changed (will run docker compose up --no-build --remove-orphans)")
	}
	if len(ctx.servicesToRebuild) > 0 {
		printUpdateInfo("Will rebuild: %s", strings.Join(sortedKeys(ctx.servicesToRebuild), ", "))
	}
	if len(ctx.servicesToRestart) > 0 {
		printUpdateInfo("Will restart: %s", strings.Join(sortedKeys(ctx.servicesToRestart), ", "))
	}
}

func printPostUpdateCheck(report *check.Report, warnCount int, failCount int) {
	if report == nil {
		return
	}
	if verbose || warnCount > 0 || failCount > 0 {
		report.OutputText(os.Stdout)
	}
}

func sortedKeys(m map[string]bool) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func shortSHA(sha string) string {
	sha = strings.TrimSpace(sha)
	if len(sha) > 8 {
		return sha[:8]
	}
	return sha
}

func runCmd(name string, args ...string) (string, error) {
	cmd := exec.Command(name, args...)
	cmd.Stdin = os.Stdin
	if verbose {
		fmt.Printf(" → %s %s\n", name, strings.Join(args, " "))
		var buf bytes.Buffer
		cmd.Stdout = io.MultiWriter(os.Stdout, &buf)
		cmd.Stderr = io.MultiWriter(os.Stderr, &buf)
		err := cmd.Run()
		text := strings.TrimSpace(buf.String())
		if err != nil {
			if text != "" {
				return text, fmt.Errorf("%w", err)
			}
			return text, err
		}
		return text, nil
	}

	out, err := cmd.CombinedOutput()
	text := strings.TrimSpace(string(out))
	if err != nil {
		if text != "" {
			return text, fmt.Errorf("%w: %s", err, text)
		}
		return text, err
	}
	return text, nil
}

func runGitCmd(args ...string) (string, error) {
	gitArgs := make([]string, 0, len(args)+2)
	if gitSafeDirectory != "" {
		gitArgs = append(gitArgs, "-c", "safe.directory="+gitSafeDirectory)
	}
	gitArgs = append(gitArgs, args...)
	return runCmd("git", gitArgs...)
}

func findGitRootFromCWD() (string, error) {
	start, err := os.Getwd()
	if err != nil {
		return "", err
	}
	dir := start
	for {
		if hasGitDir(dir) {
			if abs, err := filepath.Abs(dir); err == nil {
				return abs, nil
			}
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	return "", errors.New("no .git directory found in parent chain")
}

func hasGitDir(dir string) bool {
	info, err := os.Stat(filepath.Join(dir, ".git"))
	if err != nil {
		return false
	}
	// `.git` can be a directory or a file (worktrees/submodules); both indicate a git root.
	return info.IsDir() || info.Mode().IsRegular()
}

func bestEffortCWD() string {
	if wd, err := os.Getwd(); err == nil && wd != "" {
		return wd
	}
	return "<repo-path>"
}
