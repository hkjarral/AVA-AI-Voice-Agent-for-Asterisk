# Cloud provider startup connection recovery (#676)

A temporary network failure during the initial WebSocket handshake can otherwise end an answered call. Cloud full-agent providers now support opt-in, bounded connection retries. Provider-specific authentication and socket options stay with each adapter; a shared utility counts attempts and bounds backoff. Session setup, greetings, tools, and established-session reconnection are never replayed by this utility.

## Upgrade compatibility and configuration

| Field | Default | Valid values | Meaning |
|---|---|---|---|
| `connect_timeout_sec` | `10` | finite number, greater than 0, at most 60 | Socket-opening timeout per attempt |
| `connect_max_retries` | `0` | integer 0–3 | Extra initial connection attempts; 1 means 2 total attempts |
| `connect_total_timeout_sec` | absent / `null` | finite number, greater than 0, at most 180 | Optional connection-phase deadline, including backoff and pre-connect authentication |

Supported: Google Live (Developer API and Vertex), OpenAI Realtime, Grok, Deepgram Voice Agent, and ElevenLabs Agent. Named provider instances use the same fields. Existing provider-template inheritance continues where supported by the adapter.

Missing fields preserve the single initial attempt and 10-second opening timeout. No configuration migration enables retries. The shipped YAML contains only explanatory comments; the wizard does not enable recovery. The UI displays defaults without inserting fields when opened or when unrelated values change, and explicit zero retries survive save/reload. No database migration is required. Local and modular STT/LLM/TTS behavior is unchanged.

In **Providers → Edit → Startup connection recovery (Expert)**, enable one retry on the provider being tested. Retain the 10-second opening timeout initially; reduce it only after measuring handshakes on your installation. Optionally set a total budget, for example 25 seconds. Save and follow the apply plan to restart AI Engine; provider connection settings require restart.

```yaml
providers:
  google_live:
    # Keep the rest of the existing provider configuration.
    connect_timeout_sec: 10
    connect_max_retries: 1
    connect_total_timeout_sec: 25
```

Transient opening timeouts, temporary DNS/network errors, and HTTP 500/502/503/504 responses can be retried. Invalid credentials, permanent DNS/configuration errors, TLS certificate failures, HTTP 429, and other permanent handshake rejections fail immediately. Retry delays are short and bounded. A fresh attempt does not guarantee a different upstream IP.

ElevenLabs obtains a new signed URL for each attempt. Its existing HTTP authentication timeout remains 10 seconds; a configured total deadline also bounds that request. Google Vertex token acquisition consumes the configured connection budget but is not itself retried; its existing credential/fallback policy remains. Cancelling Vertex acquisition cannot forcibly stop an already running credential worker thread, although the configured HTTP request timeout limits it.

The total budget excludes session setup after a successful opening handshake, failure-prompt playback, and teardown. Existing setup deadlines and terminal failure actions remain in force. More attempts can increase the wait before a failure announcement, redirect, or external-platform finalization. Keepalive protects established sockets and is a separate setting.

Rollback: set `connect_max_retries: 0`, restore `connect_timeout_sec: 10`, clear `connect_total_timeout_sec`, save, and restart AI Engine. This preserves all existing terminal failure handling.

Verify the UI reset before call testing: set a maximum wait of 25 seconds, save and reopen the provider, then clear that field, make an unrelated edit, save and reopen again. The field must stay blank and `connect_total_timeout_sec` must be absent from the saved YAML. Clearing a saved per-attempt timeout or retry count likewise removes the YAML field and displays its legacy default (10 seconds or zero retries). Restore any unrelated edits before placing calls. Clearing the optional deadline changes connection timing after the engine restart; no extra attempts are enabled unless retries are configured.

## Real-call qualification matrix

Use the deployed branch's actual supported transport and existing test Agents/dialplan routes. Select each provider via its Agent or `AI_PROVIDER` override. Test Google Developer API and Vertex separately if both are available. Local is a compatibility control. The Providers **Test Connection** button is useful for credential checks; it does not qualify a real call's media, greeting, tools, or cleanup.

| Provider | Fault-proxy target hostname | Additional evidence |
|---|---|---|
| Google Developer API | `generativelanguage.googleapis.com` | One setupComplete and one greeting |
| Google Vertex | Actual configured regional/global `aiplatform.googleapis.com` hostname | Vertex authentication/mode retained; one setupComplete |
| OpenAI Realtime | `api.openai.com` | session.created, configuration, one greeting/response request |
| Grok | `api.x.ai` | session.created and one initial greeting |
| Deepgram Voice Agent | `agent.deepgram.com` | One Settings/configuration cycle |
| ElevenLabs Agent | `api.elevenlabs.io` | Signed-URL acquisition per attempt; one conversation/session-start event |
| Local | No injected cloud fault | Existing connection/reconnect behavior retained |

For each provider, record call ID, provider instance, configuration, test start time, and heard behavior. Repeat these cases:

