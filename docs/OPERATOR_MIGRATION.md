# Operator Migration: Contexts to Agents

v7.4 removes Contexts from product navigation and runtime routing. Agents stored in
`data/operator/agents.db` are the source of truth for call personas, providers,
prompts, voices, and per-Agent tool access.

See [AGENTS.md](AGENTS.md) for day-to-day Agent configuration and
[INSTALLATION.md](INSTALLATION.md#upgrade-to-v740-existing-checkout) for the complete
upgrade procedure.

## Before the first v7.4 start

Back up these files while the old release is still healthy:

- `data/operator/agents.db` when it already exists;
- `data/call_history.db`;
- `.env`, `config/ai-agent.yaml`, `config/ai-agent.local.yaml`, and `config/users.json`;
- `config/contexts/`; and
- any external secrets or media used by the deployment.

Also record `git status --short` and preserve intentional source changes. The v7.4
updater takes its own snapshots, but those do not replace an independent backup.

## One-time compatibility import

The **AI Engine**, not the Admin UI, owns the startup safety gate. Before it accepts
calls, it merges the legacy YAML Contexts and then:

1. acquires a same-directory migration lock;
2. checks whether `agents.db` already contains Agent rows;
3. validates every legacy Context, including its tool policy;
4. creates a temporary database and imports all rows in one transaction;
5. runs SQLite `integrity_check`; and
6. atomically replaces the empty target database.

A populated Agent store always wins and is never overwritten or reseeded. If any
Context is malformed, the entire import fails and the engine does not start serving
calls. This prevents a partially migrated routing configuration.

The legacy Context named `default` becomes the default Agent. If there is no Context
with that name, the first imported Agent becomes default. Fresh systems with no legacy
Contexts are seeded through the setup flow with Receptionist, Sales, and Support;
existing systems are not given those defaults.

## Tool policy conversion

Legacy Context `tool_overrides` are normalized into the Agent's resource policy. After
migration, review each Agent under **Agents → Edit → Tool Access**:

| Tool family | Inventory location | Agent policy |
|---|---|---|
| Transfers | Tools → transfer destinations | inherit, selected destination keys, or deny |
| Google Calendar | Tools → Google calendars | inherit, selected calendar keys, or deny |
| Microsoft Calendar | Tools → Microsoft accounts/calendars | inherit, selected account/calendar keys, or deny |
| Voicemail | Tools → voicemail destinations | inherit, selected mailbox keys, or deny |

Legacy single-calendar/account values and `tools.leave_voicemail.extension` remain
available as a compatible `default` resource. Empty or stale selected lists fail closed.
Global disabled settings always win over an Agent policy.

## Dialplan transition

Use the Agent slug shown in the Agents UI:

```asterisk
same => n,Set(AI_AGENT=receptionist)
same => n,Stasis(asterisk-ai-voice-agent)
```

`AI_CONTEXT` remains only as a deprecated compatibility selector for existing
dialplans. It first attempts a unique display-name match and then a slug match. Replace
it during normal PBX maintenance; do not add it to new examples or deployments.

## Legacy import report and YAML drift

The advanced **Agents → legacy Context import report** compares current legacy YAML
with the stored import baseline. It can:

- **Import YAML changes** by validating and upserting matching Agents; or
- **Acknowledge** the change while keeping the database as-is.

This page is a migration/recovery aid, not a second live configuration system. Editing
legacy Context YAML does not change a running call and is not the normal way to manage
v7.4 Agents. Make routine changes in the Agents UI/API and keep `agents.db` backed up.

## Disaster-recovery export

To create a readable recovery copy of the Agent store:

```bash
docker compose -p asterisk-ai-voice-agent exec -T admin_ui \
  python -m export_agents_yaml > agents-recovered.yaml
```

The output is a legacy-compatible `contexts:` block containing the recoverable Agent
fields. Treat it as a diagnostic/export artifact. Restoring `agents.db` is the preferred
v7.4 recovery method.

## Rollback boundaries

Do **not** delete `agents.db` as a generic rollback step. With v7.4 code still running,
legacy Contexts may be imported again into an empty store; with no valid import source,
Agent routing fails closed.

To restore v7.4 Agent data:

1. Stop `ai_engine` and `admin_ui`.
2. Copy the failed database aside for diagnosis.
3. Restore the matching backed-up `data/operator/agents.db` with its ownership and mode.
4. Start `admin_ui` and inspect the Agent list, then start `ai_engine` and run `agent check`.
5. Place a test call before returning the system to service.

The Admin UI update rollback restores application code and operator configuration from
`.agent/update-backups/`; it deliberately does not rewrite the live Agent or Call History
databases. Returning all the way to a pre-v7.4 YAML runtime therefore requires both the
older application release and its matching pre-v7.4 configuration. Preserve the v7.4
databases separately so a later forward upgrade can recover them.

## Post-migration verification

1. Confirm the expected Agent count, display names/slugs, default, and active states.
2. Compare prompts, provider/pipeline selections, voices, greetings, and extensions.
3. Review resource access for transfer, both calendars, and voicemail.
4. Place a call using `AI_AGENT` and exercise one allowed tool.
5. Confirm the canonical tool name and result appear in Call History.
6. Review recent AI Engine logs for migration, lookup, or tool-policy errors.

## Related documentation

- [Agents](AGENTS.md)
- [Configuration Reference](Configuration-Reference.md)
- [Tools Setup Guide](../admin_ui/Tools_Setup_Guide.md)
- [v7.4 migration notes](MIGRATION.md#v73x-to-v740)
