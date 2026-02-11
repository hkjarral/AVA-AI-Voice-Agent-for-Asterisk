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
	"regexp"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/hkjarral/asterisk-ai-voice-agent/cli/internal/check"
	"github.com/hkjarral/asterisk-ai-voice-agent/cli/internal/configmerge"
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
	updateIncludeUI     bool
	updateCheckout      bool
	updateBackupID      string
	updatePlan          bool
	updatePlanJSON      bool
	gitSafeDirectory    string
)

var semverTagRe = regexp.MustCompile(`^(v)?([0-9]+\.[0-9]+\.[0-9]+)$`)

func normalizeSemverTagRef(ref string) (string, bool) {
	r := strings.TrimSpace(ref)
	if r == "" {
		return "", false
	}
	m := semverTagRe.FindStringSubmatch(r)
	if m == nil {
		return "", false
	}
	return "v" + m[2], true
}

var updateCmd = &cobra.Command{
	Use:   "update",
	Short: "Pull latest code and apply updates",
	Long: `Update Asterisk AI Voice Agent to the latest code and apply changes safely.

This command:
  - Backs up operator config (.env, config/ai-agent.local.yaml, config/users.json, config/contexts/)
  - Also snapshots config/ai-agent.yaml for recovery/migration if it was edited locally
  - Safely fast-forwards to origin/main (no forced merges by default)
  - Preserves local tracked changes using git stash (optional)
  - Rebuilds/restarts only the containers impacted by the change set
  - Verifies success by running agent check (optional)

Safety notes:
  - If you edited config/ai-agent.yaml directly, updates can conflict. This updater automatically migrates
    those edits into config/ai-agent.local.yaml and resets config/ai-agent.yaml back to upstream defaults.
  - No hard resets are performed.
  - Fast-forward only: if your branch has diverged, the update stops with guidance.`,
	RunE: func(cmd *cobra.Command, args []string) error {
		return runUpdate()
	},
}

func init() {
	updateCmd.Flags().StringVar(&updateRemote, "remote", "origin", "git remote name")
	updateCmd.Flags().StringVar(&updateRef, "ref", "main", "git ref to update to (branch like main, or tag like v5.2.4)")
	updateCmd.Flags().BoolVar(&updateNoStash, "no-stash", false, "abort if repo has local changes instead of stashing")
	updateCmd.Flags().BoolVar(&updateStashUntracked, "stash-untracked", false, "include untracked files when stashing (does not include ignored files)")
	updateCmd.Flags().StringVar(&updateRebuild, "rebuild", string(rebuildAuto), "rebuild mode: auto|none|all")
	updateCmd.Flags().BoolVar(&updateForceRecreate, "force-recreate", false, "force recreate containers during docker compose up")
	updateCmd.Flags().BoolVar(&updateSkipCheck, "skip-check", false, "skip running agent check after update")
	updateCmd.Flags().BoolVar(&updateSelfUpdate, "self-update", true, "auto-update the agent CLI binary if a newer release is available")
	updateCmd.Flags().BoolVar(&updateIncludeUI, "include-ui", true, "include admin_ui rebuild/restart when changes require it")
	updateCmd.Flags().BoolVar(&updateCheckout, "checkout", false, "allow switching to --ref branch before updating (UI-driven updates typically enable this)")
	updateCmd.Flags().StringVar(&updateBackupID, "backup-id", "", "use a stable backup identifier (creates .agent/update-backups/<id>)")
	updateCmd.Flags().BoolVar(&updatePlan, "plan", false, "print the update plan (git/diff/docker actions) without applying it")
	updateCmd.Flags().BoolVar(&updatePlanJSON, "plan-json", false, "when used with --plan, output the plan as JSON")
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

	skippedServices map[string]string // service -> "rebuild"|"restart" (filtered by flags)
}

