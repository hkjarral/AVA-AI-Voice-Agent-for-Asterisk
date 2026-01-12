# Supported Platforms Matrix

This project is designed to run on Linux hosts that either:

- Run Asterisk/FreePBX on the same machine, or
- Can reliably reach an Asterisk/FreePBX host over the network.

We do **not** target macOS/Windows as production hosts for Asterisk. Those are dev-only environments.

## Support Tiers

- **Tier 1 (CI-tested):** We expect this to work out-of-the-box and keep it green in CI.
- **Tier 2 (Community-verified):** Known to work based on community reports + troubleshooting artifacts.
- **Tier 3 (Best-effort):** Likely works if Docker/Compose requirements are met, but not verified.

## Current Verification Status

| Platform | Tier | Status | Notes |
|----------|------|--------|------|
| **PBX Distro** `12.7.8-2306-1.sng7` | Tier 2 | ✅ Verified (project dev server) | Only fully verified end-to-end environment to date |
| Ubuntu 22.04 | Tier 1 | ⏳ Pending | Add CI + community verification |
| Ubuntu 24.04 | Tier 1 | ⏳ Pending | Add CI + community verification |
| Debian 11/12 | Tier 2 | ⏳ Pending | Community verification requested |
| Rocky/Alma 9 | Tier 2 | ⏳ Pending | Community verification requested |
| Fedora (latest) | Tier 3 | ⚠ Best-effort | Rootless Docker common; we warn rather than “guarantee” |
| openSUSE (Leap/Tumbleweed) | Tier 3 | ⚠ Best-effort | Docker/Podman + socket paths vary; Admin UI Docker management may require manual `.env` tuning |
| Podman (`docker` shim / rootless) | Tier 3 | ⚠ Best-effort | Supported for “run containers” best-effort; Docker-management features may not work reliably |

## Baseline Requirements (All Tiers)

- Docker + Docker Compose v2
- x86_64 Linux host (Tier 1/2). Docker Desktop/macOS/Windows and ARM hosts are Tier 3 best-effort for testing only.
- Asterisk ARI reachable and credentials configured in `.env`

## Tier 3 (Best-effort) Expectations

Tier 3 environments are welcome, but we optimize for failures that are **explainable** and **diagnosable** (not “it works everywhere”).

- **Docker management from Admin UI requires a working Docker API socket** inside `admin-ui`.
  - Default is `/var/run/docker.sock`.
  - Rootless commonly uses `$XDG_RUNTIME_DIR/docker.sock` (often `/run/user/<uid>/docker.sock`).
  - Persist this by setting `DOCKER_SOCK=...` in `.env` and recreating `admin-ui`.
- **Health checks are performed from inside the `admin-ui` container**.
  - On Tier 3 hosts, you may need to set `HEALTH_CHECK_AI_ENGINE_URL` and `HEALTH_CHECK_LOCAL_AI_URL` in `.env` so probes use reachable addresses.
- **Podman is best-effort**. If Admin UI Docker operations fail under Podman, use Docker Engine for a supported path.
- **Unsupported distros**: `./preflight.sh` will warn and provide manual installation guidance. Provide artifacts (below) so we can improve detection and docs.

## Evidence Required for Tier 2 (Community-verified)

When reporting “works on X”, include:

- `./preflight.sh` output (and `./preflight.sh --apply-fixes` if used)
- `agent check --json` output
- One confirmed baseline call flow:
  - Provider: Deepgram/OpenAI Realtime/Google Live/ElevenLabs/local
  - Transport: AudioSocket or ExternalMedia RTP

If a report includes these artifacts, we can promote the platform in this matrix.
