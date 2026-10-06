# Develop and Validate With an AI Assistant

Start with [AVA.mdc](../../AVA.mdc) and the [quickstart](quickstart.md).
This workflow applies to the user's actual deployment, including production.
Use the existing authorization for the task; clarify missing targets or disruptive
actions before executing them. A separate lab is an option, not a prerequisite
for inspecting or supporting an existing installation.

## 1. Establish Access and a Baseline

Inspect the workspace before asking the user for facts available in files or tools.
Keep a local working note with the goal, source/deployed revisions, active
configuration sources, intended changes, test results, and remaining work. Keep
private deployment details out of commits. For an ongoing task, read its handoff
and confirm current state; historical chats are evidence, not new authorization.

Record:

- Laptop/IDE checkout path, Git remotes, branch, revision, and existing changes.
- Deployment host/SSH alias, port/jump host if needed, remote repository path,
  installed revision, Compose project/files, and running services.
- Asterisk version/modules, inbound/outbound route, provider/model or pipeline,
  transport, and test extension. Distinguish the PBX host from a remote AI host.
- Whether this is production, any active calls, and the authorized change window.
- A known-good revision and how to restore both application and persistent state.

If remote access is unavailable, explain the exact connection failure and give
the next setup step. Continue source review or support-package analysis meanwhile.
Do not invent a maintainer server or assume that a public hostname bypasses a VPN
or jump host.

### SSH setup

Prefer an existing working SSH configuration. If setup is needed, help the user
install their **public** key on their server using their established access method.
The private key stays on their machine; an SSH agent can unlock it once for the
session. Verify the server fingerprint through a trusted channel at first connection.
Do not disable host-key checking or put passwords in commands.

Example local `~/.ssh/config` entry; replace values with the user's details:

```sshconfig
Host ava-host
    HostName your-server.example.com
    User your-deploy-user
    Port 22
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    # ProxyJump your-existing-jump-alias
```

