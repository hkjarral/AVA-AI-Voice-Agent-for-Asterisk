# Local AI Server WebSocket Protocol

This document describes the WebSocket API exposed by the local AI server (default `ws://127.0.0.1:8765`). It supports selective operation modes for STT, LLM, TTS, and a full pipeline.

- Default address: `ws://127.0.0.1:8765`
- Engine/client URL: `LOCAL_WS_URL` (used by `providers.*.base_url/ws_url` in `config/ai-agent.yaml`)
- Server bind: `LOCAL_WS_HOST` + `LOCAL_WS_PORT` (server-side)
- Optional auth: `LOCAL_WS_AUTH_TOKEN` (server-side)
- Modes: `full`, `stt`, `llm`, `tts` (default `full`)
- Binary messages (client → server): raw PCM16 mono frames (assumed 16 kHz unless you set `rate` on JSON `audio`)
- Binary messages (server → client): μ-law 8 kHz audio bytes for TTS playback (used by `full` pipeline)
- JSON messages: control, status, text requests, or base64 audio frames

Source of truth:

- WebSocket router: `local_ai_server/ws_protocol.py`
  - Message handling: `handle_json_message()`, `handle_binary_message()`
- Runtime/pipeline implementation: `local_ai_server/server.py`
  - STT routing + segmentation: `_process_stt_stream*()`
  - LLM + tool gateway: `_handle_llm_request()`, `_handle_llm_tool_request()`
  - TTS + binary/audio metadata emission: `_handle_tts_request()`, `_emit_tts_audio()`
- Switch/status/capabilities control plane:
  - `local_ai_server/control_plane.py`
  - `local_ai_server/model_manager.py`
  - `local_ai_server/status_builder.py`
- Protocol contract/schema generator: `local_ai_server/protocol_contract.py`

---

## Connection and Modes

### Authentication (optional)

If `LOCAL_WS_AUTH_TOKEN` is set on the server, clients must authenticate before any other messages (including binary audio).

Request:

```json
{ "type": "auth", "auth_token": "..." }
```

Notes:

- The server also accepts `token` as an alias for `auth_token`.
- You may include `call_id` to correlate logs before the first audio frame.

Response:

```json
{ "type": "auth_response", "status": "ok" }
```

If authentication is required and not completed, the server responds with:

```json
{ "type": "auth_response", "status": "error", "message": "authentication_required" }
```

If the token is wrong:

```json
{ "type": "auth_response", "status": "error", "message": "invalid_auth_token" }
```

### Mode selection

Optionally set a default mode for subsequent binary audio frames.

Request:

```json
{
  "type": "set_mode",
  "mode": "stt",           
  "call_id": "1234-5678"   
}
```

Response:

```json
{
  "type": "mode_ready",
  "mode": "stt",
  "call_id": "1234-5678"
}
```

Notes:

- Supported modes: `full`, `stt`, `llm`, `tts`.
- `call_id` is optional but useful for correlating events.
- If you never call `set_mode`, the default is `full`.

---

## Message Types (JSON)

- `auth` → Authenticate session (if enabled); responds with `auth_response`.
- `set_mode` → Changes session mode; responds with `mode_ready`.
- `audio` → Base64 audio frames for STT/LLM/FULL flows (recommended: PCM16 mono @ 16 kHz).
- `barge_in` → Clears Whisper-family STT suppression window; responds with `barge_in_ack`.
- `llm_request` → Ask LLM with text; responds with `llm_response`.
- `llm_tool_request` → Run tool-call parser/repair/structured gateway; responds with `llm_tool_response`.
- `tts_request` → Synthesize TTS from text; responds with `tts_response` (base64 μ-law).
- `reload_models` → Reload all models; responds with `reload_response`.
- `reload_llm` → Reload only LLM; responds with `reload_response`.
- `switch_model` → Switch backend/model paths at runtime; responds with `switch_response`.
- `status` → Report loaded backends/models; responds with `status_response`.
- `capabilities` → Return installed backend availability; responds with `capabilities_response`.
- `backends` → Return built-in backend registry info; responds with `backends_response`.
- `backend_schema` → Return config schema/availability for one backend; responds with `backend_schema_response`.

