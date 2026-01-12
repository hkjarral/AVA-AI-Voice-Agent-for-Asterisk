# Agent CLI Tools

Go-based command-line interface for Asterisk AI Voice Agent operations.

## Overview

The `agent` CLI provides a comprehensive set of tools for setup, diagnostics, and troubleshooting. All commands are built as a single Go binary for easy distribution.

**Current Status**: ✅ CLI v5.0 (simplified surface)

## Available Commands

- **`agent setup`** - Interactive setup wizard
- **`agent check`** - Standard diagnostics report
- **`agent rca`** - Post-call root cause analysis
- **`agent version`** - Show version information

Legacy aliases (hidden from `--help` in v5.0):
- `agent init` → `agent setup`
- `agent doctor` → `agent check`
- `agent troubleshoot` → `agent rca`

## Installation

### Quick Install (Recommended)

**Linux/macOS:**
```bash
curl -sSL https://raw.githubusercontent.com/hkjarral/Asterisk-AI-Voice-Agent/main/scripts/install-cli.sh | bash
```

This will:
- Detect your platform automatically
- Download the latest binary
- Verify checksums
- Install to `/usr/local/bin`
- Test the installation

### Manual Download

Download pre-built binaries from [GitHub Releases](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases):

**Linux:**
```bash
# AMD64 (most Linux servers)
curl -L -o agent https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/latest/download/agent-linux-amd64
chmod +x agent
sudo mv agent /usr/local/bin/

# ARM64 (Raspberry Pi, AWS Graviton)
curl -L -o agent https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/latest/download/agent-linux-arm64
chmod +x agent
sudo mv agent /usr/local/bin/
```

**macOS:**
```bash
# Intel Macs
curl -L -o agent https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/latest/download/agent-darwin-amd64
chmod +x agent
sudo mv agent /usr/local/bin/

# Apple Silicon (M1/M2/M3)
curl -L -o agent https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/latest/download/agent-darwin-arm64
chmod +x agent
sudo mv agent /usr/local/bin/
```

**Windows:**
Download `agent-windows-amd64.exe` from releases and add to your PATH.

### Verify Installation

```bash
agent version
```

## Building from Source

### Prerequisites

- Go 1.21 or newer
- Linux/macOS/Windows

### Build Instructions

```bash
# From project root
make cli-build

# Or build manually
cd cli
go build -o ../bin/agent ./cmd/agent
```

### Build for All Platforms

```bash
# Creates binaries for Linux, macOS, Windows (AMD64 & ARM64)
make cli-build-all

# Generate checksums
make cli-checksums

# Complete release build
make cli-release
```

## Command Reference

### `agent setup` - Interactive Setup Wizard

Guided setup wizard to configure Asterisk AI Voice Agent from scratch.

**Usage:**
```bash
agent setup
```

**Steps:**
1. **Asterisk ARI Connection** - Host, username, password validation
2. **Audio Transport** - AudioSocket (modern) or ExternalMedia RTP (legacy)
3. **AI Provider** - OpenAI, Deepgram, Google, or Local Hybrid
4. **Configuration Review** - Saves to `.env` and restarts services

**Example:**
```bash
$ agent setup

Step 1/4: Asterisk ARI Connection
Enter Asterisk host [127.0.0.1]: 
Enter ARI username: AIAgent
Enter ARI password: ******
✓ Testing ARI connection... Success!

Step 2/4: Audio Transport Selection
  1) AudioSocket (Modern, for full agents) [RECOMMENDED]
  2) ExternalMedia RTP (Legacy, for hybrid pipelines)
Your choice [1]: 1

Step 3/4: AI Provider Selection
  1) OpenAI Realtime (0.5-1.5s response time)
  2) Deepgram Voice Agent (1-2s response time)
  3) Local Hybrid (3-7s, privacy-focused)
Your choice [1]: 1

Enter OpenAI API Key: sk-...
✓ API key validated
✓ Configuration saved to .env
✓ Docker services restarted

Setup complete! 🎉
```

---

### `agent check` - Standard Diagnostics Report

Comprehensive health check and diagnostics tool.

**Usage:**
```bash
agent check [--json] [-v] [--no-color]
```

**Flags:**
- `--json` - Output as JSON (JSON only)
- `--verbose` - Show detailed check output

**Exit Codes:**
- `0` - All checks passed ✅
- `1` - Warnings detected (non-critical) ⚠️
- `2` - Failures detected (critical) ❌

