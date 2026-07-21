#!/usr/bin/env bash
# Recover AAVA installations when the Admin UI update planner cannot run.
#
# This script intentionally wraps the supported `agent update` path. It repairs
# only bounded metadata ownership that older updater paths commonly left behind,
# repairs enough Git metadata for safe inspection, captures diagnostics/backups,
# installs the requested CLI, and then lets the CLI perform the real update,
# database snapshots, Docker changes, and check.

set -Eeuo pipefail

REPO_DEFAULT="/opt/Asterisk-AI-Voice-Agent"
REMOTE="origin"
REF="latest"
LOCAL_CHANGES="ask"
CHECKOUT_MODE="auto"
INCLUDE_UI="true"
AGENT_BIN="/usr/local/bin/agent"
YES="false"
PLAN_ONLY="false"
SKIP_REPAIR="false"
SKIP_CHECK="false"
STASH_UNTRACKED="false"

REPO=""
RECOVERY_DIR=""
TRAVERSAL_STATE=""
TEMP_HOME=""
TEMP_CLI_DIR=""
TEMP_BRANCH_CLI_DIR=""
SETPRIV_BIN=""
TARGET_UID=""
TARGET_GID=""
TARGET_GROUPS=""
TARGET_HOME=""
UNMERGED_COPIED="false"

usage() {
  cat <<'USAGE'
Usage:
  sudo bash scripts/update-recover.sh [options]

Recommended for a stuck release upgrade:
  AAVA_RECOVERY_REF=v7.4.2
  curl -fsSL "https://raw.githubusercontent.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/${AAVA_RECOVERY_REF}/scripts/update-recover.sh" \
    | sudo bash -s -- --repo /opt/Asterisk-AI-Voice-Agent --ref "${AAVA_RECOVERY_REF}" --include-ui

Options:
  --repo PATH                  AAVA checkout path (default: current Git checkout, then /opt/Asterisk-AI-Voice-Agent)
  --ref REF                    Target release tag or branch (default: latest published release)
  --remote NAME                Git remote to update from (default: origin)
  --local-changes POLICY       ask, retain, overwrite, or abort (default: ask)
  --include-ui                 Rebuild/restart Admin UI if needed (default)
  --no-include-ui              Exclude Admin UI rebuild/restart
  --checkout auto|true|false   Allow branch checkout; auto=false for release tags, true for branches
  --agent-bin PATH             Agent CLI path to install/run (default: /usr/local/bin/agent)
  --yes                        Do not ask for the final confirmation
  --plan-only                  Stop after installing CLI, repairing metadata, and writing a plan
  --no-repair                  Skip bounded ownership repair
  --skip-check                 Pass --skip-check to agent update
  --stash-untracked            Include untracked files in retain-mode updater stash
  -h, --help                   Show this help

Local-change policies:
  ask        Prompt in an interactive terminal if tracked code changes are present
  retain     Stash tracked local changes and reapply them after the update; may conflict
  overwrite  Discard tracked source-code edits after preserving recovery patches
  abort      Stop before update if tracked local changes are present

Safety:
  The script captures diagnostics, tracked-change patches, and a best-effort
  config/data backup before update. It may repair .git and Git-tracked path
  ownership before it can inspect local changes; later ownership repair is
  limited to .agent. Untracked runtime/operator files are not removed unless
  the underlying updater is explicitly run with a compatible untracked-stash
  policy.
USAGE
}

log() {
  printf '%s\n' "$*"
}

die() {
  log "ERROR: $*" >&2
  exit 2
}