Notes:

- Message `type` is normalized server-side (`lower()`, trim, `-` → `_`), so `set-mode` and `set_mode` are treated the same.
- Unknown or malformed message types are logged and ignored (no error response).

### Common fields

- `call_id` (string, optional): Correlate the request with your call/session.
- `request_id` (string, optional): Correlate multiple responses to a single request.

---

## Audio Streaming (STT / FULL)

You can stream audio via:

- JSON frames: `{ "type": "audio", "data": "<base64 pcm16>", "rate": 16000, "mode": "full" }`
- Binary frames: send raw PCM16 bytes directly after `set_mode`.

Recommended input: PCM16 mono at 16 kHz. If you send another rate, the server resamples to 16 kHz internally using sox.

### JSON audio example (full pipeline)

Request:

```json
{
  "type": "audio",
  "mode": "full",
  "rate": 16000,
  "call_id": "1234-5678",
  "request_id": "r1",
  "data": "<base64 pcm16 chunk>"
}
```

Expected responses (sequence):

- `stt_result` (zero or more partials)
- `stt_result` (one final)
- `llm_response`
- (optional) `tts_audio` metadata (only if `request_id` is provided)
- one binary WebSocket message containing μ-law 8 kHz audio bytes

Example events:

```json
{ "type": "stt_result", "text": "hello", "call_id": "1234-5678", "mode": "full", "is_final": false, "is_partial": true, "request_id": "r1" }
{ "type": "stt_result", "text": "hello there", "call_id": "1234-5678", "mode": "full", "is_final": true, "is_partial": false, "request_id": "r1", "confidence": 0.91 }
{ "type": "llm_response", "text": "Hi there, how can I help you?", "call_id": "1234-5678", "mode": "llm", "request_id": "r1" }
{ "type": "tts_audio", "call_id": "1234-5678", "mode": "full", "request_id": "r1", "encoding": "mulaw", "sample_rate_hz": 8000, "byte_length": 16347 }
```

If `request_id` is set, the server emits `tts_audio` metadata before the binary audio. If `request_id` is omitted, you will only receive the binary audio bytes.

### Binary audio example (stt-only)

1) Set mode:

```json
{ "type": "set_mode", "mode": "stt", "call_id": "abc" }
```

2) Send binary PCM16 frames (no JSON wrapper). The server will emit:

```json
{ "type": "stt_result", "text": "...", "call_id": "abc", "mode": "stt", "is_final": false, "is_partial": true }
{ "type": "stt_result", "text": "...", "call_id": "abc", "mode": "stt", "is_final": true,  "is_partial": false }
```

Notes:

- The server uses an idle finalizer (`LOCAL_STT_IDLE_MS`, default 5000 ms) to promote a final transcript if no more audio arrives; duplicate/empty finals are suppressed per `local_ai_server/server.py`.
- For `faster_whisper` / `whisper_cpp`, STT is utterance-segmented (energy + silence endpoint) and primarily emits final transcripts (not high-frequency partials).
- Punctuation-only non-linguistic finals (for example just `?`) are suppressed in `llm`/`full` modes to avoid LLM/TTS loops.

---

## Barge-In (Whisper echo guard control)

When using `faster_whisper` or `whisper_cpp`, Local AI Server suppresses STT while it is transmitting TTS audio to avoid self-transcription loops. If upstream detects caller barge-in, send:

```json
{
  "type": "barge_in",
  "call_id": "1234-5678",
  "request_id": "bi-1"
}
```

Response:

```json
{
  "type": "barge_in_ack",
  "status": "ok",
  "call_id": "1234-5678",
  "request_id": "bi-1"
}
```

---

## LLM-only

Request:

```json
{
  "type": "llm_request",
  "text": "What are your business hours?",
  "call_id": "1234-5678",
  "request_id": "q1"
}
```

Response:

```json
{
  "type": "llm_response",
  "text": "We're open from 9am to 5pm, Monday through Friday.",
  "call_id": "1234-5678",
  "mode": "llm",
  "request_id": "q1"
}
```

---

## LLM Tool Gateway (`llm_tool_request`)

