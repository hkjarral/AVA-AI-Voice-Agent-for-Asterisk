#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/app/project}"
JOB_ID="${AAVA_UPDATE_JOB_ID:-}"
MODE="${AAVA_UPDATE_MODE:-run}" # run|plan
INCLUDE_UI="${AAVA_UPDATE_INCLUDE_UI:-false}" # true|false
REMOTE="${AAVA_UPDATE_REMOTE:-origin}"
REF="${AAVA_UPDATE_REF:-main}"
CHECKOUT="${AAVA_UPDATE_CHECKOUT:-false}" # true|false

UPDATES_DIR="${PROJECT_ROOT}/.agent/updates"
JOBS_DIR="${UPDATES_DIR}/jobs"
BIN_DIR="${PROJECT_ROOT}/.agent/bin"
AGENT_BIN="${BIN_DIR}/agent"
BUILTIN_AGENT="/usr/local/bin/agent"
BACKUP_DIR_REL=".agent/update-backups/${JOB_ID}"
BACKUP_DIR="${PROJECT_ROOT}/${BACKUP_DIR_REL}"

now_iso() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

ensure_dirs() {
  mkdir -p "${JOBS_DIR}" "${BIN_DIR}"
}

install_agent_if_needed() {
  # Prefer the baked-in agent binary from the updater image (built from the repo's cli/).
  if [ -x "${BUILTIN_AGENT}" ]; then
    return
  fi

  if [ -x "${AGENT_BIN}" ]; then
    return
  fi

  if [ ! -f "${PROJECT_ROOT}/scripts/install-cli.sh" ]; then
    echo "ERR: missing ${PROJECT_ROOT}/scripts/install-cli.sh (project not mounted?)" >&2
    exit 2
  fi

  echo "Installing agent CLI into ${BIN_DIR}..." >&2
  INSTALL_DIR="${BIN_DIR}" bash "${PROJECT_ROOT}/scripts/install-cli.sh" >&2
}

write_job_state() {
  local status="$1" # running|success|failed
  local exit_code="${2:-}"
  local state_file="${JOBS_DIR}/${JOB_ID}.json"

  local patch
  patch="$(jq -n \
    --arg job_id "${JOB_ID}" \
    --arg status "${status}" \
    --arg started_at "${JOB_STARTED_AT:-}" \
    --arg finished_at "${JOB_FINISHED_AT:-}" \
    --arg include_ui "${INCLUDE_UI}" \
    --arg exit_code "${exit_code}" \
    --arg log_path "${JOB_LOG_PATH:-}" \
    '{
      job_id: $job_id,
      status: $status,
      started_at: $started_at,
      finished_at: $finished_at,
      include_ui: ($include_ui == "true"),
      exit_code: (if $exit_code == "" then null else ($exit_code|tonumber) end),
      log_path: (if $log_path == "" then null else $log_path end)
    }')"

  if [ -f "${state_file}" ]; then
    jq -s '.[0] * .[1]' "${state_file}" <(echo "${patch}") > "${state_file}.tmp"
  else
    echo "${patch}" > "${state_file}.tmp"
  fi

  mv "${state_file}.tmp" "${state_file}"
}

run_plan() {
  install_agent_if_needed

  if [ -x "${BUILTIN_AGENT}" ]; then
    exec "${BUILTIN_AGENT}" update --self-update=false --plan --plan-json --remote="${REMOTE}" --ref="${REF}" --checkout="${CHECKOUT}" --include-ui="${INCLUDE_UI}"
  fi
  exec "${AGENT_BIN}" update --self-update=false --plan --plan-json --remote="${REMOTE}" --ref="${REF}" --checkout="${CHECKOUT}" --include-ui="${INCLUDE_UI}"
}