warn() {
  log "WARN: $*" >&2
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

is_release_ref() {
  [[ "$1" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

normalize_release_ref() {
  local ref="$1"
  if [[ "$ref" =~ ^v?([0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
    printf 'v%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

strip_trailing_slashes() {
  local path="$1"
  while [ "$path" != "/" ] && [ "${path%/}" != "$path" ]; do
    path="${path%/}"
  done
  printf '%s\n' "$path"
}

resolve_existing_path() {
  if command -v realpath >/dev/null 2>&1; then
    realpath -e -- "$1"
  else
    readlink -f -- "$1"
  fi
}

redact_remote_url() {
  sed -E \
    -e 's#([[:alpha:]][[:alnum:].+.-]*://)[^/@[:space:]]+@#\1[redacted]@#g' \
    -e 's#([?&](([Aa][Cc][Cc][Ee][Ss][Ss]|[Aa][Uu][Tt][Hh]|[Bb][Ee][Aa][Rr][Ee][Rr]|[Oo][Aa][Uu][Tt][Hh]|[Pp][Rr][Ii][Vv][Aa][Tt][Ee]|[Rr][Ee][Ff][Rr][Ee][Ss][Hh])[_-]?[Tt][Oo][Kk][Ee][Nn]|[Cc][Ll][Ii][Ee][Nn][Tt][_-]?[Ss][Ee][Cc][Rr][Ee][Tt]|[Pp][Aa][Ss][Ss][Ww][Oo][Rr][Dd]|[Tt][Oo][Kk][Ee][Nn])=)[^&#[:space:]]+#\1[redacted]#g'
}

require_plain_recovery_dir() {
  local path="$1"
  if [ -L "${path}" ]; then
    die "refusing symlinked recovery state: ${path}"
  fi
  if [ -e "${path}" ] && [ ! -d "${path}" ]; then
    die "refusing non-directory recovery state: ${path}"
  fi
}

compute_target_groups() {
  local user_name
  if [ -n "${TARGET_GROUPS}" ]; then
    return 0
  fi
  if [ -z "${TARGET_UID}" ]; then
    TARGET_UID="$(stat -c '%u' "${REPO}")" || die "failed to read checkout owner UID"
  fi
  if [ -z "${TARGET_GID}" ]; then
    TARGET_GID="$(stat -c '%g' "${REPO}")" || die "failed to read checkout owner GID"
  fi
  user_name="$(getent passwd "${TARGET_UID}" 2>/dev/null | cut -d: -f1 | head -n 1 || true)"
  if [ -n "${user_name}" ]; then
    TARGET_GROUPS="$(id -G "${user_name}" 2>/dev/null | tr ' ' ',' || true)"
  fi
  TARGET_GROUPS="${TARGET_GROUPS:-${TARGET_GID}}"
}

ensure_owner_context() {
  if [ -z "${TARGET_UID}" ]; then
    TARGET_UID="$(stat -c '%u' "${REPO}")" || die "failed to read checkout owner UID"
  fi
  if [ -z "${TARGET_GID}" ]; then
    TARGET_GID="$(stat -c '%g' "${REPO}")" || die "failed to read checkout owner GID"
  fi
  if [ -z "${TARGET_HOME}" ]; then
    TARGET_HOME="$(getent passwd "${TARGET_UID}" 2>/dev/null | cut -d: -f6 | head -n 1 || true)"
  fi
  if [ -z "${TARGET_HOME}" ] || [ ! -d "${TARGET_HOME}" ]; then
    TARGET_HOME="/tmp"
  fi
  compute_target_groups
  if [ "${TARGET_UID}" != "0" ]; then
    SETPRIV_BIN="${SETPRIV_BIN:-$(command -v setpriv || true)}"
    [ -n "${SETPRIV_BIN}" ] || die "setpriv is required to inspect and update checkout as owner UID ${TARGET_UID}; install util-linux and retry"
  fi
}

run_as_checkout_owner_home() {
  ensure_owner_context
  if [ "${TARGET_UID}" = "0" ]; then
    "$@"
  else
    "${SETPRIV_BIN}" --reuid="${TARGET_UID}" --regid="${TARGET_GID}" --groups="${TARGET_GROUPS}" \
      /usr/bin/env "HOME=${TARGET_HOME}" "$@"
  fi
}

safe_chown_tree() {
  local path="$1"
  python3 - "${path}" "${TARGET_UID}" "${TARGET_GID}" <<'PY'
import os
import stat
import sys

root = sys.argv[1]
uid = int(sys.argv[2])
gid = int(sys.argv[3])

st = os.lstat(root)
if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
    raise SystemExit(f"refusing unsafe ownership root: {root}")

for dirpath, dirnames, filenames, dirfd in os.fwalk(root, topdown=True, follow_symlinks=False):
    os.fchown(dirfd, uid, gid)
    keep = []
    for name in list(dirnames):
        try:
            child = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        os.chown(name, uid, gid, dir_fd=dirfd, follow_symlinks=False)
        if stat.S_ISDIR(child.st_mode):
            keep.append(name)
    dirnames[:] = keep
    for name in filenames:
        try:
            os.chown(name, uid, gid, dir_fd=dirfd, follow_symlinks=False)
        except FileNotFoundError:
            continue
PY
}

safe_chown_tracked_paths() {
  local tracked_list="$1"
  python3 - "${REPO}" "${TARGET_UID}" "${TARGET_GID}" "${tracked_list}" <<'PY'
import os
import stat
import sys

repo = sys.argv[1]
uid = int(sys.argv[2])
gid = int(sys.argv[3])
tracked_list = sys.argv[4]
protected_roots = {"data", "models", "secrets"}

root_fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY)
try:
    with open(tracked_list, "rb") as fh:
        rels = [entry.decode("utf-8", "surrogateescape") for entry in fh.read().split(b"\0") if entry]
    for rel in rels:
        parts = rel.split("/")
        if rel.startswith("/") or any(part in ("", ".", "..") for part in parts):
            raise SystemExit(f"refusing unsafe tracked path: {rel}")
        protected_subtree = parts[0] in protected_roots
        parent_fd = os.dup(root_fd)
        try:
            for index, part in enumerate(parts):
                last = index == len(parts) - 1
                try:
                    child = os.stat(part, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    break
                if not (protected_subtree and not last):
                    os.chown(part, uid, gid, dir_fd=parent_fd, follow_symlinks=False)
                if not last:
                    if not stat.S_ISDIR(child.st_mode):
                        raise SystemExit(f"refusing non-directory tracked parent: {rel}")
                    next_fd = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=parent_fd,
                    )
                    os.close(parent_fd)
                    parent_fd = next_fd
        finally:
            os.close(parent_fd)
finally:
    os.close(root_fd)
PY
}

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --repo)
        [ "$#" -ge 2 ] || die "--repo requires a path"
        REPO="$2"
        shift 2
        ;;
      --ref)
        [ "$#" -ge 2 ] || die "--ref requires a tag or branch"
        REF="$2"
        shift 2
        ;;
      --remote)
        [ "$#" -ge 2 ] || die "--remote requires a name"
        REMOTE="$2"
        shift 2
        ;;
      --local-changes)
        [ "$#" -ge 2 ] || die "--local-changes requires ask, retain, overwrite, or abort"
        LOCAL_CHANGES="$2"
        shift 2
        ;;
      --include-ui)
        INCLUDE_UI="true"
        shift
        ;;
      --no-include-ui)
        INCLUDE_UI="false"
        shift
        ;;
      --checkout)
        [ "$#" -ge 2 ] || die "--checkout requires auto, true, or false"
        CHECKOUT_MODE="$2"
        shift 2
        ;;
      --agent-bin)
        [ "$#" -ge 2 ] || die "--agent-bin requires a path"
        AGENT_BIN="$2"
        shift 2
        ;;
      --yes)
        YES="true"
        shift
        ;;
      --plan-only)
        PLAN_ONLY="true"
        shift
        ;;
      --no-repair)
        SKIP_REPAIR="true"
        shift
        ;;
      --skip-check)
        SKIP_CHECK="true"
        shift
        ;;
      --stash-untracked)
        STASH_UNTRACKED="true"
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "unknown option: $1"
        ;;
    esac
  done
}