Use this endpoint when the engine already has assistant text and needs normalized tool calls.

Request:

```json
{
  "type": "llm_tool_request",
  "text": "<tool_call>{\"name\":\"hangup_call\",\"arguments\":{\"farewell_message\":\"Goodbye\"}}</tool_call>",
  "call_id": "1234-5678",
  "request_id": "tool-1",
  "tool_choice": "auto",
  "tool_policy": "auto",
  "allowed_tools": ["hangup_call"],
  "tools": [{ "name": "hangup_call", "parameters": { "type": "object" } }],
  "latest_user_text": "thanks bye"
}
```

Response:

```json
{
  "type": "llm_tool_response",
  "call_id": "1234-5678",
  "request_id": "tool-1",
  "text": "",
  "tool_calls": [
    { "name": "hangup_call", "parameters": { "farewell_message": "Goodbye" } }
  ],
  "finish_reason": "tool_calls",
  "tool_path": "parser",
  "tool_parse_failures": 0,
  "repair_attempts": 0,
  "structured_attempts": 0,
  "protocol_version": 2
}
```

Notes:

- `tool_path` values: `parser`, `structured`, `repair`, `heuristic`, `none`.
- When `LOCAL_TOOL_GATEWAY_ENABLED=1`, structured/repair paths are used when parsing fails.
- Fast-path hangup heuristic can emit `hangup_call` if end-of-call intent is detected in `latest_user_text`.

---

## TTS-only

Request:

```json
{
  "type": "tts_request",
  "text": "Hello, how can I help you?",
  "call_id": "1234-5678",
  "request_id": "t1"
}
```

Response:

```json
{
  "type": "tts_response",
  "text": "Hello, how can I help you?",
  "call_id": "1234-5678",
  "request_id": "t1",
  "audio_data": "<base64 mulaw bytes>",
  "encoding": "mulaw",
  "sample_rate_hz": 8000,
  "byte_length": 12446
}
```

---

## Hot Reload

- Reload all models:

```json
{ "type": "reload_models" }
```

Response:

```json
{ "type": "reload_response", "status": "success", "message": "All models reloaded successfully" }
```

- Reload LLM only:

```json
{ "type": "reload_llm" }
```

Optional request fields:

- `llm_model_path` (or alias `model_path`) to update the active model path before reload.

Response:

```json
{ "type": "reload_response", "status": "success", "message": "LLM model reloaded with optimizations (ctx=..., batch=..., temp=..., max_tokens=...)" }
```

---

## Status

Request:

```json
{ "type": "status" }
```

Response:

```json
{
  "type": "status_response",
  "status": "ok",
  "stt_backend": "vosk|kroko|sherpa|faster_whisper|whisper_cpp",
  "tts_backend": "piper|kokoro|melotts|silero",
  "models": {
    "stt": { "loaded": true, "path": "/app/models/stt/...", "display": "vosk-model-en-us-0.22" },
    "llm": {
      "loaded": true,
      "path": "/app/models/llm/...",
      "display": "phi-3-mini-4k-instruct.Q4_K_M.gguf",
      "config": {
        "context": 2048,
        "threads": 16,
        "batch": 128,
        "max_tokens": 64,
        "temperature": 0.4,
        "top_p": 0.85,
        "repeat_penalty": 1.05,
        "gpu_layers": 50
      },
      "prompt_fit": {
        "system_prompt_chars": 123,
        "system_prompt_tokens": 68,
        "safe_max_tokens": 1972
      },
      "auto_context": { "requested_ctx": 2048, "effective_ctx": 2048 },
      "tool_capability": { "level": "partial", "chat_format": "chatml" }
    },
    "tts": { "loaded": true, "path": "/app/models/tts/...", "display": "en_US-lessac-medium.onnx" }
  },
  "kroko": { "embedded": false, "port": 6006, "language": "en-US", "url": "wss://...", "model_path": "/app/models/kroko/..." },
  "kokoro": { "mode": "local|api|hf", "voice": "af_heart", "model_path": "/app/models/tts/kokoro", "api_base_url": "https://.../api/v1", "api_key_set": false },
  "gpu": {
    "runtime_detected": true,
    "runtime_usable": true,
    "source": "nvidia_smi",
    "name": "NVIDIA GeForce RTX 4090",
    "memory_gb": 24.0,
    "error": null
  },
  "config": {
    "log_level": "INFO",
    "debug_audio": false,
    "mock_models": false,
    "runtime_mode": "full|minimal",
    "tool_gateway_enabled": true,
    "degraded": false,
    "startup_errors": {},
    "runtime_fallbacks": {}
  }
}
```

