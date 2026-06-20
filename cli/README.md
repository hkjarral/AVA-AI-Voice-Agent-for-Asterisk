# Agent CLI

Go command-line tools for setup, validation, diagnostics, post-call RCA, dialplan generation, and safe updates.

The operator-facing reference is [`docs/CLI_TOOLS_GUIDE.md`](../docs/CLI_TOOLS_GUIDE.md).

## Commands

Visible commands in v7.0.1:

- `agent setup` — interactive configuration and dynamic provider/pipeline discovery
- `agent check` — standard health report and Local AI Server round-trip tests
- `agent rca` — deterministic call analysis with optional LLM interpretation
- `agent config validate` — configuration validation
- `agent dialplan` — `AI_AGENT` dialplan snippet generator
- `agent update` — plan or apply a safe repository update
- `agent version` — version and build information

Hidden compatibility commands are `doctor`, `troubleshoot`, `init`, `quickstart`, and `demo`. They delegate to maintained command paths; removed legacy flag behavior returns an explicit error.

## Build

Prerequisites:

- Go 1.22 or newer
- Linux, macOS, or Windows

From the repository root:

```bash
make cli-build
./bin/agent version
```

Manual development build:

```bash
cd cli
go build -o ../bin/agent ./cmd/agent
```

Release builds inject `main.version` and `main.buildTime` through the Makefile. Build every supported artifact with:

```bash
make cli-release
```

## Test

```bash
cd cli
go test ./...
go vet ./...
```

The Local AI helper scripts must also compile on Python 3.6 because supported operator hosts may use an older host Python even though containers use a newer runtime:

```bash
python3 -m py_compile ../scripts/check_local_server.py ../scripts/local_test_report.py
```

For a live development-server check, build a Linux binary and run it from the repository root so it can find `.env`, configuration, Compose files, and helper scripts.

## Structure

```text
cli/
├── cmd/agent/                 Cobra commands and update workflow
└── internal/
    ├── check/                 Standard diagnostics
    ├── config/                Configuration validation
    ├── dialplan/              Dialplan snippet generation
    ├── troubleshoot/          RCA, call-history enrichment, metrics, baselines
    └── wizard/                Setup and target discovery
```

## Design rules

- JSON modes keep stdout machine-readable; progress and warnings go to stderr.
- RCA derives call identity and outcome from Call History and uses logs only for call-scoped diagnostic evidence.
- LLM diagnosis is optional and must not replace deterministic findings.
- Setup writes operator overrides instead of freezing the base configuration.
- Update backups use SQLite's online backup API rather than copying live database files.
- Compatibility aliases must delegate to maintained implementations instead of carrying duplicate setup or diagnostic logic.

## Installation

Released binaries are installed through:

```bash
curl -sSL https://raw.githubusercontent.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/main/scripts/install-cli.sh | bash
```

See the [CLI Tools Guide](../docs/CLI_TOOLS_GUIDE.md) for command examples and troubleshooting semantics.