validate_args() {
  LOCAL_CHANGES="$(printf '%s' "${LOCAL_CHANGES}" | tr '[:upper:]' '[:lower:]')"
  CHECKOUT_MODE="$(printf '%s' "${CHECKOUT_MODE}" | tr '[:upper:]' '[:lower:]')"
  case "${LOCAL_CHANGES}" in
    ask|retain|overwrite|abort) ;;
    *) die "--local-changes must be ask, retain, overwrite, or abort" ;;
  esac
  case "${CHECKOUT_MODE}" in
    auto|true|false) ;;
    *) die "--checkout must be auto, true, or false" ;;
  esac
  case "${INCLUDE_UI}" in
    true|false) ;;
    *) die "--include-ui state must be true or false" ;;
  esac
  case "${YES}" in
    true|false) ;;
    *) die "--yes state must be true or false" ;;
  esac
  case "${PLAN_ONLY}" in
    true|false) ;;
    *) die "--plan-only state must be true or false" ;;
  esac
  case "${SKIP_REPAIR}" in
    true|false) ;;
    *) die "--no-repair state must be true or false" ;;
  esac
  case "${SKIP_CHECK}" in
    true|false) ;;
    *) die "--skip-check state must be true or false" ;;
  esac
  case "${STASH_UNTRACKED}" in
    true|false) ;;
    *) die "--stash-untracked state must be true or false" ;;
  esac
  if [ "${STASH_UNTRACKED}" = "true" ] && [ "${LOCAL_CHANGES}" = "overwrite" ]; then
    die "--stash-untracked cannot be combined with --local-changes=overwrite; use retain or preserve untracked files manually first"
  fi
  case "${AGENT_BIN}" in
    /*/agent|/agent) ;;
    *) die "--agent-bin must be an absolute path ending in /agent" ;;
  esac
  [ -n "${REMOTE}" ] || die "--remote cannot be empty"
  [ -n "${REF}" ] || die "--ref cannot be empty"
}

detect_repo() {
  local candidate
  if [ -n "${REPO}" ]; then
    candidate="${REPO}"
  elif [ -d ".git" ]; then
    candidate="$(pwd)"
  elif [ -d "${REPO_DEFAULT}/.git" ]; then
    candidate="${REPO_DEFAULT}"
  else
    die "could not detect the AAVA checkout; pass --repo /path/to/Asterisk-AI-Voice-Agent"
  fi
  candidate="$(strip_trailing_slashes "${candidate}")"
  [ -d "${candidate}" ] || die "repo path does not exist: ${candidate}"
  REPO="$(cd "${candidate}" && pwd -P)" || die "cannot enter repo path: ${candidate}"
  [ -d "${REPO}/.git" ] || die "automatic recovery requires a normal checkout with a .git directory: ${REPO}"
}

git_repo() {
  run_as_checkout_owner_home git \
    -c "safe.directory=${REPO}" \
    -c core.fsmonitor=false \
    -c core.hooksPath=/dev/null \
    --git-dir="${REPO}/.git" \
    --work-tree="${REPO}" \
    "$@"
}

git_repo_preserve_diff() {
  run_as_checkout_owner_home /usr/bin/env -u GIT_EXTERNAL_DIFF -u GIT_DIFF_OPTS git \
    -c "safe.directory=${REPO}" \
    -c core.fsmonitor=false \
    -c core.hooksPath=/dev/null \
    -c diff.external= \
    --git-dir="${REPO}/.git" \
    --work-tree="${REPO}" \
    diff --binary --no-ext-diff --no-textconv "$@"
}

capture_command() {
  local name="$1"
  shift
  {
    printf '$'
    printf ' %q' "$@"
    printf '\n'
    "$@"
  } >"${RECOVERY_DIR}/${name}.log" 2>&1 || true
}

capture_git() {
  local name="$1"
  shift
  {
    printf '$ git'
    printf ' %q' "$@"
    printf '\n'
    git_repo "$@"
  } >"${RECOVERY_DIR}/${name}.log" 2>&1 || true
}

capture_git_remotes() {
  {
    printf '$ git remote -v\n'
    git_repo remote -v | redact_remote_url
  } >"${RECOVERY_DIR}/remotes.log" 2>&1 || true
}

prepare_recovery_dir() {
  local recovery_base ts
  recovery_base="/var/tmp"
  [ -d "${recovery_base}" ] || recovery_base="/tmp"
  ts="$(date -u +%Y%m%d_%H%M%S)"
  RECOVERY_DIR="$(mktemp -d "${recovery_base}/aava-update-recovery-${ts}.XXXXXX")" \
    || die "failed to create recovery directory under ${recovery_base}"
  require_plain_recovery_dir "${RECOVERY_DIR}"
  chmod 0700 -- "${RECOVERY_DIR}" 2>/dev/null || true
  ensure_owner_context
}

capture_diagnostics() {
  log "==> Capturing diagnostics"
  {
    printf 'timestamp_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'repo=%s\n' "${REPO}"
    printf 'remote=%s\n' "${REMOTE}"
    printf 'ref=%s\n' "${REF}"
    printf 'include_ui=%s\n' "${INCLUDE_UI}"
    printf 'checkout_mode=%s\n' "${CHECKOUT_MODE}"
    printf 'local_changes=%s\n' "${LOCAL_CHANGES}"
    printf 'uname=%s\n' "$(uname -a)"
    printf 'euid=%s\n' "$(id -u)"
  } >"${RECOVERY_DIR}/recovery.env"

  capture_command uname uname -a
  capture_command id id
  capture_command df df -h "${REPO}"
  capture_command repo-ownership ls -ldn "${REPO}" "${REPO}/.git" "${REPO}/.agent"
  capture_git status status --short --branch
  capture_git rev-parse rev-parse HEAD
  capture_git_remotes
  capture_git stash-list stash list
  if command -v docker >/dev/null 2>&1; then
    capture_command docker-ps docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
  fi
}

copy_unmerged_files() {
  if [ "${UNMERGED_COPIED}" = "true" ]; then
    return 0
  fi
  local conflict_dir list_file rel src dst
  conflict_dir="${RECOVERY_DIR}/unmerged-files"
  list_file="${RECOVERY_DIR}/unmerged-tracked-files.txt"
  mkdir -p -- "${conflict_dir}"
  git_repo diff --name-only --diff-filter=U -z >"${list_file}.nul" \
    || die "failed to enumerate unmerged tracked files"
  : >"${list_file}"
  while IFS= read -r -d '' rel; do
    case "${rel}" in
      ""|/*|../*|*/../*|*/..)
        die "refusing unsafe unmerged path: ${rel}"
        ;;
    esac
    printf '%s\n' "${rel}" >>"${list_file}"
    src="${REPO}/${rel}"
    dst="${conflict_dir}/${rel}"
    if [ -e "${src}" ] || [ -L "${src}" ]; then
      mkdir -p -- "$(dirname "${dst}")"
      cp -a -- "${src}" "${dst}"
    fi
  done <"${list_file}.nul"
  UNMERGED_COPIED="true"
}