type updatePlanReport struct {
	RepoRoot         string              `json:"repo_root"`
	Remote           string              `json:"remote"`
	Ref              string              `json:"ref"`
	CurrentBranch    string              `json:"current_branch"`
	TargetBranch     string              `json:"target_branch"`
	Checkout         bool                `json:"checkout"`
	WouldCheckout    bool                `json:"would_checkout"`
	OldSHA           string              `json:"old_sha"`
	NewSHA           string              `json:"new_sha"`
	Relation         string              `json:"relation"` // equal|behind|ahead|diverged
	CodeChanged      bool                `json:"code_changed"`
	UpdateAvailable  bool                `json:"update_available"`
	Dirty            bool                `json:"dirty"`
	NoStash          bool                `json:"no_stash"`
	StashUntracked   bool                `json:"stash_untracked"`
	WouldStash       bool                `json:"would_stash"`
	WouldAbort       bool                `json:"would_abort"`
	RebuildMode      string              `json:"rebuild_mode"`
	ComposeChanged   bool                `json:"compose_changed"`
	ServicesRebuild  []string            `json:"services_rebuild"`
	ServicesRestart  []string            `json:"services_restart"`
	SkippedServices  map[string]string   `json:"skipped_services,omitempty"`
	ChangedFileCount int                 `json:"changed_file_count"`
	ChangedFiles     []string            `json:"changed_files,omitempty"`
	FilesTruncated   bool                `json:"changed_files_truncated,omitempty"`
	Warnings         []string            `json:"warnings,omitempty"`
}

