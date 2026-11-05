# A2A Agent Tool

AVA can delegate one bounded text request during a call to a separately deployed
[Agent2Agent Protocol](https://a2a-protocol.org/) 1.0 service. AVA remains the
voice and telephony owner; the remote service owns its agent runtime and task
store. The integration uses the official A2A Python SDK and does not import a
provider implementation.

## Configure

Add an entry under `in_call_tools` in `config/ai-agent.yaml`:

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

Enable the configured tool for the intended Agent under **Agents -> Tools**.
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
- An initial timeout may have occurred after the service accepted the request.
  AVA reports that outcome as unknown and never automatically resends it.
- A timeout after a task ID is known performs one best-effort cancellation and
  reports the retained task ID for operator diagnostics.

The integration does not claim A2A streaming, push notifications, audio
transport, or proof that a human consumed a remote result.

## Service boundary

Any standards-compliant A2A 1.0 service with a JSON-RPC interface can be used.
For example, `pi0n00r/A2A` can run as an independent network service while AVA
stays under its existing license and deployment lifecycle.