run_update() {
  if [ -z "${JOB_ID}" ]; then
    echo "ERR: AAVA_UPDATE_JOB_ID is required for run mode" >&2
    exit 2
  fi

  JOB_STARTED_AT="$(now_iso)"
  export JOB_STARTED_AT

  JOB_LOG_PATH="${JOBS_DIR}/${JOB_ID}.log"
  export JOB_LOG_PATH

  # Snapshot pre-update HEAD so operators can rollback manually if needed.
  pre_sha="$(git -c safe.directory="${PROJECT_ROOT}" rev-parse HEAD 2>/dev/null || true)"
  pre_branch="$(git -c safe.directory="${PROJECT_ROOT}" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  if [ "${pre_branch}" = "HEAD" ] || [ -z "${pre_branch}" ]; then
    pre_branch="detached"
  fi

  pre_update_branch="aava-pre-update-${JOB_ID}"
  git -c safe.directory="${PROJECT_ROOT}" branch -f "${pre_update_branch}" HEAD >/dev/null 2>&1 || true

  # Capture a plan snapshot for history/summary (best-effort).
  plan_json=""
  if [ -x "${BUILTIN_AGENT}" ]; then
    plan_json="$("${BUILTIN_AGENT}" update --self-update=false --plan --plan-json --remote="${REMOTE}" --ref="${REF}" --checkout="${CHECKOUT}" --include-ui="${INCLUDE_UI}" 2>/dev/null || true)"
  else
    plan_json="$("${AGENT_BIN}" update --self-update=false --plan --plan-json --remote="${REMOTE}" --ref="${REF}" --checkout="${CHECKOUT}" --include-ui="${INCLUDE_UI}" 2>/dev/null || true)"
  fi

  # Merge metadata into job state so the UI can show an actionable summary even if logs are pruned.
  meta_patch="$(jq -n \
    --arg type "update" \
    --arg ref "${REF}" \
    --arg remote "${REMOTE}" \
    --arg checkout "${CHECKOUT}" \
    --arg backup_dir_rel "${BACKUP_DIR_REL}" \
    --arg pre_update_branch "${pre_update_branch}" \
    --arg pre_update_sha "${pre_sha}" \
    --arg pre_update_ref "${pre_branch}" \
    --arg plan_raw "${plan_json}" \
    '{
      type: $type,
      ref: $ref,
      remote: $remote,
      checkout: ($checkout == "true"),
      backup_dir_rel: $backup_dir_rel,
      pre_update_branch: $pre_update_branch,
      pre_update_sha: (if ($pre_update_sha|length) == 0 then null else $pre_update_sha end),
      pre_update_ref: (if ($pre_update_ref|length) == 0 then null else $pre_update_ref end),
      plan: (try ($plan_raw | fromjson) catch null)
    }')"

  state_file="${JOBS_DIR}/${JOB_ID}.json"
  if [ -f "${state_file}" ]; then
    jq -s '.[0] * .[1]' "${state_file}" <(echo "${meta_patch}") > "${state_file}.tmp" && mv "${state_file}.tmp" "${state_file}"
  else
    echo "${meta_patch}" > "${state_file}"
  fi

  write_job_state "running" ""

  install_agent_if_needed

  set +e
  if [ -x "${BUILTIN_AGENT}" ]; then
    "${BUILTIN_AGENT}" update -v --self-update=false --remote="${REMOTE}" --ref="${REF}" --checkout="${CHECKOUT}" --backup-id="${JOB_ID}" --include-ui="${INCLUDE_UI}" 2>&1 | tee "${JOB_LOG_PATH}"
  else
    "${AGENT_BIN}" update -v --self-update=false --remote="${REMOTE}" --ref="${REF}" --checkout="${CHECKOUT}" --backup-id="${JOB_ID}" --include-ui="${INCLUDE_UI}" 2>&1 | tee "${JOB_LOG_PATH}"
  fi
  code="${PIPESTATUS[0]}"
  set -e

  JOB_FINISHED_AT="$(now_iso)"
  export JOB_FINISHED_AT

  if [ "${code}" -eq 0 ]; then
    write_job_state "success" "${code}"
    # Prune logs on success (keep only a small job marker).
    rm -f "${JOB_LOG_PATH}"
  else
    write_job_state "failed" "${code}"
  fi

  exit "${code}"
}

main() {
  ensure_dirs
  cd "${PROJECT_ROOT}"

  case "${MODE}" in
    plan) run_plan ;;
    run) run_update ;;
    *)
      echo "ERR: unknown mode: ${MODE} (expected run|plan)" >&2
      exit 2
      ;;
  esac
}

main "$@"