Schema:

- Base contract: `docs/local-ai-server/protocol.schema.json`
- Canonical generator/source: `local_ai_server/protocol_contract.py`
- Note: operational/advanced messages (`barge_in`, backend registry introspection) are documented here and may not always be represented in the published schema file.

---

## Model Switching

`switch_model` updates server-side model/backend selections and reloads models without restarting the container.

Request (examples):

```json
{ "type": "switch_model", "stt_backend": "kroko" }
```

```json
{ "type": "switch_model", "stt_backend": "sherpa", "sherpa_model_path": "/app/models/stt/sherpa-onnx-streaming-zipformer-en-2023-06-26" }
```

```json
{ "type": "switch_model", "stt_backend": "kroko", "kroko_embedded": true, "kroko_port": 6006, "kroko_model_path": "/app/models/kroko/kroko-en-v1.0.onnx" }
```

```json
{ "type": "switch_model", "tts_backend": "silero", "silero_speaker": "xenia", "silero_language": "ru", "silero_model_id": "v3_1_ru" }
```

```json
{ "type": "switch_model", "tts_backend": "kokoro", "kokoro_voice": "af_heart" }
```

```json
{ "type": "switch_model", "tts_backend": "kokoro", "kokoro_mode": "api", "kokoro_api_base_url": "https://voice-generator.pages.dev/api/v1" }
```

```json
{ "type": "switch_model", "llm_model_path": "/app/models/llm/phi-3-mini-4k-instruct.Q4_K_M.gguf" }
```

```json
{
  "type": "switch_model",
  "llm_config": {
    "context": 2048,
    "max_tokens": 128,
    "chat_format": "llama-3",
    "gpu_layers": -1,
    "system_prompt": "You are a helpful voice assistant."
  }
}
```

```json
{
  "type": "switch_model",
  "stt_backend": "faster_whisper",
  "stt_config": {
    "model": "medium",
    "device": "cuda",
    "compute_type": "float16"
  }
}
```

```json
{ "type": "switch_model", "stt_backend": "faster_whisper", "faster_whisper_language": "ru" }
```

```json
{ "type": "switch_model", "stt_backend": "whisper_cpp", "whisper_cpp_language": "ru", "stt_model_path": "/app/models/stt/ggml-base.bin" }
```

```json
{ "type": "switch_model", "stt_backend": "sherpa", "sherpa_model_type": "offline", "sherpa_model_path": "/app/models/stt/sherpa-onnx-zipformer-en-2023-06-26", "sherpa_vad_model_path": "/app/models/vad/silero_vad.onnx" }
```

```json
{ "type": "switch_model", "stt_backend": "tone", "tone_model_path": "/app/models/stt/t-one", "tone_decoder_type": "beam_search", "tone_kenlm_path": "/app/models/stt/t-one/kenlm.bin" }
```

Notes:
- `sherpa_model_type=online` expects a streaming Sherpa model directory such as `sherpa-onnx-streaming-zipformer-en-2023-06-26`
- `sherpa_model_type=offline` expects a non-streaming Sherpa transducer model directory such as `sherpa-onnx-zipformer-en-2023-06-26`
- Offline mode will reject streaming Sherpa model directories

Response:

```json
{ "type": "switch_response", "status": "success", "message": "...", "changed": ["stt_backend=kroko"] }
```

Optional fields:

- `dry_run` (boolean): when `true`, the server updates its in-memory configuration and responds with `switch_response`, but does **not** reload models. This is intended for diagnostics/smoke tests.

Accepted payload shapes:

