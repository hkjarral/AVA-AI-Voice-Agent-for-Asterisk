# Operator Migration: YAML → agents.db

This guide covers the one-time migration that moves your YAML-defined contexts into `agents.db`, the ongoing drift detection that keeps them in sync, and the rollback procedure if something goes wrong.

See [AGENTS.md](AGENTS.md) for an overview of what agents are and how they are used at call time.

---

## One-time migration

When the Admin UI (`admin_ui`) container starts for the first time after upgrading to v1a:

1. It reads every merged effective context from `ai-agent.yaml` and `config/contexts/*.yaml` (inline YAML wins over external files; `system_prompt` → `prompt` mapping applied).
2. Each context is imported into `agents.db` with full provenance (`source_file` recorded).
3. Imported rows are marked `is_operator_managed = 0` — the engine treats them identically to hand-created agents; the flag is for audit purposes only.
4. The `default` YAML context (if present) becomes the default agent in `agents.db`.
5. A normalized SHA-256 of the effective context dictionary is stored in `schema_migrations` as the drift baseline.

The migration is **idempotent**: if `agents.db` already exists and contains rows, the migration is skipped. You will not lose data by restarting the Admin UI container.

The migration runs inside a file lock and a database transaction. Per-context validation errors are logged and skipped rather than aborting the whole batch. Unexpected errors roll back cleanly.

---

## Drift detection

After migration, the Admin UI computes the effective context hash on startup and compares it to the stored baseline. If you edit `ai-agent.yaml` or `config/contexts/*.yaml` after migration, drift is detected and surfaced through four channels:

| Channel | What you see |
|---------|--------------|
| Admin UI startup log | `[agents_migration] YAML drift detected — hash changed` |
| Agents page banner | Yellow warning banner with a link to the Migration Status page |
| `agent check` (CLI) | `WARN  agents.db present but YAML drift detected` with a remediation hint |
| Migration Status page | Hash comparison (stored vs. current, first 12 chars each) and action buttons |

Navigate to **Admin UI → Agents → Migration Status** (`/agents/migration`) for details and actions.

---

## Reconcile vs. Acknowledge

Two actions are available when drift is detected:

### Import YAML changes (reconcile)

Upserts changed and newly-added YAML contexts into `agents.db`. Existing agents that have been edited via the Agents tab are not overwritten unless their slug matches a changed YAML context. A toast confirms how many agents were applied.

Use this when you intentionally updated `ai-agent.yaml` and want those changes reflected in the Agents tab.

### Acknowledge — keep DB as-is

Records the current YAML hash as the new baseline without changing any agent data. The drift warning is silenced.

Use this when the YAML changed for an unrelated reason (e.g. a `git pull`) and you want to keep your database state as the source of truth.

---

## export-agents-yaml (disaster recovery)

If `agents.db` contains agent configurations that are not in your YAML files and you need a recovery copy:

```bash
docker exec admin_ui python -m export_agents_yaml > contexts-recovered.yaml
```

This reads all agents from `agents.db` and emits a `contexts:` YAML block compatible with `ai-agent.yaml`, preserving `provider`, `prompt`, `voice`, `greeting`, `audio_profile` (as `profile`), `tools`, and any extra fields (`pipeline`, `background_music`, etc.).

The output file can be merged back into `ai-agent.yaml` or used as a standalone `config/contexts/` file.

---

## Rollback (if migration misbehaves)

1. `docker compose stop ai_engine admin_ui`
2. `rm ./data/operator/agents.db ./data/operator/agents.db-wal ./data/operator/agents.db-shm` (host path, relative to repo root — the compose bind is `./data` → `/app/data`)
3. `docker compose start ai_engine admin_ui` — the engine falls back to reading
   `ai-agent.yaml` + `config/contexts/` directly (pre-migration behavior).
4. Please file a GitHub issue with the admin_ui startup log attached.

---

## Related

- [AGENTS.md](AGENTS.md) — agent overview, channel variables, headless/YAML mode, stats.
- [Configuration-Reference.md](Configuration-Reference.md) — full YAML context schema.