func runUpdate() (retErr error) {
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
		skippedServices:   map[string]string{},
	}

	defer func() {
		if retErr != nil && !updatePlan {
			printUpdateFailureRecovery(ctx, retErr)
		}
	}()

	ctx.oldSHA, err = gitRevParse("HEAD")
	if err != nil {
		return err
	}

	// Plan-only: show what would happen without changing the repo or containers.
	if updatePlan {
		return runUpdatePlan(ctx)
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

	tagRef, isTag := normalizeSemverTagRef(updateRef)
	if isTag {
		updateRef = tagRef
		printUpdateStep(fmt.Sprintf("Fetching %s tag %s", updateRemote, updateRef))
	} else {
		printUpdateStep(fmt.Sprintf("Fetching %s/%s", updateRemote, updateRef))
	}
	if err := gitFetch(updateRemote, updateRef); err != nil {
		return err
	}
	// Keep tags current so "git describe --tags" reflects newly published versions.
	_ = gitFetchTags(updateRemote)
	targetRemoteRef := fmt.Sprintf("%s/%s", updateRemote, updateRef)
	targetRev := targetRemoteRef
	if isTag {
		targetRev = updateRef
	}
	targetLabel := targetRemoteRef
	if isTag {
		targetLabel = updateRef
	}
	targetSHA, err := gitRevParse(targetRev)
	if err != nil {
		return err
	}
	ctx.newSHA = targetSHA

	currentBranch, _ := gitCurrentBranch()
	if isTag && (strings.TrimSpace(currentBranch) == "" || strings.TrimSpace(currentBranch) == "HEAD") {
		return fmt.Errorf("cannot update to tag %q from a detached HEAD; checkout a branch (e.g., `git checkout main`) and re-run", updateRef)
	}
	if !isTag {
		branchMismatch := strings.TrimSpace(currentBranch) == "" || strings.TrimSpace(currentBranch) == "HEAD" || strings.TrimSpace(currentBranch) != strings.TrimSpace(updateRef)
		if branchMismatch {
			if !updateCheckout {
				return fmt.Errorf("target ref %q differs from current branch %q; re-run with --checkout to allow switching branches", updateRef, currentBranch)
			}
			printUpdateStep(fmt.Sprintf("Checking out %s", updateRef))
			exists, existsErr := gitLocalBranchExists(updateRef)
			if existsErr != nil {
				return existsErr
			}
			if exists {
				if err := gitCheckout(updateRef); err != nil {
					return err
				}
			} else {
				if err := gitCheckoutTrack(updateRef, targetRemoteRef); err != nil {
					return err
				}
			}
		}
	}

	branchHead, err := gitRevParse("HEAD")
	if err != nil {
		return err
	}

	updateAvailable, relErr := gitIsAncestor(branchHead, targetSHA)
	if relErr != nil {
		return relErr
	}
	remoteIsAncestor, relErr2 := gitIsAncestor(targetSHA, branchHead)
	if relErr2 != nil {
		return relErr2
	}

	finalSHA := branchHead
	if strings.TrimSpace(branchHead) == strings.TrimSpace(targetSHA) {
		printUpdateInfo("Already up to date on %s (%s)", updateRef, shortSHA(branchHead))
		finalSHA = branchHead
	} else if updateAvailable {
		printUpdateStep("Fast-forwarding code")
		mergeRef := targetRemoteRef
		if isTag {
			mergeRef = updateRef
		}
		if err := gitMergeFastForward(mergeRef); err != nil {
			return err
		}
		finalSHA = targetSHA
	} else if remoteIsAncestor {
		printUpdateInfo("Local branch is ahead of %s; skipping fast-forward update", targetLabel)
		finalSHA = branchHead
	} else {
		return fmt.Errorf("cannot fast-forward: local branch has diverged from %s (resolve manually and re-run)", targetLabel)
	}
	ctx.newSHA = finalSHA

	if ctx.stashed {
		printUpdateStep("Restoring stashed changes")
		if err := gitStashPop(ctx); err != nil {
			printUpdateInfo("⚠ Stash pop conflict detected; recovering from backup")
			if recoverErr := recoverFromStashConflict(ctx); recoverErr != nil {
				return fmt.Errorf("stash pop failed and recovery also failed: %w (original: %v)", recoverErr, err)
			}
			printUpdateInfo("Operator config restored from backup — update will continue")
		}
	}

	// If the operator edited config/ai-agent.yaml directly (tracked), move those edits into
	// config/ai-agent.local.yaml so future updates remain conflict-free and new upstream knobs
	// are inherited by default.
	if err := migrateBaseConfigEditsToLocal(); err != nil {
		return err
	}

	if strings.TrimSpace(ctx.oldSHA) != strings.TrimSpace(ctx.newSHA) {
		ctx.changedFiles, err = gitDiffNames(ctx.oldSHA, ctx.newSHA)
		if err != nil {
			return err
		}
		decideDockerActions(ctx)
		applyServiceFilters(ctx)
	}

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

func runUpdatePlan(ctx *updateContext) error {
	dirty, err := gitIsDirty(updateStashUntracked)
	if err != nil {
		return err
	}

	currentBranch, _ := gitCurrentBranch()
	tagRef, isTag := normalizeSemverTagRef(updateRef)
	if isTag {
		updateRef = tagRef
	}
	wouldCheckout := !isTag && updateCheckout && (strings.TrimSpace(currentBranch) == "" || strings.TrimSpace(currentBranch) == "HEAD" || strings.TrimSpace(currentBranch) != strings.TrimSpace(updateRef))

	if err := gitFetch(updateRemote, updateRef); err != nil {
		return err
	}
	_ = gitFetchTags(updateRemote)

	rev := fmt.Sprintf("%s/%s", updateRemote, updateRef)
	if isTag {
		rev = updateRef
	}
	newSHA, err := gitRevParse(rev)
	if err != nil {
		return err
	}

	ctx.newSHA = newSHA
	updateAvailable, relErr := gitIsAncestor(ctx.oldSHA, ctx.newSHA)
	if relErr != nil {
		return relErr
	}
	remoteIsAncestor, relErr2 := gitIsAncestor(ctx.newSHA, ctx.oldSHA)
	if relErr2 != nil {
		return relErr2
	}
	codeChanged := strings.TrimSpace(ctx.oldSHA) != strings.TrimSpace(ctx.newSHA)
	// Git treats a commit as its own ancestor, so when SHAs match `gitIsAncestor(old,new)` is true.
	// For plan/reporting, treat identical SHAs as "no update available".
	updateAvailable = updateAvailable && codeChanged
	if codeChanged {
		ctx.changedFiles, err = gitDiffNames(ctx.oldSHA, ctx.newSHA)
		if err != nil {
			return err
		}
		decideDockerActions(ctx)
		applyServiceFilters(ctx)
	} else {
		ctx.changedFiles = nil
	}

	wouldStash := dirty && !updateNoStash
	wouldAbort := dirty && updateNoStash

	relation := "equal"
	if codeChanged {
		switch {
		case updateAvailable:
			relation = "behind"
		case remoteIsAncestor:
			relation = "ahead"
		default:
			relation = "diverged"
		}
	}

	limit := 200
	files := ctx.changedFiles
	truncated := false
	if len(files) > limit {
		files = files[:limit]
		truncated = true
	}

	rep := &updatePlanReport{
		RepoRoot:         ctx.repoRoot,
		Remote:           updateRemote,
		Ref:              updateRef,
		CurrentBranch:    strings.TrimSpace(currentBranch),
		TargetBranch:     strings.TrimSpace(updateRef),
		Checkout:         updateCheckout,
		WouldCheckout:    wouldCheckout,
		OldSHA:           ctx.oldSHA,
		NewSHA:           ctx.newSHA,
		Relation:         relation,
		CodeChanged:      codeChanged,
		UpdateAvailable:  updateAvailable,
		Dirty:            dirty,
		NoStash:          updateNoStash,
		StashUntracked:   updateStashUntracked,
		WouldStash:       wouldStash,
		WouldAbort:       wouldAbort,
		RebuildMode:      strings.ToLower(strings.TrimSpace(updateRebuild)),
		ComposeChanged:   ctx.composeChanged,
		ServicesRebuild:  sortedKeys(ctx.servicesToRebuild),
		ServicesRestart:  sortedKeys(ctx.servicesToRestart),
		SkippedServices:  nil,
		ChangedFileCount: len(ctx.changedFiles),
		ChangedFiles:     files,
		FilesTruncated:   truncated,
	}
	if len(ctx.skippedServices) > 0 {
		rep.SkippedServices = ctx.skippedServices
	}
	if wouldCheckout && strings.TrimSpace(currentBranch) != "" && strings.TrimSpace(currentBranch) != "HEAD" && strings.TrimSpace(currentBranch) != strings.TrimSpace(updateRef) {
		rep.Warnings = append(rep.Warnings, fmt.Sprintf("Selected ref %q differs from current branch %q; update will checkout/switch branches (use --checkout=false to disallow).", updateRef, currentBranch))
	}
	if !updateIncludeUI && (ctx.skippedServices["admin_ui"] != "") {
		rep.Warnings = append(rep.Warnings, "Admin UI changes detected but excluded (use --include-ui to apply admin_ui rebuild/restart).")
	}
	if !updateIncludeUI && ctx.composeChanged {
		rep.Warnings = append(rep.Warnings, "Compose files changed; admin_ui changes (if any) are excluded unless --include-ui is enabled.")
	}
	if !updateAvailable && remoteIsAncestor && strings.TrimSpace(ctx.newSHA) != strings.TrimSpace(ctx.oldSHA) {
		rep.Warnings = append(rep.Warnings, fmt.Sprintf("Local branch is ahead of %s/%s; no fast-forward update available.", updateRemote, updateRef))
	}
	if !updateAvailable && !remoteIsAncestor && strings.TrimSpace(ctx.newSHA) != strings.TrimSpace(ctx.oldSHA) {
		rep.Warnings = append(rep.Warnings, fmt.Sprintf("Local branch has diverged from %s/%s; update requires manual resolution.", updateRemote, updateRef))
	}

	if updatePlanJSON {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(rep)
	}

	printUpdateStep("Update plan")
	printUpdateInfo("Repo: %s", ctx.repoRoot)
	printUpdateInfo("From: %s", shortSHA(ctx.oldSHA))
	printUpdateInfo("To:   %s", shortSHA(ctx.newSHA))
	if wouldAbort {
		printUpdateInfo("Would abort: working tree is dirty and --no-stash was set")
	} else if wouldStash {
		printUpdateInfo("Would stash: working tree has local changes")
	}
	printDockerActionsPlanned(ctx)
	if len(rep.Warnings) > 0 {
		for _, w := range rep.Warnings {
			printUpdateInfo("Warning: %s", w)
		}
	}
	return nil
}

func applyServiceFilters(ctx *updateContext) {
	// UI-driven updates may want to avoid restarting/rebuilding admin_ui by default.
	if !updateIncludeUI {
		if ctx.servicesToRebuild["admin_ui"] {
			delete(ctx.servicesToRebuild, "admin_ui")
			ctx.skippedServices["admin_ui"] = "rebuild"
		}
		if ctx.servicesToRestart["admin_ui"] {
			delete(ctx.servicesToRestart, "admin_ui")
			ctx.skippedServices["admin_ui"] = "restart"
		}
	}
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
	id := strings.TrimSpace(updateBackupID)
	if id != "" {
		id = sanitizeBackupID(id)
		if id == "" {
			return errors.New("invalid --backup-id")
		}
	}

	dirName := time.Now().UTC().Format("20060102_150405")
	if id != "" {
		dirName = id
	}

	backupDir := filepath.Join(ctx.repoRoot, ".agent", "update-backups", dirName)
	if err := os.MkdirAll(backupDir, 0o755); err != nil {
		return fmt.Errorf("failed to create backup directory: %w", err)
	}
	ctx.backupDir = backupDir

	paths := []string{
		".env",
		filepath.Join("config", "ai-agent.yaml"),
		filepath.Join("config", "ai-agent.local.yaml"),
		filepath.Join("config", "users.json"),
		filepath.Join("config", "contexts"),
	}

	for _, rel := range paths {
		if err := backupPathIfExists(rel, backupDir); err != nil {
			return err
		}
	}
	return nil
}

func sanitizeBackupID(s string) string {
	s = strings.TrimSpace(s)
	if s == "" {
		return ""
	}
	if len(s) > 80 {
		s = s[:80]
	}
	var out strings.Builder
	out.Grow(len(s))
	for _, r := range s {
		switch {
		case r >= 'a' && r <= 'z':
			out.WriteRune(r)
		case r >= 'A' && r <= 'Z':
			out.WriteRune(r)
		case r >= '0' && r <= '9':
			out.WriteRune(r)
		case r == '-' || r == '_' || r == '.':
			out.WriteRune(r)
		default:
			out.WriteByte('_')
		}
	}
	return strings.Trim(out.String(), "._-")
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

// recoverFromStashConflict handles a failed git stash pop by resetting the conflicted
// working tree, dropping the failed stash, and restoring operator-owned config files
// from the pre-update backup so the update can continue.
func recoverFromStashConflict(ctx *updateContext) error {
	// 1. Reset the conflicted working tree to the (already merged) HEAD.
	if _, err := runGitCmd("checkout", "--", "."); err != nil {
		return fmt.Errorf("git checkout -- . failed: %w", err)
	}

	// 2. Drop the stash entry that caused the conflict.
	//    After a failed `stash pop`, the stash entry is preserved at stash@{0}.
	if _, err := runGitCmd("stash", "drop"); err != nil {
		// Non-fatal: the stash may have been consumed on some git versions.
		printUpdateInfo("Note: could not drop stash (may already be consumed): %v", err)
	}

	// 3. Restore operator config from the backup created earlier in this run.
	if ctx.backupDir == "" {
		return errors.New("no backup directory available for recovery")
	}

	// Restore operator-owned files. Do NOT restore config/ai-agent.yaml over the updated upstream base.
	// If the operator had edits in ai-agent.yaml, we migrate them into ai-agent.local.yaml below.
	configFiles := []string{
		".env",
		filepath.Join("config", "ai-agent.local.yaml"),
		filepath.Join("config", "users.json"),
	}

	for _, rel := range configFiles {
		src := filepath.Join(ctx.backupDir, rel)
		if _, err := os.Stat(src); err != nil {
			continue // backup didn't include this file (e.g. local.yaml may not exist yet)
		}
		if err := copyFile(src, rel); err != nil {
			return fmt.Errorf("failed to restore %s from backup: %w", rel, err)
		}
		printUpdateInfo("Restored %s", rel)
	}

	// Restore contexts directory if backed up.
	ctxSrc := filepath.Join(ctx.backupDir, "config", "contexts")
	if info, err := os.Stat(ctxSrc); err == nil && info.IsDir() {
		ctxDst := filepath.Join("config", "contexts")
		_ = os.RemoveAll(ctxDst)
		if err := copyDir(ctxSrc, ctxDst); err != nil {
			return fmt.Errorf("failed to restore config/contexts from backup: %w", err)
		}
		printUpdateInfo("Restored config/contexts/")
	}

	ctx.stashed = false

	// Best-effort: if backup included ai-agent.yaml edits, migrate them into ai-agent.local.yaml.
	// Fall back to restoring the base file only if migration fails due to YAML parse errors.
	backupBase := filepath.Join(ctx.backupDir, "config", "ai-agent.yaml")
	if _, err := os.Stat(backupBase); err == nil {
		if err := migrateBackupBaseConfigEditsToLocal(ctx.oldSHA, backupBase); err != nil {
			printUpdateInfo("WARN: failed to migrate backed-up ai-agent.yaml edits into ai-agent.local.yaml: %v", err)
			// Conservative fallback: restore backup ai-agent.yaml so operator config isn't silently lost.
			// This may reintroduce drift; operators should move overrides into ai-agent.local.yaml.
			if copyErr := copyFile(backupBase, filepath.Join("config", "ai-agent.yaml")); copyErr == nil {
				printUpdateInfo("Restored config/ai-agent.yaml (fallback)")
			} else {
				printUpdateInfo(
					"WARN: could not restore backup ai-agent.yaml either: %v (backup still at %s)",
					copyErr,
					ctx.backupDir,
				)
			}
		}
	}
	return nil
}

func gitFetch(remote string, ref string) error {
	remote = strings.TrimSpace(remote)
	ref = strings.TrimSpace(ref)
	if remote == "" || ref == "" {
		return errors.New("git fetch: remote/ref is empty")
	}

	// Defense-in-depth: avoid git option injection via remote names (git interprets args starting
	// with '-' as options even when passed via exec.Command).
	if strings.HasPrefix(remote, "-") || strings.ContainsAny(remote, " \t\r\n") {
		return fmt.Errorf("invalid git remote %q", remote)
	}

	// Defense-in-depth: avoid git option injection via ref names (git interprets args starting
	// with '-' as options even when passed via exec.Command).
	if strings.HasPrefix(ref, "-") || strings.ContainsAny(ref, " \t\r\n") {
		return fmt.Errorf("invalid git ref %q", ref)
	}

	// Normalize common ref inputs.
	ref = strings.TrimPrefix(ref, "refs/heads/")
	ref = strings.TrimPrefix(ref, "refs/tags/")
	ref = strings.TrimPrefix(ref, remote+"/")

	// Semver tag refs (vX.Y.Z) are fetched as tags, not remote-tracking branches.
	if tag, ok := normalizeSemverTagRef(ref); ok {
		refspec := fmt.Sprintf("+refs/tags/%s:refs/tags/%s", tag, tag)
		_, err := runGitCmd("fetch", "--prune", remote, refspec)
		if err != nil {
			return fmt.Errorf("git fetch --prune %s %s failed: %w", remote, refspec, err)
		}
		return nil
	}

	// Ensure the remote-tracking ref (refs/remotes/<remote>/<ref>) is updated.
	// `git fetch <remote> <ref>` updates FETCH_HEAD but does not always advance origin/<ref>
	// depending on the remote's fetch refspec. Using an explicit refspec prevents false "up to date"
	// decisions when we later read origin/<ref>.
	refspec := fmt.Sprintf("+refs/heads/%s:refs/remotes/%s/%s", ref, remote, ref)
	_, err := runGitCmd("fetch", "--prune", remote, refspec)
	if err != nil {
		return fmt.Errorf("git fetch --prune %s %s failed: %w", remote, ref, err)
	}
	return nil
}

func migrateBackupBaseConfigEditsToLocal(oldSHA string, backupBasePath string) error {
	// Determine what the operator changed in ai-agent.yaml prior to the update, then carry only those
	// edits forward into ai-agent.local.yaml. This avoids freezing upstream defaults when the base file
	// changes between releases.
	baseBefore, err := gitShowYAMLMap(oldSHA, filepath.Join("config", "ai-agent.yaml"))
	if err != nil {
		return err
	}
	backupBase, err := configmerge.ReadYAMLFile(backupBasePath)
	if err != nil {
		return err
	}

	patch := configmerge.ComputeOverrideNoDeletes(baseBefore, backupBase)
	localPath := filepath.Join("config", "ai-agent.local.yaml")
	local := map[string]any{}
	if _, statErr := os.Stat(localPath); statErr == nil {
		m, err := configmerge.ReadYAMLFile(localPath)
		if err != nil {
			return fmt.Errorf("failed to parse existing %s during migration: %w", localPath, err)
		}
		if m != nil {
			local = m
		}
	} else if !os.IsNotExist(statErr) {
		return fmt.Errorf("failed to stat %s: %w", localPath, statErr)
	}

	mergedLocal := configmerge.DeepMerge(local, patch)
	if err := configmerge.WriteYAMLFileAtomic(localPath, mergedLocal); err != nil {
		return err
	}
	// Ensure base file is reset to upstream version.
	if _, err := runGitCmd("checkout", "--", filepath.Join("config", "ai-agent.yaml")); err != nil {
		return fmt.Errorf("failed to reset %s to upstream: %w", filepath.Join("config", "ai-agent.yaml"), err)
	}
	return nil
}

func migrateBaseConfigEditsToLocal() error {
	baseRel := filepath.Join("config", "ai-agent.yaml")
	modified, err := gitFileModified(baseRel)
	if err != nil {
		return err
	}
	if !modified {
		return nil
	}

	printUpdateStep("Migrating operator config into ai-agent.local.yaml")

	baseClean, err := gitShowYAMLMap("HEAD", baseRel)
	if err != nil {
		return err
	}

	baseWorking, err := configmerge.ReadYAMLFile(baseRel)
	if err != nil {
		return err
	}
	patch := configmerge.ComputeOverrideNoDeletes(baseClean, baseWorking)
	localRel := filepath.Join("config", "ai-agent.local.yaml")
	localExisting := map[string]any{}
	if _, statErr := os.Stat(localRel); statErr == nil {
		m, err := configmerge.ReadYAMLFile(localRel)
		if err != nil {
			return fmt.Errorf("failed to parse existing %s during migration: %w", localRel, err)
		}
		if m != nil {
			localExisting = m
		}
	} else if !os.IsNotExist(statErr) {
		return fmt.Errorf("failed to stat %s: %w", localRel, statErr)
	}
	localNew := configmerge.DeepMerge(localExisting, patch)
	if err := configmerge.WriteYAMLFileAtomic(localRel, localNew); err != nil {
		return err
	}
	if _, err := runGitCmd("checkout", "--", baseRel); err != nil {
		return fmt.Errorf("failed to reset %s to upstream: %w", baseRel, err)
	}
	printUpdateInfo("Moved local edits from %s into %s", baseRel, localRel)
	return nil
}

func gitFileModified(path string) (bool, error) {
	// Use status porcelain so we detect staged-only changes too.
	out, err := runGitCmd("status", "--porcelain", "--", path)
	if err != nil {
		return false, err
	}
	return strings.TrimSpace(out) != "", nil
}

func gitShowYAMLMap(ref string, relPath string) (map[string]any, error) {
	out, err := runGitCmd("show", fmt.Sprintf("%s:%s", ref, filepath.ToSlash(relPath)))
	if err != nil {
		return nil, err
	}
	return configmerge.ParseYAML([]byte(out))
}

func gitFetchTags(remote string) error {
	_, err := runGitCmd("fetch", "--tags", remote)
	if err != nil {
		return fmt.Errorf("git fetch --tags %s failed: %w", remote, err)
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

func gitCurrentBranch() (string, error) {
	out, err := runGitCmd("rev-parse", "--abbrev-ref", "HEAD")
	if err != nil {
		return "", fmt.Errorf("git rev-parse --abbrev-ref HEAD failed: %w", err)
	}
	return strings.TrimSpace(out), nil
}

func gitLocalBranchExists(branch string) (bool, error) {
	branch = strings.TrimSpace(branch)
	if branch == "" {
		return false, errors.New("branch name is empty")
	}

	gitArgs := make([]string, 0, 6)
	if gitSafeDirectory != "" {
		gitArgs = append(gitArgs, "-c", "safe.directory="+gitSafeDirectory)
	}
	gitArgs = append(gitArgs, "show-ref", "--verify", "--quiet", "refs/heads/"+branch)

	cmd := exec.Command("git", gitArgs...)
	cmd.Stdin = nil
	cmd.Stdout = io.Discard
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	err := cmd.Run()
	if err == nil {
		return true, nil
	}
	if exitErr, ok := err.(*exec.ExitError); ok {
		if exitErr.ExitCode() == 1 {
			return false, nil
		}
	}
	msg := strings.TrimSpace(stderr.String())
	if msg != "" {
		return false, fmt.Errorf("git show-ref failed: %s", msg)
	}
	return false, fmt.Errorf("git show-ref failed: %w", err)
}

func gitCheckout(branch string) error {
	_, err := runGitCmd("checkout", branch)
	if err != nil {
		return fmt.Errorf("git checkout %s failed: %w", branch, err)
	}
	return nil
}

func gitCheckoutTrack(branch string, remoteRef string) error {
	_, err := runGitCmd("checkout", "-b", branch, "--track", remoteRef)
	if err != nil {
		return fmt.Errorf("git checkout -b %s --track %s failed: %w", branch, remoteRef, err)
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

	// When compose files change, avoid doing an unscoped `docker compose up` because it can
	// implicitly (re)create services the operator never started (e.g., local_ai_server) and
	// fail if their images aren't present. Instead, scope to services that are already running
	// plus any services we explicitly intend to rebuild/restart.
	runningServices := map[string]bool{}
	out, err := runCmd("docker", "compose", "ps", "--services", "--status", "running")
	if err != nil {
		// Fallback for older compose versions (or environments where --status isn't supported).
		out, err = runCmd("docker", "compose", "ps", "--services")
	}
	if err == nil {
		for _, line := range strings.Split(out, "\n") {
			svc := strings.TrimSpace(line)
			if svc != "" {
				runningServices[svc] = true
			}
		}
	}

	if ctx.composeChanged {
		// Avoid implicit builds when Compose files change (some deployments use pull_policy: build).
		// The rebuild/restart logic below will handle builds explicitly when needed.
		args := []string{"compose", "up", "-d", "--remove-orphans", "--no-build"}
		if updateForceRecreate {
			args = append(args, "--force-recreate")
		}

		targets := map[string]bool{}
		for svc := range runningServices {
			targets[svc] = true
		}
		for svc := range ctx.servicesToRebuild {
			targets[svc] = true
		}
		for svc := range ctx.servicesToRestart {
			targets[svc] = true
		}

		// If admin_ui updates are excluded, ensure we never recreate/restart it as part of the
		// compose-changed `up` step.
		if !updateIncludeUI {
			delete(targets, "admin_ui")
		}

		// Only run compose-up if we have explicit targets; otherwise, don't implicitly start services.
		if len(targets) > 0 {
			args = append(args, sortedKeys(targets)...)
			if _, err := runCmd("docker", args...); err != nil {
				return fmt.Errorf("docker compose up (remove-orphans) failed: %w", err)
			}
		}
	}

	rebuildServices := sortedKeys(ctx.servicesToRebuild)
	restartServices := sortedKeys(ctx.servicesToRestart)

	// Avoid starting services that aren't already running unless explicitly targeted by rebuild/restart.
	if !updateIncludeUI {
		// If admin_ui is excluded, drop it even if a caller accidentally marked it.
		rebuildServices = filterSlice(rebuildServices, func(s string) bool { return s != "admin_ui" })
		restartServices = filterSlice(restartServices, func(s string) bool { return s != "admin_ui" })
	}

	// If a service isn't running, and we aren't rebuilding it, prefer to skip a plain restart
	// attempt (restart would fail anyway). We'll still allow the rebuild path to start it.
	if len(restartServices) > 0 {
		restartServices = filterSlice(restartServices, func(svc string) bool {
			return runningServices[svc]
		})
	}

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

func filterSlice(in []string, keep func(string) bool) []string {
	if len(in) == 0 {
		return nil
	}
	out := make([]string, 0, len(in))
	for _, v := range in {
		if keep(v) {
			out = append(out, v)
		}
	}
	return out
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

func printUpdateFailureRecovery(ctx *updateContext, err error) {
	fmt.Printf("\n==> Update failed\n")
	printUpdateInfo("Error: %v", err)

	if ctx == nil {
		return
	}

	if ctx.backupDir != "" {
		printUpdateInfo("Backups: %s", ctx.backupDir)
		fmt.Println("Recovery (restore operator-owned config):")
		fmt.Printf("  cp %s .env\n", filepath.Join(ctx.backupDir, ".env"))
		fmt.Printf("  cp %s %s\n", filepath.Join(ctx.backupDir, "config", "ai-agent.yaml"), filepath.Join("config", "ai-agent.yaml"))
		fmt.Printf("  cp %s %s  # if exists\n", filepath.Join(ctx.backupDir, "config", "ai-agent.local.yaml"), filepath.Join("config", "ai-agent.local.yaml"))
		fmt.Printf("  cp %s %s\n", filepath.Join(ctx.backupDir, "config", "users.json"), filepath.Join("config", "users.json"))
		fmt.Println("  # Replace contexts directory (if needed):")
		fmt.Printf("  rm -rf %s && cp -r %s %s\n",
			filepath.Join("config", "contexts"),
			filepath.Join(ctx.backupDir, "config", "contexts"),
			filepath.Join("config", "contexts"),
		)
	}

	if ctx.stashed {
		fmt.Println("Recovery (git stash):")
		fmt.Println("  git stash list")
		fmt.Println("  git stash pop   # may conflict; resolve if needed")
	}

	if ctx.repoRoot != "" {
		fmt.Println("If git reports 'dubious ownership':")
		fmt.Printf("  git config --global --add safe.directory %s\n", ctx.repoRoot)
	}
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

func updateHumanWriter() io.Writer {
	// When emitting machine-readable JSON plans, keep human output on stderr so stdout stays valid JSON.
	if updatePlan && updatePlanJSON {
		return os.Stderr
	}
	return os.Stdout
}

func printUpdateStep(title string) {
	fmt.Fprintf(updateHumanWriter(), "\n==> %s\n", title)
}

func printUpdateInfo(format string, args ...any) {
	fmt.Fprintf(updateHumanWriter(), " - "+format+"\n", args...)
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

func gitIsAncestor(ancestor string, descendant string) (bool, error) {
	gitArgs := make([]string, 0, 6)
	if gitSafeDirectory != "" {
		gitArgs = append(gitArgs, "-c", "safe.directory="+gitSafeDirectory)
	}
	gitArgs = append(gitArgs, "merge-base", "--is-ancestor", ancestor, descendant)

	cmd := exec.Command("git", gitArgs...)
	cmd.Stdin = nil
	cmd.Stdout = io.Discard
	var stderr bytes.Buffer
	cmd.Stderr = &stderr

	err := cmd.Run()
	if err == nil {
		return true, nil
	}
	if exitErr, ok := err.(*exec.ExitError); ok {
		// Exit status 1 means "not an ancestor" for --is-ancestor.
		if exitErr.ExitCode() == 1 {
			return false, nil
		}
	}
	msg := strings.TrimSpace(stderr.String())
	if msg != "" {
		return false, fmt.Errorf("git merge-base --is-ancestor failed: %s", msg)
	}
	return false, fmt.Errorf("git merge-base --is-ancestor failed: %w", err)
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