- Top-level keys (for compatibility): `stt_backend`, `tts_backend`, `llm_model_path`, `kokoro_*`, `kroko_*`, `sherpa_model_path`, `sherpa_model_type`, `sherpa_vad_model_path`, `tone_model_path`, `tone_decoder_type`, `tone_kenlm_path`, `faster_whisper_language`, `whisper_cpp_language`, `stt_model_path`, `tts_model_path`, `silero_speaker`, `silero_language`, `silero_model_id`, `silero_model_path`.
- Nested config objects:
  - `stt_config`: `model`, `device`, `compute_type`, `faster_whisper_language`, `whisper_cpp_language`, `sherpa_model_type`, `sherpa_vad_model_path`, `tone_model_path`, `tone_decoder_type`, `tone_kenlm_path`, plus Kroko aliases (`url`, `language`, `port`, `embedded`, `model_path`)
  - `tts_config`: `voice`, `mode`, `lang`, `api_base_url`, `api_key`, `api_model`, `device`, `speed`, `model_path`, `silero_speaker`, `silero_language`, `silero_model_id`, `silero_model_path`
  - `llm_config`: `model_path`, `threads`, `context`, `batch`, `max_tokens`, `temperature`, `top_p`, `repeat_penalty`, `gpu_layers`, `system_prompt`, `use_mlock`, `chat_format`

Notes:

- `chat_format` is hot-reloadable through `llm_config.chat_format`.
- Unsupported keys are ignored; valid applied keys are returned in `changed`.

---

## Capabilities

Query installed backends without loading models. Useful for Admin UI to show available options.

Request:

```json
{ "type": "capabilities" }
```

Response:

```json
{
  "type": "capabilities_response",
  "capabilities": {
    "vosk": true,
    "sherpa": true,
    "kroko_embedded": true,
    "tone": false,
    "faster_whisper": true,
    "whisper_cpp": false,
    "piper": true,
    "kokoro": true,
    "melotts": false,
    "silero": true,
    "llama": true
  }
}
```

Notes:

- `kroko_embedded`: `true` only if `/usr/local/bin/kroko-server` exists (requires `INCLUDE_KROKO_EMBEDDED=true` at build time)
- `tone`: `true` only if the T-one package is installed (requires `INCLUDE_TONE=true` at build time)
- `kokoro`: `true` if Kokoro package is installed, or `KOKORO_API_BASE_URL` is set, or model files exist on disk
- `silero`: `true` if the Silero TTS package is installed
- `vosk`, `piper`, `llama`: Reported as `true` in default/full Docker images (assumes standard dependencies are installed)
- Used by Admin UI `/api/local-ai/capabilities` endpoint to filter available options

---

## Backend Registry Introspection (advanced)

These messages are used by backend/plugin tooling and advanced UI flows.

- List registered backends:

```json
{ "type": "backends" }
```

Response:

```json
{
  "type": "backends_response",
  "stt": [{ "name": "vosk", "available": true }],
  "tts": [{ "name": "piper", "available": true }],
  "llm": [{ "name": "llama_cpp", "available": true }]
}
```

- Get backend config schema:

```json
{ "type": "backend_schema", "backend_type": "stt", "backend_name": "vosk" }
```

Response:

```json
{
  "type": "backend_schema_response",
  "backend_type": "stt",
  "backend_name": "vosk",
  "schema": {},
  "available": true
}
```

---

## Error Handling Contract (Current Behavior)

This section documents what the server currently does (as implemented in `local_ai_server/server.py` and `local_ai_server/ws_protocol.py`). Some of this will be improved in a later refactor phase (notably: graceful degradation on missing model files).

- **Invalid JSON**: logs a warning and ignores the message (no response).
- **Missing `type` field**: logs a warning and ignores the message (no response).
- **Unknown `type`**: logs a warning and ignores the message (no response).
- **Auth required but not completed**:
  - JSON messages: server responds with `{ "type": "auth_response", "status": "error", "message": "authentication_required" }`.
  - Binary audio: server responds with the same `auth_response` and drops audio frames.
- **Startup model load failures** (STT/LLM/TTS):
  - Default behavior is **degraded start**: the server starts, and `status_response.models.*.loaded=false` reflects missing components.
  - Set `LOCAL_AI_FAIL_FAST=1` to restore **fail-fast** startup (exceptions abort startup).
