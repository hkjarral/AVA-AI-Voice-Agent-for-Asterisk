# Migration Guide

This guide covers upgrading between major versions of Asterisk AI Voice Agent.

## v6.0.0 to v6.1.1

**No breaking changes.** All new features are additive or opt-in.

```bash
# Standard upgrade
git pull
docker compose -p asterisk-ai-voice-agent up -d --build --force-recreate
```

New in v6.1.1:
- Operator config overrides via `config/ai-agent.local.yaml` (optional, git-ignored)
- Live agent transfer tool (opt-in via tool allowlist)
- ViciDial outbound dialer compatibility (opt-in via `.env`)

## v5.x to v6.0.0

### Breaking Changes

1. **OpenAI Realtime API version default changed to GA**
   - The default `api_version` is now `ga` (was `beta`)
   - GA uses nested audio schema (`audio.input.format` / `audio.output.format` with MIME types)
   - **To keep old behavior**: Set `api_version: beta` explicitly in your provider config

2. **Email template autoescaping enabled**
   - `template_renderer.py` now uses `autoescape=True` by default
   - Custom HTML templates that use raw HTML variables need Jinja2's `| safe` filter

### Upgrade Steps

```bash
# 1. Backup your configuration
cp .env .env.backup
cp config/ai-agent.yaml config/ai-agent.yaml.backup

# 2. Pull the latest code
git pull

# 3. Run preflight to update environment
sudo ./preflight.sh --apply-fixes

# 4. Rebuild and restart all containers
docker compose -p asterisk-ai-voice-agent up -d --build --force-recreate

# 5. Verify health
curl http://localhost:15000/health
agent check
```

If you were using `api_version: beta` explicitly, no OpenAI changes are needed. If you relied on the default, review your OpenAI provider config.

## v4.x to v6.0.0

### Major Changes

- **Config schema v4**: Milestone 13 migrated configuration format. Run `scripts/migrate_config_v4.py --dry-run` to preview changes, then `--apply` to migrate.
- **Diagnostic settings moved to `.env`**: Settings like `DIAG_EGRESS_SWAP_MODE`, `DIAG_ENABLE_TAPS`, and `STREAMING_LOG_LEVEL` are now environment variables, not YAML keys.
- **Prometheus/Grafana removed**: The monitoring stack is no longer shipped. Use Admin UI Call History for per-call debugging and bring your own Prometheus if needed.
- **Admin UI added**: Web interface on port 3003 for configuration and monitoring.
- **Multiple new providers**: Google Live, ElevenLabs Agent added since v4.x.
- **Tool calling system**: Unified tool framework with telephony and business tools.

### Upgrade Steps

```bash
# 1. Backup everything
cp -r config/ config.backup/
cp .env .env.backup

# 2. Pull the latest code
git pull

# 3. Run config migration (preview first)
python scripts/migrate_config_v4.py --dry-run
python scripts/migrate_config_v4.py --apply

# 4. Run preflight
sudo ./preflight.sh --apply-fixes

# 5. Rebuild containers
docker compose -p asterisk-ai-voice-agent up -d --build --force-recreate

# 6. Verify
agent check
curl http://localhost:15000/health
```

### Post-Migration

- Access Admin UI at `http://localhost:3003` (login: admin/admin, change immediately)
- Review your provider configuration in the Admin UI Setup Wizard
- Check Call History for your first test call to verify everything works

## General Upgrade Procedure

For any version upgrade:

1. **Backup** your `.env`, `config/ai-agent.yaml`, and `config/ai-agent.local.yaml`
2. **Pull** the latest code: `git pull` (or use `agent update` which handles backup/restore automatically)
3. **Run preflight**: `sudo ./preflight.sh --apply-fixes`
4. **Rebuild**: `docker compose -p asterisk-ai-voice-agent up -d --build --force-recreate`
5. **Verify**: `agent check` and make a test call

For detailed release notes, see [CHANGELOG.md](../CHANGELOG.md).
