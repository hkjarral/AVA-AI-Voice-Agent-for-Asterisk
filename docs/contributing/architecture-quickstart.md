# Architecture Quick Start

AVA connects an Asterisk call to an AI provider or a modular speech pipeline.
ARI controls the call; a separately configured media transport carries audio.

```text
Caller ↔ Asterisk ↔ AI Engine ↔ Voice provider or STT → LLM → TTS
           ARI + media             Tools and per-call state
```

For detailed implementation contracts, start with [AVA.mdc](../../AVA.mdc)
and the [architecture deep dive](architecture-deep-dive.md).

## Services and ownership

| Component | Responsibility |
| --- | --- |
| `ai_engine` | ARI events, call routing, media, provider/pipeline sessions, tools and Call History |
| `admin_ui` | Provider/Agent configuration, diagnostics, history and update controls |
| `local_ai_server` (optional) | Local STT, LLM and TTS inference for local/hybrid configurations |
| `updater` (optional) | Reviewed update plans, backups, service recreation and recovery |

Run only the services required by the actual deployment. Models and operator
data persist outside disposable container layers; preserve their volumes and
configured paths during upgrades.

## Call selection and lifecycle

Calls enter `Stasis(asterisk-ai-voice-agent)`. The dialplan's `AI_AGENT`
selects an Agent slug from `agents.db`; legacy `AI_CONTEXT` remains a
display-name-first compatibility selector, not a live YAML persona source.

Provider selection follows the explicit `AI_PROVIDER` override, the selected
Agent, then the configured global default. Named pipelines use
`PipelineOrchestrator`; see [selection and precedence](../Configuration-Reference.md#call-selection--precedence-provider--pipeline--agent)
rather than assuming that every pipeline always uses one global setting.

A typical call proceeds through routing, media/provider setup, conversation,
tool execution, terminal audio drain, hangup and cleanup. Failure, cancellation,
transfer and retry paths must preserve the same call ownership.
See [Agents](../AGENTS.md) and the [FreePBX guide](../FreePBX-Integration-Guide.md)
for dialplan examples and configuration.

## Audio transports

| Transport | Configuration and boundaries |
| --- | --- |
| AudioSocket | Shipped `audio_transport` default; TCP listener (8090 by default), with codecs/rates governed by the selected Audio Profile |
| ExternalMedia RTP | Optional UDP media (18080 by default); provider/playback combinations require matching configuration |
| Media WebSocket | Opt-in per-call Asterisk media leg, authenticated listener and version/module requirements |

Transport choice is independent of full-agent versus pipeline execution.
[TransportOrchestrator](../../src/core/transport_orchestrator.py) resolves media
settings; provider wire formats must not be inferred from PBX codec names.
Use the [compatibility matrix](../Transport-Mode-Compatibility.md) and
[WebSocket guide](../WebSocket-Transport.md) for qualified combinations and
version floors. Historical baseline passes do not certify every new candidate.

## Providers and pipelines

Full-agent providers in `src/providers/` own their upstream voice sessions:
OpenAI Realtime, Deepgram, Google Live/Vertex, ElevenLabs, Grok and Local.
They differ in tool schemas, turn-taking, codecs and lifecycle events.

Modular adapters in `src/pipelines/` separate STT, LLM and TTS. They can combine
local and cloud backends; hybrid mode does not imply that all data stays local.
Latency depends on models, hardware, network and turn detection, not just mode.

## Shared state and tools

- `SessionStore` owns shared call state; `agent_store.py` stores Agent
  definitions, and `call_history.py` handles persistent call records.
- Playback managers emit audio. `ConversationCoordinator` and
  `AudioGatingManager` coordinate capture, playback and interruption. Do not add
  competing per-call state stores or assume one gating rule fits all providers.
- `ToolRegistry` and provider adapters expose authorized tool schemas. The
  engine dispatches pre-, in- and post-call tools according to their definitions.
  MCP tools remain in-call only.
- Each call captures its tool generation. Tools **Save & Apply** changes new
  calls; existing calls retain their snapshot. Agent permissions may narrow
  global resources but cannot expand them.
- Transfer, calendar and other effectful tools must preserve authorization,
  call ownership, cancellation and idempotency. A timeout does not prove that
  an external effect did not occur.
- The engine coordinates terminal audio drain and cleanup. Optional post-call
  tools run only when configured; ending a call does not automatically send email.

See the [tool guide](../TOOL_CALLING_GUIDE.md), [MCP integration](../MCP_INTEGRATION.md)
and [development workflow](ai-assisted-workflow.md) for complete lifecycle and
verification requirements.

## Source map

- [Engine](../../src/engine.py): call orchestration and dispatch.
- [Core](../../src/core/): Agent/session/history stores, media and playback owners.
- [Providers](../../src/providers/): full-agent sessions.
- [Pipelines](../../src/pipelines/): modular adapters and orchestrator.
- [Tools](../../src/tools/): registry, schemas, telephony/business/HTTP/MCP tools.
- [Admin UI](../../admin_ui/): React frontend and FastAPI backend.
- [Configuration reference](../Configuration-Reference.md): baseline YAML,
  local overrides, managed credentials and Agent-store boundaries.

## Next steps

[Developer setup](quickstart.md) · [Provider development](provider-development.md) ·
[Pipeline development](pipeline-development.md) · [Tool development](tool-development.md) ·
[Debugging](debugging-guide.md) · [Testing](testing-guide.md)