- **STT unavailable during audio streaming**: server emits a final `stt_result` with empty text and `error: "stt_unavailable"` so upstream can terminate the turn cleanly.
- **LLM timeouts**: server returns a fallback `llm_response` text (does not crash the connection).
- **Model switching failures**: server responds with `{ "type": "switch_response", "status": "error", "message": "..." }`.

### Testing / Mock Mode

- `LOCAL_AI_MOCK_MODELS=1`: skip loading real STT/LLM/TTS models on startup. `status_response.config.mock_models=true` and model `loaded` flags are forced `true` for easier smoke testing of the control-plane (`auth/status/capabilities/switch_model`) without downloading multi‑GB assets.

## Client Examples

Additional example code (including an espeak-ng based lightweight TTS demo) lives under `docs/local-ai-server/examples/`.

### Python: TTS request and save μ-law file

```python
import asyncio, websockets, json

async def tts():
    async with websockets.connect("ws://127.0.0.1:8765", max_size=None) as ws:
        await ws.send(json.dumps({
            "type": "tts_request",
            "text": "Hello, how can I help you?",
            "call_id": "demo",
            "request_id": "t1",
        }))
        resp = json.loads(await ws.recv())
        assert resp["type"] == "tts_response"
        import base64
        audio_bytes = base64.b64decode(resp["audio_data"])
        with open("out.ulaw", "wb") as f:
            f.write(audio_bytes)

asyncio.run(tts())
```

### Python: STT-only with binary frames

```python
import asyncio, websockets, json

async def stt(pcm_bytes):
    async with websockets.connect("ws://127.0.0.1:8765", max_size=None) as ws:
        await ws.send(json.dumps({"type": "set_mode", "mode": "stt", "call_id": "demo"}))
        await ws.recv()  # mode_ready
        await ws.send(pcm_bytes)  # raw PCM16 mono @ 16kHz
        while True:
            msg = await ws.recv()
            if isinstance(msg, bytes):
                continue
            evt = json.loads(msg)
            if evt.get("type") == "stt_result" and evt.get("is_final"):
                print("Final:", evt["text"])
                break

# pcm_bytes = ... load/generate 16kHz PCM16 mono
# asyncio.run(stt(pcm_bytes))
```

---

## Environment Variables and Tuning

Server-side (see `local_ai_server/config.py`, `local_ai_server/server.py`):

- Models: `LOCAL_STT_MODEL_PATH`, `LOCAL_LLM_MODEL_PATH`, `LOCAL_TTS_MODEL_PATH`
- WebSocket bind: `LOCAL_WS_HOST`, `LOCAL_WS_PORT`
- Optional auth: `LOCAL_WS_AUTH_TOKEN`
- Runtime mode:
  - `LOCAL_AI_MODE` = `full|minimal`
  - If unset: defaults to `full` when `GPU_AVAILABLE=true`, otherwise `minimal`
- LLM performance:
  - `LOCAL_LLM_THREADS`, `LOCAL_LLM_CONTEXT`, `LOCAL_LLM_BATCH`, `LOCAL_LLM_MAX_TOKENS`
  - `LOCAL_LLM_TEMPERATURE`, `LOCAL_LLM_TOP_P`, `LOCAL_LLM_REPEAT_PENALTY`
  - `LOCAL_LLM_GPU_LAYERS` (`0` CPU, `-1` auto, `N` explicit)
  - `LOCAL_LLM_GPU_LAYERS_AUTO_DEFAULT` (auto-mode fallback target)
  - `LOCAL_LLM_USE_MLOCK`
  - `LOCAL_LLM_CHAT_FORMAT`
  - `LOCAL_LLM_SYSTEM_PROMPT`, `LOCAL_LLM_VOICE_PREAMBLE`, `LOCAL_LLM_STOP_TOKENS`
  - `LOCAL_LLM_INFER_TIMEOUT_SEC` (default `20.0`)
- Tool gateway:
  - `LOCAL_TOOL_GATEWAY_ENABLED` (default `1`)
