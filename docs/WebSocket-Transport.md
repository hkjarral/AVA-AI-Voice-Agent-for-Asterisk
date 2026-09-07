# Asterisk Media WebSocket transport

WebSocket is an opt-in, bidirectional media transport between Asterisk and
AAVA. AAVA still owns calls through ARI (`externalMedia` for JSON, media-only
origination for experimental plain controls); Asterisk's
`chan_websocket` creates a per-call media connection back to the AAVA listener.
The caller continues to enter the existing `Stasis(asterisk-ai-voice-agent)`
dialplan. This is **not** a migration to `Dial(WebSocket/...)`, an incoming
connection to Asterisk's `/media` endpoint, or a replacement dialplan.
When migrating from AudioSocket, change the selected media transport and its
matching Asterisk client configuration only; retain the established Stasis
route and Agent/provider selection.

## Scope and qualification

The JSON-control feature used by AAVA requires Asterisk **20.18+**, **22.8+**,
or **23.2+** on those release lines. These are feature floors, not a warranty
for a vendor build, a later release line, installed modules, or a provider.
Asterisk 21 and unrecognised release lines are rejected by AAVA until they are
qualified. Upstream documents the driver separately from the `externalMedia`
`transport_data` additions that establish these floors.

An **experimental, opt-in legacy path** supports exactly **20.17.0**, including
the development PBX build where isolated media/control proof passed. Set
`websocket_media.control_format: auto` to select plain on that release and JSON
on the existing JSON floors, or `plain` to require 20.17.0. Default remains
`json`. Unknown/unsupported versions fail closed; connection or authentication
failures never trigger downgrade. Selection is pinned per call. The UI shows
requested and effective running controls; saving is not the same as applying.

Legacy proof covered all four codecs, real correlated completion, repeated
XOFF/flush/resume, and nonce/auth isolation. It did **not** qualify real providers,
pipelines, acoustic barge-in, transfers, concurrency under load, or soak behavior.
JSON uses MARK after FLUSH; 20.17 uses explicit CONTINUE and a fresh correlated
STOP boundary because MARK is absent. Neither mode invents completion or XON.

One live qualification has passed: **Asterisk 22.10.1 on FreePBX 17**, using
OpenAI Realtime with `ulaw`, for a 128-second call with multilingual transcripts.
The operator reported good audio; logs show interruption actions and a spoken
hangup request followed by farewell drain and clean termination. This is a
bounded smoke test, not independent acoustic certification. It does not
qualify other providers, codecs, topologies, concurrency, or failure modes.
Next acceptance tests cover configured providers first; all remaining full
providers and modular pipelines stay explicitly unqualified.

## Before enabling

- Confirm the version floor and that `chan_websocket`, `res_websocket_client`,
  `res_http_websocket`, and `res_ari_channels` are running. Retain the existing
  ARI/Stasis/bridge modules, codec translation, and a working Asterisk timing
  backend.
- Choose a unique `connection_name`, listener port, and an allowlist containing
  only the Asterisk source address. Use an authenticated listener in every
  topology; the media password is separate from ARI credentials.
- Put the password only in the AI Engine environment (normally `.env`) as
  `ASTERISK_MEDIA_WS_PASSWORD` or the configured `password_env` name. Do not
  put its value in YAML, a support bundle, a ticket, or a shell command.
- For one-host installs, the default Compose topology is host networking:
  Asterisk and the engine share a network namespace and should use authenticated
  loopback (`127.0.0.1`). Loopback does not cross separate containers or hosts.
- For routed networks, bind and advertise an address reachable from Asterisk,
  limit the firewall to Asterisk-to-engine traffic, and use WSS with a
  certificate whose name matches the advertised host. Configure Asterisk to
  verify both the issuing CA and hostname.

## Configure AAVA and Asterisk

The Admin UI is the preferred editor: **Advanced → Transport → WebSocket**.
It shows the selected Asterisk version, required modules, secret-reference
presence, and a generated `websocket_client.conf` stanza. A representative
same-host configuration is:

```yaml
audio_transport: websocket
websocket_media:
  connection_mode: asterisk_outbound
  connection_name: aava_media
  bind_host: 127.0.0.1
  advertise_host: 127.0.0.1
  port: 8787
  path: /media
  format_policy: profile
  fallback_format: ulaw
  control_format: json
  direction: both
  allowed_remote_hosts: [127.0.0.1]
  auth:
    required: true
    username: aava_media
    password_env: ASTERISK_MEDIA_WS_PASSWORD
  tls:
    enabled: false
```

Add the matching named, **per-call** Asterisk client; use the same username and
password value without exposing it in source control:

```ini
[aava_media]
type = websocket_client
uri = ws://127.0.0.1:8787/media
protocols = media
username = aava_media
password = <media-password>
connection_type = per_call_config
connection_timeout = 500
reconnect_interval = 500
reconnect_attempts = 5
tls_enabled = no
```

For WSS, change the URI to `wss://<advertised-host>:<port>/media`, set
`tls_enabled=yes`, configure the appropriate CA trust file/path, and enable
server-certificate and hostname verification. Mount the listener certificate
and key read-only where the AI Engine can read them. `allowed_remote_hosts`
accepts IP literals (or `localhost`), not DNS names.

Apply Asterisk's supported reload or maintenance procedure after changing
`websocket_client.conf`. Do not add `d(...)` to a dial string: AAVA creates the
WebSocket media channel through ARI `externalMedia` with JSON controls and its
own per-call correlation nonce. Experimental plain uses ARI `POST /channels`
with an assigned auxiliary identity and `WebSocket/<client>/c(codec)v(nonce=...)`;
this is not a business outbound call or a change to the caller dialplan.

### Save, apply, and recreate safely

1. Drain or complete active calls before switching transport or changing the
   listener endpoint, credential, TLS files, or Asterisk client stanza.
2. Save the WebSocket settings in the UI, then use **Recreate AI Engine** so
   the listener, selected transport, and injected environment match the saved
   configuration. The UI distinguishes edited, saved, and running transport.
3. An `.env` change needs an AI Engine **container recreation** to inject the
   new value; a container restart alone does not update its environment. The
   apply endpoint rejects a missing required credential before stopping the
   engine, including forced applies. Incomplete settings may still be saved
   during setup. The saved-secret indicator checks presence only, not a match
   with Asterisk's password or successful authentication.
4. Reload/restart Asterisk under the PBX operating procedure, then confirm
   listener readiness and make a controlled call. Saving YAML alone never
   changes an already running listener.

The engine's readiness and new-call admission also require a current ARI module
inventory, the four WebSocket prerequisites, and a running `res_timing_*`
backend. Unknown or unavailable inventory fails closed for WebSocket, without
disabling the legacy transports. Health exposes `modules_ready`,
`module_inventory_available`, `missing_modules`, `non_running_modules`,
`timing_modules`, and `module_reason`. The UI's separate saved-selection PBX
probe is not proof of running-engine readiness. Neither probe verifies the
named client stanza, its matching password, nor an audible end-to-end call.

## Wire formats and behavior

The frozen per-call audio profile selects the media wire format. The supported
formats are `ulaw` (8 kHz), `alaw` (8 kHz), `slin` (8 kHz PCM), and `slin16`
(16 kHz PCM). Provider sample rates are independent and conversion happens at
the transport boundary. A 16 kHz wire format cannot restore frequencies
removed by an 8 kHz telephone leg.

Both control modes use `direction=both` and 20 ms media framing. They do not
support Opus, Speex, G.729, passthrough, dynamic media direction, or AAVA
connecting to Asterisk's inbound `/media` endpoint. File playback remains ARI
controlled; attended-transfer helper media can retain its existing RTP path.

Barge-in clears queued media and fences later output; audio already delivered
by Asterisk cannot be recalled. Connection, handshake, `MEDIA_START`,
correlation, or active-media loss fails that call. There is no automatic
mid-call reconnect and no automatic fallback to AudioSocket or RTP. Select a
different transport only after the call has ended or been drained.

