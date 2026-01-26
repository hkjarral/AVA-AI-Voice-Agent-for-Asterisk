#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/app/project}"
JOB_ID="${AAVA_UPDATE_JOB_ID:-}"
MODE="${AAVA_UPDATE_MODE:-run}" # run|plan|rollback
INCLUDE_UI="${AAVA_UPDATE_INCLUDE_UI:-false}" # true|false
REMOTE="${AAVA_UPDATE_REMOTE:-origin}"
REF="${AAVA_UPDATE_REF:-main}"
CHECKOUT="${AAVA_UPDATE_CHECKOUT:-false}" # true|false
ROLLBACK_FROM_JOB="${AAVA_UPDATE_ROLLBACK_FROM_JOB:-}"
UPDATE_CLI_HOST="${AAVA_UPDATE_UPDATE_CLI_HOST:-true}" # true|false
CLI_INSTALL_PATH="${AAVA_UPDATE_CLI_INSTALL_PATH:-}" # optional absolute host path
KEEP_JOB_LOGS="${AAVA_UPDATE_KEEP_JOB_LOGS:-10}" # keep last N job logs

UPDATES_DIR="${PROJECT_ROOT}/.agent/updates"
JOBS_DIR="${UPDATES_DIR}/jobs"
BIN_DIR="${PROJECT_ROOT}/.agent/bin"
AGENT_BIN="${BIN_DIR}/agent"
BUILTIN_AGENT="/usr/local/bin/agent"
BACKUP_DIR_REL=".agent/update-backups/${JOB_ID}"

now_iso() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

ensure_dirs() {
  mkdir -p "${JOBS_DIR}" "${BIN_DIR}"
}

