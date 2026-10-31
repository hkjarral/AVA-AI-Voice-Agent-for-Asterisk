# Agent CLI Tools Guide

Operator reference for the `agent` command prepared for Asterisk AI Voice Agent v7.7.1.

Run commands from the repository root on the Docker Compose host. Global flags are `--verbose` and `--no-color`.

## Primary commands

| Command | Purpose |
|---|---|
| `agent setup` | Configure ARI, transport, and the active provider or pipeline |
| `agent check` | Generate a shareable system-health report |
| `agent rca` | Analyze a completed call using persisted Call History and logs |
| `agent config validate` | Validate provider, pipeline, model, transport, and audio settings |
| `agent dialplan` | Generate an `AI_AGENT` dialplan snippet |
| `agent update` | Plan or apply a safe repository update |
| `agent version` | Print CLI version and build information |

## Installation

The normal project installer includes the CLI. To install only a released binary on Linux or macOS:

```bash
curl -sSL https://raw.githubusercontent.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/main/scripts/install-cli.sh | bash
agent version
```

## Setup

```bash
agent setup
```

The wizard reads both `config/ai-agent.yaml` and `config/ai-agent.local.yaml`, discovers the providers and pipelines actually defined by the installation, writes secrets to `.env`, and writes operator selections to `config/ai-agent.local.yaml`. Switching from a pipeline to a full-agent provider explicitly clears `active_pipeline` so the old route cannot remain active.

List available targets without changing configuration:

```bash
agent setup --list-targets
```

After an interactive setup, the CLI runs `agent check`.

## System diagnostics

```bash
agent check
agent check --json
agent check --fix
```

The standard report checks Docker and Compose, `ai_engine`, mounts and networking, ARI reachability and app registration, transport alignment, configuration, and best-effort DNS/internet reachability.

Exit codes:

- `0`: all checks passed
- `1`: non-critical warnings
- `2`: critical failure

`agent check --fix` snapshots the current configuration, attempts recovery from the latest usable update or per-file backup, restarts core services, and runs the report again. It cannot be combined with `--json`.

### Local AI Server round trip

```bash
agent check --local
agent check --local --json
agent check --remote 10.0.0.50
```

This verifies the WebSocket connection, loaded STT/LLM/TTS models, runtime configuration, GPU status, real LLM generation, Piper TTS synthesis, and a Faster-Whisper STT round trip. If the host does not have the Python `websockets` package, local mode runs the probe inside `local_ai_server`. The scripts remain compatible with Python 3.6 operator hosts.

## Post-call RCA

```bash
# Most recent call
agent rca

# Specific call: positional and flag forms are equivalent
agent rca 1781929321.74
agent rca --call 1781929321.74

# Deterministic evidence only (recommended for automation)
agent rca --call 1781929321.74 --no-llm --json

# Force an LLM interpretation after deterministic analysis
agent rca --call 1781929321.74 --llm
```

RCA combines two evidence sources:

- Call History supplies the canonical provider or pipeline, context, outcome, duration, turn count, turn latency, routing method, and codec-alignment result.
- Call-scoped logs supply transport, streaming segments, byte ratios, underflows, VAD/gating evidence, format alignment, and tool execution.

This prevents unrelated provider names elsewhere in the container logs from selecting the wrong baseline. A successful pipeline call is identified by its persisted `pipeline_name`, even when the provider field is `pipeline`.

Interpretation notes:

- Delivery drift compares encoded duration with wall time. Pauses, barge-in, synthesis, and queue waits can make it non-zero, so drift alone does not fail a call or trigger LLM diagnosis.
- Modular pipeline wall time is not treated as provider streaming drift.
- Very short or empty segments are ignored for drift assessment.
- Underflows are evaluated as a percentage of estimated 20 ms audio frames; isolated events are informational below the alert threshold.
- Recommendations use the observed runtime configuration instead of assuming fixed jitter-buffer values.

`--llm` and `--no-llm` are mutually exclusive. `--local` cannot be combined with a call ID or either LLM flag.

### Local-call report

```bash
agent rca --local
agent rca --local --json
```

This selects the newest persisted local or modular-pipeline call by `start_time`, then reports hardware, loaded models, call outcome and latency, call-filtered local logs, and tool executions for that call. The text form is suitable for the Community Test Matrix.

## Configuration validation

```bash
agent config validate
agent config validate --file config/ai-agent.yaml
agent config validate --strict
agent config validate --fix
```