After the user has established trust/key access, check non-interactive access:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 ava-host 'true'
```

Use the permissions needed by the existing deployment; do not change users or
grant access to Docker as an incidental documentation/setup step. Use a private,
ignored local note for the host/path mapping. Never commit `.env`, key files,
credentials, raw `docker inspect` output, or expanded Compose configuration.

## 2. Research and Scope

**For every new capability: research first, scope second, build third.** This
includes full-agent providers, modular STT/LLM/TTS providers, all three tool phases,
and other integrations. Verify official API contracts before finalizing the scope
or implementing dependent behavior.

Trace the current implementation before proposing a replacement. Read applicable
architecture, feature, and configuration guides, plus official vendor documentation
for the relevant version. Record the links, verified API behavior, availability or
account prerequisites, and unresolved assumptions. If official documentation is
unreachable or ambiguous, identify and resolve the gap before implementing dependent
behavior; continue independent repository research meanwhile. Model memory,
third-party examples, and an existing adapter do not replace the official contract.

Describe the user-visible outcome and the complete affected path:

| Concern | Questions to resolve |
| --- | --- |
| Runtime | Which callers, providers, pipelines, transports, and lifecycle owners change? |
| Operator experience | Can the user configure, validate, select, save, apply, and troubleshoot it through existing UI flows? |
| Backend | Are API validation, credential resolution, persistence, defaults, and restart planning consistent? |
| Compatibility | What happens to existing settings, missing fields, saved credentials, deleted resources, and upgrades/rollback? |
| Side effects | Are permissions, consent, idempotency, retries, and resource ownership preserved? |
| Verification | Which automated tests, browser actions, calls, and failure cases will establish acceptance? |

Ask with recommended choices when an architectural/product decision remains.
Small fixes need a short scope; cross-cutting work benefits from the
[milestone template](milestones/TEMPLATE.md). Keep research proportional to the task.

### Scope a New Provider or Tool

Start the scope with a short research record: official URLs and date checked,
API/SDK versions, supported deployment/authentication modes, account/plan or region
prerequisites, verified request/event/response contracts, limits, and open questions.
Explain how the integration fits existing AVA interfaces before choosing new ones.

| Extension | Read and trace | Required acceptance considerations |
| --- | --- | --- |
| Full-agent provider | [Provider development](provider-development.md), `src/providers/base.py`, config/registration and provider tool adapters | Session startup, audio formats, transcripts, model/voice options, tools, interruption, provider failure, disconnect/retry semantics, cancellation and cleanup |
| Modular STT/LLM/TTS provider | [Pipeline development](pipeline-development.md), `src/pipelines/base.py`, `src/pipelines/orchestrator.py`, role-specific adapters | Advertise only implemented roles; pipeline selection, streaming input/output, model/voice settings, rate/encoding contracts, bounded waits, cancellation and integration with existing components |
| Pre-call tool | [Tool development](tool-development.md), engine pre-call dispatch and returned-variable substitution | Correct inputs, ordering before provider startup, prompt/greeting mapping, missing/invalid outputs, timeout and explicit fail/continue policy; no secret values in logs |
| In-call tool | [Tool development](tool-development.md), registry, Agent scope and [provider schemas](schema-reference.md) | Enabled/disabled visibility, validated arguments, authorization, shared call ownership, normalized results across supported providers/pipelines, cancellation and duplicate-call protection |
| Post-call tool | [Tool development](tool-development.md), post-call dispatch, final call record and resource cleanup | Finalized data, success/failure/early-disconnect cases, work that does not require a live provider/channel, bounded retry policy, idempotent external effects, recorded results and privacy |

For every extension, trace config/schema defaults, backend validation/persistence,
managed credentials, frontend forms/selectors, Agent/global scope, wizard changes
where relevant, connection tests, restart/apply planning, observability, and docs.
Read the implementation before assuming one tool can run in every phase or every
provider supports the same schema. Preserve existing interfaces and operator data.

Define automated and live-call scenarios before coding, including missing credentials,
invalid/disabled configuration, provider/API errors, timeouts, duplicate execution,
interruption/hangup, and relevant regressions. Verify the UI-to-runtime path and
the external result where applicable. Clearly distinguish endpoint reachability
from actual STT, LLM, TTS, booking, or other functional validation.

## 3. Implement and Verify Locally

Create one focused branch from current upstream `main`; identify which remote is
the upstream project and which is the user's fork. Preserve unrelated changes,
using an isolated worktree when appropriate. When continuing a contributor's PR,
preserve their commits and credit and work within authorized branch access.

Run the relevant checks in the appropriate environment. These are common entry
points; use current [.github/workflows/ci.yml](../../.github/workflows/ci.yml) for
dependency setup, exclusions, coverage, and required checks:

| Area | Working directory | Commands |
| --- | --- | --- |
| Engine / tools / pipelines | Repository root, Python environment with project/dev requirements | `python -m pytest tests/<affected_test>.py -q` then relevant wider suites |
| Admin backend | `admin_ui/backend`, environment with backend/dev requirements | `PYTHONPATH=. python -m pytest tests -q` |
| Admin frontend | `admin_ui/frontend` | `npm ci`, `npm run lint`, `npm test`, `npm run build` |
| CLI | `cli` | `go test ./...`, `go build ./...` |
| Documentation / hygiene | Repository root | Check links/commands, `git diff --check`, relevant documentation and secret checks |

Use synthetic fixtures and deterministic failure cases where possible. Unit tests
do not need a running production engine. Select regression coverage from the actual
impact: a provider-specific fix differs from shared transport or session logic.
Do not force every provider/transport into every small change or count skipped
live tests as passing. Expand checks when new failures or changed scope justify it.

## 4. Deploy the Intended Revision

Prepare a concrete deployment plan: target, revision, affected services, reload
method, potential call interruption, backups, and rollback. Carry it out within
the user's authorization. Check active calls before disruptive work and agree on
timing when interruption is not already authorized.

1. Capture existing logs before recreating containers. Record the baseline SHA and
   relevant health/runtime state; preserve operator overrides and uncommitted work.
2. Back up affected config/secrets privately and use a SQLite-consistent backup
   method for affected databases. Preserve `data/`, including `operator/agents.db`,
   call history, OAuth state, models, and media. A code rollback alone may not undo
   a migration. Follow the current [migration](../MIGRATION.md) and
   [installation/recovery](../INSTALLATION.md) guidance.
3. Fetch and deploy the intended reviewed revision without a blanket `git reset
   --hard`, `git clean`, automatic stash, or destructive config restore. If the
   deployment is image-based, verify the image revision/digest as well as Git.
4. Inspect the actual Compose files and mounts. In the shipped source setup,
   engine Python is bind-mounted and needs a process restart/recreation; frontend
   assets are image-baked and require an Admin UI rebuild. Local AI source and
   dependency/Dockerfile changes require the affected image rebuild. `.env` changes
   need container recreation; a plain restart retains the old container environment.
5. Rebuild/recreate only affected services, using the same project and overlays as
   the running stack. Do not use a blanket `docker compose down` for routine updates.
6. Verify running code/image and effective configuration, service readiness, ARI,
   required transport listeners, and post-ready logs. A Git SHA or HTTP 200 alone
   does not prove the intended code is executing or calls work.

Example **after** identifying affected services and the deployment's Compose
configuration, on the deployment host in its repository directory:

```bash
# Example only: substitute the discovered Compose project/files and affected services.
docker compose -p asterisk-ai-voice-agent up -d --build --force-recreate admin_ui ai_engine
docker compose -p asterisk-ai-voice-agent ps
agent check
```

Do not include `ai_engine` for an Admin UI-only change unless its runtime is affected.
Do not start Local AI for a cloud-only deployment. Wait for model warmup when relevant.

## 5. Verify UI and Real Calls

For UI changes, hard-refresh after deployment (for example Cmd–Shift–R on macOS),
check the served build, and exercise the actual browser flow. Verify field help,
validation, errors/loading, credential masking, save/reopen persistence, stale
responses, and relevant empty/accessibility states. Check the backend and runtime
effect as well as the visible control. If browser access is unavailable, supply an
exact manual checklist and mark it pending until the user provides results.

Give a repeatable call script with expected outcomes. A useful baseline is greeting,
two-way conversation, interruption, the changed tool/feature, farewell, hangup, and
post-call cleanup. Add timeout, retry, caller disconnect, transfer failure, or
concurrency cases according to the change. Do not create real bookings/emails or
call external destinations outside the authorized test scope.

Record the deployed SHA, absolute call window/timezone, call ID, effective
provider/model/transport/settings, caller observations, and archived evidence. Use
the [debugging guide](debugging-guide.md). Verify no unexpected sessions/channels,
playback, provider connections, or tasks remain afterward. Restore temporary
operator settings; the user restores any logging/audio-capture settings they changed.
Required live validation can be handed to a willing tester as described in
[the quickstart](quickstart.md#no-pbx-yet).

## 6. Document, Review, and Hand Off

Update relevant docs and changelog before opening the coherent draft. Use the
[PR template](../../.github/pull_request_template.md) and
[current PR workflow](PULL_REQUEST_WORKFLOW.md) for CI and final review commands.
Triage current actionable feedback together; explain declined suggestions and
avoid unrelated changes or repeated review requests. Revalidate fixes at the new
head, including deployment/calls when their evidence is invalidated.

A final handoff states: problem/outcome, PR and head SHA, required checks, browser
and call evidence, unresolved/deferred items, current deployment state, and the
next authorized step. Report missing verification explicitly. Preserve contributor
credit. Merge readiness is distinct from permission to merge or release.