capture_preupdate_artifacts() {
  log "==> Preserving local state before update"
  git_repo status --short >/dev/null || die "failed to inspect checkout changes; update not attempted"
  if has_unmerged_paths && [ "${LOCAL_CHANGES}" = "overwrite" ]; then
    copy_unmerged_files
  fi
  if ! git_repo_preserve_diff --cached >"${RECOVERY_DIR}/staged-tracked.patch"; then
    if has_unmerged_paths && [ "${LOCAL_CHANGES}" = "overwrite" ]; then
      warn "Could not create a staged binary patch because the checkout has unmerged paths; conflicted files are copied to ${RECOVERY_DIR}/unmerged-files."
      git_repo diff --no-ext-diff --no-textconv --cached >"${RECOVERY_DIR}/staged-tracked.diff" 2>"${RECOVERY_DIR}/staged-tracked.diff.err" || true
      copy_unmerged_files
    else
      die "failed to preserve staged tracked edits; update not attempted"
    fi
  fi
  if ! git_repo_preserve_diff >"${RECOVERY_DIR}/unstaged-tracked.patch"; then
    if has_unmerged_paths && [ "${LOCAL_CHANGES}" = "overwrite" ]; then
      warn "Could not create an unstaged binary patch because the checkout has unmerged paths; conflicted files are copied to ${RECOVERY_DIR}/unmerged-files."
      git_repo diff --no-ext-diff --no-textconv >"${RECOVERY_DIR}/unstaged-tracked.diff" 2>"${RECOVERY_DIR}/unstaged-tracked.diff.err" || true
      copy_unmerged_files
    else
      die "failed to preserve unstaged tracked edits; update not attempted"
    fi
  fi
  git_repo status --porcelain --untracked-files=all >"${RECOVERY_DIR}/git-status-porcelain.txt" \
    || die "failed to save checkout status; update not attempted"

  local backup_dir
  backup_dir="${RECOVERY_DIR}/pre-update-files"
  mkdir -p -- "${backup_dir}/config" "${backup_dir}/data/operator" "${backup_dir}/data"
  for rel in ".env" "config/ai-agent.yaml" "config/ai-agent.local.yaml" "config/users.json"; do
    if [ -e "${REPO}/${rel}" ] || [ -L "${REPO}/${rel}" ]; then
      mkdir -p -- "${backup_dir}/$(dirname "${rel}")"
      cp -a -- "${REPO}/${rel}" "${backup_dir}/${rel}"
    fi
  done
  if [ -d "${REPO}/config/contexts" ]; then
    cp -a -- "${REPO}/config/contexts" "${backup_dir}/config/contexts"
  fi
  for rel in "data/operator/agents.db" "data/operator/agents.db-wal" "data/operator/agents.db-shm" \
             "data/call_history.db" "data/call_history.db-wal" "data/call_history.db-shm"; do
    if [ -e "${REPO}/${rel}" ]; then
      mkdir -p -- "${backup_dir}/$(dirname "${rel}")"
      cp -a -- "${REPO}/${rel}" "${backup_dir}/${rel}"
    fi
  done
}

tracked_dirty_files() {
  git_repo status --porcelain --untracked-files=no
}

has_unmerged_paths() {
  local unmerged
  unmerged="$(git_repo diff --name-only --diff-filter=U 2>/dev/null || true)"
  [ -n "${unmerged}" ]
}

prompt_local_changes_if_needed() {
  local dirty
  dirty="$(tracked_dirty_files)"
  if [ -z "${dirty}" ]; then
    if [ "${LOCAL_CHANGES}" = "ask" ]; then
      LOCAL_CHANGES="abort"
    fi
    return 0
  fi

  log "Tracked local changes were found:"
  printf '%s\n' "${dirty}" | sed -n '1,25p'
  if [ "$(printf '%s\n' "${dirty}" | wc -l | tr -d ' ')" -gt 25 ]; then
    log "... additional paths omitted; current status is in ${RECOVERY_DIR}/status.log"
  fi
  if has_unmerged_paths; then
    warn "Unmerged paths are present from a previous failed update or manual merge."
    case "${LOCAL_CHANGES}" in
      retain)
        die "cannot retain unmerged paths with stash; resolve them manually or re-run with --local-changes=overwrite"
        ;;
      ask)
        warn "Retain is disabled for this checkout; choose overwrite to reset conflicted tracked files, or abort."
        ;;
    esac
  fi

  case "${LOCAL_CHANGES}" in
    retain)
      warn "Retain mode will stash and reapply tracked source edits; conflicts may require manual resolution."
      return 0
      ;;
    overwrite)
      warn "Overwrite mode will discard tracked source-code edits after preserving patches in ${RECOVERY_DIR}."
      return 0
      ;;
    abort)
      die "tracked local changes are present and --local-changes=abort was selected"
      ;;
    ask) ;;
  esac

  [ -r /dev/tty ] || die "tracked local changes are present; re-run with --local-changes=retain, overwrite, or abort"
  while true; do
    {
      printf '\nChoose how to handle tracked local changes:\n'
      if has_unmerged_paths; then
        printf '  r) retain    unavailable while unmerged paths are present\n'
      else
        printf '  r) retain    stash and reapply after update (may conflict)\n'
      fi
      printf '  o) overwrite discard tracked source-code edits after preserving patches\n'
      printf '  a) abort     stop before changing the checkout\n'
      printf 'Selection [a]: '
    } >/dev/tty
    local answer
    IFS= read -r answer </dev/tty || answer=""
    answer="$(printf '%s' "${answer}" | tr '[:upper:]' '[:lower:]')"
    case "${answer}" in
      r|retain)
        if has_unmerged_paths; then
          printf 'Retain is unavailable while unmerged paths are present.\n' >/dev/tty
          continue
        fi
        LOCAL_CHANGES="retain"
        return 0
        ;;
      o|overwrite)
        if [ "${STASH_UNTRACKED}" = "true" ]; then
          printf 'Overwrite is unavailable with --stash-untracked; re-run without --stash-untracked or choose retain/abort.\n' >/dev/tty
          continue
        fi
        LOCAL_CHANGES="overwrite"
        warn "Tracked source-code edits will be discarded by the updater; recovery patches are in ${RECOVERY_DIR}."
        return 0
        ;;
      ""|a|abort)
        die "update aborted by operator before changing the checkout"
        ;;
      *)
        printf 'Unrecognized selection: %s\n' "${answer}" >/dev/tty
        ;;
    esac
  done
}

