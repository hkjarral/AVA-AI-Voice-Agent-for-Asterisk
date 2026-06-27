# Agent CLI Tools Guide

Operator reference for the `agent` command shipped with Asterisk AI Voice Agent v7.2.0.

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
agent update --ref v7.2.0
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

Before changing Git state, the updater backs up operator configuration and uses SQLite's online backup API to snapshot `data/operator/agents.db` and `data/call_history.db`. This includes committed WAL data without requiring containers to stop. Updates are fast-forward only and never use a hard reset.

With `--plan --plan-json`, progress is written to stderr and stdout contains valid JSON for automation.

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