- STT:
  - Backend select: `LOCAL_STT_BACKEND`
  - Sherpa: `SHERPA_MODEL_PATH`, `SHERPA_MODEL_TYPE`, `SHERPA_VAD_MODEL_PATH`
  - T-one: `TONE_MODEL_PATH`, `TONE_DECODER_TYPE`, `TONE_KENLM_PATH`
  - Sherpa offline tuning:
    - `SHERPA_VAD_THRESHOLD` (default `0.35`)
    - `SHERPA_VAD_MIN_SILENCE_MS` (default `700`)
    - `SHERPA_VAD_MIN_SPEECH_MS` (default `200`)
    - `SHERPA_OFFLINE_PREROLL_MS` (default `350`)
    - `SHERPA_OFFLINE_DEBUG_SEGMENTS` (default `false`; debug only)
  - Whisper.cpp: `WHISPER_CPP_MODEL_PATH` (legacy alias: `LOCAL_WHISPER_CPP_MODEL_PATH`), `WHISPER_CPP_LANGUAGE`
  - Faster-Whisper: `FASTER_WHISPER_MODEL`, `FASTER_WHISPER_DEVICE`, `FASTER_WHISPER_COMPUTE_TYPE`, `FASTER_WHISPER_LANGUAGE`
  - Kroko: `KROKO_EMBEDDED`, `KROKO_MODEL_PATH`, `KROKO_PORT`, `KROKO_URL`, `KROKO_API_KEY`, `KROKO_LANGUAGE`
  - Idle promote: `LOCAL_STT_IDLE_MS` (legacy alias: `LOCAL_STT_IDLE_TIMEOUT_MS`, default `5000`)
  - Whisper utterance segmentation (used for `faster_whisper` and `whisper_cpp`):
    - `LOCAL_STT_SEGMENT_ENERGY_THRESHOLD` (default `1200`)
    - `LOCAL_STT_SEGMENT_PREROLL_MS` (default `200`)
    - `LOCAL_STT_SEGMENT_MIN_MS` (default `250`)
    - `LOCAL_STT_SEGMENT_SILENCE_MS` (default `500`)
    - `LOCAL_STT_SEGMENT_MAX_MS` (default `12000`)
- TTS:
  - Backend select: `LOCAL_TTS_BACKEND`
  - Kokoro: `KOKORO_MODE`, `KOKORO_VOICE`, `KOKORO_LANG`, `KOKORO_MODEL_PATH`, `KOKORO_API_BASE_URL`, `KOKORO_API_KEY`, `KOKORO_API_MODEL`
  - MeloTTS: `MELOTTS_VOICE`, `MELOTTS_DEVICE`, `MELOTTS_SPEED`
- Resilience/testing:
  - `LOCAL_AI_MOCK_MODELS=1` (skip real model loading)
  - `LOCAL_AI_FAIL_FAST=1` (abort startup on model load failures instead of degraded mode)
- Logging: `LOCAL_LOG_LEVEL` (default `INFO`)

Engine-side (see `config/ai-agent.*.yaml` and `.env.example`):

- `providers.local.base_url` / `providers.local*.ws_url` (default `${LOCAL_WS_URL:-ws://127.0.0.1:8765}`)
- `providers.local*.auth_token` (default `${LOCAL_WS_AUTH_TOKEN:-}`)
- Timeouts: `${LOCAL_WS_CONNECT_TIMEOUT}`, `${LOCAL_WS_RESPONSE_TIMEOUT}`
- Chunk size (ms): `${LOCAL_WS_CHUNK_MS}`

Dependencies:

- sox (used for resampling and μ-law conversion). The container image includes it; if running outside Docker ensure `sox` is installed.

---

## Expected Event Order (Full Pipeline)

For a single request_id and continuous audio segment in `full` mode:

1. `stt_result` (0..N partial)
2. `stt_result` (1 final)
3. `llm_response`
4. (optional) `tts_audio` metadata (only if `request_id` was provided on the input)
5. Binary μ-law audio bytes (8 kHz)

Duplicate/empty finals are suppressed; see `_handle_final_transcript()` for details.

