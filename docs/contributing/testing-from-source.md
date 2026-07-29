# Testing AAVA From Source

This guide explains how to test the latest unreleased code on `main` (or a specific PR / feature branch) while **preserving your existing configuration and settings**.

## Why this guide exists

`main` is now the active development branch — new features and fixes land there before they're cut into a tagged release. Testing `main` (or a PR branch) lets you catch regressions early, validate fixes for issues you've reported, and try features before they ship.

> **Note:** the previous `develop` branch is no longer maintained. If you have an old checkout on `develop`, follow the migration steps below.

## What gets preserved

- Your `config/ai-agent.yaml` settings
- Your `config/ai-agent.local.yaml` operator overrides (if present)
- Your `.env` file (API keys, secrets)
- Your context configurations under `config/contexts/`
- Your tool configurations
- Your dialplan settings (in Asterisk/FreePBX)

---

## Quick method (recommended)

### 1. Backup your configs

```bash
cd /path/to/AVA-AI-Voice-Agent-for-Asterisk

BACKUP_DIR="config_backup_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

cp config/ai-agent.yaml "$BACKUP_DIR/"
cp config/ai-agent.local.yaml "$BACKUP_DIR/" 2>/dev/null || true
cp .env "$BACKUP_DIR/" 2>/dev/null || true
cp -r config/contexts "$BACKUP_DIR/" 2>/dev/null || true

echo "Backup saved to: $BACKUP_DIR"
```

### 2. Switch to the latest `main`

```bash
git fetch origin
git stash                  # safety: park any local changes
git checkout main
git pull origin main
```

To test a **specific PR branch** instead:

```bash
git fetch origin pull/<PR_NUMBER>/head:test-pr-<PR_NUMBER>
git checkout test-pr-<PR_NUMBER>
```

To test a **specific feature branch**:

```bash
git fetch origin <branch-name>
git checkout <branch-name>
```

### 3. Restore your configs

```bash
cp "$BACKUP_DIR/ai-agent.yaml" config/
cp "$BACKUP_DIR/ai-agent.local.yaml" config/ 2>/dev/null || true
cp "$BACKUP_DIR/.env" . 2>/dev/null || true
cp -r "$BACKUP_DIR/contexts" config/ 2>/dev/null || true
```

### 4. Rebuild and test

```bash
docker compose down
docker compose up -d --build
docker logs -f ai_engine
```

Run `agent check` to verify the system is healthy, then place a test call.

### 5. Revert to a stable release (when done)

```bash
# Pin to a tagged release (replace with the latest tag from `git tag --sort=-creatordate | head`)
git checkout v6.4.2

# Restore configs again
cp "$BACKUP_DIR/ai-agent.yaml" config/
cp "$BACKUP_DIR/ai-agent.local.yaml" config/ 2>/dev/null || true
cp "$BACKUP_DIR/.env" . 2>/dev/null || true

docker compose down
docker compose up -d --build
```

---

## Migrating from an old `develop` checkout

If your local clone is still on `develop`:

```bash
# See where develop is vs main
git fetch origin
git log --oneline origin/develop..origin/main | head        # what main has that develop doesn't
git log --oneline origin/main..origin/develop | head        # anything stranded on develop (should be empty)

# Move to main
git checkout main
git pull origin main

# Optional: drop the stale develop tracking branch locally
git branch -D develop
```

---

## Alternative: side-by-side installation

Keep the stable release and a test checkout in two separate directories so you can flip between them without rebuilding:

### 1. Clone `main` to a separate directory

```bash
git clone https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk.git AVA-test
cd AVA-test
```

### 2. Copy your configs

```bash
cp /path/to/existing/AVA-AI-Voice-Agent-for-Asterisk/config/ai-agent.yaml config/
cp /path/to/existing/AVA-AI-Voice-Agent-for-Asterisk/config/ai-agent.local.yaml config/ 2>/dev/null || true
cp /path/to/existing/AVA-AI-Voice-Agent-for-Asterisk/.env .
cp -r /path/to/existing/AVA-AI-Voice-Agent-for-Asterisk/config/contexts config/ 2>/dev/null || true
```

