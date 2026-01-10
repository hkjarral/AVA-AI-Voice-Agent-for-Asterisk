# Architecture Quick Start (10-Minute Overview)

Quick mental model of how Asterisk AI Voice Agent works. For complete details, see [Architecture Deep Dive](architecture-deep-dive.md).

## Core Concept

```
Caller → Asterisk → AI Engine → Asterisk → Caller
         (ARI)      (STT→LLM→TTS)  (Audio)
```

**Two execution modes**:
1. **Monolithic Providers**: All-in-one (OpenAI Realtime, Deepgram, Google Live)
2. **Modular Pipelines**: Mix & match STT + LLM + TTS components

## Key Components

### 1. Asterisk Integration (ARI)

**Asterisk Stasis** → Python engine via **ARI (Asterisk REST Interface)**

**Dialplan Entry**:
```
exten => _X.,1,NoOp(AI Voice Agent)
 same => n,Stasis(asterisk-ai-voice-agent)
 same => n,Hangup()
```

**What Happens**:
- Call enters Stasis application
- Engine receives `StasisStart` event
- Engine creates AudioSocket or ExternalMedia channel
- Bidirectional audio streaming begins

### 2. Audio Transports

**Two Options**:

**ExternalMedia RTP** (Shipped default config):
- RTP streams on port 18080
- Configurable codecs (μ-law, PCM16)
- Best for: Production, distributed deployments, clusters

**AudioSocket** (Simpler alternative):
- TCP connection on port 8090
- **Validated wire format**: `slin` (16-bit signed linear @ 8 kHz)
- Best for: Getting started, simple setups, debugging

**Format Flow**:
```
Caller (μ-law/various) → Asterisk → Transport (e.g., `slin`) → Engine
```

### 3. Engine Core (`src/engine.py`)

**Main responsibilities**:
- Accept ARI events (calls, audio)
- Route audio to providers/pipelines
- Manage playback (TTS → caller)
- Execute tools (transfer, hangup, etc.)
- Handle session state

**Key Methods**:
- `_on_stasis_start()`: New call
- `_audiosocket_handle_audio()`: Audio from AudioSocket
- `_on_rtp_audio()`: Audio from RTP
- `run_turn()`: Pipeline orchestration

### 4. Providers vs Pipelines

**Monolithic Providers**:
```python
# OpenAI Realtime, Deepgram, Google Live
Audio → [Provider does everything] → Audio
```
- Single WebSocket/API connection
- Provider handles STT, LLM, TTS internally
- Built-in tool calling
- Lower latency (single hop)

**Modular Pipelines**:
```python
# Example: Deepgram STT + OpenAI LLM + Piper TTS
Audio → STT → Text → LLM → Text → TTS → Audio
```
- Mix and match components
- More flexibility
- Local options (privacy, offline)
- Slightly higher latency (multiple hops)

### 5. Tool Execution

**Tools** = Actions AI can take (hangup, transfer, email, voicemail)

**Flow**:
1. User: "Transfer me to sales"
2. LLM detects intent → `tool_call`
3. Engine executes via `ToolRegistry`
4. Tool performs action (ARI redirect)
5. Result returned to conversation

**Schema Formats** (CRITICAL):
- OpenAI Realtime: **Flat** `{type, name, description, parameters}`
- OpenAI Chat: **Nested** `{type, function: {name, ...}}`
- Deepgram: **Array** `[{name, description, parameters}]`