Validation accepts `default_provider` targets that refer to either a full provider or a configured pipeline. It understands dynamically named providers, current realtime/Deepgram models, and intentional input/output sample-rate differences. `--strict` treats warnings as errors. Auto-fix is deliberately limited; use `agent check --fix` for backup-based recovery.

## Dialplan generation

```bash
# Select an operator-managed agent; use its configured target
agent dialplan --agent default

# Add an optional per-call provider or pipeline override
agent dialplan --agent sales --provider local_hybrid

# Change only the printed destination-file instruction
agent dialplan --file /etc/asterisk/extensions_custom.conf
```

Generated snippets set `AI_AGENT`. `AI_PROVIDER` is emitted only when `--provider` is supplied, so the selected agent's configured target remains authoritative by default. The command prints a snippet; it does not edit Asterisk files.

## Safe updates

Preview an update before applying it:

```bash
agent update --plan
agent update --plan --plan-json --ref main
```

Apply an update:

```bash
agent update
agent update --ref v7.7.1   # Only after this tag/artifacts are published
agent update --checkout --ref main
agent update --rebuild auto
agent update --rebuild none
agent update --rebuild all
agent update --force-recreate
agent update --skip-check
agent update --no-stash
agent update --stash-untracked
agent update --backup-id before-upgrade
agent update --self-update=false
```

Before changing Git state, the updater backs up operator configuration and uses SQLite's online backup API to snapshot `data/operator/agents.db` and `data/call_history.db`. This includes committed WAL data without requiring containers to stop. Release updates are fast-forward only. The explicit `--local-changes=overwrite` policy discards tracked source edits after backup; use `retain` or `abort` unless that loss is intentional.

With `--plan --plan-json`, progress is written to stderr and stdout contains valid JSON for automation.

### Interrupted Docker builds and same-version recovery

Before advancing Git, the updater atomically saves planned Docker actions and the
original running services in `.agent/updates/pending-docker.json`. A failed build,
recreation, or interrupted process leaves this record for the next update. Preview
shows pending actions even when Git is already up to date. Retry the same ref with
`--rebuild=auto --include-ui=true`; the normal update lock and active-call guard
still apply. A service stopped by a failed recreation can be restarted, while an
installed-but-stopped Local AI service remains build-only. The record is removed
after Docker actions succeed, before post-update diagnostics.
Backups are created in fresh directories and never overwritten by a retry. If
you supplied `--backup-id`, choose a new ID for another attempt and retain the
original pre-upgrade backup. GUI jobs already use distinct job identifiers.

An incompatible rebuild/UI exclusion, malformed record, or unrelated manual
branch change stops recovery with guidance. Do not delete the record merely to
make an update pass: inspect the intended revision and finish the Docker actions
first. Older CLI versions do not consume this record. For a build failure that
predates this journal, the updated CLI also honors `--rebuild=all` when the target
commit is already checked out; optional-service and UI filters still apply.

Some Compose/Buildx combinations reject our `build.network: host` configuration
with `additional privileges requested: pass "--allow=network.host"`. The updater
recognizes this specific error, exports the selected services with
`docker compose build --print`, and feeds that JSON to
`docker buildx bake --file - --allow=network.host --load`. It then recreates those
services using `--no-build --pull never`. Unrelated build failures do not activate
this fallback, and no additional entitlement is granted. Export/Bake failures
retain the pending actions and report which stage failed. Host networking for
builds is separate from runtime telephony networking.

Every replacement image is built before Compose reconciliation or recreation.
Compose file changes can change build inputs, so automatic mode also rebuilds
eligible services for those changes. A running engine's active-call state must be
known before checkout and again after building, before runtime changes. A failed
probe or calls arriving during the build defer the update; pending actions allow
a retry once health is restored or calls end. A stopped engine remains recoverable.
`AAVA_UPDATE_FORCE_ACTIVE_CALLS=true` is an explicit operator override, not a
recommended routine upgrade setting.
Use the PBX's existing maintenance/routing controls to prevent new calls and
drain existing calls during rollout. Health probes observe call state; they do
not lock call admission. Resume normal routing after readiness and a test call.
On an inference-only GPU host with no installed local engine, the updater cannot
prove remote engines are idle. Runtime changes to running Local AI fail closed.
Drain every consuming remote engine first, then use the explicit
`AAVA_UPDATE_FORCE_ACTIVE_CALLS=true` override for the controlled update/rollback.
A co-located but stopped engine remains recoverable, and image-only builds do
not require a remote-drain override.