prune_job_logs() {
  local keep="${KEEP_JOB_LOGS}"
  if ! [[ "${keep}" =~ ^[0-9]+$ ]]; then
    keep=10
  fi
  if [ "${keep}" -lt 1 ]; then
    keep=1
  fi

  # Only prune log files; keep job state JSON and backups for operator recovery.
  mapfile -t logs < <(ls -1t "${JOBS_DIR}"/*.log 2>/dev/null || true)
  if [ "${#logs[@]}" -le "${keep}" ]; then
    return 0
  fi

  for ((i=keep; i<${#logs[@]}; i++)); do
    rm -f "${logs[$i]}" >/dev/null 2>&1 || true
  done
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

sync_agent_cli() {
  # Goal: ensure a project-local CLI exists at `./.agent/bin/agent` after UI-driven updates
  # so operators (and future UI jobs) can rely on a recent binary without SSHing to reinstall.
  mkdir -p "${BIN_DIR}"

  # Prefer building from the updated repo using Docker + golang image (no host Go required).
  # Best-effort: if this fails, fall back to copying the bundled agent binary from this image.
  echo "==> Updating agent CLI (project-local)..." >&2

  ver="$(git -c safe.directory="${PROJECT_ROOT}" describe --tags --always --dirty 2>/dev/null || echo "dev")"
  bt="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

  set +e
  docker run --rm \
    -v "${PROJECT_ROOT}:/src" \
    -w /src/cli \
    -e AAVA_CLI_VERSION="${ver}" \
    -e AAVA_BUILD_TIME="${bt}" \
    golang:1.22-bookworm \
    bash -c "go mod download && CGO_ENABLED=0 go build -buildvcs=false -ldflags \"-X main.version='\$AAVA_CLI_VERSION' -X main.buildTime='\$AAVA_BUILD_TIME'\" -o /src/.agent/bin/agent ./cmd/agent"
  rc=$?
  set -e

  if [ "${rc}" -ne 0 ]; then
    echo "WARN: failed to build agent CLI from updated source; falling back to bundled agent binary" >&2
    if [ -x "${BUILTIN_AGENT}" ]; then
      cp -f "${BUILTIN_AGENT}" "${AGENT_BIN}" || true
      chmod +x "${AGENT_BIN}" || true
    fi
  else
    chmod +x "${AGENT_BIN}" 2>/dev/null || true
  fi

  if [ "${UPDATE_CLI_HOST}" != "true" ]; then
    echo "==> Skipping agent CLI update on host (disabled)..." >&2
    return 0
  fi

  host_agent_is_aava() {
    local host_path="$1"
    local host_dir
    local host_base
    host_dir="$(dirname "${host_path}")"
    host_base="$(basename "${host_path}")"

    docker run --rm \
      -v "${host_dir}:/hostdir:ro" \
      debian:bookworm-slim \
      bash -lc "/hostdir/${host_base} version 2>/dev/null | head -n 1 | grep -q 'Asterisk AI Voice Agent CLI'" \
      >/dev/null 2>&1
  }

  host_install_agent_to() {
    local host_path="$1"
    local host_dir
    local host_base
    host_dir="$(dirname "${host_path}")"
    host_base="$(basename "${host_path}")"

    docker run --rm \
      -v "${PROJECT_ROOT}:/src:ro" \
      -v "${host_dir}:/hostdir" \
      debian:bookworm-slim \
      bash -lc "install -m 0755 /src/.agent/bin/agent /hostdir/${host_base}"
  }

  # Prefer updating an existing, verified install path. If none is detected, install to /usr/local/bin/agent.
  local requested_path
  requested_path="$(echo "${CLI_INSTALL_PATH}" | xargs || true)"

  local target_path=""
  local conflicts=()

  if [ -n "${requested_path}" ]; then
    if [[ "${requested_path}" != /* ]]; then
      echo "WARN: invalid AAVA_UPDATE_CLI_INSTALL_PATH (must be absolute): ${requested_path}" >&2
      echo "WARN: skipping host agent CLI update (continuing)" >&2
      return 0
    fi
    target_path="${requested_path}"
  else
    for p in /usr/local/bin/agent /usr/bin/agent /bin/agent; do
      if host_agent_is_aava "${p}"; then
        target_path="${p}"
        break
      fi
      # If the file exists but doesn't look like our CLI, record it so we can avoid overwriting silently.
      set +e
      docker run --rm -v "$(dirname "${p}"):/hostdir:ro" debian:bookworm-slim bash -lc "test -f /hostdir/$(basename "${p}")" >/dev/null 2>&1
      exists_rc=$?
      set -e
      if [ "${exists_rc}" -eq 0 ]; then
        conflicts+=("${p}")
      fi
    done
  fi

  if [ -z "${target_path}" ]; then
    if [ "${#conflicts[@]}" -gt 0 ]; then
      echo "WARN: found an existing 'agent' binary on host that is not AAVA CLI: ${conflicts[*]}" >&2
      echo "HINT: set a custom install path (Update agent CLI → custom path) to avoid overwriting unknown binaries." >&2
      echo "==> Skipping host agent CLI update (continuing)" >&2
      return 0
    fi
    target_path="/usr/local/bin/agent"
  fi

  echo "==> Updating agent CLI on host: ${target_path} (best-effort)..." >&2
  set +e
  host_install_agent_to "${target_path}"
  _rc2=$?
  set -e
  if [ "${_rc2}" -ne 0 ]; then
    echo "WARN: failed to install/update agent CLI at ${target_path} on host (continuing)" >&2
    if [ -z "${requested_path}" ]; then
      echo "HINT: set a custom install path (Update agent CLI → custom path) if your host doesn't use /usr/local/bin/agent." >&2
    fi
  fi
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
    # Keep logs on success so operators can review full output after the fact.
    sync_agent_cli || true
    write_job_state "success" "${code}"
  else
    write_job_state "failed" "${code}"
  fi

  prune_job_logs || true
  exit "${code}"
}

run_rollback() {
  if [ -z "${JOB_ID}" ]; then
    echo "ERR: AAVA_UPDATE_JOB_ID is required for rollback mode" >&2
    exit 2
  fi
  if [ -z "${ROLLBACK_FROM_JOB}" ]; then
    echo "ERR: AAVA_UPDATE_ROLLBACK_FROM_JOB is required for rollback mode" >&2
    exit 2
  fi

  JOB_STARTED_AT="$(now_iso)"
  export JOB_STARTED_AT

  JOB_LOG_PATH="${JOBS_DIR}/${JOB_ID}.log"
  export JOB_LOG_PATH

  src_state="${JOBS_DIR}/${ROLLBACK_FROM_JOB}.json"
  if [ ! -f "${src_state}" ]; then
    echo "ERR: source job not found: ${ROLLBACK_FROM_JOB}" >&2
    write_job_state "failed" "2"
    exit 2
  fi

  pre_branch="$(jq -r '.pre_update_branch // empty' "${src_state}" 2>/dev/null || true)"
  backup_rel="$(jq -r '.backup_dir_rel // empty' "${src_state}" 2>/dev/null || true)"
  src_include_ui="$(jq -r '.include_ui // empty' "${src_state}" 2>/dev/null || true)"

  if [ -z "${pre_branch}" ] || [ -z "${backup_rel}" ]; then
    echo "ERR: source job missing rollback metadata (pre_update_branch/backup_dir_rel)" >&2
    write_job_state "failed" "2"
    exit 2
  fi

  include_ui_effective="${src_include_ui}"
  if [ "${include_ui_effective}" != "true" ] && [ "${include_ui_effective}" != "false" ]; then
    include_ui_effective="${INCLUDE_UI}"
  fi

  # Prefer the original job plan to determine which services were impacted.
  plan_patch="$(jq -c --arg include_ui "${include_ui_effective}" '
    def arr(x): (x // []) | map(select(. != null)) | map(tostring);
    def filter_ui(a): if $include_ui == "true" then a else a | map(select(. != "admin_ui")) end;
    def r: filter_ui(arr(.plan.services_rebuild));
    def s: filter_ui(arr(.plan.services_restart));
    def missing: ((r|length) == 0 and (s|length) == 0);
    {
      services_rebuild: (if missing then (if ($include_ui == "true") then ["ai_engine","local_ai_server","admin_ui"] else ["ai_engine","local_ai_server"] end) else r end),
      services_restart: (if missing then [] else s end),
      changed_file_count: (.plan.changed_file_count // null),
      compose_changed: (.plan.compose_changed // false)
    }' "${src_state}" 2>/dev/null || echo '{"services_rebuild":["ai_engine","local_ai_server"],"services_restart":[],"changed_file_count":null,"compose_changed":false}')"

  meta_patch="$(jq -n \
    --arg type "rollback" \
    --arg from_job_id "${ROLLBACK_FROM_JOB}" \
    --arg ref "${pre_branch}" \
    --arg backup_dir_rel "${backup_rel}" \
    --arg pre_update_branch "${pre_branch}" \
    --arg include_ui "${include_ui_effective}" \
    --argjson plan "${plan_patch}" \
    '{
      type: $type,
      rollback_from_job_id: $from_job_id,
      ref: $ref,
      backup_dir_rel: $backup_dir_rel,
      pre_update_branch: $pre_update_branch,
      include_ui: ($include_ui == "true"),
      plan: $plan
    }')"

  state_file="${JOBS_DIR}/${JOB_ID}.json"
  if [ -f "${state_file}" ]; then
    jq -s '.[0] * .[1]' "${state_file}" <(echo "${meta_patch}") > "${state_file}.tmp" && mv "${state_file}.tmp" "${state_file}"
  else
    echo "${meta_patch}" > "${state_file}"
  fi

  write_job_state "running" ""

  set +e
  (
    set -euo pipefail

    echo "==> Rollback requested" >&2
    echo "==> Source job: ${ROLLBACK_FROM_JOB}" >&2
    echo "==> Restoring code to: ${pre_branch}" >&2
    echo "==> Restoring operator config from: ${backup_rel}" >&2

    # Best-effort: preserve any current local changes before switching branches.
    if [ -n "$(git -c safe.directory="${PROJECT_ROOT}" status --porcelain 2>/dev/null || true)" ]; then
      echo "==> Working tree is dirty; stashing changes (best-effort)" >&2
      git -c safe.directory="${PROJECT_ROOT}" stash push -u -m "aava rollback ${JOB_ID}" >/dev/null 2>&1 || true
    fi

    git -c safe.directory="${PROJECT_ROOT}" checkout "${pre_branch}"

    if [ -f "${PROJECT_ROOT}/${backup_rel}/.env" ]; then
      cp -f "${PROJECT_ROOT}/${backup_rel}/.env" "${PROJECT_ROOT}/.env"
    fi
    if [ -f "${PROJECT_ROOT}/${backup_rel}/config/ai-agent.yaml" ]; then
      mkdir -p "${PROJECT_ROOT}/config"
      cp -f "${PROJECT_ROOT}/${backup_rel}/config/ai-agent.yaml" "${PROJECT_ROOT}/config/ai-agent.yaml"
    fi
    if [ -f "${PROJECT_ROOT}/${backup_rel}/config/users.json" ]; then
      mkdir -p "${PROJECT_ROOT}/config"
      cp -f "${PROJECT_ROOT}/${backup_rel}/config/users.json" "${PROJECT_ROOT}/config/users.json"
    fi
    if [ -d "${PROJECT_ROOT}/${backup_rel}/config/contexts" ]; then
      mkdir -p "${PROJECT_ROOT}/config"
      tmp_contexts="${PROJECT_ROOT}/config/contexts.rollback-tmp"
      rm -rf "${tmp_contexts}"
      cp -r "${PROJECT_ROOT}/${backup_rel}/config/contexts" "${tmp_contexts}"
      rm -rf "${PROJECT_ROOT}/config/contexts"
      mv "${tmp_contexts}" "${PROJECT_ROOT}/config/contexts"
    fi

    mapfile -t rebuild_services < <(jq -r '.services_rebuild[]?' <<<"${plan_patch}" 2>/dev/null || true)
    mapfile -t restart_services < <(jq -r '.services_restart[]?' <<<"${plan_patch}" 2>/dev/null || true)
    compose_changed="$(jq -r '.compose_changed // false' <<<"${plan_patch}" 2>/dev/null || echo false)"

    if [ "${#rebuild_services[@]}" -eq 0 ] && [ "${#restart_services[@]}" -eq 0 ]; then
      extra=""
      if [ "${include_ui_effective}" = "true" ]; then
        extra=" + admin_ui"
      fi
      echo "==> No service impact found in source plan; defaulting rollback targets to ai_engine + local_ai_server${extra}" >&2
      rebuild_services=("ai_engine" "local_ai_server")
      if [ "${include_ui_effective}" = "true" ]; then
        rebuild_services+=("admin_ui")
      fi
    fi

    if [ "${compose_changed}" = "true" ]; then
      targets=("ai_engine" "local_ai_server")
      if [ "${include_ui_effective}" = "true" ]; then
        targets+=("admin_ui")
      fi
      targets+=("${rebuild_services[@]}" "${restart_services[@]}")
      # De-dup targets
      mapfile -t targets < <(printf '%s\n' "${targets[@]}" | awk 'NF && !seen[$0]++')
      echo "==> Compose changed; reconciling services (no-build): ${targets[*]:-none}" >&2
      if [ "${#targets[@]}" -gt 0 ]; then
        docker compose up -d --remove-orphans --no-build "${targets[@]}"
      else
        docker compose up -d --remove-orphans --no-build
      fi
    fi

    if [ "${#rebuild_services[@]}" -gt 0 ]; then
      echo "==> Rebuilding services: ${rebuild_services[*]}" >&2
      docker compose up -d --build "${rebuild_services[@]}"
    fi

    if [ "${#restart_services[@]}" -gt 0 ]; then
      echo "==> Restarting services: ${restart_services[*]}" >&2
      for svc in "${restart_services[@]}"; do
        docker compose restart "${svc}" || docker compose up -d --no-build "${svc}"
      done
    fi
  ) 2>&1 | tee "${JOB_LOG_PATH}"
  code="${PIPESTATUS[0]}"
  set -e

  JOB_FINISHED_AT="$(now_iso)"
  export JOB_FINISHED_AT

  if [ "${code}" -eq 0 ]; then
    # Keep logs on success so operators can review full output after the fact.
    sync_agent_cli || true
    write_job_state "success" "${code}"
  else
    write_job_state "failed" "${code}"
  fi

  prune_job_logs || true
  exit "${code}"
}

main() {
  ensure_dirs
  cd "${PROJECT_ROOT}"

  case "${MODE}" in
    plan) run_plan ;;
    run) run_update ;;
    rollback) run_rollback ;;
    *)
      echo "ERR: unknown mode: ${MODE} (expected run|plan|rollback)" >&2
      exit 2
      ;;
  esac
}

main "$@"