resolve_latest_release() {
  local latest=""
  if command -v curl >/dev/null 2>&1; then
    latest="$(curl -fsSL --connect-timeout 20 --max-time 300 --retry 3 https://api.github.com/repos/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/releases/latest \
      | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)"
  elif command -v wget >/dev/null 2>&1; then
    latest="$(wget -q --timeout=60 --tries=3 -O- https://api.github.com/repos/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/releases/latest \
      | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)"
  else
    die "curl or wget is required to resolve --ref latest"
  fi
  [ -n "${latest}" ] || die "failed to resolve latest published release"
  printf '%s\n' "${latest}"
}

download_url() {
  local url="$1"
  local dest="$2"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --connect-timeout 20 --max-time 300 --retry 3 -o "${dest}" "${url}"
  elif command -v wget >/dev/null 2>&1; then
    wget -q --timeout=60 --tries=3 -O "${dest}" "${url}"
  else
    die "curl or wget is required to download release assets"
  fi
}

sha256_file() {
  local path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${path}" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "${path}" | awk '{print $1}'
  else
    die "sha256sum or shasum is required to verify release assets"
  fi
}

release_binary_name() {
  local os arch platform
  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  arch="$(uname -m)"
  case "${os}" in
    linux) platform="linux" ;;
    *) die "release CLI recovery supports Linux hosts only, got OS: ${os}" ;;
  esac
  case "${arch}" in
    x86_64|amd64) arch="amd64" ;;
    aarch64|arm64) arch="arm64" ;;
    *) die "unsupported architecture for release CLI recovery: ${arch}" ;;
  esac
  printf 'agent-%s-%s\n' "${platform}" "${arch}"
}

install_release_cli() {
  local version="$1"
  local install_dir
  local binary_name base_url binary_path sums_path expected actual
  install_dir="$(dirname "${AGENT_BIN}")"
  binary_name="$(release_binary_name)"
  base_url="https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/releases/download/${version}"
  TEMP_CLI_DIR="$(mktemp -d /tmp/aava-cli-install.XXXXXXXXXX)" || die "failed to create private CLI install directory"
  chmod 0700 "${TEMP_CLI_DIR}" || die "failed to secure private CLI install directory"
  binary_path="${TEMP_CLI_DIR}/${binary_name}"
  sums_path="${TEMP_CLI_DIR}/SHA256SUMS"

  log "==> Installing agent CLI ${version} to ${AGENT_BIN}"
  download_url "${base_url}/${binary_name}" "${binary_path}" \
    || die "failed to download release CLI asset ${binary_name}"
  download_url "${base_url}/SHA256SUMS" "${sums_path}" \
    || die "failed to download release CLI checksums"
  expected="$(awk -v name="${binary_name}" '$2 == name || $2 == "*" name { print $1; exit }' "${sums_path}")"
  [ -n "${expected}" ] || die "checksum for ${binary_name} not found in SHA256SUMS"
  actual="$(sha256_file "${binary_path}")"
  [ "${expected}" = "${actual}" ] || die "checksum mismatch for ${binary_name}"

  mkdir -p -- "${install_dir}" || die "failed to create CLI install directory for ${AGENT_BIN}"
  install -m 0755 "${binary_path}" "${AGENT_BIN}" \
    || die "failed to install release CLI to ${AGENT_BIN}"
  rm -rf -- "${TEMP_CLI_DIR}"
  TEMP_CLI_DIR=""
}