1. **Upgrade baseline:** fields absent, then explicit zero retries. Place a normal call: one connection attempt, normal greeting and two-way speech, barge-in, one permitted read-only tool, and hangup. No unexpected configuration values should appear after an unrelated UI save.
2. **Healthy opt-in:** enable one retry and keep timeout 10 seconds. A healthy call still uses attempt 1; greeting and media should match baseline.
3. **First-attempt recovery:** inject one 503 opening failure with the proxy below. Expect `attempt=1 retrying=true`, then `attempt=2` established, one provider setup and greeting, normal conversation, and no terminal failure action.
4. **Opening-timeout recovery:** inject a delay longer than the configured per-attempt timeout, with a total budget sufficient for retry. Expect timeout then success. A 4-second test timeout is appropriate for this supervised fault test; it is not the shipped default.
5. **Exhaustion:** inject more failures than allowed attempts. Expect precisely retries+1 attempts and exactly one configured terminal failure action, with no provider greeting. Verify Call History records a startup error. Exercise `announce_hangup` and any configured redirect separately; VICIdial calls must use platform finalization rather than direct customer-leg hangup.
6. **Deadline:** use a total budget shorter than the injected stall. Connection work must stop at that budget (allow scheduling tolerance); failure playback/cleanup occur afterward and are excluded from this measurement.
7. **Caller cancellation:** hang up during the injected stall or retry backoff. Expect no further attempts, no resurrected session or late greeting, and no remaining call/channel/task state. Repeat with two concurrent calls to check isolation.
8. **Permanent failure:** use a disposable named test provider with deliberately invalid credentials. Expect one attempt even with retries configured. Never edit the production credential file. Restore/remove the test instance afterward.

Run at least two normal and two recovery calls per configured cloud provider. Record unavailable providers as untested; a mock or a healthy call alone is not a live recovery qualification.

## Controlled faults without blocking provider IPs

`scripts/cloud_connection_fault_proxy.py` is a supervised test tool, bound only to loopback. It handles HTTP CONNECT tunnels and faults the selected hostname. TLS remains end-to-end, so API keys, audio and WebSocket payloads are neither inspected nor logged. Other allowed cloud hosts pass through. This tool is not activated by deployment and adds no runtime fault-injection feature.

Use it only during a dedicated test window with no unrelated calls. First record the current Compose launch configuration and image IDs so you can restore them. For voiprnd's host-network AI Engine, create a temporary Compose override **outside the repository**, preserving all existing override files:

```yaml
# /tmp/aava-676-proxy.yaml
services:
  ai_engine:
    environment:
      wss_proxy: http://127.0.0.1:18766
      no_proxy: localhost,127.0.0.1,::1
```

Preserve any existing `no_proxy` exclusions and add the exact ARI/PBX and Local AI hostnames or IP addresses for your installation. The example covers loopback endpoints only. An engine-wide `wss_proxy` also applies to a TLS ARI WebSocket; without a bypass the cloud-only proxy rejects it and ARI cannot reconnect. Before placing calls, confirm ARI and Local AI select direct connections, the selected cloud hostname selects the proxy, and engine health reports ARI connected with no active calls.

Confirm the container has Python websockets 15+ proxy support and the cloud hostname is not bypassed by `no_proxy`. The proxy address above requires host networking; bridge-network installations need a reachable host address and a separately secured listener.

Start the proxy in a separate SSH terminal and independent container, targeting the exact provider host. This uses the bundled Python 3.11 runtime; voiprnd’s host Python 3.6 is too old for the tool:

```bash
cd /root/Asterisk-AI-Voice-Agent
docker run --rm --network host --name aava-676-fault-proxy \
  -v "$PWD/scripts/cloud_connection_fault_proxy.py:/tmp/fault_proxy.py:ro" \
  asterisk-ai-voice-agent-ai-engine:latest \
  python /tmp/fault_proxy.py --host api.openai.com --fail-count 1 --mode reject
```

Then recreate only AI Engine with its normal Compose files plus the temporary override. On voiprnd, if its normal files are `docker-compose.yml` and `docker-compose.override.yml`:

```bash
docker compose -p asterisk-ai-voice-agent \
  -f docker-compose.yml -f docker-compose.override.yml \
  -f /tmp/aava-676-proxy.yaml up -d --no-build --force-recreate ai_engine
```

Restart the proxy between cases to reset its counter. For a timeout test, use `--mode timeout --delay-sec 15` with a shorter per-attempt deadline; for exhaustion, use `--fail-count 4` with one retry. Do not infer recovery from the proxy counter alone: correlate engine attempts, setup, media, and teardown by call ID. Simultaneous calls share the proxy's hostname counter, so use sequential calls for deterministic failure/success cases.

After testing, recreate AI Engine with its normal Compose files **without** the temporary override, then stop the proxy and delete the temporary file. Confirm `wss_proxy` is absent from the recreated container, restore provider defaults, and make one normal call. Do not stop the proxy while an engine still depends on it.

## Evidence and pass criteria

Archive raw logs before analysis using the AAVA call-log collector. Capture AI Engine, Admin UI, Local AI Server, Asterisk logs, runtime health/configuration, Git SHA, and proxy output. Record the provider connection events, session-ready event, first caller-facing audio, greeting count, tool result, hangup, and final channel/session count.

A pass requires both audible two-way interaction and objective lifecycle evidence. Healthy retries must not record a terminal provider-start error. Exhaustion must enter the configured terminal workflow once. Cancellation must leave no further connection attempts or orphan sessions. Readiness after deployment and unit tests do not establish a real-call pass; append actual call IDs and results as testing proceeds.
