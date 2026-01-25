#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/app/project}"
JOB_ID="${AAVA_UPDATE_JOB_ID:-}"
MODE="${AAVA_UPDATE_MODE:-run}" # run|plan
INCLUDE_UI="${AAVA_UPDATE_INCLUDE_UI:-false}" # true|false

UPDATES_DIR="${PROJECT_ROOT}/.agent/updates"
JOBS_DIR="${UPDATES_DIR}/jobs"
BIN_DIR="${PROJECT_ROOT}/.agent/bin"
AGENT_BIN="${BIN_DIR}/agent"
BUILTIN_AGENT="/usr/local/bin/agent"

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

  jq -n \
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
    }' > "${state_file}.tmp"

  mv "${state_file}.tmp" "${state_file}"
}

run_plan() {
  install_agent_if_needed

  if [ -x "${BUILTIN_AGENT}" ]; then
    exec "${BUILTIN_AGENT}" update --self-update=false --plan --plan-json --include-ui="${INCLUDE_UI}"
  fi
  exec "${AGENT_BIN}" update --self-update=false --plan --plan-json --include-ui="${INCLUDE_UI}"
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

  write_job_state "running" ""

  install_agent_if_needed

  set +e
  if [ -x "${BUILTIN_AGENT}" ]; then
    "${BUILTIN_AGENT}" update -v --self-update=false --include-ui="${INCLUDE_UI}" 2>&1 | tee "${JOB_LOG_PATH}"
  else
    "${AGENT_BIN}" update -v --include-ui="${INCLUDE_UI}" 2>&1 | tee "${JOB_LOG_PATH}"
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