install_branch_cli() {
  local ref="$1"
  need_cmd docker
  local remote_url tmp_src clone_err clone_url escaped_remote_url owner_gitconfig xdg_gitconfig escaped_include
  remote_url="$(git_repo ls-remote --get-url "${REMOTE}" 2>/dev/null || true)"
  if [ -z "${remote_url}" ]; then
    remote_url="$(git_repo config --get "remote.${REMOTE}.url" 2>/dev/null || true)"
  fi
  [ -n "${remote_url}" ] || die "failed to resolve remote ${REMOTE} for CLI source"

  tmp_src="$(mktemp -d /tmp/aava-cli-src.XXXXXXXXXX)" || die "failed to create temporary CLI source directory"
  TEMP_BRANCH_CLI_DIR="${tmp_src}"
  chown "${TARGET_UID}:${TARGET_GID}" "${tmp_src}" \
    || die "failed to hand temporary CLI source directory to checkout owner"
  owner_gitconfig="${TARGET_HOME}/.gitconfig"
  xdg_gitconfig="${TARGET_HOME}/.config/git/config"
  : >"${tmp_src}/gitconfig" || die "failed to create temporary Git URL rewrite config"
  for owner_gitconfig in "${owner_gitconfig}" "${xdg_gitconfig}"; do
    if [ -f "${owner_gitconfig}" ]; then
      escaped_include="${owner_gitconfig//\\/\\\\}"
      escaped_include="${escaped_include//\"/\\\"}"
      printf '[include]\n\tpath = "%s"\n' "${escaped_include}" >>"${tmp_src}/gitconfig" \
        || die "failed to include checkout-owner Git config"
    fi
  done
  escaped_remote_url="${remote_url//\\/\\\\}"
  escaped_remote_url="${escaped_remote_url//\"/\\\"}"
  printf '[url "%s"]\n\tinsteadOf = aava-recovery-origin:\n' "${escaped_remote_url}" >>"${tmp_src}/gitconfig" \
    || die "failed to write temporary Git URL rewrite config"
  chmod 0600 "${tmp_src}/gitconfig" \
    || die "failed to secure temporary Git URL rewrite config"
  chown "${TARGET_UID}:${TARGET_GID}" "${tmp_src}/gitconfig" \
    || die "failed to hand temporary Git URL rewrite config to checkout owner"
  clone_err="${tmp_src}/git-clone.err"
  clone_url="aava-recovery-origin:"
  log "==> Building agent CLI from ${REMOTE}/${ref}"
  if ! run_as_checkout_owner_home /usr/bin/env "GIT_CONFIG_GLOBAL=${tmp_src}/gitconfig" \
    git clone --quiet --depth 1 --single-branch --branch "${ref}" -- "${clone_url}" "${tmp_src}/repo" \
    2>"${clone_err}"; then
    if [ -s "${clone_err}" ]; then
      redact_remote_url <"${clone_err}" >&2 || true
    fi
    die "failed to fetch selected CLI source ${ref} from $(printf '%s\n' "${remote_url}" | redact_remote_url)"
  fi
  mkdir -p -- "${tmp_src}/out"
  docker run --rm \
    -v "${tmp_src}/repo/cli:/src:ro,Z" \
    -v "${tmp_src}/out:/out:Z" \
    -w /src \
    -e HOME=/tmp \
    -e GOCACHE=/tmp/go-build \
    -e GOMODCACHE=/tmp/go-mod \
    -e "AAVA_CLI_VERSION=${ref}" \
    golang:1.22-bookworm \
    bash -c 'go mod download && CGO_ENABLED=0 go build -buildvcs=false -ldflags "-X main.version=$AAVA_CLI_VERSION" -o /out/agent ./cmd/agent' \
    || die "failed to build CLI from selected ref"
  mkdir -p -- "$(dirname "${AGENT_BIN}")" \
    || die "failed to create CLI install directory for ${AGENT_BIN}"
  install -m 0755 "${tmp_src}/out/agent" "${AGENT_BIN}" \
    || die "failed to install selected-ref CLI to ${AGENT_BIN}"
  rm -rf -- "${tmp_src}"
  TEMP_BRANCH_CLI_DIR=""
}

install_target_cli() {
  if [ "${REF}" = "latest" ]; then
    REF="$(resolve_latest_release)"
  fi
  if is_release_ref "${REF}"; then
    REF="$(normalize_release_ref "${REF}")"
    install_release_cli "${REF}"
  else
    install_branch_cli "${REF}"
  fi
  run_as_checkout_owner_home "${AGENT_BIN}" version >"${RECOVERY_DIR}/agent-version.log" 2>&1 \
    || die "installed agent CLI is not runnable: ${AGENT_BIN}"
}

checkout_value() {
  case "${CHECKOUT_MODE}" in
    true|false)
      printf '%s\n' "${CHECKOUT_MODE}"
      ;;
    auto)
      if is_release_ref "${REF}"; then
        printf 'false\n'
      else
        printf 'true\n'
      fi
      ;;
  esac
}

repair_git_metadata_ownership() {
  if [ "${SKIP_REPAIR}" = "true" ]; then
    log "==> Skipping ownership repair"
    return 0
  fi

  log "==> Repairing bounded Git metadata ownership"
  TARGET_UID="$(stat -c '%u' "${REPO}")" || die "failed to read checkout owner UID"
  TARGET_GID="$(stat -c '%g' "${REPO}")" || die "failed to read checkout owner GID"

  if [ -L "${REPO}/.git" ] || [ ! -d "${REPO}/.git" ]; then
    die "refusing automatic repair for linked, symlinked, or missing .git metadata"
  fi

  local expected_git_dir
  expected_git_dir="$(resolve_existing_path "${REPO}/.git")" || die "failed to resolve .git directory"

  safe_chown_tree "${expected_git_dir}" \
    || die "failed to repair .git ownership"
}

repair_tracked_paths_ownership() {
  if [ "${SKIP_REPAIR}" = "true" ]; then
    return 0
  fi

  log "==> Repairing bounded tracked checkout ownership"
  local tracked_list
  tracked_list="$(mktemp)" || die "failed to create tracked ownership scan"
  git_repo ls-files -z >"${tracked_list}" || {
    rm -f -- "${tracked_list}"
    die "failed to enumerate tracked checkout paths"
  }
  safe_chown_tracked_paths "${tracked_list}" || {
    rm -f -- "${tracked_list}"
    die "failed to repair tracked checkout ownership"
  }
  rm -f -- "${tracked_list}"
}

repair_agent_state_ownership() {
  if [ "${SKIP_REPAIR}" = "true" ]; then
    return 0
  fi

  log "==> Repairing bounded updater state ownership"
  if [ -L "${REPO}/.agent" ]; then
    die "refusing symlinked .agent state"
  fi
  if [ -e "${REPO}/.agent" ]; then
    [ -d "${REPO}/.agent" ] || die "refusing non-directory .agent state"
    safe_chown_tree "${REPO}/.agent" \
      || die "failed to repair .agent ownership"
  fi
}