### 3. Avoid port collisions (if running concurrently)

AAVA uses host network mode by default, so two instances on the same host **will collide** on the AudioSocket port (`8090`), ExternalMedia RTP range (`18080-18099/udp`), the health endpoint (`15000`), the local AI server WebSocket (`8765`), and ARI (`8088`). To run side-by-side you'd need to change the bind ports for the test instance — adjust `audiosocket.port` and `external_media.rtp_port`/`port_range` in `config/ai-agent.yaml`, and `LOCAL_WS_PORT` and `HEALTH_BIND_HOST` in `.env`. Most contributors find swapping branches in one checkout (steps 1–4 above) simpler than maintaining two parallel instances.

### 4. Run

```bash
docker compose up -d --build
```

---

## Config migration notes

### New settings on `main`

`main` may have new configuration options ahead of the last release. Check for:

1. **New YAML fields**: `diff config/ai-agent.yaml config/ai-agent.example.yaml`
2. **New environment variables**: check `.env.example` for additions
3. **Schema changes**: read the relevant entries in `CHANGELOG.md` (`[Unreleased]` section)

### Merging new options

```bash
diff config/ai-agent.yaml config/ai-agent.example.yaml
# or:
vimdiff config/ai-agent.yaml config/ai-agent.example.yaml
```

---

## Reporting issues

When testing pre-release code, please report any issues:

1. **Join Discord**: [https://discord.gg/ysg8fphxUe](https://discord.gg/ysg8fphxUe)
2. **Collect diagnostics**:

   ```bash
   ./scripts/rca_collect.sh
   ```

3. **Include**:
   - Branch & commit: `git rev-parse --short HEAD` and `git status`
   - Error logs: `docker logs ai_engine 2>&1 | tail -100`
   - Provider in use (Deepgram, OpenAI, Google, Local, etc.)
   - Steps to reproduce

---

## One-liner: backup → switch to `main` → restore → rebuild

```bash
BACKUP="config_backup_$(date +%Y%m%d_%H%M%S)" && \
mkdir -p "$BACKUP" && \
cp config/ai-agent.yaml config/ai-agent.local.yaml .env "$BACKUP" 2>/dev/null; \
cp -r config/contexts "$BACKUP" 2>/dev/null; \
git fetch origin && git checkout main && git pull origin main && \
cp "$BACKUP/ai-agent.yaml" config/ && \
cp "$BACKUP/ai-agent.local.yaml" config/ 2>/dev/null; \
cp "$BACKUP/.env" . 2>/dev/null; \
cp -r "$BACKUP/contexts" config/ 2>/dev/null; \
docker compose down && docker compose up -d --build
```

---

## FAQ

**Q: Will my dialplan break?**
A: Usually no. Dialplan changes are rare and called out in `CHANGELOG.md` when they happen.

**Q: What if `main` has breaking config changes?**
A: Check `CHANGELOG.md` `[Unreleased]` for migration notes. Most changes are additive and the engine logs deprecation warnings before removal.

**Q: Can I contribute fixes while testing?**
A: Yes — branch from `main` for new work: `git checkout -b fix/my-fix main`, then PR against `main` per [CONTRIBUTING.md](../../CONTRIBUTING.md).

**Q: How do I see what's new on `main` since the last release?**
A:

```bash
git log $(git describe --tags --abbrev=0)..main --oneline
# or read the CHANGELOG.md `[Unreleased]` section
```

---

## See also

- [Quickstart](quickstart.md) — first-time development environment setup
- [Testing guide](testing-guide.md) — running the test suite
- [Debugging guide](debugging-guide.md) — troubleshooting issues
- [CONTRIBUTING.md](../../CONTRIBUTING.md) — branching policy and PR workflow
