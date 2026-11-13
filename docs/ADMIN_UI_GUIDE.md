# Admin UI Guide

The Admin UI is a web-based interface for configuring, monitoring, and managing your Asterisk AI Voice Agent deployment.

## Quick Start

For v7.7.2, read [migration](MIGRATION.md#v771-to-v772),
[Agents workspace](AGENTS_EXPERIENCE_ROLLOUT.md), and [integration backups](INTEGRATION_BACKUPS.md).
Hard-refresh after rebuilding Admin UI. Agents now use searchable cards/list,
a routed four-section editor and changed-field saves. Visual avatars do not
change voice or routing; tool counts describe configuration, not health.

Tools opens focused editors. **Update tool** stages HTTP/native edits; **Save & Apply**
saves/publishes new-call settings. Follow the explicit setup, consent and recovery
steps for [Calendly](Calendly-calendar-tool.md), [Pushover](Pushover.md),
[summaries](In-Call-Summaries.md), [Teams](Microsoft-calendar-tool.md), and
[optional A2A delegation](A2A-Agent-Tool.md).
Preview/verification is not a booking/send; explicitly approved setup tests can
create real external effects.

For a fresh install, follow the [v7.7.2 installer](INSTALLATION.md#path-a-admin-ui-setup-wizard-recommended)
and [first-call walkthrough](FIRST_CALL.md). The installer prepares and starts
Admin UI at port 3003. Engine startup and PBX routing follow in the wizard.

**First login**: a one-time password is printed to the admin_ui logs on first start (`docker compose -p asterisk-ai-voice-agent logs admin_ui | grep -i password`); you must change it at first login. `admin`/`admin` no longer works.

## Pages Overview

### Dashboard

The dashboard shows a live system topology with:
- Container status (ai_engine, local_ai_server, admin_ui)
- Asterisk ARI connection status (green/red pill)
- Active provider and pipeline information
- CPU, memory, and disk usage

Clickable cards navigate directly to the relevant settings pages.

### Setup Wizard

The wizard walks you through initial configuration:
1. **Provider selection** — Choose your AI provider (OpenAI, Deepgram, Google, ElevenLabs, Local Hybrid)
2. **API key entry** — Enter and validate your provider credentials
3. **Transport selection** — AudioSocket (default) or ExternalMedia RTP. The opt-in [WebSocket transport](WebSocket-Transport.md) is configured after setup under **Advanced → Audio Transport**.
4. **Test** — Verify the configuration produces a healthy engine

The wizard writes to `config/ai-agent.local.yaml` (operator overrides), so upstream updates to the base config never conflict.

### Configuration

#### Agents
Create and edit operator-managed agents, select their provider or pipeline, configure prompts/tools/voice, and copy a ready-to-use dialplan snippet. Resource access for transfers, Google Calendar, Microsoft Calendar, and voicemail can inherit the global inventory, allow selected resource keys, or deny that family. Under **Caller Inactivity Overrides**, an agent can partially override the global watchdog timing/messages. Inbound agents inherit the global policy; outbound agents must explicitly enable it here.

#### Providers

The optional [local transcription API](OpenAI-Compatible-Transcription.md) is
configured through Admin's environment, not a new browser screen. It uses
separate Basic/Bearer speech credentials and the selected Local AI STT backend.
Configure full-agent providers and modular STT/LLM/TTS components. Each provider
card shows its current status.

For a new modular provider, changing STT/LLM/TTS updates the subtype controls to
match that capability. A compatible subtype is inferred; otherwise choose one
before configuring its fields. Existing draft settings are preserved across
switches, and subtype settings are not duplicated in Additional Configuration
Fields. Saved modular providers keep their capability locked.

For Google Gemini modular LLM providers, the new default is
`models/gemini-3.8-flash`; existing saved selections are preserved. This does not
change native Google Live's model default. Temperature is greyed out and disabled
for Gemini 3.6 and newer, Gemini 3.5 Flash-Lite, and the Gemini 3.5 Flash alias
(which routes to 3.6). Persistent helper text explains that the model manages
temperature automatically. The saved value is retained and becomes editable again
when you select a model that supports it.

Google modular LLM requests omit `candidate_count` for Gemini 3 and later;
supported older models retain it. Isolated Gemini 3+ summaries reserve at least
4096 output tokens for thinking and answer text, and use low thinking on known
compatible text models (including the default Gemini 3.8 Flash). The requested
word limit remains in the summary prompt. The adapter selects a complete `STOP`
candidate, excluding thought text, even when another candidate hits `MAX_TOKENS`.
If no complete answer remains, summaries fail without partial text. Live calls
instead speak "I'm sorry, I couldn't complete that response. Could you ask again?"
through the normal TTS path, subject to call cleanup and transfer ownership gates.
No automatic retry is added. Call-generation token limits and older-model summary
settings are unchanged.

Model contracts: [sampling](https://ai.google.dev/gemini-api/docs/generate-content/whats-new-gemini-3.6),
[aliases](https://ai.google.dev/gemini-api/docs/deprecations), and
[thinking/output limits](https://ai.google.dev/gemini-api/docs/generate-content/thinking).

For a modular HTTP provider, edit its endpoint and credential source, then click
**Test Connection** to test the current form before saving. Custom hosts, ports
and API paths are preserved. Results show the tested destination origin and a
readable success or failure reason; changing settings clears the old result.
Tests on separate provider cards keep their own loading states and results.
Closing or reopening an editor discards responses from its previous session.
The credential verification action tests the saved provider configuration.

A successful LLM model list does not prove that the selected model can perform
inference. Telnyx connection testing additionally exercises a minimal chat
request. Speech endpoint success establishes reachability only and explicitly
does not verify authentication or transcription/synthesis. Save the intended
settings and follow any apply/restart prompt before validating call behavior.
See [modular HTTP provider tests](Configuration-Reference.md#admin-ui-modular-http-provider-tests)
for endpoint selection, scoped credentials, no-auth servers and test limits.

#### Pipelines
Configure modular STT/LLM/TTS pipelines for mix-and-match provider combinations.

Pipeline and Audio Profile saves/deletes show a spinner while pending and disable
other controls on that page to prevent overlapping configuration writes. The editor
cannot be changed or dismissed until the request finishes. A failed save keeps the
draft open for correction or retry. Call History deletion shows the same pending
feedback in both the table and call details; confirmation and error notifications
remain in place. Saving configuration still requires the displayed apply/restart
action before it affects runtime behavior.

#### Audio Profiles
Transport, codec, and output-downsampling settings selected globally, by Agent, or with `AI_AUDIO_PROFILE`. Use `telephony_ulaw_8k` for the compatibility baseline or opt an Agent into `telephony_enhanced_8k` for alias-safe 16/24 kHz-to-8 kHz downsampling. On a genuinely wideband extension or SIP trunk that negotiates G.722 (or another wideband codec), `wideband_pcm_16k` requests provider-native PCM, converts it once at the 16 kHz boundary, and uses per-call `slin16` AudioSocket framing. It requires Asterisk 20.17+, 21.12+, 22.7+, or 23.1+. The engine rejects that profile on older or unknown versions instead of risking bad audio. ExternalMedia RTP supports the two 8 kHz telephony profiles; its Transport page displays this compatibility boundary. Keep G.711/PSTN Agents on an 8 kHz profile. Rollback is simply assigning the Agent back to `telephony_ulaw_8k`.

#### Tools
Enable/disable AI-powered actions (transfers, hangup, email, voicemail) and configure the global inventory. **Save & Apply** publishes built-in and managed HTTP tool changes to new calls without restarting the engine; active calls retain their captured generation.

Configure inventory here, then open an Agent to narrow which transfer destination,
Google calendar, Microsoft account/calendar, or voicemail mailbox it can use. A global
disable always wins. If Save & Apply validation fails, the last good tool generation
continues serving calls and the unsaved configuration is not published.

If a Save or Apply response is lost, its outcome is unknown. A confirmed save
triggers a read-only status check; **Refresh status** retries that check without
saving, reloading, or restarting anything. Unreachable, invalid, or inconsistent
engine state stays unknown and blocks another Save/Apply. Verified status reports
whether the current saved configuration is active, needs Apply, or needs a
restart. It does not discard unsaved edits or identify a particular tool generation.

Email-template previews use synthetic data and do not send email. Closing or
switching the template editor discards its preview requests; editing a draft or
changing transcript options clears stale output. Each opening starts from the
current template, while background default refreshes preserve customized edits.
Saving locks editing and dismissal until persistence finishes; a failed save keeps
the draft available for retry. Use **Save & Apply** to publish tool settings.

Under **In-Call → Transfer & live agents → Destinations**, **Discover endpoints**
lists candidates using the existing ARI connection. Select candidates, confirm
their dialable number, description and dialplan context, then add them to your
edits and use **Save & Apply**. Discovery itself never changes configuration or
calls. See [assisted transfer destination setup](ARI-Endpoint-Discovery.md) for
permissions, failure behavior, and the distinction between endpoints and a PBX
directory.

#### Advanced Settings → Voice Activity Detection
The **Caller Inactivity** card controls the engine-level no-input watchdog. v7.3.1 defaults to a 30-second idle window, one “Are you still there?” check-in, and a 15-second reply grace period before the configured final message and hangup. The clock pauses while the agent is greeting, speaking, processing, or transferring. Changes are hot-reloaded for new calls; active calls keep the policy captured when they started.

### Call History

Per-call debugging and analytics:
- Searchable list of all calls with timestamps, duration, and provider
- Full conversation transcripts
- Tool call history with parameters and results
- Opt-in call metadata with pre-call/updated-during-call provenance badges
- Exact call metadata field/value filtering and CSV/JSON export
- Multi-outcome filter with **Only** / **Hide** modes (for example, hide abandoned calls), plus tool usage and duration range filters
- Summary statistics, the list and CSV/JSON exports all follow the same active filters; the panel reads **Call Statistics (filtered)** while any filter is set
- Call quality metrics
- A distinct **No input timeout** outcome for calls ended by the inactivity policy

Use Call History as the primary debugging tool — it provides more context than raw logs.

### Live Logs

WebSocket-based real-time log streaming from `ai_engine`. Filter by log level or search for specific call IDs.

### YAML Editor

Monaco-based editor with syntax highlighting and validation for direct editing of `config/ai-agent.yaml` and `config/ai-agent.local.yaml`.

### Environment Variables

Visual editor for `.env` variables. Changes require a container restart to take effect.

Save, Refresh, connection-test actions and Apply confirmations are serialized on
this page. An accepted request can finish after navigation, but obsolete responses
cannot change another view, and later restart steps are not started after leaving.
Save and Apply recheck cumulative backend drift before reporting pending changes.
If verification fails, a Save response is lost, Apply stops with a warning, or an
Admin UI restart is only scheduled, use **Refresh** after the service returns
to verify what is still pending before requesting another Apply. Existing active-call
guards and confirmation requirements still apply.

### System

#### Asterisk
Live ARI connection status, required Asterisk module checklist, configuration audit with guided fix commands. Supports both local and remote Asterisk deployments.

#### Containers
Start, stop, restart, and rebuild Docker containers directly from the UI.

#### Models
Browse, download, and manage local STT, TTS, and LLM models. Tabs split the catalog by type (`STT`, `TTS`, `LLM`) plus an `Installed` view that lists what's already on disk. The catalog itself lives in `admin_ui/backend/api/models_catalog.py` and ships with ~250 entries across the three types; new entries land via PRs and CI verifies every download URL is reachable on each PR plus weekly on `main` (see `scripts/check_catalog_urls.py`).

The top of the page exposes the **Local AI Server** card with three pickers — STT, LLM, TTS — that show the currently-loaded model and let you switch the engine to any installed file. Switching applies the change to `.env` and reloads the engine in place; the running engine recovers from the swap without dropping calls already in progress.

Downloaded models live under `models/<type>/` on the host (mounted into the container at `/app/models/<type>/`). The Installed tab counts files matching the catalog; deleting via the trash icon removes the file plus any sidecars (`.json` config for Piper, `.sha256` integrity sidecar).

##### Community Models (best-effort)
Operators can add LLM, TTS, or STT models that aren't in the curated catalog by pasting a HuggingFace download URL. Useful for trying new GGUF releases (Qwen3-4B, SmolLM2 variants, etc.) before they land in the official catalog.

**Off by default.** Toggle on via the "Community Models" card at the bottom of the Models page; the choice persists to `.env` as `ENABLE_CUSTOM_MODELS=true`. When disabled, the panel collapses to a one-line note and no community entries appear in the main lists.

When enabled:
1. Click **+ Add custom model**. The form requires a Type (LLM/TTS/STT), Display name, and HTTPS Download URL; optional fields are Config URL (for Piper TTS), Chat format hint (for LLM, e.g. `chatml` / `llama-3` / `mistral-instruct`), Expected SHA256 (verified after download), and free-form Notes.
2. The entry appears immediately in the relevant tab (STT/TTS/LLM) with a yellow **Community** badge alongside curated entries.
3. Click **Download** on the entry as you would a curated model. Progress polls in the existing download UI.
4. After download, expand the entry's chevron in the Community Models panel to **Inspect GGUF header** (LLM only): architecture, parameter count, quantization, context length, layer count, file size, and an estimated RAM requirement are surfaced. Architectures not in the verified-supported list (`llama`, `qwen2`, `qwen3`, `phi3`, `phi4`, `gemma`, `gemma2`, `mistral`, `mixtral`, `tinyllama`, etc.) get a yellow warning rather than blocking the download.
5. Switch the engine to the new model via the LLM picker at the top of the page (a page reload may be needed for the picker to see the new file).
6. The trash icon removes both the JSON entry and the on-disk file (plus all sidecars).

The "best-effort" framing is genuine: catalog URL liveness, GGUF magic verification, file size and RAM estimation are checked, but the model isn't actually loaded as part of validation. If a community model fails to load or behaves badly at runtime, please open a GitHub issue.

**Storage**:
- Entries: `data/custom_models.json` (gitignored, persists across container rebuilds and upgrades)
- Files: `models/<type>/custom_<type>_<slug>__<basename>` — the `custom_…__` prefix is namespaced with the unique entry id so two community entries with the same upstream filename can never collide on disk.

**Security**:
- HTTPS-only download URLs (rejects `http://` to prevent SSRF via redirect)
- Path-traversal blocked on every endpoint that touches a model file
- The `/delete-file` endpoint refuses to act on community-model files (community models must go through `DELETE /api/custom-models/{id}` so JSON and disk stay in sync)

## Per-Instance Provider Credentials (v6.5.2)

Full-agent provider forms (Grok, OpenAI Realtime, Deepgram, Google Live, ElevenLabs Agent) include a uniform **Provider Credentials** card. Paste the API key (or upload service-account JSON for Google Vertex) directly into the card; the Admin UI writes it to a per-instance file under `/app/project/secrets/providers/<provider_key>/` on the server (chmod 0600). The provider's `api_key_file` / `agent_id_file` / `credentials_path` field in YAML is updated automatically — no manual editing required, no secrets stored in `.env` or YAML.

This is the canonical credential storage path for multi-instance deployments (e.g. `acme_grok` and `globex_grok` each with isolated keys). Legacy single-instance configs that set `XAI_API_KEY` / `OPENAI_API_KEY` / etc. in `.env` continue to work as a fallback.

The **System → Environment** page includes a "Per-Instance Provider Credentials" status section listing every configured provider's effective credential source. It distinguishes a usable managed file, configured file, resolved environment variable, inline value, legacy shared Google Vertex file, and a missing/unresolved reference. A `${VARIABLE}` placeholder is not reported as configured unless that variable currently resolves. Legacy shared Vertex credentials remain in place as a compatibility fallback; the UI does not copy or delete them when reporting status, and a new upload creates an explicit per-instance override.

## System Topology (v6.5.2)

The dashboard System Topology card uses tri-state health indicators (`Checking…` / healthy / error) with a 2-strike debounce: a single failed probe does not immediately flip a dot red. This eliminates the false-positive red flashes that previously appeared during engine warmup or transient localhost probe timeouts.

- ARI, AI Engine, and Local AI Server each get the tri-state + debounce treatment
- Per-provider readiness uses the same 2-strike pattern so providers start at `Checking…` instead of red on first paint
- Backend probe timeouts raised (`ai_engine` `/health` connect 1.5s → 5s; `local_ai_server` WebSocket open_timeout 2.5s → 5s)
- Provider cards are grouped by provider type with multi-instance sub-rows, so two `*_grok` or `*_google_live` instances render as the same provider kind with separate readiness dots
- Asterisk + AI Engine cards stretch to match the Providers column height; Models live AI Server output in a 3-column responsive grid

## Help tooltips (v6.5.2)

~260 inline help tooltips were backfilled across the admin UI in v6.5.2 — provider forms (Grok 17, OpenAI Realtime 24, Deepgram 22, ElevenLabs 10, Local 30, Google Live 29, Azure 21, OpenAI 17, Telnyx 7, Ollama 6), Setup Wizard (26 fields), LLM/MCP/Profiles/Models pages. The `HelpTooltip` component is viewport-aware: it measures the trigger and flips the popover from above to below when the icon is near the top of a scrolled modal, so the content stays visible.

## Security

The Admin UI has Docker socket access for container management. Treat it as a control plane with elevated privileges.

**Production requirements**:
- `admin`/`admin` is removed: retrieve the one-time password from the admin_ui logs (`docker compose -p asterisk-ai-voice-agent logs admin_ui | grep -i password`) and set a new one at first login
- Set `JWT_SECRET` in `.env` (preflight generates this automatically)
- Restrict port 3003 via firewall, VPN, or reverse proxy
- Never expose directly to the internet without authentication

See [SECURITY.md](../SECURITY.md) section 2.1 for detailed Admin UI security guidance including nginx reverse proxy configuration.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Can't access UI | Verify container is running: `docker compose ps admin_ui` |
| Login fails | Check `JWT_SECRET` is set in `.env`. Reset by deleting `data/users.json` and restarting. |
| Config changes not taking effect | Click "Restart" on the affected container, or run `docker compose restart ai_engine` |
| Dashboard shows disconnected | Check that `ai_engine` container is running and ARI credentials in `.env` are correct |
| Stale data after update | Hard refresh (Ctrl+Shift+R) to clear cached frontend assets |

### Call recording playback and download

In **Call History → Call Details**, use **Play recording** or **Download recording**.
Both require the existing Admin UI login and return the same browser-playable WAV
representation. ULAW is converted to PCM WAV; GSM and compressed WAV require SoX in
the Admin UI image and are decoded to PCM16 with seekable WAV headers. Download does not return the original compressed source file.
The player shows duration after loading audio and supports keyboard seeking.
**Refresh recording** retries discovery when a recording arrives late. Missing,
empty, ambiguous, unsafe-path, and request-error states are shown separately.
Closing or switching call details cancels pending recording requests and stops audio.

Recordings remain owned by Asterisk under the configured `ASTERISK_RECORDING_PATH`
(read-only in Admin UI). Discovery supports the flat and `YYYY/MM/DD` layouts and
requires an exact call-ID token. The date alone never establishes an association.
Files with the **same directory and filename stem** are considered format variants,
with WAV preferred over ULAW, then GSM. Different stems/directories for the same ID,
or duplicate case variants of an extension, are ambiguous: access returns 409
until the operator resolves the files. Symlinks in the recording path are rejected,
including links to other files within the mount. API metadata does not expose host
filesystem paths (`file_path` is retained as null for older clients).

`GET /api/calls/{record_id}/recording/download` returns an attachment; existing
`/recording/audio` and `/recording.wav` playback routes remain available. All use a
private request snapshot, support HTTP byte ranges on the WAV representation, and
set `Cache-Control: private, no-store`. Source files larger than 256 MiB are rejected
with 413; files changing during the snapshot return 409 and can be retried after
recording finishes. A missing/deleted call record cannot be used to retrieve audio.
Deleting history does not delete Asterisk's recording files. In-flight snapshots
may finish after the original file or history row is deleted, and are removed when
the response completes or disconnects. Restart discards in-flight requests; fresh
requests rediscover files. No database migration is required; rollback is code-only.

This is the smaller scope of issue #767. A durable catalog, checksums, background
reconciliation and new recording-specific permission models remain deferred pending
evidence and design agreement. There is no change to active calls or recording policy.

## Call Scheduling lead lists

Selecting a campaign, changing the lead search/state filter, or changing pages
loads a new lead list. Previous rows are hidden until that view loads, and a
failed request offers **Retry campaign details**. Background refreshes retain the
current list while loading; obsolete responses cannot replace the selected view.

Pending-lead guidance updates with the latest campaign statistics. Counts are
shown as unknown while their first request is loading. The readiness badge stays
unknown after a failed detail read and shows **Needs attention** when known
prerequisites are missing. A campaign-list refresh failure does not stop fresh
lead/statistic reads for a campaign still present in the current list. Changing
**Show archived** resets pagination and clears the selection and lead actions until the new list loads,
including when that list request fails. A lead confirmation that
outlives a change of view does not apply to the old lead. Existing campaign and
lead state rules continue to control which actions are allowed.


## Update previews and recovery

Changing the update target or preview options hides the previous preview and
keeps **Proceed** disabled until the new preview succeeds. A failed preview can
be retried with **Refresh preview**. Changing options or refreshing a preview
invalidates an earlier confirmation; review and confirm the current preview.
Update and rollback actions stay locked while confirmation or submission is pending.
A submitted update failure identifies its original target even if options changed
while the request was pending.

Returning to Updates resumes a saved job ID already present when the page opens.
Actions remain locked while that job's state is unknown or Admin UI is reconnecting.
A temporarily missing job is retried; a repeatedly missing/pruned job is cleared
with an explicit message. Job output, including **Load full log**, belongs to the
current job.

While the submitting page remains open, both updates and rollbacks check
Recent Runs after an uncertain submission outcome (a lost response, server error
or conflict). If the backend reports exactly one active job, the page follows it
without repeating the action; this can also be a job started by another operator.
If no active job can be confirmed, the submission error remains visible.
Definitive rejections remain visible without reconciliation. Conflict rejections
also stay visible while following the other active job; successful status polls
do not erase the refusal. A failed browser status-cache write does not invalidate
a successful **Check updates** result.

A successful reply saves the accepted job ID when browser storage is available,
including after navigating away from Updates within the app. A page reopened
before that late reply does not automatically adopt the storage change: reopen
Updates after the reply, or refresh Recent Runs and use **Follow job**.
If the submitting page is still open when storage fails, it follows the job and
displays its ID and a storage warning. If that page has already closed, automatic
recovery is not guaranteed. After returning or reloading, check Recent Runs and
**Follow job** before retrying an update or rollback. Backend locking and active-call safeguards remain
in effect; rollback retains its separate active-call safety choice.
