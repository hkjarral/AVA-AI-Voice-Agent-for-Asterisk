# CLI Tools Guide (`agent`)

Operator-focused reference for the `agent` CLI (setup + diagnostics + post-call RCA + updates).

## What it does

- `agent setup`: interactive onboarding (providers, transport, dialplan hints).
- `agent check`: shareable diagnostics report for support (recommended first step when debugging).
- `agent check --fix`: one-shot recovery flow for config/update failures (restore from latest valid backup, restart core services, re-check).
- `agent rca`: post-call RCA using Call History and logs.
- `agent update`: safe pull + rebuild/restart + verify workflow for repo-based installs.
- `agent version`: version/build info (attach to issues).

## Installation

### If you installed the full project

The CLI is included with standard installs (for example via `install.sh` / Admin UI workflows). If `agent` is already on your PATH, skip ahead to Usage.

### CLI-only install (prebuilt binaries)

From a Linux/macOS host:

```bash
curl -sSL https://raw.githubusercontent.com/hkjarral/Asterisk-AI-Voice-Agent/main/scripts/install-cli.sh | bash
```

Verify:

```bash
agent version
```

## Usage

Run these commands on the host that runs Docker Compose for this repo (the CLI shells out to Docker/Compose and reads your local `.env` and `config/ai-agent.yaml`).

Global flags (all commands):

```bash
agent <command> --verbose
agent <command> --no-color
```

### `agent setup`

```bash
agent setup
```

Typically guides:
1) ARI host/credentials validation  
2) transport selection (AudioSocket vs ExternalMedia)  
3) provider selection (OpenAI/Deepgram/Google/Local/etc.)  
4) writes config + restarts services  

### `agent check`

```bash
agent check
```

Useful flags:

```bash
agent check --json
agent check --verbose
agent check --no-color
agent check --fix
```

`agent check --fix` behavior:

- Runs diagnostics first and prints the report.
- If failures/warnings exist, attempts backup-based recovery:
  - snapshots current state to `.agent/check-fix-backups/<timestamp>/`
  - restores from latest usable backup set (`.agent/update-backups/...`) or per-file `*.bak.*` backups
  - restarts `ai_engine` and `admin_ui`
- Re-runs diagnostics and exits with normal `agent check` exit codes.

Notes:

- `--fix` cannot be combined with `--json`.
- Base `config/ai-agent.yaml` is restored only when current base YAML is missing/invalid/conflicted.

### `agent rca`

```bash
# Most recent call
agent rca

# Specific call ID
agent rca --call <call_id>
```

Flags:

```bash
agent rca --call <call_id>
agent rca --llm
agent rca --json
```

### `agent update`

```bash
agent update
```

Use this for repo-based installs when you want a conservative “update + rebuild + verify” flow.

Flags:

```bash
agent update --remote origin
agent update --ref main
agent update --ref v6.1.1
agent update --checkout
agent update --include-ui
agent update --rebuild auto
agent update --rebuild none
agent update --rebuild all
agent update --force-recreate
agent update --skip-check
agent update --no-stash
agent update --stash-untracked
agent update --backup-id my-recovery-point
agent update --plan
agent update --plan --plan-json
agent update --self-update
```

### `agent version`

```bash
agent version
```

## Functional legacy commands (hidden)

These are still functional but hidden from the main help output:

- `agent doctor` (alias of `agent check`)
- `agent troubleshoot` (legacy alias path to RCA runner)
- `agent init` and `agent quickstart` (legacy setup flows)
- `agent dialplan` (dialplan snippet helper)
- `agent demo` (audio pipeline validation)
- `agent config validate` (legacy config validation)

Useful legacy flags:

```bash
agent doctor --json
agent troubleshoot --call <id> --list --last --symptom <symptom> --interactive --collect-only --no-llm --llm --json
agent init --non-interactive --template <template>
agent dialplan --provider <provider> --file <path>
agent demo --wav <file.wav> --loop <n> --save
agent config validate --file config/ai-agent.yaml --fix --strict
```

`agent config validate --fix` is functional but intentionally limited (it does not perform full backup-based recovery).

## Notes

- CLI details for building from source live in `cli/README.md`.
- For call-level debugging, use **Admin UI → Call History** first, then `agent rca` for a concise root-cause summary.
