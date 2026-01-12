# Agent CLI v5.0 — Simplified Operator Workflow

**Status**: Design / implementation plan

## Goals

- Reduce CLI surface area to **3 high-value commands**:
  - `agent setup` — setup/config + dialplan guidance
  - `agent check` — single shareable diagnostics report ("attach this to issues")
  - `agent rca` — call-focused RCA (post-call)
- Make `agent check` a **single source of truth** for:
  - host + docker + compose
  - container status, mounts, network mode
  - ARI reachability **from inside `ai_engine` only**
  - transport/compatibility alignment (config vs runtime expectations)
  - persistence readiness (media + call history DB)
  - best-effort internet/DNS reachability
- Keep flags minimal and consistent.

## Non-goals

- No auto-fix in v5.0 (`--fix` removed until it truly fixes).
- No requirement to install extra tools inside `ai_engine` (e.g., `curl`).
- No attempt to read remote Asterisk dialplan files directly.

## Command Surface (v5.0)

### `agent version`

**Purpose**: show version/build info (useful in support requests and issue templates).

### 1) `agent setup`

**Purpose**: interactive setup and validation entrypoint.

**Behavior**:
- Guides user through:
  - ARI host/port/scheme/auth
  - transport selection
  - provider selection + key presence checks
  - writes `.env` + `config/ai-agent.yaml`
  - prints the expected Stasis app name and minimal dialplan snippet
- Ends by running `agent check`.

**Flags**:
- `-v/--verbose`
- `--no-color`

### 2) `agent check`

**Purpose**: the standard support report.

**Behavior**:
- Prints a structured report to stdout (copy/paste friendly; redirect to a file if needed).
- **Runs all probes using `docker exec ai_engine ...`** (no `docker run`, no external containers).
- Internet reachability is **best-effort** (warn/skip only).

**Flags**:
- `-v/--verbose` (include raw probe details)
- `--json` (JSON-only output to stdout)
- `--no-color` (disable color; also auto-disabled when stdout is not a TTY)

**Report sections** (order is stable):
1. Header: CLI version/build, timestamp, host identifiers
2. Host OS: `/etc/os-release`, kernel, arch
3. Docker/Compose: versions and availability
4. Containers: `ai_engine` running/healthy, image info
5. Network mode: `NetworkMode` and port exposure expectations
6. Mounts: `/app/data`, `/mnt/asterisk_media` present + writable
7. Call history DB: SQLite temp write test under `/app/data` (canonical DB: `/app/data/call_history.db`)
8. Config effective summary: `audio_transport`, `active_pipeline`, `downstream_mode`, format/ports
9. Transport compatibility: validate against `docs/Transport-Mode-Compatibility.md`
10. Advertise host alignment:
    - `EXTERNAL_MEDIA_ADVERTISE_HOST`
    - `AUDIOSOCKET_ADVERTISE_HOST`
    - compare to network mode + Asterisk topology
11. ARI probe (container-side only):
    - GET `/ari/asterisk/info` (Asterisk version)
    - GET `/ari/applications` (verify expected `app_name`)
12. Dialplan guidance:
    - If Asterisk local: optionally read dialplan file and grep for `Stasis(<app_name>)`
    - If Asterisk remote: print the commands the user should run and paste output
13. Best-effort internet/DNS: in-container DNS resolve + TCP connect checks
14. Summary: PASS/WARN/FAIL counts + top remediations

### 3) `agent rca`

**Purpose**: post-call RCA.

**Behavior**:
- Defaults to the most recent call.
- Prints a shareable RCA summary to stdout (redirect to a file if needed).
- Emits top likely causes + exact remediations.

**Flags**:
- `-v/--verbose`
- `--no-color`
- `--json` (JSON-only output to stdout)
- `--call <id>` (optional)

## Probes: `docker exec ai_engine` only

All in-container probes must work without installing additional packages.

### Required Python availability

The `ai_engine` image is Python-based (`python:3.11-slim-bookworm`) and includes:
- stdlib modules: `os`, `json`, `sqlite3`, `socket`, `ssl`, `urllib.request`
- repo requirements include: `PyYAML` and `websockets`
- `curl` is intentionally not present.

### Planned exec probes

- **Config parse**: `python -c` loads `/app/config/ai-agent.yaml` via `yaml.safe_load`.
- **Mount + perms**: create/delete temp file in `/mnt/asterisk_media/ai-generated` and `/app/data`.
- **SQLite write test**: create `/app/data/.call_history_sqlite_test.db` (matches `preflight.sh` pattern).
- **ARI probe** (no curl): use `urllib.request` + Basic Auth:
  - `/ari/asterisk/info`
  - `/ari/applications` (verify app name)
- **Best-effort DNS/TCP** (no external container): `socket.getaddrinfo` + `socket.create_connection`.

## Known recurring failures (from community reports)

- ARI unreachable from inside container (wrong `ASTERISK_HOST`, wrong topology assumptions).
- Media mount RO/permission mismatch (PlaybackManager fallback to `/tmp`).
- Local AI server absent when config expects it.
- NAT advertise host mis-set for ExternalMedia RTP.

`agent check` must directly detect and print remediation for these.

## Backwards Compatibility

- Keep old commands as aliases (hidden in help) until v5.1:
  - `agent doctor` → alias of `agent check`
  - `agent troubleshoot` → alias of `agent rca`
  - `agent init` / `agent quickstart` → alias of `agent setup`

## Documentation changes (v5.0)

- Update docs to reference `agent setup`, `agent check`, `agent rca` and call this CLI version **5.0**.

## Publishing (CLI release)

- Build artifacts via Makefile targets (CI or maintainer machine):
  - `make cli-build-all`
  - `make cli-checksums`
- Publish GitHub Release tagged `v5.0.0` (or `agent-cli-v5.0.0` if decoupled) including:
  - `agent-<os>-<arch>` binaries
  - `SHA256SUMS`
- Update installer guidance (`scripts/install-cli.sh`) to show `agent check/rca/setup`.