The host SQLite fallback uses Python 3.7+ and SQLite's online backup API, even if
the engine is stopped: Admin UI may still be writing. Missing Python, invalid
databases, or failed snapshots stop the update before checkout. Automatic backups
currently cover the default Agent/History paths. An `AGENTS_DB_PATH` or
`CALL_HISTORY_DB_PATH` relocation stops automatic updates rather than silently
backing up the wrong file. Use the manual upgrade path with coherent snapshots of
the actual configured files and bind mounts; never temporarily unset an override
to bypass this check.

For GUI rollback, choose the original job whose pre-update revision is the desired
baseline, not a later retry already at the newer commit. The updater image embeds
the compatible build helper so rollback to older source still works. Successful
rollback archives pending forward actions under `.agent/updates/rolled-back/`;
failed rollback keeps them for another rollback attempt. For a manual rollback,
retain the journal until the restored revision's builds/recreations and health
checks succeed, then move it aside as recovery evidence. Operator databases and
managed credentials are not automatically rewound; follow the
[integration rollback boundaries](INTEGRATION_BACKUPS.md).

### Manual builds on affected Compose/Buildx versions

After recording local changes, taking coherent private backups, and fetching or
fast-forwarding to the intended release, use explicit services. With Docker access
in your shell, from the checkout root:

```bash
./scripts/compose-build.sh ai_engine admin_ui &&
  docker compose up -d \
    --no-build --pull never --force-recreate ai_engine admin_ui &&
  agent check
```

The helper honors `COMPOSE_FILE` and `COMPOSE_PROJECT_NAME`; use the same settings
for the build and recreation. Build Local AI only when it is installed/selected,
and leave a stopped Local AI service stopped. Recreate during a window with no
active calls; direct Docker commands do not enforce the CLI's call guard.

For older checkouts without the helper, build with the explicit Bake grant:

```bash
( set -o pipefail
  docker compose build --print ai_engine admin_ui |
    docker buildx bake --file - --allow=network.host --load
) && docker compose up -d \
  --no-build --pull never --force-recreate ai_engine admin_ui
```

`--allow` belongs to `docker buildx bake`, not `docker compose up`. Verify the Git
revision, `agent version`, and Docker plugin versions separately. This recovery
fix ships in v7.7.1: installing v7.7.0 again does not install it. A fixed host
CLI or a rebuilt/replaced updater image containing the fix is required for
automatic recovery; pulling source does not replace those binaries. Until a
release containing the fix is installed, use the manual procedure above. After
recreation, run `agent check`, hard-refresh Admin UI, verify the running services,
and make a test call before considering the upgrade complete.

For relocated databases, map each configured container path to its real host file
and make a coherent snapshot before the manual upgrade. Replace both paths below,
use a new private destination, and repeat for every configured Agent/History/
integration database; retain managed secrets and configuration separately:

```bash
( umask 077
  python3 - /actual/host/path/agents.db /private/backup/agents.db <<'PY'
import pathlib, sqlite3, sys
source, destination = map(pathlib.Path, sys.argv[1:])
if destination.exists():
    raise RuntimeError("Choose a new backup destination")
destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
src = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
dst = sqlite3.connect(str(destination))
try:
    src.backup(dst)
    assert dst.execute("PRAGMA quick_check").fetchone() == ("ok",)
finally:
    dst.close()
    src.close()
PY
)
```

When reporting a failure, include `docker compose version`, `docker buildx version`,
the selected ref, and sanitized update output. Versions must come from the
environment running the update: a GUI updater container can use different Docker
plugins from the host. See [issue #790](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/issues/790)
and [Docker's entitlement-forwarding fix](https://github.com/docker/compose/pull/14325).

## Compatibility aliases

These commands remain hidden for existing scripts:

- `agent doctor` delegates to `agent check`.
- `agent troubleshoot` uses the same RCA engine and retains advanced legacy flags such as `--list`, `--symptom`, and `--collect-only`.
- `agent init` and `agent quickstart` delegate to `agent setup`.
- `agent demo` delegates to `agent check`.

Legacy flags that no longer have an implementation return a clear error instead of silently claiming success. In particular, `agent init --non-interactive`, `agent init --template`, and the old `agent demo --wav/--loop/--save` workflow are not supported.

## Recommended troubleshooting sequence

```bash
agent version
agent check
agent config validate
# Place or reproduce one test call
agent rca --call <call_id> --no-llm
```

For local inference problems, insert `agent check --local`. Attach JSON output from `agent check` and `agent rca` when opening an issue.

Build and development details are in [`cli/README.md`](../cli/README.md).