See [Common Pitfalls](COMMON_PITFALLS.md#tool-execution-issues) for details.

### 6. Audio Gating (VAD)

**Problem**: Agent must not hear itself speaking

**Solution**: Audio gate controlled by Voice Activity Detection

**States**:
- **Gate Open**: User audio flows to AI
- **Gate Closed**: User audio blocked (agent speaking)

**Controlled by**:
- WebRTC VAD (detects user speech start/stop)
- TTS playback events (close during agent speech)
- Provider events (response.audio.delta, etc.)

**Critical Setting** (OpenAI Realtime):
```yaml
vad:
  webrtc_aggressiveness: 1  # Level 0 detects echo as speech!
```

### 7. Session Management

**SessionStore** (`src/core/session_store.py`):
- Tracks conversation history
- Stores call metadata
- Manages transcripts
- Persists for email summaries

**Lifecycle**:
1. Call starts → Session created
2. Each turn → Added to history
3. Tool execution → Saved in session
4. Call ends → Session finalized → Email sent

## Configuration Flow

```yaml
# config/ai-agent.yaml
default_provider: openai_realtime
active_pipeline: local_hybrid

contexts:
  default:
    provider: openai_realtime
    profile: telephony_ulaw_8k
```

**Lookup Order**:
1. Dialplan override: `AI_PROVIDER` (if set)
2. Context override: `contexts.<name>.provider` (if set for the selected context)
3. Global default: `default_provider`

If the selected provider path is a pipeline-based configuration, the engine uses `active_pipeline`.

## Data Flow Example (Pipeline)

**User says "Hello"**:

1. **Audio In**: Caller → Asterisk → AudioSocket → Engine (`slin`, 16-bit PCM @ 8 kHz)
2. **STT**: Engine → Vosk → "Hello" (text)
3. **LLM**: Text + history → OpenAI API → "Hi! How can I help you?"
4. **TTS**: Text → Piper → audio bytes (PCM16)
5. **Playback**: audio → Asterisk → Caller
6. **Session**: Turn saved to SessionStore

**User says "Transfer me to sales"**:

1-3. Same (Audio → STT → LLM)
4. **LLM Output**: `tool_calls: [{name: "transfer", args: {destination: "sales"}}]`
5. **Tool Execution**: `UnifiedTransferTool.execute()` → ARI redirect
6. **Call Transferred**: Control returned to dialplan

## File Structure (Important Files)

```
src/
  engine.py              # Core orchestrator
  ari_client.py          # Asterisk ARI communication
  
  providers/
    openai.py            # OpenAI Realtime provider
    deepgram.py          # Deepgram Voice Agent
    google.py            # Google Live API
  
  pipelines/
    deepgram.py          # Deepgram STT/TTS adapters
    openai.py            # OpenAI LLM adapter (Chat API)
    local.py             # Vosk, Phi-3, Piper adapters
  
  tools/
    base.py              # Tool base class, schema generation
    registry.py          # Tool registry, schema export
    telephony/           # Transfer, hangup, voicemail
    business/            # Email summary, transcript
  
  core/
    session_store.py     # Conversation state
    playback_manager.py  # Audio playback (file-based)
    streaming_playback_manager.py  # Real-time streaming
    adaptive_streaming.py  # Pacing, gating
    vad.py               # Voice activity detection

config/
  ai-agent.yaml          # Main configuration
  contexts/              # System prompts
```

## Common Patterns

### Adding a New Tool

1. Create tool class in `src/tools/telephony/` or `src/tools/business/`
2. Inherit from `Tool` base class
3. Implement `execute()` method
4. Register in `src/tools/registry.py`
5. Add to config `tools:` list
6. Test with real call

### Adding a New Provider

1. Create adapter in `src/pipelines/{name}.py`
2. Implement required methods: `transcribe()`, `generate()`, `synthesize()`
3. Add config section in `ai-agent.yaml`
4. Register in Engine's provider lookup
5. Test audio format compatibility

### Debugging a Call

1. **Get call ID** from logs: `call_id=1763584609.6250`
2. **Collect logs**: `./scripts/rca_collect.sh {call_id}`
3. **Analyze**: Check STT output, LLM responses, tool execution, audio metrics
4. **Common issues**: Schema format, VAD settings, audio codec mismatch

## Next Steps

- **Hands-on**: [Quick Start Guide](quickstart.md) - Set up dev environment
- **Avoid mistakes**: [Common Pitfalls](COMMON_PITFALLS.md) - Real issues & fixes
- **Deep dive**: [Architecture Deep Dive](architecture-deep-dive.md) - Complete technical details
- **Build tools**: [Tool Development](tool-development.md) - Create custom tools
- **Add providers**: [Provider Development](provider-development.md) - Integrate new APIs

## Questions?

- **Tool not working?** → [Common Pitfalls: Tool Execution](COMMON_PITFALLS.md#tool-execution-issues)
- **Audio issues?** → [Common Pitfalls: Audio & Codecs](COMMON_PITFALLS.md#audio--codec-issues)
- **Provider errors?** → [Common Pitfalls: Provider-Specific](COMMON_PITFALLS.md#provider-specific-issues)
- **Still stuck?** → [Debugging Guide](debugging-guide.md)

---

**Estimated reading time: 10 minutes** ⏱️

Ready to start developing? Continue to [Quick Start Guide](quickstart.md) →