**What it includes (high-level):**
- Docker + Compose environment details
- `ai_engine` container status, mounts, and network mode
- Container-side ARI probes + app registration check
- Transport compatibility + advertise host alignment
- Best-effort internet/DNS reachability (FYI / skip on failure)

**Example:**
```bash
$ agent check

[1/11] Docker Daemon...     ✅ Docker running (v26.1.4)
[2/11] Containers...        ✅ ai_engine running (healthy)
[3/11] Asterisk ARI...      ✅ Connected to 127.0.0.1:8088
[4/11] AudioSocket Port...  ✅ Port 8090 listening
[5/11] Configuration...     ✅ YAML valid
[6/11] API Keys...          ✅ OPENAI_API_KEY present
[7/11] Provider Connectivity... ✅ OpenAI API reachable (134ms)

Summary: 10 passed, 0 warnings, 0 failures
✅ System is healthy - ready for calls!
```

**Use in Scripts:**
```bash
if ! agent check; then
    echo "Health check failed"
    exit 1
fi
```

---

### `agent rca` - Post-Call Analysis

Analyze the most recent call (or a specific call ID) and print an RCA report.

**Usage:**
```bash
# Analyze most recent call
agent rca

# Analyze specific call
agent rca --call <call_id>

# JSON-only output
agent rca --json

# Verbose output
agent rca -v
```

Advanced (legacy alias; hidden from `agent --help`):
```bash
agent troubleshoot --list
agent troubleshoot --last --symptom <no-audio|garbled|echo|interruption|one-way>
```

---

## Hidden (Legacy) Commands

CLI v5.0 intentionally keeps a small visible surface (`agent setup/check/rca/version`). For backwards compatibility and advanced workflows, these commands still exist but are hidden from `agent --help`:

- Compatibility aliases: `agent init`, `agent doctor`, `agent troubleshoot`
- Advanced tools: `agent demo`, `agent dialplan`, `agent config validate`

### `agent version` - Show Version

**Usage:**
```bash
agent version
```

**Output:**
```
Asterisk AI Voice Agent CLI
Version: vX.Y.Z
Built: YYYY-MM-DDTHH:MM:SSZ
Repository: https://github.com/hkjarral/Asterisk-AI-Voice-Agent
```

---

## Common Workflows

### First-Time Setup
```bash
# 1. Run interactive setup
agent setup

# 2. Run standard diagnostics report
agent check

# 3. Make a test call
```

### Troubleshooting Issues
```bash
# 1. Run standard diagnostics report (attach output to issues)
agent check

# 2. Analyze most recent call
agent rca
```

### CI/CD Integration
```bash
#!/bin/bash
# Pre-deployment validation

agent check --json || exit 1

echo "✅ Validation passed - deploying..."
```

## Additional Resources

- **[TROUBLESHOOTING_GUIDE.md](../docs/TROUBLESHOOTING_GUIDE.md)** - General troubleshooting
- **[CHANGELOG.md](../CHANGELOG.md)** - CLI tools features and updates
- **[INSTALLATION.md](../docs/INSTALLATION.md)** - Full installation guide

## Development

### Project Structure

```
cli/
├── cmd/agent/           # Main CLI commands
│   ├── main.go          # Root command and app entry
│   ├── init.go          # Setup wizard
│   ├── doctor.go        # Health checks
│   ├── demo.go          # Audio validation
│   ├── troubleshoot.go  # Post-call analysis
│   └── version.go       # Version command
└── internal/            # Internal packages
    ├── wizard/          # Interactive setup wizard
    ├── health/          # Health check system
    ├── audio/           # Audio test utilities
    └── rca/             # Root cause analysis
```

### Dependencies

```bash
# Install dependencies
go mod download

# Update dependencies
go get -u ./...
go mod tidy
```

### Testing

```bash
# Run tests
go test ./...

# Run with coverage
go test -cover ./...
```

## Roadmap

See `docs/ROADMAP.md`.

## Exit Codes

Commands follow standard Unix exit code conventions:

- **0** - Success
- **1** - Warning (non-critical issues detected)
- **2** - Failure (critical issues detected)

Use in scripts:

```bash
#!/bin/bash
if ! ./bin/agent check; then
    echo "Health check failed - see output above"
    exit 1
fi
```

## Support

- **Documentation**: [docs/CLI_TOOLS_GUIDE.md](../docs/CLI_TOOLS_GUIDE.md)
- **Issues**: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/issues
- **Discussions**: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/discussions

## License

Same as parent project - see [LICENSE](../LICENSE)