secure_recovery_artifacts() {
  python3 - "${RECOVERY_DIR}" "${TARGET_UID}" "${TARGET_GID}" <<'PY'
import os
import stat
import sys

root = sys.argv[1]
uid = int(sys.argv[2])
gid = int(sys.argv[3])

root_st = os.lstat(root)
if stat.S_ISLNK(root_st.st_mode) or not stat.S_ISDIR(root_st.st_mode):
    raise SystemExit(f"refusing unsafe recovery artifact root: {root}")

for dirpath, dirnames, filenames, dirfd in os.fwalk(root, topdown=True, follow_symlinks=False):
    os.fchown(dirfd, uid, gid)
    os.fchmod(dirfd, 0o700)

    keep = []
    for name in list(dirnames):
        try:
            child = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        os.chown(name, uid, gid, dir_fd=dirfd, follow_symlinks=False)
        if stat.S_ISDIR(child.st_mode):
            keep.append(name)
    dirnames[:] = keep

    for name in filenames:
        try:
            child = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        os.chown(name, uid, gid, dir_fd=dirfd, follow_symlinks=False)
        if not stat.S_ISREG(child.st_mode):
            continue
        fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0), dir_fd=dirfd)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
PY
}

cleanup() {
  local status=$?
  local restore_failed="false"
  if [ -n "${TRAVERSAL_STATE}" ] && [ -f "${TRAVERSAL_STATE}" ]; then
    while IFS="$(printf '\t')" read -r mode parent; do
      if [ -n "${mode}" ] && [ -n "${parent}" ]; then
        if ! chmod "${mode}" -- "${parent}" 2>/dev/null; then
          warn "failed to restore traversal permissions ${mode} on ${parent}; state file retained at ${TRAVERSAL_STATE}"
          restore_failed="true"
        fi
      fi
    done <"${TRAVERSAL_STATE}"
    if [ "${restore_failed}" = "false" ]; then
      rm -f -- "${TRAVERSAL_STATE}" 2>/dev/null || true
    elif [ "${status}" -eq 0 ]; then
      status=2
    fi
  fi
  if [ -n "${TEMP_HOME}" ]; then
    rm -rf -- "${TEMP_HOME}" 2>/dev/null || true
  fi
  if [ -n "${TEMP_CLI_DIR}" ]; then
    rm -rf -- "${TEMP_CLI_DIR}" 2>/dev/null || true
  fi
  if [ -n "${TEMP_BRANCH_CLI_DIR}" ]; then
    rm -rf -- "${TEMP_BRANCH_CLI_DIR}" 2>/dev/null || true
  fi
  if [ -n "${RECOVERY_DIR}" ] && [ -d "${RECOVERY_DIR}" ] && [ ! -L "${RECOVERY_DIR}" ] \
    && [ -n "${TARGET_UID}" ] && [ -n "${TARGET_GID}" ]; then
    secure_recovery_artifacts 2>/dev/null || true
  fi
  exit "${status}"
}

signal_exit() {
  exit "$1"
}

prepare_updater_state_dirs() {
  local agent_dir updates_dir backups_dir
  agent_dir="${REPO}/.agent"
  updates_dir="${agent_dir}/updates"
  backups_dir="${agent_dir}/update-backups"
  require_plain_recovery_dir "${agent_dir}"
  mkdir -p -- "${agent_dir}" || die "failed to create updater state directory: ${agent_dir}"
  require_plain_recovery_dir "${agent_dir}"
  require_plain_recovery_dir "${updates_dir}"
  require_plain_recovery_dir "${backups_dir}"
  mkdir -p -- "${updates_dir}" "${backups_dir}" || die "failed to create updater runtime directories"
  require_plain_recovery_dir "${updates_dir}"
  require_plain_recovery_dir "${backups_dir}"
  chown --no-dereference "${TARGET_UID}:${TARGET_GID}" "${agent_dir}" "${updates_dir}" "${backups_dir}" \
    || die "failed to hand updater runtime metadata to checkout owner"
  chmod 0750 -- "${agent_dir}" "${updates_dir}" "${backups_dir}" 2>/dev/null || true
}

prepare_owner_execution() {
  ensure_owner_context
  if [ "${TARGET_UID}" = "0" ]; then
    return 0
  fi

  TEMP_HOME="$(mktemp -d /tmp/aava-update-home.XXXXXXXXXX)" || die "failed to create temporary HOME"
  chmod 0700 -- "${TEMP_HOME}"
  chown "${TARGET_UID}:${TARGET_GID}" "${TEMP_HOME}"

  TRAVERSAL_STATE="$(mktemp)" || die "failed to create traversal restore state"
  local parent mode
  parent="$(dirname "${REPO}")"
  while [ "${parent}" != "/" ]; do
    if ! "${SETPRIV_BIN}" --reuid="${TARGET_UID}" --regid="${TARGET_GID}" --groups="${TARGET_GROUPS}" test -x "${parent}"; then
      mode="$(stat -c '%a' "${parent}")" || die "failed to read mode for ${parent}"
      printf '%s\t%s\n' "${mode}" "${parent}" >>"${TRAVERSAL_STATE}"
      chmod a+x -- "${parent}" || die "failed to make updater parent traversable: ${parent}"
    fi
    parent="$(dirname "${parent}")"
  done
}

run_as_owner() {
  if [ "${TARGET_UID:-0}" = "0" ]; then
    (cd "${REPO}" && "$@")
  else
    local update_home
    update_home="${TARGET_HOME}"
    "${SETPRIV_BIN}" --reuid="${TARGET_UID}" --regid="${TARGET_GID}" --groups="${TARGET_GROUPS}" \
      /usr/bin/env "HOME=${update_home}" /bin/sh -c 'cd "$1" && shift && exec "$@"' sh "${REPO}" "$@"
  fi
}

check_owner_docker_access() {
  local docker_sock user_name
  docker_sock="${DOCKER_SOCK:-/var/run/docker.sock}"
  if [ "${TARGET_UID:-0}" = "0" ] || [ ! -S "${docker_sock}" ]; then
    return 0
  fi

  if run_as_checkout_owner_home test -r "${docker_sock}" \
    && run_as_checkout_owner_home test -w "${docker_sock}"; then
    return 0
  fi

  user_name="$(getent passwd "${TARGET_UID}" 2>/dev/null | cut -d: -f1 | head -n 1 || true)"
  if [ -n "${user_name}" ]; then
    die "checkout owner ${user_name} cannot access ${docker_sock}; add that user to the Docker socket group, re-login/restart the service session, then rerun recovery"
  fi
  die "checkout owner UID ${TARGET_UID} cannot access ${docker_sock}; grant that account Docker socket access, then rerun recovery"
}