## Qualification and rollback

Before downgrading to a JSON-only application build, restore the pre-deployment
YAML (including local overrides), or remove/reset `control_format: auto/plain`
while still on the new build. Old strict schemas reject that field even when
AudioSocket/RTP is selected. Preserve operator/provider data, drain calls, and
restore both application images and their matching configuration. No Asterisk
upgrade is required for the experimental 20.17.0 path.

Use this table for each intended provider/profile/topology; a green health
check alone is not call qualification.

| Gate | Required evidence | Pass condition |
|---|---|---|
| Admission | version, modules, timing backend, listener status | supported version and all prerequisites ready |
| Security | redacted config, source allowlist, auth, TLS if routed | exact peer allowed; credential never logged |
| Media | greeting, two-way speech, silence, barge-in | expected audio and interruption behavior |
| Call lifecycle | intentional farewell/hangup, cleanup, Call History | one clean terminal record; no leaked media leg |
| Feature flow | applicable playback, tool, and transfer paths | expected result for the selected provider/profile |
| Resilience | planned network/media-loss test | affected call fails cleanly; no implicit transport switch |

Release acceptance is complete only when the intended combination has passed
the table, the configured rollback was rehearsed, and support staff can collect
a redacted bundle. Record Asterisk/FreePBX/AAVA versions and build IDs, selected
transport/profile/provider, topology (loopback or WSS), timestamp/timezone,
call ID, redacted listener and Asterisk client settings, module/status output,
and relevant engine/Asterisk log excerpts. For a barge-in investigation, also
capture the `ai_agent_barge_in_actions_total{source=...}` observation and the
call's channel-destroyed `cause`/`cause_txt` log fields. Exclude passwords, API
keys, authorization headers, full raw log archives, and call audio unless the
operator has approved their secure handling.

To roll back, drain calls, select `audiosocket` or `externalmedia` in the UI,
save and apply/recreate the AI Engine as required, reload Asterisk if its
configuration changed, and make a smoke call. Provider, Agent, and existing
Stasis dialplan settings can remain unchanged. Leaving an unused Asterisk
client stanza is harmless; remove it later under normal change control.

## Troubleshooting

WebSocket health exposes two different loss indicators: `metrics.input_drops`
counts full listener input queues; `engine_input_rejections` counts audio rejected
after listener delivery, grouped by readiness, ownership, inactive session,
missing binding, or decode/ingress error. Zero queue drops alone does not prove
caller audio reached STT. The first rejection of each reason is logged per call;
`RCA_CALL_END.websocket_input_rejections` includes that call's totals. A few
`not_ready` frames during setup can be expected; continued rejections after the
greeting or `media_rx_confirmed=false` require investigation. These counters reset
on engine restart and do not replace acoustic testing.

For a greeting-only call, correlate both attach completions with media readiness.
The setup implementation serializes the main lifecycle and auxiliary StasisStart
handler per call, preserves terminal/ready states, and refuses late writes to a
removed or replaced session. Do not work around readiness failures by admitting
audio before bridge/channel ownership is established.

| Symptom | Check | Safe next action |
|---|---|---|
| WebSocket cannot be applied or a new call is refused | version/module status or missing engine secret | correct the prerequisite, recreate the engine, then retry |
| Handshake rejected | URI/path, source allowlist, username/password match | correct one side without logging the password; reload/apply and retest |
| Routed connection fails TLS | WSS URI, CA trust, hostname, certificate paths | fix trust/name/path and retain certificate verification |
| Media call ends during setup | listener readiness, JSON-control floor, `connection_name` | inspect redacted engine/Asterisk errors; do not add `Dial(WebSocket/...)` |
| Call loses media | call ID and lifecycle logs | treat the call as failed; place the next call only after repair or rollback |

References: [Asterisk WebSocket channel driver](https://docs.asterisk.org/Configuration/Channel-Drivers/WebSocket/) and [Asterisk WebSocket client configuration](https://docs.asterisk.org/Latest_API/API_Documentation/Module_Configuration/res_websocket_client/).