For Whisper-family STT backends (`faster_whisper`, `whisper_cpp`), STT is temporarily suppressed while server-generated TTS audio is playing; send `barge_in` to clear suppression immediately when caller interruption is detected.

---

## Error Responses

Current implementation does **not** emit a generic `{"type":"error"}` envelope for all failures.

Error behavior is component/message specific:

- Auth failures: `auth_response` with `status=error`.
- Switch failures: `switch_response` with `status=error`.
- STT unavailable during audio processing: final `stt_result` with `text=""` and `error="stt_unavailable"`.
- LLM timeout/error during `llm`/`full`: fallback text in `llm_response` (connection stays alive).
- Invalid JSON / unknown message types: logged and ignored (no response frame).

---

## Common Issues and Resolutions

- STT returns empty often
  - Cause: utterances too short or speech energy below threshold. For Whisper-family STT, lower `LOCAL_STT_SEGMENT_ENERGY_THRESHOLD` (for example `1200` → `800`) and verify `LOCAL_STT_SEGMENT_SILENCE_MS`.
- Transcript contains only punctuation (`?`, `.`)
  - Current behavior: punctuation-only finals are treated as non-linguistic and suppressed in `llm`/`full` mode to avoid feedback loops.
- No TTS audio received
  - For `tts_request`, the response is JSON `tts_response` containing `audio_data` (base64 μ-law @ 8 kHz).
  - For `full` mode, the server sends a binary WebSocket frame containing μ-law bytes (and may also send `tts_audio` metadata if `request_id` was provided).
- LLM timeout (slow responses)
  - Increase `LOCAL_LLM_INFER_TIMEOUT_SEC`; reduce `LOCAL_LLM_MAX_TOKENS`; use faster model or fewer threads context.
- Model load failures
  - Check paths: `LOCAL_*_MODEL_PATH`; run `make model-setup`; verify models exist inside the container.
- Resample or μ-law conversion errors
  - Ensure `sox` is installed in the environment. Logs will show conversion failures.
- Mode mismatch warnings
  - Sending audio with `mode=tts` is ignored. Use `tts_request` (text in) for TTS.
- High memory usage
  - Lower `LOCAL_LLM_CONTEXT`, `LOCAL_LLM_BATCH`; tune threads; consider a smaller model.

---

## Performance Characteristics

Latency depends heavily on backend/model choices and whether GPU is used.

### Observed on modern GPU hosts (project community tests)

- Host profile: RTX 4090 (24GB), high-core CPU, Docker GPU stack.
- Example stack: `whisper_cpp` + `Llama-3.1-8B Q4_K_M` + `Kokoro local`.
- Observed (2026-02-27 test matrix):
  - End-to-end turn latency: ~449 ms
  - LLM latency: ~128 ms average (11 samples, last=49 ms)

### Practical tuning notes

- Whisper-family STT (`faster_whisper`, `whisper_cpp`) now uses utterance segmentation to improve coherence; tune segmentation thresholds before changing models.
- `LOCAL_LLM_GPU_LAYERS=-1` with `LOCAL_LLM_GPU_LAYERS_AUTO_DEFAULT` gives stable offload defaults by VRAM class.
- Keep `LOCAL_LLM_CONTEXT` moderate (for example 2048) for telephony; larger contexts increase latency and memory pressure.
- Prefer `kokoro` local or `piper` for predictable low-latency local deployment.

### Concurrency

- Bottleneck is usually LLM inference throughput and TTS generation under overlap.
- For predictable production, scale by running multiple local-ai-server instances and sharding calls at the engine/orchestrator layer.

---

## Versioning and Compatibility

- Message types and fields in this document map to current implementation in `local_ai_server/ws_protocol.py` + `local_ai_server/server.py`.
- `type` normalization (`-` and `_` interchange) is backward compatible for existing clients.
- Backward-compatible env aliases are supported in config loading:
  - `LOCAL_WHISPER_CPP_MODEL_PATH` → `WHISPER_CPP_MODEL_PATH`
  - `LOCAL_STT_IDLE_TIMEOUT_MS` → `LOCAL_STT_IDLE_MS`
- The engine's local provider uses the same WS contract for local pipeline transports defined in `config/ai-agent.*.yaml`.