confirm_update() {
  if [ "${YES}" = "true" ] || [ "${PLAN_ONLY}" = "true" ]; then
    return 0
  fi
  [ -r /dev/tty ] || die "non-interactive recovery requires --yes after reviewing ${RECOVERY_DIR}/update-plan.json"
  {
    printf '\nReady to update:\n'
    printf '  Repo:          %s\n' "${REPO}"
    printf '  Target ref:    %s\n' "${REF}"
    printf '  Include UI:    %s\n' "${INCLUDE_UI}"
    printf '  Checkout:      %s\n' "$(checkout_value)"
    printf '  Local changes: %s\n' "${LOCAL_CHANGES}"
    printf '  Recovery dir:  %s\n' "${RECOVERY_DIR}"
    printf '\nContinue with agent update? [y/N]: '
  } >/dev/tty
  local answer
  IFS= read -r answer </dev/tty || answer=""
  answer="$(printf '%s' "${answer}" | tr '[:upper:]' '[:lower:]')"
  case "${answer}" in
    y|yes) ;;
    *) die "update aborted by operator before applying changes" ;;
  esac
}

run_plan() {
  local checkout
  checkout="$(checkout_value)"
  log "==> Computing update plan"
  local args=(
    update
    --self-update=false
    --plan
    --plan-json
    --remote="${REMOTE}"
    --ref="${REF}"
    --checkout="${checkout}"
    --include-ui="${INCLUDE_UI}"
    --local-changes="${LOCAL_CHANGES}"
  )
  if [ "${SKIP_CHECK}" = "true" ]; then
    args+=(--skip-check)
  fi
  if [ "${STASH_UNTRACKED}" = "true" ]; then
    args+=(--stash-untracked)
  fi
  local plan_err_raw="${RECOVERY_DIR}/update-plan.err.raw"
  if ! run_as_owner "${AGENT_BIN}" "${args[@]}" >"${RECOVERY_DIR}/update-plan.json" 2>"${plan_err_raw}"; then
    redact_remote_url <"${plan_err_raw}" >"${RECOVERY_DIR}/update-plan.err" || true
    rm -f -- "${plan_err_raw}"
    cat "${RECOVERY_DIR}/update-plan.err" >&2 || true
    die "update plan failed; diagnostics are in ${RECOVERY_DIR}"
  fi
  rm -f -- "${plan_err_raw}"
}

run_update() {
  local checkout
  checkout="$(checkout_value)"
  log "==> Running agent update"
  local args=(
    update
    --self-update=false
    --remote="${REMOTE}"
    --ref="${REF}"
    --checkout="${checkout}"
    --include-ui="${INCLUDE_UI}"
    --local-changes="${LOCAL_CHANGES}"
  )
  if [ "${SKIP_CHECK}" = "true" ]; then
    args+=(--skip-check)
  fi
  if [ "${STASH_UNTRACKED}" = "true" ]; then
    args+=(--stash-untracked)
  fi

  : >"${RECOVERY_DIR}/agent-update.log"
  chown "${TARGET_UID}:${TARGET_GID}" "${RECOVERY_DIR}/agent-update.log" 2>/dev/null || true
  set +e
  run_as_owner "${AGENT_BIN}" "${args[@]}" 2>&1 | redact_remote_url | tee "${RECOVERY_DIR}/agent-update.log"
  local rc=${PIPESTATUS[0]}
  set -e
  if [ "${rc}" -ne 0 ]; then
    die "agent update failed; review ${RECOVERY_DIR}/agent-update.log and CLI recovery output above"
  fi
}

print_restore_guidance() {
  cat <<EOF

==> Recovery completed
Recovery directory:
  ${RECOVERY_DIR}

Useful files:
  ${RECOVERY_DIR}/agent-update.log
  ${RECOVERY_DIR}/update-plan.json
  ${RECOVERY_DIR}/pre-update-files/
  ${RECOVERY_DIR}/staged-tracked.patch
  ${RECOVERY_DIR}/unstaged-tracked.patch

Restore guidance:
  - The agent updater also printed its own backup directory when it ran.
  - To restore operator config from this script's pre-update copy, inspect:
      ${RECOVERY_DIR}/pre-update-files/
  - If --local-changes=overwrite was used, tracked source-code edits were discarded
    by the updater. The preserved patches above can be inspected or reapplied manually.
  - Untracked runtime/operator files were not removed by this script.
EOF
}

main() {
  parse_args "$@"
  validate_args

  [ "$(uname -s)" = "Linux" ] || die "this recovery script supports Linux AAVA/Asterisk Docker deployments only"
  [ "${EUID}" -eq 0 ] || die "run this script with sudo/root so it can repair bounded metadata and install the CLI"
  need_cmd bash
  need_cmd git
  need_cmd python3
  need_cmd stat
  need_cmd mktemp
  need_cmd chown
  need_cmd chmod
  need_cmd install
  need_cmd date
  need_cmd awk
  need_cmd sed
  need_cmd tr
  need_cmd tee
  need_cmd cp
  command -v realpath >/dev/null 2>&1 || need_cmd readlink

  detect_repo
  trap cleanup EXIT
  trap 'signal_exit 130' INT
  trap 'signal_exit 143' TERM
  trap 'signal_exit 129' HUP
  prepare_recovery_dir

  prepare_owner_execution
  repair_git_metadata_ownership
  repair_tracked_paths_ownership
  capture_diagnostics
  prompt_local_changes_if_needed
  capture_preupdate_artifacts
  install_target_cli
  repair_agent_state_ownership
  prepare_updater_state_dirs
  check_owner_docker_access
  run_plan
  if [ "${PLAN_ONLY}" = "true" ]; then
    log "Plan written to ${RECOVERY_DIR}/update-plan.json"
    exit 0
  fi
  confirm_update
  run_update
  print_restore_guidance
}

main "$@"
