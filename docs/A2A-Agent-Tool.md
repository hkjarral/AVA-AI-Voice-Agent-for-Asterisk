# A2A Agent Tool

AVA can delegate one bounded text request during a call to a separately deployed
[Agent2Agent Protocol](https://a2a-protocol.org/) 1.0 service. AVA remains the
voice and telephony owner; the remote service owns its agent runtime and task
store. The integration uses the official A2A Python SDK and does not import a
provider implementation.

## Configure

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
The first release is YAML-configured; it does not add an Admin UI editor for A2A
endpoints.

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
