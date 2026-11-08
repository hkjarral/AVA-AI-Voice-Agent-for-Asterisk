# A2A Agent Tool

AVA can delegate one bounded text request during a call to a separately deployed
[Agent2Agent Protocol](https://a2a-protocol.org/) 1.0 service. AVA remains the
voice and telephony owner; the remote service owns its agent runtime and task
store. The integration uses the official A2A Python SDK and does not import a
provider implementation.

## Configure in the Admin UI

1. Open **Tools → In-Call → Add Tool** and select **A2A Agent**.
2. Choose a stable tool name, endpoint base URL and description explaining when
   the voice agent should delegate. Names cannot be changed after creation.
3. Select **None** or **Bearer token** authentication. Tokens are stored as
   private managed credential files; YAML and API responses contain only their
   references. A saved token stays masked; leave its field blank to retain it.
4. Set the timeout. **Advanced** exposes polling, request/response character
   limits and the caller-facing failure message. **Enabled** controls execution.
5. Click **Update tool**, then **Save & Apply**. Edits are staged in the Tools
   page until saved; credential uploads take effect immediately and are not
   undone by Cancel. Hot reload affects new calls; active calls retain their
   original settings and credential file.
6. Open **Agents → Edit Agent → Tools → In-call** and select the tool with the
   **A2A** badge. Save the Agent. Other Agents do not receive access automatically.

Remove the tool from every saved Agent before deleting it in Tools. The UI shows
which Agents use it, and the backend also enforces this restriction on YAML and
managed-tool deletes. If the Agent store cannot be read, deletion is blocked.

There is no network connection test in this editor. Saving validates settings;
Agent Card discovery and remote task execution happen only when invoked during
an allowed call. The remote service must expose a public Agent Card.

## Configure in YAML

Add an entry under `in_call_tools` in the local override
`config/ai-agent.local.yaml`:

```yaml
in_call_tools:
  delegate_to_agent:
    kind: a2a_agent
    agent_url: https://agents.example.com
    description: Delegate a caller's research request to the remote agent.
    bearer_token_env: AVA_A2A_BEARER_TOKEN
    timeout_ms: 15000
    poll_interval_ms: 250
    max_input_chars: 4000
    max_response_chars: 1000
    error_message: The remote agent could not complete that request.
```

Set the named environment variable in the Engine environment. Never place a
Bearer token or credentials in YAML or in the URL. Bearer authentication
requires HTTPS, except for a service reached through loopback.

Apply the YAML change to the Engine before assigning the tool. If you change
Compose's `.env` file, recreate the Engine container so it loads the new
environment. Enable the tool for the intended Agent under **Agents → Tools →
In-call**. Invalid entries are skipped with an Engine warning on startup.
Admin saves reject invalid A2A settings, and a failed Engine reload keeps the
running tool configuration.
Existing YAML settings and unknown extension fields survive UI edits. Existing
`bearer_token_env` references remain supported; new references saved through Admin
must use the dedicated `AVA_A2A_` or `AAVA_A2A_` prefix. New UI credentials use
`bearer_token_file` pointing to an opaque
`/app/project/secrets/providers/a2a-tool-<id>/api-key` file mounted into both
Admin and Engine. Keep `secrets/providers` in private deployment backups and
restore it alongside configuration. Do not copy tokens into exported YAML.
Credential replacements create a new immutable file so active calls are safe;
old files are retained for existing snapshots and rollback.

Use `enabled: false` to stop execution. A2A is always in-call and requires
per-agent opt-in; `is_global: true` is rejected by Admin.

## Clean up retained credentials

Credential uploads can leave unused files after a cancelled or invalid edit,
replacement, or deletion. Cleanup is manual because file age does not establish
whether a running call, browser draft or retained rollback still needs a token.

1. Finish or discard all Tools edits and close the editors. Export the current
   **merged** configuration from Raw YAML, including local overrides, and keep
   its credential references alongside the rollback/configuration backups you
   intend to retain. Keep those backups private.
2. During a maintenance window, prevent new calls, let active calls finish, then
   stop the Engine and Admin UI. This releases active-call snapshots and prevents
   new credential uploads or configuration writes during cleanup.
3. From the deployment checkout, list candidate directories without reading
   their token contents:

   ```bash
   find secrets/providers -mindepth 1 -maxdepth 1 -type d -name 'a2a-tool-*' -print
   ```

4. For each candidate, confirm it is an Admin-generated `a2a-tool-<32 hex digits>`
   directory. Keep it if **any** credential reference in the exported merged
   configuration or a retained rollback/configuration backup points into it.
   Keep candidates whose ownership or references you cannot establish. Remove
   only individually reviewed, unreferenced directories; do not bulk-delete by
   prefix or age. Never remove another provider's credential directory.
5. Restart Admin and Engine, verify Engine health and saved tool settings, then
   allow calls again. A rollback whose credential file was removed requires the
   credential to be entered again before its tool can authenticate.

## Runtime contract

- AVA discovers the public Agent Card and requires a JSON-RPC 1.0 interface on
  the configured origin. A card cannot redirect authenticated task traffic to a
  different origin.
- AVA sends one `text/plain` message with an opaque context identifier and polls
  the resulting task within the configured timeout.
- A completed task must contain a non-empty text artifact within
  `max_response_chars`. Oversized or non-text results fail closed.
- If AVA has a task ID when the call or tool is cancelled, it requests
  `CancelTask` before closing the client.
- The service may accept a request before a timeout or connection failure loses
  its reply. AVA reports this outcome as uncertain and never resends the request
  automatically.
- A timeout or connection failure during polling triggers one best-effort
  cancellation and retains the task ID for diagnostics.

The integration does not claim A2A streaming, push notifications, audio
transport, or proof that a human consumed a remote result.

## Service boundary

The service must expose a public Agent Card and a same-origin A2A 1.0 JSON-RPC endpoint.
For example, `pi0n00r/A2A` can run as an independent network service while AVA
stays under its existing license and deployment lifecycle.
