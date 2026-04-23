# Changelog

All notable changes to the Asterisk AI Voice Agent project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Google Live voice picker — full 30-voice catalog (#349)**: Expanded the `google_live` TTS voice picker in the admin UI from 8 hardcoded voices (split arbitrarily into Female/Male) to the full 30-voice catalog that Gemini native-audio Live models support. Voices are labeled with Google's official tone descriptors (Bright, Firm, Smooth, Warm, etc.) from the [speech-generation docs](https://ai.google.dev/gemini-api/docs/speech-generation), listed alphabetically. Female/Male grouping dropped since Google does not publish gender metadata for these voices. Help text updated ("24 languages" → "70+ languages") to match Google's current multilingual capability. Default voice `Aoede` preserved; no backend changes — the `google_live` provider already accepts any string, so all 22 new voices work with existing operator YAML configs unchanged.

### Fixed

- **Google Live voice picker data-loss footgun (#349)**: Voice picker now renders a "Custom" optgroup for YAML-configured voices outside the 30-voice Gemini catalog (e.g. a new Google voice before we ship a UI update), mirroring the existing pattern on the model picker. Previously, a controlled `<select>` with a value not matching any `<option>` fell through to browser-default rendering (first option selected visually while React state held the configured value), so operators "fixing" the mismatched display silently overwrote their YAML-set custom voice on save.

### Planned

- Additional provider integrations
- Enhanced monitoring features

## [6.4.1] - 2026-04-09

### Added

- **CPU-Only Latency Optimization — Streaming LLM→TTS Overlap (#301)**: Reduces perceived response latency from 3-10s to sub-2s on pipeline configurations by overlapping LLM token generation with per-sentence TTS synthesis. Instead of waiting for the full LLM response before starting TTS, tokens are streamed and split at sentence boundaries (`.!?`), with each sentence synthesized and played immediately. Includes multi-chunk TTS protocol extension (`utterance_id`, `chunk_index`, `is_final` metadata fields — backward compatible), `supports_streaming` capability flag on LLM adapters (currently OpenAI-compatible only), and per-call tool state isolation for concurrent streaming sessions. Tested and validated on voiprnd with Google Live, Deepgram, OpenAI Realtime, and Local Hybrid (Qwen3-30B + Piper) pipelines.
- **Pipeline Filler Audio**: Play a brief acknowledgment phrase (e.g. "One moment please.") via the pipeline's TTS adapter immediately when a user turn is detected, before LLM inference starts. Provides instant perceived responsiveness. Filler uses the same TTS voice as real responses (Piper/Kokoro/etc.), not eSpeak. Configurable via `streaming.pipeline_filler_enabled` and `streaming.pipeline_filler_phrases` in `ai-agent.yaml`. Admin UI toggle on Streaming page.
- **Direct PCM→µ-law Conversion**: `pcm16_to_ulaw_8k()` static method in `AudioProcessor` bypasses WAV file roundtrip in all 5 TTS backends (Piper, Kokoro, MeloTTS, Silero, Matcha). Saves 10-50ms per TTS call by eliminating disk I/O.
- **eSpeak NG Backend**: New `EspeakNGBackend` class in `tts_backends.py` for ultra-fast filler phrase synthesis. Uses `espeak-ng --stdout` with in-memory WAV parsing (no temp files, no sox subprocess). Falls back gracefully when eSpeak is unavailable.
- **TTS Phrase Cache**: Optional in-memory cache for repeated short phrases (greetings, confirmations). LRU eviction at 256 entries, cache key includes backend + model path + voice/speaker params. Cleared on `reload_models()`. Enable via `LOCAL_TTS_PHRASE_CACHE_ENABLED=true` env var.
- **Qwen 2.5-1.5B Instruct as Recommended CPU LLM**: New model catalog entry (`qwen25_1_5b`, 940MB Q4_K_M). Achieves ~15-30 tok/s on CPU vs Phi-3's ~0.8 tok/s. Reliable `hangup_call` tool execution via heuristic parsing. ChatML format. Setup Wizard auto-recommends for CPU-only deployments with "⚡ CPU Recommended" badge. Default LLM path updated in `config.py`. `model_setup.sh` updated for light/medium CPU tiers (Phi-3 and TinyLlama kept as fallbacks).
- **LLM Streaming in Local AI Server**: `process_llm_chat_streaming()` async generator yields tokens from `llama-cpp-python`'s `create_chat_completion(stream=True)` via asyncio.Queue + thread executor. Idle timeout prevents `_llm_lock` from being held indefinitely. Used by `_process_full_pipeline_streaming()` for sentence-by-sentence TTS in full mode.
- **OpenAI LLM Adapter Streaming**: `generate_stream()` on `OpenAILLMAdapter` streams tokens via SSE with tool call accumulation from `delta.tool_calls`. Falls back to non-streaming for realtime transport mode.
- **Admin UI Latency Settings**: New "Latency Optimization" section on Streaming page with toggles for pipeline streaming overlap, pipeline filler audio, and filler phrase configuration. Max latency display added to Call History detail view.

### Fixed

- **Barge-in config regression**: Barge-in thresholds were accidentally hardened globally (`energy_threshold` 1000→3000, `initial_protection_ms` 200→1500, etc.) affecting all providers. Reverted to responsive pre-branch values.
- **Config defaults for host networking**: Reverted `advertise_host: ai_engine` and `ws_url: ws://local_ai_server:8765` back to `127.0.0.1` / `${LOCAL_WS_URL:-ws://127.0.0.1:8765}`. Docker service names only resolve in bridge networking; host networking (the default) requires `127.0.0.1`. This caused "Allocation failed" on AudioSocket originate for all users on host networking.
- **Filler audio slot conflict**: Filler audio occupied the streaming playback slot, blocking the real LLM→TTS streaming session. Fixed by calling `stop_streaming_playback` after filler completes, with `tts_ended_ts` backdated to avoid the post-TTS echo protection dead zone.
- **Tool calls lost during streaming**: When the LLM returned both text and tool calls (e.g. farewell + `hangup_call`), `generate_stream()` consumed the text but tool call deltas were silently dropped. Agent would say goodbye but never hang up. Fixed by accumulating `delta.tool_calls` from SSE chunks per-call and executing after playback.
- **Streaming capability detection**: `hasattr(adapter, "generate_stream")` was always true because the base `LLMComponent` defines a default. Replaced with `supports_streaming` class attribute. Non-streaming adapters (local_llm, ollama) now correctly use the serial path.
- **TTS phrase cache non-functional UI toggle**: The StreamingPage toggle wrote to `config.local_ai_server.*` in YAML, but `local_ai_server` reads from `LOCAL_TTS_PHRASE_CACHE` env var. Removed broken toggle (setting managed via `.env`).
- **pcm16_to_ulaw_8k fallback data corruption**: If `audioop.ratecv` succeeded but `lin2ulaw` failed, the fallback WAV wrapper used the already-resampled data with the original sample rate. Now preserves original PCM data for fallback.

### Improved

- **Preflight hardening**: 8 gaps addressed:
  - CRITICAL: GPU nvidia-container-toolkit install now gated behind `--apply-fixes` (previously ran `apt-get install` + `systemctl restart docker` unconditionally, killing running containers on re-run)
  - Docker Buildx missing now detected as `log_fail` with auto-fix command (was silently skipped — root cause of "Docker Compose requires buildx plugin" errors)
  - Buildx version regex uses portable `grep -oE` instead of GNU-only `grep -oP`
  - Port checks expanded: 3003 (Admin UI), 8090 (AudioSocket), 15000 (Health), 18080 (ExternalMedia RTP)
  - System resource checks: RAM (4GB fail / 8GB warn), disk space (10GB warning on models mount)
  - Network connectivity check for huggingface.co before model downloads
  - `HOST_PROJECT_ROOT` validated and auto-seeded in `.env` for Admin UI container management
  - GPU passthrough test cached for 60 minutes via marker file (skips ~200MB CUDA image pull on re-run)
- **Reduced jitter buffer warmup**: `streaming.min_start_ms` from 120ms to 60ms for faster first audio across all providers.
- **Reduced Whisper STT buffer**: 1s to 500ms for faster transcript delivery with Whisper backends.
- **Whisper echo-guard multi-chunk support**: `_arm_whisper_stt_suppression()` stacks chunk durations for streaming responses instead of resetting per-chunk. Barge-in clears suppression immediately via `_clear_whisper_stt_suppression`.
- **Semantic front-loading prompt**: Streaming pipeline appends instruction to begin responses with a brief acknowledgment clause, ensuring the first TTS chunk is meaningful.
- **TTS cache key includes voice/speaker params**: Prevents serving stale audio after voice configuration changes. Cache cleared on `reload_models()`.
- **Whisper first-run model download exceeds startup polling timeout (#299)**: The wizard's server-readiness polling loop used a hardcoded 2-minute timeout for all non-build starts. First-run HuggingFace model downloads (e.g. `distil-large-v3`, `turbo`, `large-v3`) take 3+ minutes on typical connections, causing a false "Polling timed out" error even though the server came up successfully. Directly related to the #297 fix — correcting the turbo model path now causes the download to actually proceed, making this timeout reliably hit. Fix: backend `/api/wizard/local/server-logs` now detects HuggingFace download activity in container logs and returns a `downloading: true` flag; frontend polling loop bumps its ceiling from 2 minutes to 10 minutes on first detection and shows a "⬇️ Downloading model from HuggingFace, please wait…" status message.
- **Sherpa offline transducer crash on startup (#296)**: Wizard never wrote `SHERPA_MODEL_TYPE` to `.env`, so offline transducer models were loaded by the online (streaming) recognizer (`SherpaONNXSTTBackend`). The online recognizer calls `OnlineRecognizer.from_transducer()` against offline model files that lack the `encoder_dims` metadata field, causing an immediate crash and Docker restart loop. Fix: added `model_type` field (`"online"` / `"offline"`) to all `SHERPA_STT_MODELS` catalog entries and updated both wizard code paths (`download_selected_models` and `save_setup_config`) to emit `SHERPA_MODEL_TYPE` alongside `SHERPA_MODEL_PATH`.
- **Whisper Large v3 Turbo STT model init failure (#297)**: `FASTER_WHISPER_STT_MODELS` listed `model_path: "large-v3-turbo"` but `faster-whisper` 1.0.3 (our pinned version) does not recognize `"turbo"` or `"large-v3-turbo"` as valid model size shorthands — turbo support was only added in `faster-whisper` 1.1.0. Fix: changed `model_path` to the HuggingFace repo ID `"deepdml/faster-whisper-large-v3-turbo-ct2"`, which `faster-whisper` 1.0.3 accepts directly (any `owner/repo` string bypasses the model size enum and downloads via `snapshot_download`). Also added `download_root` to persist downloaded models under the bind-mounted volume (`/app/models/stt/faster_whisper_cache`) so they survive container rebuilds.
- **AI briefing always falls back to Basic TTS on non-English deployments (#292)**: Two root causes identified from debug logs. (1) The Local AI Server was running in `LOCAL_AI_MODE=minimal`, causing the LLM handler to return a hardcoded fallback string (`"I'm here to help you. How can I assist you today?"`) that the sanitizer correctly rejected as unusable. (2) Even with a working LLM, the briefing prompt contained no language instruction, so English-language summaries were synthesized by non-English TTS voices (e.g. German Piper), producing garbled audio for the receiving agent. Fix: added `ai_briefing_language` config field (`tools.attended_transfer.ai_briefing_language`); when set, the instruction `"Write the briefing in {language}."` is injected into the LLM prompt. Exposed as a new **Briefing Language** text field in the Admin UI AI Briefing section (shown only when `screening_mode == ai_briefing`). Blank = English (existing behaviour unchanged).
- **Upgrade fails with "No such image: local-ai-server:latest" (#293)**: When upgrading across a release that added or changed `local_ai_server/` source files, the CLI update pipeline marked `local_ai_server` for rebuild via auto file-change detection. Two code paths then tried to start it using `--no-build`, failing immediately on deployments that never built the Local AI image. The `run.sh` rollback path compounded the problem by hardcoding `local_ai_server` in compose-changed targets unconditionally. Fix: (1) compose-changed `--no-build` step now restricts targets to currently-running services only; (2) `rebuildServices` list filtered to running services, matching the existing guard on `restartServices`; (3) `run.sh` rollback path queries running services dynamically instead of hardcoding service names.

## [6.4.0] - 2026-03-28

### Added

- **Attended Transfer Streaming & Screening (#283)**: Advanced attended (warm) transfer system with three screening modes — `basic_tts` (caller ID + context announcement), `ai_briefing` (experimental AI-written conversation summary via Local AI Server LLM), and `caller_recording` (records caller stating name/reason, plays clip to agent). New streaming delivery mode uses ExternalMedia RTP helper to avoid shared storage dependency, with automatic fallback to file playback. Provider-agnostic runtime tool guidance dynamically exposes configured transfer targets and extension inventories to LLM providers. Live Agents UI redesigned with compact flex layout, auto-polling for agent availability, and conditional Admin UI fields for each screening mode. Includes telephony tools surface audit with deprecation plan for legacy paths.
- **Sherpa Offline STT with VAD (#286)**: VAD-gated offline transducer mode for Sherpa-ONNX. Uses Silero VAD model for per-session voice activity detection with configurable thresholds (`SHERPA_VAD_THRESHOLD`, `SHERPA_VAD_MIN_SILENCE_MS`, `SHERPA_VAD_MIN_SPEECH_MS`). Includes preroll padding to avoid clipped utterance prefixes (`SHERPA_OFFLINE_PREROLL_MS`), streaming-vs-offline model auto-detection with validation, and optional debug diagnostics (`SHERPA_OFFLINE_DEBUG_SEGMENTS`). Set `SHERPA_MODEL_TYPE=offline` to enable.
- **T-one STT Backend (#286)**: Native Russian telephony ASR using the T-one streaming CTC pipeline. Supports beam search and greedy decoding with optional kenlm language model. 300ms chunk framing with internal 16→8kHz handling. Requires conditional Docker build: `docker compose build --build-arg INCLUDE_TONE=true`. Contributed by [@octo-patch](https://github.com/octo-patch).
- **Silero TTS Backend (#286)**: Multi-language text-to-speech with native 8kHz telephony output (no resampling needed). Supports 6 languages (ru, en, de, es, fr, ua) with multiple speaker voices (xenia, aidar, baya, kseniya, eugene for Russian). Model variants v3_0_ru and v3_1_ru. Requires conditional Docker build: `docker compose build --build-arg INCLUDE_SILERO=true`. Contributed by [@octo-patch](https://github.com/octo-patch).
- **Fullscreen toggle for UI panels (#278, #280)**: New `FullscreenPanel` reusable component with `Maximize2`/`Minimize2` toggle, portal-based fullscreen overlay at `z-40`, ref-counted body scroll lock, and Escape key support. Applied to Live System Topology (Dashboard), Call Statistics grid, and Call History table with pagination.
- **Per-message conversation timestamps (#277, #280)**: All 12 `conversation_history.append()` sites in engine now include `time.time()` epoch seconds via `_ts_msg()` helper. Frontend Call Log UI displays human-readable timestamps with epoch-to-millisecond coercion. Added `_sanitize_for_llm()` to strip non-standard keys before sending to LLM adapters, preventing 400/422 rejections on strict OpenAI-compatible providers.

### Fixed

- **HTTP tool output variable array extraction (#281, #282)**: JSONPath wildcards like `records[*].fields.name` now correctly return arrays instead of empty strings. Extracted duplicated `_extract_path` logic into shared `path_utils.extract_path()` with support for `[*]` wildcards, bare `[0]`/`[*]` on root arrays, hyphenated field names (e.g. `line-items[*].sku`), and explicit null-vs-missing semantics. Array/dict results are now JSON-serialized via `json.dumps` instead of Python `str()`. Added `"data"` to sanitizer `keep_keys` so extracted variables reach OpenAI/Deepgram providers, with progressive byte-cap enforcement (drop `result` → `data` → binary-search trim `message`). Fixed `_build_result_message` dropping falsy scalars (`0`, `False`).
- **Silero TTS initialization (#286)**: Fixed torch.hub trust prompt blocking startup, synthetic dropdown value leaking into model path, status path mismatch with dropdown format, and hot-switch backend allowlist.
- **Sherpa offline VAD use-after-free (#286)**: Fixed crash where VAD segment samples were referenced after `vad.pop()` freed the buffer — now copies samples before pop.
- **T-one server numpy import (#286)**: Fixed numpy import compatibility in T-one backend initialization.
- **Trivy CI action**: Updated `aquasecurity/trivy-action` from non-existent `0.33.1` tag to `0.35.0`.

### Improved

- **Attended transfer tool descriptions**: Refreshed tool catalog transfer descriptions for clarity. Extension status checks restricted to configured targets for safety. Explicit target support added to live agent transfer.
- **Admin UI Live Agents**: Compact layout with actions moved to first row. Dynamic auto-polling for agent availability. Conditional display of hangup expert settings when disabled. Fixed in-call HTTP tool discovery.
- **Docker conditional builds**: New `INCLUDE_TONE` and `INCLUDE_SILERO` build args for optional backend inclusion without bloating default image size. Silero models pinned to v5.5 tag for reproducible builds.
- **Sherpa offline tuning knobs**: Documented optimal VAD parameters for telephony use cases in `docs/LOCAL_ONLY_SETUP.md`.

## [6.3.2] - 2026-03-12

### Added

- **Microsoft Azure Speech Service STT & TTS**: Full modular pipeline support with three adapters — `AzureSTTFastAdapter` (REST batch transcription), `AzureSTTRealtimeAdapter` (WebSocket streaming with VAD), and `AzureTTSAdapter` (SSML synthesis with streaming and non-streaming modes). Includes Admin UI forms, quick-add templates, security key injection, A-law/μ-law passthrough, and validated config in `ai-agent.yaml`. Contributed by [@egorky](https://github.com/egorky).
- **MiniMax LLM Pipeline Adapter**: New LLM provider supporting MiniMax M2.7 models (latest flagship with enhanced reasoning and coding) via OpenAI-compatible API. Includes tool-calling support, Admin UI integration, and full test suite. Contributed by [@octo-patch](https://github.com/octo-patch).
- **Call Recording Playback**: Play back Asterisk/FreePBX call recordings directly from the Call Details modal in the Admin UI. Recordings are auto-matched by channel unique ID from the monitor directory (`YYYY/MM/DD/` layout). Includes play/pause controls, seek bar, time display, filename, and file size. Empty WAV files (header-only) are shown as "no audio captured". Configurable via `ASTERISK_RECORDING_PATH` env var.
- **Google Calendar delete()**: Full delete event implementation with timezone handling fixes and code quality improvements. Contributed by [@gcsuri](https://github.com/gcsuri).

### Fixed

- **OpenAI Realtime farewell timeout**: Cancel farewell timeout before emitting HangupReady to prevent race condition on call termination.
- **Greeting protection duration**: Increased to 5000ms for more reliable greeting playback on slower connections.
- **Local AI setup wizard**: Show "Setting Up Local AI Server" until server is actually ready; improved build log filtering for progress tracking.
- **NVIDIA GPU setup**: Auto-install nvidia-container-toolkit and improved wizard progress tracking.

### Security

- **Azure SSRF prevention**: Shared `validate_azure_region()` regex validator applied across Pydantic config models, runtime URL builders, and admin API endpoint.
- **PII logging discipline**: Removed transcript/text preview strings from all log statements across engine, Azure, and OpenAI adapters — logs now contain metadata only (length, role, flags).
- **Azure key injection**: Type-based detection (`cfg_type == "azure"`) for custom-named Azure providers, not just name-prefix matching.
- **Input validation hardening**: LLM aggregation thresholds clamped with try/except and min=1 guards in both Python backend and React frontend.

### Improved

- **Azure STT bounded pre-speech buffer**: Audio buffer only accumulates when speech is detected or speaking is active, preventing unbounded memory growth during long silences.
- **Azure VAD timeout propagation**: `vad_silence_timeout_ms` and `vad_initial_silence_timeout_ms` now properly passed through `_compose_options`.
- **Azure variant validation**: Constrained to `Literal["realtime", "fast"]` across config model, orchestrator, and Admin UI (select dropdown instead of free text).
- **PEP 563 deferred annotations**: `from __future__ import annotations` in azure.py enables safe optional-dependency type hints when aiohttp is not installed.

## [6.3.1] - 2026-02-23

### Added

- **CLI verification tooling**: `agent check --local/--remote` for Local AI Server + Asterisk/Admin UI validation; `agent rca --local` for automated local test report generation and Community Test Matrix submissions.
- **Community Test Matrix**: Added `docs/COMMUNITY_TEST_MATRIX.md` and a GitHub issue template for standardized local AI test result reporting.
- **Admin UI backend enable/rebuild flow**: One-click backend enable with progress tracking for optional Local AI Server backends (e.g., Faster-Whisper, Whisper.cpp, MeloTTS).
- **Local AI Server WS protocol contract**: JSON-schema-based protocol contract + smoke test utilities to stabilize client/server evolution.

### Improved

- **CPU-first onboarding**: Local AI Server defaults to `runtime_mode=minimal` when preflight reports no GPU (`GPU_AVAILABLE=false`) so CPU-only systems start reliably without LLM model files.
- **GPU ergonomics**: GPU layer auto-detection for `LOCAL_LLM_GPU_LAYERS=-1`, preflight warnings for GPU-detected-but-CPU-configured LLM runs, and GPU compose overlay improvements for out-of-the-box backend switching.
- **Model lifecycle UX**: Expanded model catalog metadata, clearer rebuild requirements, and improved model switching + status reflection (including Kroko embedded inference and Kokoro settings alignment).

### Fixed

- **Local provider audio + barge-in stability**: Robust barge-in protocol handling, improved gating to avoid STT/TTS talk-loops, and stabilized timing/latency tracking for local calls.
- **Tool-call parsing robustness**: Hardened local tool-call extraction against malformed wrappers/markdown/control-token leaks; improved clean-text extraction to prevent tool syntax from reaching TTS.
- **Hangup correctness**: Added/strengthened hangup guardrails for local LLMs to prevent hallucinated end-call tool executions; improved farewell handling to avoid spoken tool chatter.
- **MeloTTS reliability**: Fixed build/runtime pinning, warmup behavior, and upstream install regressions to restore stable container rebuilds.
- **AAVA-200 — Cosmetic UI bug at end of local full setup**: Fixed "No such container" error displayed at completion of the local AI setup wizard flow.
- **AAVA-199 — Transfer tool enabling error on fresh install**: Fixed `TypeError: Cannot read properties of null` when enabling the transfer tool on a fresh installation.
- **AAVA-193 — Setup Wizard local_ai_server start/logs fails**: Fixed compose execution context issue where the wizard's start and log-streaming commands failed due to incorrect working directory.
- **AAVA-195 — Kroko embedded selection when binary unavailable**: Admin UI now gates Kroko embedded mode behind capability detection across Wizard, Env page, and model selectors; added rebuild hints for all optional backends (Faster-Whisper, Whisper.cpp, MeloTTS) and added missing Whisper.cpp backend to Env page STT dropdown.

### Refactored

- **Local AI Server internals**: Consolidated and hardened model loading, status reporting, and model switching paths with degraded-start behavior and operator-visible fallbacks.

### Guardrails

- **Structured local tool gateway**: Allowlist-driven tool execution path with repair/structured-decision fallbacks; explicit blocking of unsafe terminal tools without user intent (especially `hangup_call`).

### Performance

- **LLM startup tuning**: Improved default context sizing on GPU, optional auto-context selection/caching, and startup warmup/latency instrumentation for operator visibility.

### Security

- **Concurrent rebuild race condition**: Fixed TOCTOU race on `_active_rebuild` flag — atomic check-and-set under lock prevents duplicate Docker builds.
- **Safe archive extraction**: Zip and tar `extractall` calls now pass validated member lists to prevent path traversal via crafted archives (zip-slip).
- **GGUF magic-byte validation**: Downloaded `.gguf` model files are validated for the `GGUF` magic header before being accepted; corrupt or non-GGUF files are cleaned up automatically.
- **Active-call guard on model switch**: `/api/local-ai/switch` blocks model switches while AI Engine reports active calls (override via `force_incompatible_apply`); unreachable engine treated as blocked.
- **Path traversal hardening**: All model download paths, voice file names, and custom LLM model paths are sanitized via `_safe_filename` and `_safe_join_under_dir` helpers; strict filename validation rejects directory traversal patterns.

### Docs

- **Local AI onboarding docs refresh**: Updated installation + local-only setup guides, hardware requirements, local profiles, and CLI tools guide; added archived GPU learnings + audit report for GA readiness tracking.

## [6.2.2] - 2026-02-20

### Fixed

- **Vertex AI Credentials Not Found**: Auto-inject `GOOGLE_APPLICATION_CREDENTIALS` env var when the service account JSON file exists at the default mount path (`/app/project/secrets/gcp-service-account.json`). Handles 3 cases: env var unset, env var pointing to missing file (override), and stale pointer with no fallback (unset to prevent ADC crash).
- **Vertex AI ADC Graceful Fallback**: When `use_vertex_ai: true` but ADC fails (e.g. no service account uploaded), the Google Live provider now falls back to Developer API (api_key) mode instead of crashing the call with `DefaultCredentialsError`. Consistent `_vertex_active` instance flag ensures model path, tool responses, and connection URL all use the correct API format after fallback.
- **Secrets Directory Permissions**: `setup_secrets_directory()` in `install.sh` now always fixes ownership to UID 1000 (appuser) with mode 2770, not just on creation. Fixes "Permission denied" when uploading Vertex AI credentials via Admin UI (backend runs as appuser via gosu).
- **False "Apply Changes" for local_ai_server** (AAVA-192 related): Environment page no longer shows restart prompts for containers that aren't running — prevents confusing drift detection when `local_ai_server` is intentionally stopped.
- **AAVA-192 — install.sh Duplicate YAML Keys**: Fallback path in `update_yaml_llm()` no longer blindly appends a duplicate `llm:` block. Uses Python/PyYAML or sed-based in-place update when the block already exists.
- **AAVA-185 — Dashboard Pipeline Variant Display**: Wizard now sets `active_pipeline` and `default_provider` to the variant-specific name (e.g. `local_hybrid_groq`) when Groq LLM is selected. Dashboard topology adds defensive variant matching for backward compatibility.

### Added

- **Secrets Directory in install.sh**: `setup_secrets_directory()` creates `./secrets/` with correct permissions (2770) during installation, aligning with `preflight.sh`.
- **COMPOSE_PROJECT_NAME Auto-set**: `install.sh` now ensures `COMPOSE_PROJECT_NAME=asterisk-ai-voice-agent` is set in `.env` for consistency with `preflight.sh`.
- **Auto-upsert GOOGLE_APPLICATION_CREDENTIALS on Upload**: When Vertex AI credentials are uploaded via Admin UI, the env var is automatically added to `.env` for persistence across container recreates.

### Migration from v6.2.1

1. **No breaking changes.** All fixes are backward compatible.
2. **Vertex AI users**: Credentials will be auto-detected on next container restart — no manual `.env` editing needed.
3. **Docker rebuild required**: Run `docker compose up -d --build --force-recreate` to pick up all fixes.

## [6.2.1] - 2026-02-20

### Added

- **Google Vertex AI Live API Support**: Enterprise-grade authentication for Google Live API using GCP service accounts. Switch between Developer API (API key) and Vertex AI (OAuth2) modes via Admin UI toggle. Includes credential upload/verify/delete endpoints, environment injection, and preflight secrets directory validation ([PR #235](https://github.com/hkjarral/Asterisk-AI-Voice-Agent/pull/235)).
- **Admin UI Vertex AI Configuration**: New Vertex AI section in Google Live provider settings with project/location selectors, credential upload widget, and real-time verification status.
- **Preflight Secrets Directory Check**: `preflight.sh` now validates and auto-creates `./secrets/` with correct ownership and permissions (2770) for service account JSON files.
- **Golden Baseline Config**: `config/ai-agent.golden-google-live.yaml` updated with Vertex AI configuration examples.

### Changed

- **Google Live Provider**: Dual-mode endpoint construction — Vertex AI uses `{location}-aiplatform.googleapis.com` with OAuth2 bearer tokens; Developer API unchanged.
- **Tool Response Format**: Removed `id` field from `functionResponses` when using Vertex AI (not supported by Vertex API).

### Fixed

- **Async Blocking Call**: `credentials.refresh()` now runs in thread pool to avoid blocking asyncio event loop.
- **Exception Leaking**: Vertex AI endpoints no longer expose internal error details to clients.
- **Null Filename Check**: `upload_vertex_credentials` validates `file.filename` before use.

### Documentation

- Milestone 25: Google Vertex AI Live API Support (`docs/contributing/milestones/milestone-25-google-vertex.md`)

### Migration from v6.2.0

1. **No breaking changes.** Vertex AI is opt-in; existing API key mode works unchanged.
2. **Vertex AI users**: Upload service account JSON via Admin UI → Google Live → Vertex AI Configuration.
3. **Docker rebuild required**: Run `docker compose up -d --build --force-recreate` for secrets volume mount.

## [6.2.0] - 2026-02-15

### Added

- **NumPy Audio Resampler**: Replaced legacy `audioop.ratecv` with NumPy linear interpolation at all 19 call sites, eliminating audio crackling artifacts. Community contribution by [@turgutguvercin](https://github.com/turgutguvercin) ([PR #204](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/pull/204)).
- **Google Native Audio Latest Model**: Support for `gemini-2.5-flash-native-audio-latest` — Google's audio-native model with true audio understanding, tuned defaults for telephony use cases.
- **Google Live VAD Tuning**: `realtimeInputConfig` support for short utterance detection; configurable via Admin UI advanced settings.
- **Google Live Keepalive Expert Knobs**: Smoother config updates and WebSocket keepalive tuning for long-running sessions.
- **Google Live Input Gain Normalization**: Provider-level input gain for consistent audio levels across telephony trunks.
- **Admin UI Tool Catalog**: Read-only page listing all available built-in and MCP tools with descriptions and parameter schemas (PR #211).
- **Admin UI Google Live Settings**: VAD tuning and hangup fallback tooltips exposed as advanced provider settings.
- **Agent CLI `check --fix`**: Auto-repair common configuration issues with minimal production baseline config for recovery; hardened restore logic (PR #210).
- **Telnyx AI Inference LLM**: New modular pipeline provider `telnyx_llm` using OpenAI-compatible Chat Completions via Telnyx AI Inference. Access 53+ models (GPT-4o, Claude, Llama, Mistral) with a single `TELNYX_API_KEY`. Includes golden baseline config, Admin UI provider form, and setup guide. Community contribution by Abhishek @ Telnyx ([PR #219](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/pull/219)).
- **Preflight `--force`**: Bypass unsupported OS check for exotic distributions.

### Changed

- **Google Live Farewell Handling**: Settled on farewell-play-out design (removed experimental auto-reconnect on 1008 disconnect); neutralized tool response to prevent duplicate farewells.
- **Google Live TTS Gating**: Per-segment re-arm so silence replaces echo on every turn; enabled on AudioSocket transport.
- **Call Ending Prompts**: Tightened prompts to prevent verbal farewell before `hangup_call` tool invocation across all providers.
- **Transparent Model Name Flow**: Removed silent fallback/remapping for Google Live model names; aligned names across UI and wizard.
- **Demo Contexts Restored**: All 11 demo contexts from v6.0.0 baseline restored.

### Fixed

- **Audio Crackling**: NumPy resampler fixes crackling caused by `audioop.ratecv` discontinuities ([PR #204](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/pull/204), [@turgutguvercin](https://github.com/turgutguvercin)).
- **Call Termination Hardening**: 13 fixes across providers, engine, and AudioSocket for reliable call endings and proper cleanup.
- **Google Live Duplicate Farewell**: 6+ iterations eliminating race conditions between tool-ack `turnComplete`, forced farewell, and model post-hangup speech.
- **Google Live Premature Hangup**: Fixed hangup firing on tool-acknowledgment `turnComplete` before farewell audio finishes playing.
- **Google Live Model Names**: Transparent model name flow — removed silent fallback/remapping; aligned names across UI and wizard.
- **Pipeline Call History**: Tool calls now recorded in session so they appear in Admin UI Call History.
- **Provider Topology Accuracy**: Clear stale `provider_name` on pipeline calls so UI topology is accurate.
- **Agent CLI Conflict Markers**: Reduced conflict-marker false positives in `agent check`.
- **Agent CLI Wizard Logger**: Removed invalid logger kwargs.
- **Agent CLI Restore Safety**: Hardened `check --fix` restore to avoid partial writes.
- **Admin UI Tool Catalog**: Removed unreachable except in tool catalog (PR #212).

### Security

- **CodeQL SSRF Fix**: Google API key now passed as params instead of URL path to prevent SSRF (CodeQL alert).

### Documentation

- Telnyx AI Inference Provider Setup Guide (`docs/Provider-Telnyx-Setup.md`)
- Milestones 23 (NAT) and 24 (Phase Tools) added to milestone history
- Complete documentation, roadmap, and community alignment overhaul
- Roadmap and Milestone History links added to main README hero nav bar

### Migration from v6.1.1

1. **No breaking changes.** All new features are additive or opt-in.
2. Existing `config/ai-agent.yaml` continues to work unchanged.
3. **Docker rebuild required**: Run `docker compose up -d --build --force-recreate` for all containers.
4. **Telnyx users**: Add `TELNYX_API_KEY` to `.env` and configure `telnyx_hybrid` pipeline. See `docs/Provider-Telnyx-Setup.md`.
5. **Google Live users**: Default model updated to `gemini-2.5-flash-native-audio-latest`. No action needed unless you pinned a specific model.

## [6.1.1] - 2026-02-09

### Added

- **Operator Config Override (`ai-agent.local.yaml`)**: Operator customizations are now stored in `config/ai-agent.local.yaml` (git-ignored), deep-merged on top of the base `config/ai-agent.yaml` at startup. All Admin UI saves, CLI wizard writes, and `agent setup` output target the local file, so upstream `git pull` never conflicts with operator config. Local overrides can delete base keys by setting them to `null`.
- **Graceful Stash Pop Recovery**: `agent update` now automatically recovers from `git stash pop` merge conflicts by resetting the working tree, dropping the failed stash, and restoring operator config (`.env`, `ai-agent.yaml`, `ai-agent.local.yaml`, `users.json`, `contexts/`) from the pre-update backup.
- **Live Agent Transfer Tool**: Explicit `live_agent_transfer` tool with ARI-based extension status checks, auto-derived internal extension keys, and fallback routing to configured live-agent destinations.
- **ARI Extension Status API**: Admin UI endpoint to query Asterisk device/endpoint state for live agent availability before transferring.
- **ViciDial Outbound Dialer Compatibility**: Configurable `AAVA_OUTBOUND_DIAL_CONTEXT`, `AAVA_OUTBOUND_DIAL_PREFIX`, `AAVA_OUTBOUND_CHANNEL_TECH`, and `AAVA_OUTBOUND_PBX_TYPE` (`freepbx`/`vicidial`/`generic`) for outbound campaign origination.
- **GPU Host/Runtime Indicators**: Runtime GPU probe details in local AI server status with CUDA guard for STT/TTS backend selection and CPU fallback when GPU is unavailable.
- **GPU-Aware Compatibility Checks**: Force rebuild flow for incompatible runtime/device combinations with `force_incompatible_apply` flag for intentional overrides.
- **Local-Hybrid Wizard Persistence**: Setup wizard correctly persists `local_hybrid` pipeline, local STT/TTS backend selections, and model mappings through env and YAML config.
- **Asterisk Config Discovery (Admin UI)**: New **System → Asterisk** page with live ARI connection status, required module checklist, configuration audit from preflight, and guided fix snippets. Dashboard pill shows Asterisk connection state (green/red) with click-through. Supports both local and remote Asterisk deployments.
- **Preflight Asterisk Config Audit**: `preflight.sh` now audits `ari.conf`, `http.conf`, `extensions_custom.conf`, and 4 key Asterisk modules (`app_audiosocket`, `res_ari`, `res_stasis`, `chan_pjsip`), writing results to `data/asterisk_status.json` for the Admin UI.

### Changed

- **Tool Name Canonicalization**: `transfer` is now an alias for `blind_transfer`. `live_agent` and `transfer_to_live_agent` are aliases for `live_agent_transfer`. Tool allowlisting uses `canonicalize_tool_name()` for alias-aware matching across contexts.
- **Admin UI Live Agent Section**: Status pills with real-time ARI checks, rebalanced row columns, destination override hidden behind Advanced toggle, internal extensions labeled as live agents.
- **Admin UI Docker Compose GPU Overlay**: `start`/`recreate`/`rebuild` actions for `local_ai_server` now include `docker-compose.gpu.yml` when `GPU_AVAILABLE=true`.

### Fixed

- **`blind_transfer` Destination Resolution**: Fixed numeric target resolution and cross-provider naming (`transfer` vs `blind_transfer`) in tool allowlists.
- **`live_agent_transfer` Fallback Routing**: Prevents mapping to non-live-agent destinations; falls back to explicit live-agent config or internal extensions.
- **Streaming `provider_grace_ms` Cap**: Reverted then re-fixed the 60ms hardcoded cap on `provider_grace_ms` that degraded streaming latency.
- **`check_extension_status` Opt-In**: Made the extension status tool opt-in; removed hardcoded transfer key examples from tool registry.
- **Admin UI Log Troubleshooting**: Exclude per-frame audio noise from log views; add milestone tracking markers.
- **Admin UI System API Indentation**: Fixed YAML indentation in system config API responses.
- **Environment Drift Detection**: Ignore compose-injected `HEALTH_BIND_HOST` (only track `HEALTH_CHECK_*` prefix) to prevent perpetual "pending restart" drift in Env UI.
- **Docker Image Metadata**: Handle missing Docker image metadata in containers API (`ImageNotFound` after prune/rebuild) without failing the endpoint.
- **GPU Compose Overlay Preservation**: Preserve GPU compose overlay for `local_ai_server` UI actions (start, recreate, rebuild) so GPU device requests are not silently dropped.
- **EnvPage GPU-Aware CUDA Options**: CUDA device option for Faster-Whisper and MeloTTS only shown when `GPU_AVAILABLE=true`.

### Documentation

- ViciDial Integration Guide (`docs/Vicidial-Setup.md`)
- Fixed `transfer_call` references to canonical `transfer` / `blind_transfer` in ElevenLabs milestone docs
- Updated 9 docs for `ai-agent.local.yaml` config override system and stash pop recovery procedures

### Migration from v6.0.0

1. **No breaking changes.** All new features are additive or opt-in.
2. Existing `config/ai-agent.yaml` continues to work unchanged. The new `ai-agent.local.yaml` is optional.
3. **Docker rebuild required**: Run `docker compose up -d --build --force-recreate` for all containers.
4. **ViciDial users**: Set `AAVA_OUTBOUND_PBX_TYPE=vicidial` in `.env` and configure dial context/prefix as needed.

## [6.0.0] - 2026-02-07

### ⚠️ Breaking Changes

- **OpenAI Realtime API version default changed to GA**: The default `api_version` is now `ga` (was `beta`). GA uses a nested audio schema (`audio.input.format` / `audio.output.format` with MIME types) instead of flat fields. Set `api_version: beta` explicitly to keep the old behavior.
- **Email template autoescaping enabled**: `template_renderer.py` now uses `autoescape=True` by default. Custom HTML templates that rely on unescaped variable output may need to use Jinja2's `| safe` filter for intentionally raw HTML variables.

### Added

- **OpenAI Realtime GA API Support**: Full Beta-to-GA migration with `api_version` toggle (`ga` / `beta`). GA mode uses nested `audio.input.format` / `audio.output.format` with MIME types (`audio/pcm`, `audio/pcmu`, `audio/pcma`), `turn_detection` under `audio.input`, and `output_modalities` instead of `modalities`. Production-validated with `gpt-4o-realtime-preview-2024-12-17`.
- **OpenAI Realtime `project_id`**: New config field and UI input for OpenAI project tracking via the `OpenAI-Project` header.
- **OpenAI Realtime Voice Expansion**: 10 voices with gender labels (alloy, ash, ballad, cedar, coral, echo, marin, sage, shimmer, verse). Removed unsupported voices (nova, fable, onyx).
- **Email System Overhaul**: New SMTP client (`smtp_client.py`) with rate limiting and deduplication, email dispatcher with provider abstraction (Resend / SMTP / auto-detect), HTML template engine with Jinja2 sandboxed rendering, per-context `from_email` / `admin_email` overrides, subject prefix, `call_outcome` and `hangup_initiator` template variables.
- **Email Template Editor**: Admin UI modal for editing HTML email templates with defaults, preview, and per-tool configuration.
- **SMTP Test Email**: Admin UI button to send a test email using current SMTP settings before saving.
- **Google Live Hangup Fallback Watchdog**: Tunable timeout-based fallback for calls where Google Live does not emit `turnComplete`. Four new config fields: `hangup_fallback_audio_idle_sec`, `hangup_fallback_min_armed_sec`, `hangup_fallback_no_audio_timeout_sec`, `hangup_fallback_turn_complete_timeout_sec`.
- **Google Live `toolConfig`**: Explicit tool configuration in setup message; fixed empty `required` arrays that caused 1008 disconnects.
- **NAT / Advertise Host (Milestone 23)**: `AUDIOSOCKET_ADVERTISE_HOST` and `EXTERNAL_MEDIA_ADVERTISE_HOST` environment variables for split-horizon / NAT deployments where the advertised address differs from the bind address.
- **GPU Acceleration Path**: `docker-compose.gpu.yml` overlay, `Dockerfile.gpu` with CUDA wheel build for llama-cpp-python, preflight GPU detection (`GPU_AVAILABLE` env var), and gated GPU layer configuration.
- **Dashboard Live System Topology**: Clickable topology nodes navigate to settings pages (Providers → `/providers`, Pipelines → `/pipelines`, Models → `/models`, Asterisk/AI Engine → `/env`). Active model cards, pipeline path highlighting, platform info bar, compact resource strip.
- **Admin UI Modernization**: Modern confirm dialogs (replaced all `alert()` / `confirm()`), toast notifications, `AlertDialog` component, SVG T-junction arrows, hover transitions, loading spinners.
- **EnvPage Refactor**: Tab-based UI (AI Engine / Local AI / System) with comprehensive variable coverage, Docker Build Settings section for `INCLUDE_*` build-time flags.
- **Help Section**: In-app documentation viewer with terminal-style rendering.
- **Models Page Redesign**: Stacked full-width layout with active model cards showing real-time usage during calls.
- **Env Drift Detection**: "Apply Changes" now detects `.env` drift vs running containers and recomputes the apply plan. SMTP/Resend env changes correctly trigger `ai_engine` restart.

### Changed

- **Hangup Tool Simplified (v5.0 design)**: Removed transcript-offer guardrails from `hangup_call` tool. The AI now manages transcript offers via system prompt instead of tool-level blocking. Simpler, fewer race conditions.
- **UI `downstream_mode` options**: Replaced invalid `burst` option with valid `file` option to match backend schema.
- **UI `response_modalities` serialization**: Now serializes as `List[str]` (e.g., `["audio"]`) instead of a comma-separated string.

### Fixed

- **Google Live Hangup Before `turnComplete`**: Fixed race where hangup could fire before the provider emitted `turnComplete`, causing stuck calls.
- **Google Live Model Normalization**: Hardened model name normalization and provider options to prevent 1008 errors from malformed setup messages.
- **OpenAI Realtime GA Schema Stabilization**: 15+ iterative fixes for GA session.update format — MIME types, nested format objects, rate fields, turn_detection placement, delta type handling, output format enforcement.
- **OpenAI Realtime GA `response.create`**: Removed `output_modalities` and `input` array from GA response.create (rejected by API).
- **OpenAI Realtime GA `delta` Handling**: Fixed crash where `response.output_audio.delta` sends `delta` as a base64 string, not a dict.
- **Email Template Save**: Fixed template persistence and `call_outcome` variable availability.
- **Request Transcript Email**: Fixed issue where `request_transcript` used the last email address instead of the one from the current request.
- **UI No-Op Knobs Removed**: Removed `create_response` checkbox from OpenAI Realtime form (GA hardcodes `true`), removed `continuous_input` checkbox from Google Live form (no backend field).
- **Test Alignment**: OpenAI Realtime provider test updated for GA payload shape; hangup tool tests aligned with v5.0 simplified design.

### Security

- **Email Template Autoescaping**: `SandboxedEnvironment(autoescape=True)` prevents HTML injection from user-controlled template variables (e.g., caller speech transcripts). Pre-escaped keys (`transcript_html`, `transcript`) use `Markup()` to avoid double-escaping.

### Migration from v5.3.1

1. **OpenAI Realtime users**: If you were using the default `api_version` (previously `beta`), the new default is `ga`. To keep beta behavior, explicitly set `providers.openai_realtime.api_version: beta` in your YAML config.
2. **Email users**: SMTP support is new. Existing Resend-only setups continue to work unchanged. To use SMTP, add `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` to your `.env`.
3. **Docker rebuild required**: Run `docker compose up -d --build --force-recreate` for all containers.
4. **Run preflight**: Execute `./preflight.sh` to detect GPU availability and validate system state.

## [5.3.1] - 2026-02-01

### Added

- **Deepgram Language Configuration**: Voice Agent language is now configurable via Admin UI and YAML (`agent_language` field). Supports 30+ languages including English variants, Spanish, French, German, Japanese, Chinese, and more.
- **Phase Tools (Milestone 24)**: Pre-call HTTP lookups (enrichment), in-call HTTP tools (AI-invoked during conversation), and post-call webhooks (fire-and-forget automation).
- **HTTP Tool Debug Trace Logs**: When `LOG_LEVEL=debug`, pre/in/post-call HTTP tools emit `[HTTP_TOOL_TRACE]` logs showing the resolved request (URL/headers/body), referenced variables, and a bounded response preview to speed up troubleshooting.
- **Extension Availability Tool (AAVA-53)**: New `check_extension_status` tool to query Asterisk device state (e.g., `PJSIP/2765`) so the AI can decide whether to transfer or continue.
- **Hangup Policy Controls**: `hangup_call` now supports a configurable policy (markers and guardrails) via `tools.hangup_call.policy`.
- **Admin UI YAML Error Recovery**: YAML parse errors now show a banner with line/column and the Raw YAML page can still load content for quick fixes.
- **Agent CLI RCA Enhancements**: `agent rca [call_id]` support, optional `--llm` forcing, and tool call extraction to improve post-call debugging.

### Fixed

- **RTP Greeting Cutoff on External Trunk Calls**: Fixed issue where greeting audio was cut off on calls from external trunks. Root cause: Asterisk ExternalMedia requires audio to flow through the mixing bridge before it sends RTP. Added "RTP kick" that plays brief silence through the bridge immediately after ExternalMedia setup, triggering RTP flow in ~40-50ms instead of waiting 5-7 seconds for caller audio.
- **Admin UI Setup Wizard (AAVA-164)**: ElevenLabs Agent ID field now auto-populates from `.env` file when re-running the wizard.
- **Admin UI Log Export (AAVA-162)**: Exported debug logs now redact email addresses (`[EMAIL_REDACTED]`) to protect user privacy.
- **Admin UI Environment Changes (AAVA-161)**: "Apply Changes" after modifying `.env` variables now uses `docker compose --force-recreate` instead of container restart, ensuring environment variable changes are actually applied (e.g., `LOG_TO_FILE`, `LOG_FILE_PATH`).
- **In-Call HTTP Tools Config Wiring**: Align tool schema across engine/Admin UI/docs and support context allowlisting of in-call HTTP tools.
- **Admin UI Variable Usability Hint**: HTTP tool editors now highlight variable names that can be referenced elsewhere (e.g., `{patient_id}`), reducing confusion with JSON extraction paths (e.g., `patient.id`).
- **Google Live Transcription Stability**: Reduce duplicate transcript fragments and improve output PCM rate detection from provider mimeType.

### Security

- Admin UI HTTP tool **Test** now blocks localhost/private targets by default (SSRF mitigation); can be overridden for trusted networks via environment variables.

## [5.2.5] - 2026-01-28

### Added

- Updates: publish the `asterisk-ai-voice-agent-updater` image to GHCR on release tags (aligned with other containers).
- Admin UI: Stable/Main/Advanced update targets + render the latest release notes (from `CHANGELOG.md`) after **Check updates**.
- Agent CLI: `agent update` supports semver tag refs like `v5.2.5` (in addition to branches).

### Changed

- Config: fresh installs now default to **AudioSocket** (`audio_transport: audiosocket`).

### Fixed

- Admin UI Updates: prefer pulling the published updater image and fall back to a local build using host networking in restricted DNS/egress environments.

## [5.2.4] - 2026-01-26

### Fixed

- Admin UI: Dashboard no longer performs update checks that can trigger building the updater image; updates are checked only from **System → Updates** on explicit user action.
- Admin UI: Docker Services “Restart” uses Docker SDK restart (avoids compose recreate failures on hosts where repo paths like `/root/...` are not accessible inside the `admin_ui` container).
- Admin UI: compose-based start/recreate/build operations are executed via the detached updater runner to ensure host-path resolution is correct.

## [5.2.3] - 2026-01-26

### Fixed

- Agent CLI: `agent update` no longer runs an unscoped `docker compose up` when Compose files change; it targets only running/impacted services to avoid unintentionally creating optional services (e.g., `local_ai_server`) and failing on systems that never built those images.

## [5.2.2] - 2026-01-26

### Fixed

- Agent CLI: `agent update` now fetches branches using an explicit refspec so `origin/<ref>` is reliably updated (prevents false “Already up to date” results when the remote-tracking branch is stale).

## [5.2.1] - 2026-01-25

### Added

- Admin UI: **System → Updates** page with a GitHub-style flow: **Check updates → choose branch → preview file/container impact → proceed**.
- Admin UI: branch dropdown supports updating to **any remote branch** (useful for testing feature/fix branches via UI).
- Admin UI: **Recent Runs** table (last 10) with job summaries (success/failure, rebuild/restart actions, file count) and recovery helpers.
- Admin UI: one-click **Rollback** for failed update jobs (restores pre-update code + operator config from the backup; rebuilds/restarts only impacted services).
- Admin UI: keep full update logs for both success and failure runs.
- Agent CLI: `agent update` enhancements for UI-driven updates:
  - `--plan` / `--plan-json` to preview actions without applying
  - `--checkout` to allow switching branches when updating to a non-current ref
  - `--include-ui` to include/exclude `admin_ui` rebuild/restart
  - `--backup-id` for stable backups (used by UI jobs)
  - UI-driven updates also refresh the project-local CLI binary at `./.agent/bin/agent`

### Changed

- Updates: “latest version” checks consider **`v*` tags only** and compute status using commit ancestry (handles “local ahead” cleanly).
- Updates: UI-triggered jobs write state under `./.agent/updates/jobs/` and backups under `./.agent/update-backups/<job_id>/`.
- Updater: update/plan execution runs in a **detached updater container** so jobs survive `admin_ui` rebuild/restarts.

### Fixed

- Updates: docker compose operations from inside containers now resolve bind mounts correctly by mounting the repo at the **same absolute host path**.
- Updates: respect non-default Docker socket mounts (e.g., `DOCKER_SOCK`) when starting updater jobs.
- Updates: avoid transient “Update job not found” UI errors immediately after starting a job.

## [5.1.7] - 2026-01-24

### Added

- Engine: provider-agnostic **Upstream Squelch** for continuous-audio providers with native VAD (improves end-of-turn detection in noisy environments).
- Admin UI: new **Upstream Squelch** controls and tooltips under Advanced → VAD.
- Streaming (ExternalMedia RTP): add a short, configurable wait for the remote RTP endpoint during the initial greeting before falling back to file playback (reduces “dead air until caller speaks” on some Asterisk setups).

### Changed

- Config: default `farewell_hangup_delay_sec` is now `5` seconds (provider override still wins when set).
- Prompts: updated OpenAI Realtime demo greeting text.

### Fixed

- Admin UI: auto-detect the media directory group inside the container and add `appuser` to that GID at startup, preventing false “media directory not writable” warnings after host reboots on systems where Asterisk uses a non-default group ID.
- Hangup tool: provider-agnostic transcript gating and contact-confirmation guardrails to prevent premature hangups and repeated transcript prompts.
- OpenAI Realtime: avoid cutting off farewell speech by waiting for `output_audio.done` before emitting `HangupReady`.

## [5.1.6] - 2026-01-20

### Added

- Admin UI Dashboard: show current project version in the **System Ready** card (best-effort detection via `git describe` with fallback to README parsing; supports `AAVA_PROJECT_VERSION` override).

### Changed

- Docs/CLI: align operator-facing version references to `v5.1.6` (avoid hardcoded `v5.0` strings in CLI help/output where possible).
- Agent CLI: default `agent version` string now reports `5.1.6` (still overridable via `-ldflags`).

### Fixed

- Admin UI Setup Wizard: OpenAI Realtime now prompts for the OpenAI API key (no Groq key mix-up) and validates correctly.
- Agent CLI `agent rca`: detect AudioSocket vs ExternalMedia calls and tailor transport diagnostics (avoid AudioSocket-only false positives).
- Admin UI System Ready: report **host** Docker Compose version via container labels (`com.docker.compose.version`) with fallback to container CLI detection when labels aren’t available.
- Admin UI Web Terminal: input text is now legible on dark background (light text).
- Docker builds: pin base OS/runtime to avoid upstream tag drift (`admin_ui`/`ai_engine`: Debian 12 `bookworm` + Python 3.11; `local_ai_server`: Debian 13 `trixie` + Python 3.11 for Kroko compatibility).
- Ollama pipelines: prevent overly-eager `hangup_call` tool calls from ending calls when the caller did not indicate end-of-call intent; support `num_ctx` pass-through and honor `tools_enabled` for tool calling.
- Email transcripts: improve Outlook compatibility by rendering transcript newlines as `<br/>` (HTML-escaped) so caller/AI lines display correctly across clients.

## [5.0.1] - 2026-01-14

### Fixed

- **CLI Command Names**: v5.0.0 binaries were built with old command names; this release includes the updated CLI:
  - `agent check` - Standard diagnostics report (renamed from `agent doctor`)
  - `agent rca` - Post-call root cause analysis (renamed from `agent troubleshoot`)
  - `agent setup` - Interactive setup wizard (renamed from `agent init`)
  - Legacy aliases (`doctor`, `troubleshoot`, `init`, `demo`) remain available as hidden commands for backward compatibility

- **Preflight Script - Remote Asterisk Support** (AAVA-150):
  - New `check_data_permissions()` function runs regardless of Asterisk location
  - Fixes `call_history.db` read-only error for users with remote/containerized Asterisk
  - Detects and fixes root-owned database files that container user cannot write to

- **Preflight Script - Symlink Handling** (AAVA-150):
  - Non-empty directories at `/var/lib/asterisk/sounds/ai-generated` now auto-backed up with `--apply-fixes`
  - Fixes the "ai-generated/ai-generated/" double-path issue
  - Prevents silent symlink creation failures

## [5.0.0] - 2026-01-07

### Added

- Outbound Campaign Dialer (**Alpha**) (Admin UI → Call Scheduling):
  - Campaign scheduler (single-node) with pacing + concurrency controls
  - CSV lead import (skip-existing), lead actions (recycle/reset/ignore/delete)
  - Dialplan-assisted voicemail detection via Asterisk `AMD()` and voicemail drop
  - Optional consent gate (DTMF `1` accept / `2` deny / timeout tracking)
  - Recording library (upload once, reuse across campaigns) + shipped default consent/voicemail prompts
- Groq Speech (STT + TTS) adapters for modular pipeline deployments
- Attended (warm) transfer tool with DTMF acceptance flow
- Preflight hardening and operator guidance (cross-platform checks, docker socket guidance, local-ai “minimal vs full” mode)

### Changed

- Standardized Docker Compose service/container names: `admin_ui`, `ai_engine`, `local_ai_server`
- Container base OS alignment:
  - `admin_ui`: Python 3.11 on Debian 12 (`bookworm`)
  - `ai_engine`: Python 3.11 on Debian 12 (`bookworm`)
  - `local_ai_server`: Python 3.11 on Debian 13 (`trixie`) (intentional for embedded Kroko/glibc compatibility)

### Fixed

- Outbound dialer reliability fixes (caller-id inheritance, outcome tracking, SQLite write/lock edge cases, duplicate side-effects)
- Admin UI stability improvements (tooltips, recording preview UX, safer compose/health behaviors)
- Ollama pipeline robustness (provider config resolution + tool-result handling improvements)

### Docs

- Added `docs/OUTBOUND_CALLING.md` and updated FreePBX + installation docs for v5.0.0

## [4.6.0] - 2025-12-29

### Added

- ARI connectivity enhancements:
  - `ASTERISK_ARI_PORT` support
  - `ASTERISK_ARI_SCHEME` (`http|https`) with `ws://` vs `wss://` alignment
  - `ASTERISK_ARI_SSL_VERIFY` toggle for self-signed or hostname mismatch environments
- Pipeline robustness: invalid pipelines are detected and fall back deterministically instead of silently using placeholder adapters
- Admin UI logging improvements: structured event support and improved Logs viewing UX

### Changed

- Admin UI config management: safer `.env` parsing/writes and clearer apply guidance (“save vs apply” determinism)
- Admin UI health checks: Tier 3/best-effort probe fallbacks with explicit warnings when configured overrides are unreachable
- Admin UI container actions: safer `admin_ui` restart behavior from within the UI
- Compose env semantics: `.env` is authoritative; avoid `${VAR:-default}` fallbacks in compose that prevent UI env changes from taking effect

### Fixed

- Preflight: Debian-family best-effort detection improvements and Debian 12 Docker repo codename fallback (`bookworm`) when `VERSION_CODENAME` is missing
- Admin UI Docker management hardening: restrict compose operations to AAVA services and reduce information exposure in error messages

### Docs

- Upgrade guidance: `v4.6.0 → v5.0.0` checklist in `docs/INSTALLATION.md`
- IPv6 policy: warn/recommend disabling IPv6 for GA stability and document mitigation steps
- Supported platforms: explicit Tier 3 best-effort guidance for openSUSE and Podman

## [4.5.3] - 2025-12-22

### Added

- ExternalMedia RTP hardening: remote endpoint pinning (`external_media.lock_remote_endpoint`) and allowlist support (`external_media.allowed_remote_hosts`)
- Tests for RTP routing/security and Prometheus label cardinality
- Admin UI backend: model switching mappings for `faster_whisper` STT and `melotts` TTS

### Changed

- Default provider now `local_hybrid` (pipeline-first GA default)
- Readiness probe is pipeline-aware when `default_provider` references a pipeline (e.g., `local_hybrid`)
- Prometheus metrics are low-cardinality only (removed per-call labels like `call_id`; per-call detail lives in Call History)

### Fixed

- ExternalMedia RTP SSRC routing: prevent cross-call audio mixing by using authoritative `call_id` in engine callback
- Admin UI HealthWidget rebuild payload: stop mis-parsing model/voice identifiers
- Local provider readiness badge: report “configured” vs “connected” semantics correctly

### Removed

- Legacy bundled Prometheus/Grafana monitoring stack and `monitoring/` assets from the main repo path (Call History-first debugging; bring-your-own monitoring)

### Improved (Onboarding & DX)

- Preflight is now **required** (not recommended) in all documentation
- Admin UI Dockerfile default bind aligned to `0.0.0.0` (matches docker-compose.yml for out-of-box accessibility)
- Prominent ASCII security warning box in `preflight.sh` and `install.sh` post-install output
- Timezone (`TZ`) now configurable via `.env` with `America/Phoenix` default
- README Quick Start includes verification step with health check command
- INSTALLATION.md Path A corrected: preflight required, proper service ordering
- Remote server access (`http://<server-ip>:3003`) now primary instruction alongside localhost

## [4.5.2] - 2025-12-16

### Added

- **Kokoro API mode**: OpenAI-compatible TTS endpoint (`KOKORO_MODE=api`)
- **Kroko Embedded models**: Downloadable from Admin UI Models Page
- **Model hot-swap**: Switch STT/TTS/LLM via WebSocket without container restart
- **MCP tool integration**: External tool framework with Admin UI config
- **Aviation ATIS tool**: Live METAR data from aviationweather.gov

### Changed

- Websockets connection logs moved to DEBUG level
- Local provider auto-reconnects on disconnect (12 min retry)

### Fixed

- Wizard: Kroko embedded detection, Kokoro voice selector alignment
- Compatibility: websockets 15.x, resend 2.x, sherpa-onnx 1.12.19

## [4.5.0] - 2025-12-11

### Fixed - Admin UI Stability 🔧

#### ConfigEditor Critical Fixes (A1-A3, A9)

- **Missing State Hooks**: Added `loading`, `saving`, `error`, `success`, `restartRequired` useState declarations
- **Duplicate Import**: Removed duplicate `AudioSocketConfig` import
- **Provider Type Persistence**: New providers now correctly save their `type` field
- **Provider Name Validation**: Empty provider names are now rejected with error message
- **Unused Code Cleanup**: Removed 15+ unused icon imports, reducing bundle size

#### Save Flow Improvements (A6)

- **Restart Required Banner**: UI now displays amber banner when config changes require engine restart
- **Toast Notifications**: Replaced browser `alert()` with inline dismissible notifications
- **Loading Spinner**: Added visual feedback during config fetch

#### Docker Operations (A4-A5)

- **Dynamic Path Resolution**: Uses `shutil.which()` to find docker-compose instead of hardcoded paths
- **Cleaner Restarts**: Uses `container.restart()` via Docker SDK instead of destructive stop/rm/up flow
- **Fallback Support**: Gracefully falls back to docker-compose if Docker SDK fails

#### Config File Safety (A8, A11, A12)

- **Atomic Writes**: Config and .env files written via temp file + atomic rename (prevents corruption on crash)
- **Backup Rotation**: Only keeps last 5 backups per file (prevents disk exhaustion)
- **Env Validation**: Rejects empty keys, newlines in values, and `=` in keys before writing .env

### Added - Stability Improvements 🛡️

#### Enhanced Timer Logging (L2)

- **Structured Timer Logs**: All timer operations now log with `[TIMER]` prefix for easy filtering
- **Timer Lifecycle Tracking**: Logs show scheduled, executed, and cancelled timer events
- **Pending Timer Count**: `get_pending_timer_count()` method exposes timer queue depth

#### Health Check Improvements (L4)

- **Uptime Tracking**: `/health` endpoint now returns `uptime_seconds`
- **Pending Timers**: Health response includes `pending_timers` count
- **Active Sessions**: Added `active_sessions` field (alias for `active_calls`)
- **Real Conversation Metrics**: `conversation` object now pulls live data from ConversationCoordinator

#### Graceful Shutdown Handler (M4)

- **SIGTERM Handling**: `docker stop` now waits up to 30 seconds for active calls to complete
- **Shutdown Logging**: `[SHUTDOWN]` prefixed logs track graceful shutdown progress
- **Configurable Timeout**: `engine.stop(graceful_timeout=30)` parameter for custom drain time

### Changed

#### Code Cleanup (H1)

- **Removed Legacy Code**: Deleted commented `active_calls` legacy code block from engine.py
- **SessionStore is Single Source**: All session state now uses SessionStore exclusively

## [4.4.3] - 2025-12-10

### Fixed - Admin UI Bug Fixes 🔧

#### Models Page
- **Installed Models Display**: Fixed parsing of nested API response structure
- **Model Delete**: Added `DELETE /api/local-ai/models` endpoint with path mapping
- **Error Messages**: Properly extract and display API error details (not generic "Request failed")

#### Dashboard
- **STT/TTS Dropdowns**: Show individual model names in optgroups instead of counts (e.g., "vosk-model-en" instead of "Vosk (2)")
- **Metrics Display**: Added null guards to prevent "NaN%" when backend is unavailable

#### Providers Page
- **Local Provider Form**: Fixed form visibility for full agent mode local providers
- **Currently Loaded Section**: Added live display of STT/LLM/TTS model status
- **Test Connection - Local**: Now tests actual local_ai_server WebSocket connection and verifies all 3 models are loaded
- **Test Connection - ElevenLabs**: Fixed validation using `/v1/voices` endpoint (was using `/v1/user` which requires special permissions)

#### Health Widget
- **Kroko/Kokoro Mode Detection**: Correctly parses embedded/local mode from health response paths

#### Model Switching
- **Container Restart**: Uses `docker-compose down/up` instead of Docker SDK to properly reload environment variables

### Added - Cross-Platform Support (AAVA-126) 🌍

#### Pre-flight Script (`preflight.sh`)
- **Comprehensive System Checks**: OS detection, Docker version, Compose version, architecture verification
- **Auto-fix Mode**: Run with `--apply-fixes` to automatically resolve fixable issues
- **Multi-distro Support**: Ubuntu, Debian, CentOS, RHEL, Rocky, Alma, Fedora, Sangoma/FreePBX
- **Rootless Docker Detection**: Proper handling for rootless Docker installations
- **SELinux Handling**: Automatic context fix commands for RHEL-family systems
- **Asterisk Detection**: Finds Asterisk config directory and FreePBX installations
- **Port Availability Check**: Verifies Admin UI port (3003) is available
- **Environment Setup**: Creates `.env` from `.env.example` if missing

#### Admin UI Integration
- **System Status Widget**: Dashboard displays preflight check results
- **Platform API**: `GET /api/system/platform` returns system compatibility info
- **Preflight API**: `POST /api/system/preflight` triggers fresh system check

### Added - Developer Experience 🛠️

- **React.lazy Code Splitting**: Heavy pages (Wizard, RawYaml, Terminal, Logs, Models) now lazy-loaded for faster initial bundle
- **ESLint + Prettier**: Added configuration with lint/format/audit npm scripts
- **Frontend README**: Documentation for setup, build, and available scripts

## [4.4.2] - 2025-12-08

### Added - Local AI Server Enhancements 🎯

#### New STT Backends
- **Kroko ASR Integration (AAVA-92)**: High-quality streaming ASR with 12+ languages
  - Hosted API support (`wss://app.kroko.ai`)
  - On-premise server support
  - No hallucination - factual transcripts only
  - Configure via `LOCAL_STT_BACKEND=kroko`
- **Sherpa-ONNX STT (AAVA-95)**: Local streaming ASR using sherpa-onnx
  - Low-latency streaming recognition
  - Multiple model support (Zipformer, etc.)
  - Configure via `LOCAL_STT_BACKEND=sherpa`

#### New TTS Backends
- **Kokoro TTS (AAVA-95)**: High-quality neural TTS
  - Multiple voices: `af_heart`, `af_bella`, `am_michael`
  - Natural prosody and intonation
  - Configure via `LOCAL_TTS_BACKEND=kokoro`
- **ElevenLabs TTS Adapter (AAVA-114)**: Cloud TTS for modular pipelines
  - Factory pattern integration
  - Premium voice quality

#### Model Management System (AAVA-99, 101, 102, 103, 104)
- **Dashboard Quick-Switch**: Change STT/TTS/LLM models directly from dashboard
- **Model Enumeration API**: `GET /api/local-ai/models/available`
- **Model Switch API**: `POST /api/local-ai/models/switch` with hot-reload
- **2-Step UI Flow (AAVA-111)**: "Pending" badge + "Apply & Restart" button
- **Error Handling (AAVA-108)**: Rollback on switch failure

### Added - Admin UI Improvements

- **Pipeline UI Backend Display (AAVA-116)**: Shows active STT/TTS backend for local components
- **Directory Health Card (AAVA-93)**: Dashboard shows media directory permissions
- **Pipeline Orchestrator Logging (AAVA-106)**: Logs active backends on startup
- **YAML Config Sync (AAVA-107)**: Model selection synced to `ai-agent.yaml`

### Added - DevOps & CI

- **Optional Build Args (AAVA-112)**: Exclude unused backends from Docker build
  - `INCLUDE_VOSK`, `INCLUDE_SHERPA`, `INCLUDE_PIPER`, `INCLUDE_KOKORO`, `INCLUDE_LLAMA`
  - Default: all enabled (backward compatible)
  - Reduces image size for specialized deployments
- **CI Image Size Checks (AAVA-113)**: Size budgets in GitHub Actions
  - ai-engine: 1.5GB budget
  - local-ai-server: 4GB budget
- **Enhanced Trivy Scanning (AAVA-113)**: Both images scanned for vulnerabilities
- **Outdated Dependency Reporting**: Warning in CI for outdated packages

### Added - Documentation

- **LOCAL_ONLY_SETUP.md**: Comprehensive guide for fully local deployment
  - Vosk, Sherpa-ONNX, Kroko STT options
  - Piper, Kokoro TTS options
  - Phi-3 LLM configuration
  - Hardware recommendations
- **Docker Build Troubleshooting (AAVA-119)**: DNS resolution, BuildKit issues
  - Solutions for `docker-compose` vs `docker compose`
  - Network configuration guides

### Fixed

- **Local Pipeline Validation (AAVA-118)**: Local components validate against websocket URLs
  - Pipeline no longer disabled on validation failure
  - Fixes "call drops after greeting" for local setups
- **TTS Response Contract (AAVA-105)**: JSON with base64 audio instead of binary frames
- **Docker Image Debloat (AAVA-109, 110)**: Removed unused dependencies
- **Config Validation (AAVA-115)**: Capability/suffix mismatch detection
- **Sherpa-ONNX API Handling**: Handle string return type from `get_result()`
- **Container Restart Logic**: Fixed docker-compose commands in Admin UI

### Changed

- **Wizard Model Detection (AAVA-98)**: Detects Sherpa STT and Kokoro TTS models
- **Status API (AAVA-96)**: Correctly reports kroko/sherpa/kokoro backends
- **Friendly Model Names**: Status shows basename instead of full path

### Technical Details

- **Files Added**: 
  - `docs/LOCAL_ONLY_SETUP.md`
  - `local_ai_server/requirements-base.txt`
- **Files Modified**:
  - `local_ai_server/Dockerfile` (conditional backend installs)
  - `local_ai_server/main.py` (Kroko, Sherpa, Kokoro backends)
  - `.github/workflows/ci.yml` (image size checks)
  - `.github/workflows/trivy.yml` (dual image scanning)
  - `admin_ui/frontend/src/components/config/PipelineForm.tsx`
  - `src/pipelines/base.py`, `src/pipelines/orchestrator.py`

## [4.4.1] - 2025-11-30

### Added - Admin UI v1.0 🎉
- **Web-Based Administration Interface**: Modern React + TypeScript UI replacing CLI setup workflow
  - **Setup Wizard**: Visual provider configuration with API key validation (replaces `agent quickstart`)
  - **Configuration Management**: Full CRUD for providers, pipelines, contexts, and audio profiles
  - **System Dashboard**: Real-time monitoring (CPU, memory, disk usage, container status)
  - **Live Logs**: WebSocket-based log streaming from ai-engine
  - **Raw YAML Editor**: Monaco-based editor with syntax validation
  - **Environment Manager**: Visual editor for `.env` variables
  - **Container Control**: Start/stop/restart containers from UI
- **JWT Authentication System**: Production-ready security
  - Token-based authentication with 24-hour expiry
  - Password hashing (pbkdf2_sha256)
  - Default credentials: admin/admin (must be changed on first login)
  - Change password functionality
  - Auto-created default admin user
  - Optional JWT secret configuration (development default provided)
- **Docker Integration**: Multi-stage build and deployment
  - Single container with frontend + backend
  - Port 3003 (configurable)
  - Volume mounts for config/users.json access
  - Health check endpoint
  - Restart policies
- **Comprehensive Documentation**:
  - `admin_ui/UI_Setup_Guide.md`: Complete setup and troubleshooting guide
  - Docker deployment (recommended)
  - Standalone deployment with daemon mode (nohup, screen, systemd)
  - Production deployment with reverse proxy (Nginx, Traefik)
  - Security best practices and JWT configuration
  - Upgrade path from CLI setup

### Added - Provider System Enhancements
- **Provider Registration System**: Explicit validation of supported provider types
  - `REGISTERED_PROVIDER_TYPES` defines engine-supported providers
  - Unregistered providers show warning but can be saved
  - Pipeline dropdowns only show registered providers
- **Local Full Agent**: 100% on-premises deployment option
  - New `local` provider with `type: full` for monolithic Local AI Server mode
  - Wizard option "Local (Full)" - no API keys required
  - Health check verification before setup completion
- **Provider Classification**: Clear distinction between Full Agent and Modular providers
  - `isFullAgentProvider()` logic based on type and capabilities
  - Full agents blocked from modular pipeline slots
  - Modular providers require explicit `capabilities` arrays

### Added - ElevenLabs Conversational AI Provider (AAVA-90)
- **Full Agent Provider**: ElevenLabs Conversational AI integration
  - WebSocket-based real-time voice conversations (STT + LLM + TTS)
  - Premium voice quality with natural conversation flow
  - Tool calling support (tools defined in ElevenLabs dashboard, executed locally)
  - Audio format: PCM16 16kHz, automatic resampling from telephony (μ-law 8kHz)
- **Configuration**: 
  - `ELEVENLABS_API_KEY` and `ELEVENLABS_AGENT_ID` environment variables
  - Provider config in `ai-agent.yaml` under `providers.elevenlabs_agent`
  - Admin UI support: Wizard option, provider form, card badges
- **Documentation**: `docs/contributing/references/Provider-ElevenLabs-Implementation.md` (578 lines)
- **Files**: `src/providers/elevenlabs_agent.py`, `src/providers/elevenlabs_config.py`, `src/tools/adapters/elevenlabs.py`

### Added - Background Music Support (AAVA-89)
- **In-Call Background Music**: Play music during AI conversations
  - Uses Asterisk Music On Hold (MOH) via snoop channel
  - Configurable per-context via `background_music` field
  - Admin UI toggle in Context configuration
- **Implementation**: Snoop channel with MOH starts when call begins, stops on hangup
- **Configuration**: Set MOH class name (default: "default") in context settings
- **Note**: Music is heard by AI (affects VAD); use low-volume ambient music for best results

### Changed
- **Port Configuration**: Admin UI runs on port 3003 (updated from 3000)
- **Version Numbers**: Admin UI frontend package.json updated to 1.0.0
- **docker-compose.yml**: Added admin-ui service with proper volume mounts
- **LocalProviderConfig**: Added `base_url` field for consistency with other full agents

### Technical Details
- **Frontend**: React 18, TypeScript, Vite, TailwindCSS, Monaco Editor
- **Backend**: FastAPI, Python 3.10, JWT auth, YAML/JSON config management
- **Build**: Multi-stage Dockerfile (Node.js build → Python runtime)
- **Authentication**: JWT tokens, OAuth2 password flow, session management
- **API**: RESTful endpoints with OpenAPI/Swagger documentation
- **Real-time**: WebSocket support for log streaming

### Security
- JWT-based authentication (optional custom secret for production)
- Password hashing with pbkdf2_sha256
- Route protection on all API endpoints
- CORS configuration (restrict in production)
- HTTPS support via reverse proxy
- Default credentials documented with change instructions

### Migration
- **New Installations**: Use setup wizard on first access
- **Existing Users**: Config auto-detected, wizard skipped
- **CLI Coexistence**: `agent` CLI tools continue to work
- **Backward Compatible**: No breaking changes to ai-engine

## [4.3.0] - 2025-11-19

### Added
- **Holistic Tool Support for Pipelines (AAVA-85)**: Complete tool execution system across all pipeline types
  - Enabled all 6 tools (hangup, transfer, email, transcript, voicemail) for `local_hybrid` pipeline
  - Session history persistence for tool context
  - Explicit ARI hangup implementation
- **Comprehensive Documentation Structure**: Complete reorganization of project documentation
  - New `docs/contributing/` structure for developer documentation
  - Provider setup guides: `Provider-Deepgram-Setup.md`, `Provider-OpenAI-Setup.md`, `Provider-Google-Setup.md`
  - Developer guides: quickstart, architecture overview, architecture deep dive, common pitfalls
  - Technical references for all provider implementations
- **Community Integration**: Discord server integration (https://discord.gg/ysg8fphxUe)
- **Milestone 18**: Hybrid Pipelines Tool Implementation documentation

### Fixed
- **OpenAI Realtime Tool Schema Regression**: Corrected tool schema format for chat completions
- **Tool Execution Flow**: Resolved AttributeError and execution blocking issues
- **Playback Race Conditions**: Fixed audio cutoff during tool execution
- **Hangup Method**: Corrected method name (`hangup_channel()` vs `delete_channel()`)
- **Pydantic Compatibility**: Fixed v1/v2 compatibility (`model_dump` → `dict`)
- **Milestone Numbering**: Corrected duplicate milestone-8, renumbered monitoring stack to milestone-14

### Changed
- **Documentation Structure**: Reorganized docs into User, Provider, Operations, Developer, and Project sections
- **Merged Documentation**: Combined Deepgram API reference into implementation guide (single comprehensive doc)
- **Consolidated Guides**: CLI tools → `cli/README.md`, Queue setup → FreePBX Integration Guide
- **Renamed Files**: Clearer naming for pipeline implementations and architecture docs
- **Link Format**: All documentation links now use relative paths (GitHub-clickable)

### Removed
- **Obsolete Documentation**: 8 outdated docs removed (2,763 lines)
  - `call-framework.md`, `AudioSocket-Provider-Alignment.md`, `CLI_TOOLS_GUIDE.md`
  - `LOCAL_AI_SERVER_LOGGING_OPTIMIZATION.md`, `ASTERISK_QUEUE_SETUP.md`
  - `ExternalMedia_Deployment_Guide.md`, `deepgram-agent-api.md`
- **Broken References**: Replaced `linear-issues-community-features.md` with Discord server

## [4.2.1] - 2025-11-18

### Added

#### Streamlined Onboarding Experience
- **🚀 Interactive Setup Wizard**: New `agent quickstart` command guides first-time users through complete setup
  - Step-by-step provider selection (OpenAI, Deepgram, Google, Local Hybrid)
  - Real-time API key validation before saving
  - Asterisk ARI connection testing
  - Automatic dialplan snippet generation
  - Clear next steps and FreePBX integration instructions
- **🔧 Enhanced install.sh**: Improved installer with CLI integration
  - ARI connection validation after credentials input
  - Shows Asterisk version on successful connection
  - Offers CLI tool installation with platform auto-detection
  - Launches `agent dialplan` helper if CLI installed
  - Graceful fallbacks for unsupported platforms or download failures
- **📝 Dialplan Generation Helper**: New `agent dialplan` command
  - Generates provider-specific dialplan snippets
  - Supports all providers: OpenAI Realtime, Deepgram, Google Live, Local Hybrid
  - Shows FreePBX Custom Destination setup steps
  - Includes context override examples (AI_PROVIDER, AI_CONTEXT variables)
  - Print-only approach (no auto-write to files)
- **✅ Configuration Validation**: New `agent config validate` command
  - Validates YAML syntax and structure
  - Checks required fields and provider configurations
  - Verifies sample rate alignment across providers
  - Validates transport compatibility
  - Checks barge-in configuration
  - `--fix` flag for interactive auto-fix
  - `--strict` mode for CI/CD (treats warnings as errors)
  - Exit codes: 0 (valid), 1 (warnings), 2 (errors)
- **🩺 Doctor Auto-Fix**: Enhanced `agent doctor` with `--fix` flag
  - Focuses on YAML config validation issues
  - Guides users to `agent config validate --fix` for detailed repairs
  - Re-runs health checks after fixes applied

#### API and Connection Validation
- **API Key Validation**: Real-time validation before saving credentials
  - OpenAI: Validates against `/v1/models` endpoint, checks for GPT models
  - Deepgram: Validates against `/v1/projects` endpoint
  - Google: Format validation (length check)
  - Clear error messages with troubleshooting guidance
  - Network timeout handling (10 second limit)
- **ARI Connection Testing**: Validates Asterisk connectivity during setup
  - Tests connection to `/ari/asterisk/info`
  - Extracts and displays Asterisk version
  - Shows troubleshooting steps on failure
  - Continues with warning if validation fails

#### Documentation
- **CLI Tools Guide**: Updated with all new v4.2 commands
  - Comprehensive `agent quickstart` reference with example session
  - `agent dialplan` usage and output examples
  - `agent config validate` with validation checks and flags
  - Version bumped to v4.2
- **README.md**: Updated Quick Start section
  - Two-path approach: Interactive Quickstart vs Manual Setup
  - Highlights new `agent quickstart` wizard
  - Shows new CLI commands (`dialplan`, `config validate --fix`)
  - Updated version references to v4.2
- **Developer Experience**: Enhanced setup documentation
  - Clear separation between first-time and advanced user paths
  - Better CLI tool discovery and installation guidance

### Fixed

#### OpenAI Realtime Provider
- **Hangup Tool Reliability**: Fixed issue where calls wouldn't hang up when OpenAI failed to generate farewell audio
  - Now emits `HangupReady` immediately when `response.done` arrives without audio
  - Eliminated reliance on timeout-only fallback mechanism
  - Ensures consistent call termination regardless of OpenAI audio generation
- **Self-Interruption Prevention**: Resolved agent overhearing itself and interrupting mid-response
  - Increased `post_tts_end_protection_ms` from 100ms to 800ms (8x longer guard window)
  - Tuned `turn_detection.threshold` from 0.5 to 0.6 (less sensitive to agent's own voice)
  - Increased `turn_detection.silence_duration_ms` from 600ms to 1000ms (more patient turn-taking)
  - Result: Clean, natural conversation flow without choppy interruptions
- **Greeting Timing**: Attempted optimization of `session.updated` ACK timeout (reverted due to audio issues)

#### Local Hybrid Pipeline
- **Critical Sample Rate Fix**: Resolved Vosk STT recognition failure
  - Changed `external_media.format` from `slin` to `slin16` and `sample_rate` from 8000 to 16000
  - Enabled RTP server resampling to match Vosk's native 16kHz requirement
  - Audio now correctly resampled: 8kHz μ-law → decode → 16kHz PCM16 → Vosk
  - Result: Clear two-way conversation with accurate transcription
- **Audio Flow Debugging**: Added comprehensive debug logging for troubleshooting
  - Traces audio bytes, RMS levels, sample counts through full pipeline
  - Helps diagnose future audio routing or quality issues

#### Logging Optimization
- **Production Log Volume**: Reduced local-ai-server log noise
  - Implemented `LOCAL_DEBUG` environment flag to gate verbose audio flow logs
  - Moved detailed audio processing logs (`FEEDING VOSK`, RMS calculation) behind debug flag
  - Preserved essential logs (STT finals, LLM results, TTS output, connection events)
  - Result: ~90% log volume reduction in production with `LOCAL_DEBUG=0`
- **Configuration Clarity**: Improved `.env.example` documentation
  - Clear section headers distinguishing ai-engine vs local-ai-server settings
  - Explicit warnings about log volume impact of debug flags
  - Better guidance on production vs development logging levels

### Added

#### Documentation
- **Local Hybrid Golden Baseline**: Complete production-validated configuration reference
  - Performance metrics, architecture, sample rate fix details
  - Call quality assessment and tuning recommendations
  - See `docs/case-studies/Local-Hybrid-Golden-Baseline.md`
- **Logging Optimization Guide**: Comprehensive logging strategy documentation
  - Debug flag usage, log volume comparison, configuration examples
  - See `docs/LOCAL_AI_SERVER_LOGGING_OPTIMIZATION.md`

#### Unified Transfer Tool (AAVA-63, AAVA-74)
- **Unified Transfer System**: Single `transfer` tool replaces separate `transfer_call` and `transfer_to_queue` tools
  - **Extension Transfers**: Direct dial to specific agents (ARI `redirect`, channel stays in Stasis)
  - **Queue Transfers**: Transfer to ACD queues for next available agent (ARI `continue` to `ext-queues`)
  - **Ring Group Transfers**: Transfer to ring groups that ring multiple agents simultaneously (ARI `continue` to `ext-group`)
- **Smart Routing**: Automatic routing based on destination type configuration
- **Proper Cleanup Handling**: `transfer_active` flag prevents premature caller hangup for queue/ring group transfers
- **Production Verified**: All three transfer types validated on live production server
- **Configuration**: Unified `tools.transfer.destinations` structure with type-based routing

#### Voicemail Tool (AAVA-51)
- **Voicemail Routing**: New `leave_voicemail` tool sends callers to voicemail
  - Routes to FreePBX voicemail via `ext-local,vmu{extension},1` dialplan pattern
  - Uses ARI `continue()` pattern consistent with queue/ring group transfers
  - `transfer_active` flag prevents premature caller hangup
  - Configurable voicemail box extension number
- **Interactive Prompt Strategy**: Tool asks "Are you ready to leave a message now?" to work around FreePBX VoiceMail app behavior
  - VoiceMail app requires bidirectional RTP and voice activity before playing greeting
  - Without caller interaction, 5-8 second delay occurs before greeting plays
  - Caller response establishes RTP path and triggers greeting immediately
- **Comprehensive Documentation**: Detailed behavioral analysis and timeline evidence in module docstring
- **Production Verified**: Tested and deployed on live production server

### Changed
- **Breaking**: Removed `transfer_call` and `transfer_to_queue` tools in favor of unified `transfer` tool
- **Configuration Migration**: Update from separate tool configs to unified `transfer.destinations` structure

## [4.2.0] - 2025-11-14

### 🚀 Major Feature: Google Live Provider (Real-Time Agent)

Version 4.2 introduces the **Google Live provider** - a real-time bidirectional streaming agent powered by Gemini 2.5 Flash with native audio capabilities. This provider delivers ultra-low latency (<1 second) and true duplex communication, making it the fastest option in the Asterisk AI Voice Agent.

### Added

#### Google Live Provider (AAVA-75)
- **Real-Time Bidirectional Streaming**: Full-duplex communication with Gemini 2.5 Flash
  - Native audio processing (no separate STT/TTS pipeline)
  - Ultra-low latency: <1 second response time
  - True duplex: Natural interruptions and barge-in
  - WebSocket-based streaming communication
- **Provider Implementation**: `src/providers/google_live.py`
  - WebSocket connection to Gemini Live API
  - Bidirectional audio streaming with automatic resampling
  - Native tool execution via Google function declarations
  - Session management with context retention
- **Tool Adapter**: `src/tools/adapters/google.py`
  - Converts tools to Google function declaration format
  - Handles async tool execution in streaming mode
  - Sends tool responses back to Gemini
- **Audio Processing**: Automatic resampling for telephony compatibility
  - Input: 8kHz μ-law → 16kHz PCM16 → Gemini
  - Output: 24kHz PCM16 from Gemini → 8kHz μ-law → Asterisk
- **Configurable Parameters**: Full YAML configuration support
  - LLM generation parameters (temperature, max_output_tokens, top_p, top_k)
  - Response modalities (audio, text, audio_text)
  - Transcription toggles (enable_input_transcription, enable_output_transcription)
  - Voice selection (Aoede, Kore, Leda, Puck, Charon, etc.)
- **Golden Baseline**: Validated production-ready configuration
  - See `docs/GOOGLE_LIVE_GOLDEN_BASELINE.md` for complete reference
  - Call quality: Excellent, clean two-way conversation
  - Response latency: <1 second (fastest available)
  - All features validated: duplex, barge-in, tools, transcriptions

#### Transcription System (AAVA-75)
- **Dual Transcription Support**: User and AI speech transcription
  - `inputTranscription`: Captures user speech
  - `outputTranscription`: Captures AI speech
  - Turn-complete based: Saves only final utterances
  - Incremental fragment concatenation for complete transcripts
- **Email Summary Integration**: Complete conversation history in emails
  - Auto-triggered email summaries at call end
  - Manual transcript requests via `request_transcript` tool
  - Transcripts include both user and AI speech
- **Conversation History**: Full conversation tracking
  - Stored in session for context retention
  - Available for email summaries and transcript requests
  - Proper turn management with `turnComplete` flag

### Fixed

#### Transcript Email Timing (CRITICAL)
- **Issue**: `request_transcript` tool sent email immediately (mid-call), missing final conversation
- **Fix**: Defer transcript sending until call end
  - Store email address in session during call
  - Send complete transcript at call cleanup with full conversation history
  - Prevents incomplete transcripts missing final exchanges
- **Impact**: Transcripts now include complete conversation including goodbye

#### Call Ending Protocol
- **Issue**: AI didn't hang up calls after completing tasks, leaving silence
- **Fix**: Explicit call ending protocol in system prompts
  - Step-by-step protocol for detecting conversation end
  - "Is there anything else?" prompt after completing tasks
  - Immediate `hangup_call` tool execution on confirmation
  - Never leave calls hanging in silence
- **Impact**: Professional call termination, no manual hangup needed

#### Greeting Implementation
- **Issue**: Cannot pre-fill model responses in Gemini Live API
- **Fix**: Send user turn requesting AI to speak greeting
  - Changed from pre-filled model response to user request
  - AI generates and speaks personalized greeting naturally
  - Properly uses caller name in greeting
- **Impact**: Greetings now work correctly with caller personalization

#### Incremental Transcription Handling
- **Issue**: API sends word-by-word fragments, not cumulative text
- **Fix**: Concatenate fragments instead of replacing buffer
  - Buffer accumulates fragments until `turnComplete`
  - Prevents fragmented/incomplete transcriptions
  - Matches actual API behavior (differs from documentation)
- **Impact**: Complete, clean transcriptions of all speech

### Changed
- **Documentation**: Renamed `docs/GOOGLE_CLOUD_SETUP.md` → `docs/GOOGLE_PROVIDER_SETUP.md`
  - Updated to cover both Google Live and Cloud Pipeline modes
  - Added comprehensive setup instructions for both
  - Separate dialplan examples for each mode
- **Configuration Examples**: Updated `config/ai-agent.yaml`
  - Added `demo_google_live` context with full configuration
  - Includes all new configurable parameters with inline docs
  - Clear call ending protocol in system prompts

### Performance
- **Latency**: <1 second response time (fastest provider)
- **Audio Quality**: Excellent, natural conversation flow
- **Duplex Communication**: True full-duplex with seamless interruptions
- **Reliability**: Production-tested with clean call termination

### Lessons Learned
- Trust API turn completion signals over custom heuristics
- API behavior may differ from documentation - always validate with testing
- Defer email sending until call end for complete transcripts
- Be explicit about call ending protocols in system prompts
- Provide maximum user flexibility via YAML configuration

## [4.0.0] - 2025-10-29

### 🎉 Major Release: Modular Pipeline Architecture

Version 4.0 introduces a **production-ready modular pipeline architecture** that enables flexible combinations of Speech-to-Text (STT), Large Language Models (LLM), and Text-to-Speech (TTS) providers. This release represents a complete architectural evolution while maintaining backward compatibility with existing deployments.

### Added

#### Core Architecture
- **Modular Pipeline System**: Mix and match STT, LLM, and TTS providers
  - Local STT (Vosk) + Cloud LLM (OpenAI) + Local TTS (Piper)
  - Cloud STT (Deepgram) + Cloud LLM (OpenAI) + Cloud TTS (Deepgram)
  - Fully local pipeline (Vosk + Phi-3/Llama + Piper)
- **Unified Configuration Format**: Single YAML file for all pipeline and provider settings
- **Golden Baseline Configurations**: Three validated, production-ready configurations:
  - **OpenAI Realtime**: Cloud monolithic agent (fastest, <2s response)
  - **Deepgram Voice Agent**: Enterprise cloud agent with Think stage
  - **Local Hybrid**: Privacy-focused with local STT/TTS + cloud LLM

#### Audio Transport
- **Dual Transport Support**: AudioSocket (TCP) and ExternalMedia RTP (UDP)
- **Automatic Transport Selection**: Optimal transport chosen per configuration
- **Enhanced Audio Processing**: Improved resampling, echo cancellation, and codec handling
- **Pipeline Audio Routing**: Fixed audio path for pipeline configurations
- **Transport Compatibility Matrix**: Documented all configuration + transport combinations

#### Monitoring & Observability
- **Production Monitoring Stack**: Prometheus + Grafana with 5 pre-built dashboards
  - System Overview: Active calls, provider distribution
  - Call Quality: Turn latency (p50/p95/p99), processing time
  - Audio Quality: RMS levels, underflows, jitter buffer depth
  - Provider Performance: Provider-specific metrics and health
  - Barge-In Analysis: Interrupt behavior and timing
- **50+ Metrics**: Comprehensive call quality, audio quality, and system health metrics
- **Alert Rules**: Critical and warning alerts for production monitoring
- **Health Endpoint**: `/metrics` endpoint on port 15000 for Prometheus scraping

#### Installation & Setup
- **Interactive Installer**: `install.sh` with guided pipeline selection
  - Choose from 3 golden baseline configurations
  - Automatic dependency setup per configuration
  - Model downloads for local pipelines
  - Environment validation and configuration
- **Two-File Configuration Model**: 
  - `.env` for secrets and credentials (gitignored)
  - `config/ai-agent.yaml` for pipeline definitions (committed)
- **Streamlined User Journey**: From clone to first call in <15 minutes

#### Documentation
- **FreePBX Integration Guide**: Complete v4.0 guide with channel variables
  - `AI_CONTEXT`: Department/call-type specific routing
  - `AI_GREETING`: Per-call greeting customization
  - `AI_PERSONA`: Dynamic persona switching
  - Remote deployment configurations (NFS, Docker, Kubernetes)
  - Network and shared storage setup for distributed deployments
- **Configuration Reference**: Comprehensive YAML parameter documentation
- **Transport Compatibility Guide**: Validated configuration + transport combinations
- **Golden Baseline Case Studies**: Detailed performance analysis and tuning guides
- **Inline YAML Documentation**: Comprehensive comments with ranges and impacts

#### Developer Experience
- **CLI Tools**: Go-based `agent` command with 5 subcommands
  - `agent init`: Interactive setup wizard
  - `agent doctor`: Health diagnostics and validation
  - `agent demo`: Demo call functionality
  - `agent troubleshoot`: Interactive troubleshooting assistant
  - `agent version`: Version and build information
- **Enhanced Logging**: Structured logging with context and call tracking
- **RCA Tools**: Root cause analysis scripts for audio quality debugging
- **Test Infrastructure**: Baseline validation and regression testing
- **IDE Integration**: Full development context preserved in develop branch

### Changed

#### Configuration
- **YAML Structure**: Streamlined provider configuration format
- **Settings Consolidation**: Removed unused/duplicate settings (`llm.model`, `external_media.jitter_buffer_ms`)
- **downstream_mode Enforcement**: Now properly gates streaming vs file playback
- **Security Model**: Credentials **ONLY** in `.env`, never in YAML files

#### Audio Processing
- **VAD Configuration**: Optimized Voice Activity Detection for each provider
  - OpenAI Realtime: `webrtc_aggressiveness: 1` (balanced mode)
  - Server-side VAD support for providers that offer it
- **Barge-In System**: Enhanced interrupt detection with configurable thresholds
- **Audio Routing**: Fixed pipeline audio routing for AudioSocket and RTP transports

#### Performance
- **Response Times**: Validated response times for all golden baselines:
  - OpenAI Realtime: 0.5-1.5s typical
  - Deepgram Hybrid: <3s typical
  - Local Hybrid: 3-7s depending on hardware
- **Echo Cancellation**: Improved echo filtering with SSRC-based detection
- **Jitter Buffer**: Optimized buffer management for streaming playback

### Fixed

- **AudioSocket Pipeline Audio**: Fixed audio routing to STT adapters in pipeline mode
- **RTP Echo Loop**: Added SSRC-based filtering to prevent echo feedback
- **Provider Bytes Tracking**: Corrected audio chunk accounting for accurate pacing
- **Normalizer Consistency**: Fixed audio normalization for consistent output
- **Configuration Loading**: Ensured all config values properly honored at runtime
- **Sample Rate Handling**: Fixed provider-specific sample rate overrides

### Deprecated

- **Legacy YAML Templates**: Replaced with 3 golden baseline configurations
  - `ai-agent.openai-agent.yaml` → `ai-agent.golden-openai.yaml`
  - `ai-agent.deepgram-agent.yaml` → `ai-agent.golden-deepgram.yaml`
  - `ai-agent.hybrid.yaml` → `ai-agent.golden-local-hybrid.yaml`
- **Development Artifacts**: Moved to `archived/` folder (not tracked in git)

### Technical Details

#### System Requirements
- **Minimum**: 4 CPU cores, 8GB RAM (cloud configurations)
- **Recommended**: 8+ CPU cores, 16GB RAM (local pipelines)
- **GPU**: Optional for local-ai-server (improves LLM performance)

#### Compatibility
- **Asterisk**: 18+ required (for AudioSocket support)
- **FreePBX**: 15+ recommended
- **Python**: 3.10+
- **Docker**: 20.10+
- **Docker Compose**: 2.0+

#### Breaking Changes
**None** - This release maintains backward compatibility with existing deployments. Users can continue using existing configurations while adopting new features incrementally.

### Migration Guide

**No migration needed** - This is the first production release. There are no users on v3.0 requiring migration.

For new deployments:
1. Clone repository
2. Run `./install.sh` and select a golden baseline
3. Configure `.env` with your credentials
4. Deploy with `docker compose up -d`
5. Follow the FreePBX Integration Guide to configure Asterisk

### Contributors

- Haider Jarral (@hkjarral) - Architecture, implementation, documentation

### Links

- **Repository**: https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk
- **Documentation**: [docs/README.md](docs/README.md)
- **FreePBX Guide**: [docs/FreePBX-Integration-Guide.md](docs/FreePBX-Integration-Guide.md)
- **Metrics/Observability**: [docs/MONITORING_GUIDE.md](docs/MONITORING_GUIDE.md)

---

## [4.1.0] - 2025-11-10

### 🎉 Tool Calling & Agent CLI Release

Version 4.1 introduces **unified tool calling architecture** enabling AI agents to perform actions like call transfers and email management, plus production-ready **Agent CLI tools** for operations.

### Added

#### Tool Calling System
- **Unified Tool Architecture**: Write tools once, use with any provider
  - Base classes: `Tool`, `ToolDefinition`, `ToolRegistry` (`src/tools/base.py`, 231 lines)
  - Execution context with session and ARI access (`src/tools/context.py`, 108 lines)
  - Singleton registry for tool management (`src/tools/registry.py`, 198 lines)
  - Provider adapters for Deepgram (202 lines) and OpenAI Realtime (215 lines)

#### Telephony Tools
- **Transfer Call Tool**: Warm and blind transfers with direct SIP origination
  - `src/tools/telephony/transfer.py` (504 lines)
  - Department name resolution (e.g., "support" → extension 6000)
  - Warm transfer: AI stays on line until agent answers
  - Blind transfer: Immediate redirect
  - Production validated: <150ms execution time
  - Call IDs: `1762731796.4233` (Deepgram), `1762734947.4251` (OpenAI)
- **Cancel Transfer Tool**: Cancel in-progress transfer before agent answers
  - `src/tools/telephony/cancel_transfer.py`
  - Allows caller to change mind during ring
- **Hangup Call Tool**: Graceful call termination with farewell message
  - `src/tools/telephony/hangup.py`
  - Customizable farewell message
  - Works with all providers

#### Email Tools
- **Request Transcript Tool**: Caller-initiated transcript delivery
  - `src/tools/business/request_transcript.py` (475 lines)
  - Email parsing from speech ("john dot smith at gmail dot com")
  - Domain validation via DNS MX record lookup
  - Confirmation flow (AI reads back email for verification)
  - Deduplication (prevents sending same email multiple times)
  - Admin receives BCC on all transcript requests
  - Resend API integration
- **Send Email Summary Tool**: Auto-send call summaries to admin
  - `src/tools/business/email_summary.py` (347 lines)
  - Triggered automatically after every call
  - Full conversation transcript with timestamps
  - Call metadata (duration, caller ID, date/time)
  - Professional HTML formatting
  - Admin email configuration in YAML

#### Agent CLI Tools
- **Binary Distribution System**:
  - Makefile build system for 5 platforms (Linux, macOS, Windows)
  - GitHub Actions CI/CD for automated releases (`.github/workflows/release-cli.yml`)
  - One-line installer: `curl -sSL ... | bash` (`scripts/install-cli.sh`, 223 lines)
  - SHA256 checksums for security verification
  - Automated binary uploads to GitHub releases
- **CLI Commands**:
  - `agent doctor`: System health checks and validation
  - `agent troubleshoot`: Call analysis and debugging
  - `agent demo`: Feature demonstrations
  - `agent init`: Interactive setup wizard
  - `agent version`: Build and version information
- **Platform Support**:
  - Linux AMD64/ARM64 (servers, Raspberry Pi, AWS Graviton)
  - macOS AMD64/ARM64 (Intel Macs and Apple Silicon M1/M2/M3)
  - Windows AMD64
  - Pre-built binaries with automatic platform detection

#### Conversation Tracking
- **Real-time Tracking**: Both Deepgram and OpenAI Realtime track conversation turns
  - `conversation_history` field in `CallSession` model
  - Tracks role (user/assistant), content, and timestamps
  - Enables email tools to include full transcripts
  - Pattern identical across providers (46-51 lines each)

### Improved

#### Warm Transfer Implementation
- **Direct SIP Endpoint Origination**: Eliminates Local channel complexity
  - Previous: Used `Local/{ext}@{context}/n` → caused unidirectional audio
  - Current: Direct SIP origination (e.g., `SIP/6000`)
  - Result: Perfect bidirectional audio confirmed
  - No Local channels created (verified in production)
- **4-Step Cleanup Sequence**:
  1. Remove AI channel from bridge (<50ms)
  2. Stop provider session gracefully (<30ms)
  3. Add agent SIP channel to bridge (<20ms)
  4. Update session metadata (<10ms)
  - Total: <150ms for complete transfer execution
- **Production Validation**:
  - Call duration: 38+ seconds after transfer (stable)
  - Bridge type: simple_bridge (optimal, 2 channels only)
  - Audio path: Direct (1 hop, minimal latency)
  - Files: `src/tools/telephony/transfer.py`, `src/engine.py` (transfer handler)

#### OpenAI Realtime Stability
- **VAD Re-enable Timing Fix**: Correct event for greeting protection
  - Issue: Used `response.audio.done` (fires per segment) → VAD enabled too early
  - Fix: Changed to `response.done` (fires when complete response generated)
  - Impact: Greeting now plays completely before accepting interruptions
  - Lines: `src/providers/openai_realtime.py` (1206-1219)
- **API Modality Constraints**: Documented OpenAI requirements
  - Supported: `["text"]` or `["audio", "text"]`
  - Not supported: `["audio"]` alone (API rejects)
  - Known limitation: May occasionally generate text-only responses
  - Mitigation: System handles gracefully with keepalive messages
- **Race Condition Handling**: Handles variable event arrival order
  - Sometimes: `response.done` arrives before audio deltas
  - Other times: Audio deltas arrive first
  - Solution: Check `had_audio_burst` flag, re-enable VAD regardless

### Fixed

- **AAVA-57**: Direct SIP endpoint origination for warm transfers
  - Root cause: Local channels caused audio direction mismatch
  - Solution: Direct `SIP/extension` origination
  - Evidence: Call logs show no Local channels, perfect audio
- **AAVA-58**: Local channel audio direction issue (RCA documented)
  - Symptom: Caller heard agent, but agent couldn't hear caller
  - Root cause: Audio path `caller → Local;2 → Local;1 → nowhere`
  - Solution: Eliminated Local channels entirely
- **AAVA-59**: AI provider cleanup during transfer
  - Issue: AI stayed in bridge after agent answered
  - Solution: Remove external media channel before adding agent
  - Result: Clean 2-channel bridge (caller + agent only)
- **AAVA-62**: OpenAI Realtime audio generation analysis and constraints
  - Issue #1: Greeting interrupted (VAD enabled too early)
  - Issue #2: 45-second silence (OpenAI generated text-only)
  - Solution: Correct VAD timing + documented API limitation
  - Commits: `85c4235`, `80efdcd`, `6dbd51e`
- **AAVA-52**: Email tools race conditions and missing await
  - Bug #1: `context.get_session()` called without `await`
  - Bug #2: Auto-summary triggered async, session removed first
  - Bug #3: Undefined `caller_id` variable
  - Bug #4: No email confirmation flow
  - Bug #5: Duplicate emails when caller corrected address
  - Commits: `1deed05`, `700993f`, `5579ddd`, `a2d9409`, `835ac05`

### Documentation

- **New Guides**:
  - `docs/TOOL_CALLING_GUIDE.md` - Comprehensive tool calling documentation
    - Overview and supported providers
    - All 5 tools with example conversations
    - Configuration details with option explanations
    - Dialplan setup requirements
    - Testing procedures
    - Production examples with evidence
    - Troubleshooting section
    - Architecture diagrams
  - Tool sections added to `docs/FreePBX-Integration-Guide.md`
  - Enhanced `docs/CLI_TOOLS_GUIDE.md` with binary installation
- **Updated**:
  - README with v4.1 features and tool examples
  - Architecture.md with tool calling section
  - Configuration comments in `config/ai-agent.yaml`
  - SECURITY.md with v4.1 support

### Known Limitations

- **OpenAI Realtime**: May occasionally generate text-only responses
  - Root cause: API limitation with `["audio", "text"]` modalities
  - Frequency: Varies (test call had 2/4 responses without audio)
  - Impact: Caller experiences brief silence
  - Mitigation: System handles gracefully with keepalive messages
  - Not fixable: Cannot force OpenAI to always generate audio
- **Tool Calling**: Currently Deepgram and OpenAI Realtime only
  - Custom Pipeline support planned for v4.3 (AAVA-56)
  - Other providers: Anthropic Claude, Google Gemini (v4.3+)

### Architecture Validation

**Provider-Agnostic Design Confirmed**:
- Same tool code (504 lines for transfer) works with both providers
- Only adapters differ (202 lines Deepgram, 215 lines OpenAI)
- Zero code duplication in tool logic
- Adding new providers requires <250 lines of adapter code

**Line Counts**:
- Tool calling framework: 537 lines (base + context + registry)
- Transfer call tool: 504 lines (shared by all providers)
- Email summary tool: 347 lines (shared)
- Request transcript tool: 475 lines (shared)
- Deepgram adapter: 202 lines
- OpenAI adapter: 215 lines
- **Total duplication**: 0 lines ✅

### Performance Metrics

**Transfer Tool**:
- Transfer execution: <150ms
- AI cleanup time: <100ms
- Bridge technology: simple_bridge (optimal)
- Audio path: Direct (1 hop)
- Call stability: 38+ seconds validated

**Email Tools**:
- Email validation: <100ms (DNS MX lookup)
- Email delivery: ~200ms (Resend API)
- Conversation tracking: Real-time (no performance impact)

### Contributors

- Haider Jarral (@hkjarral) - Tool architecture, transfers, email tools, CLI tools, documentation

### Links

- **Repository**: https://github.com/hkjarral/Asterisk-AI-Voice-Agent
- **Tool Calling Guide**: [docs/TOOL_CALLING_GUIDE.md](docs/TOOL_CALLING_GUIDE.md)
- **FreePBX Guide**: [docs/FreePBX-Integration-Guide.md](docs/FreePBX-Integration-Guide.md)
- **CLI Tools Guide**: [cli/README.md](cli/README.md)

---

## Version History

- **v6.4.0** (2026-03-28) - Attended transfer streaming & screening, Sherpa offline STT, T-one STT, Silero TTS, HTTP wildcards, conversation timestamps, fullscreen UI
- **v6.3.2** (2026-03-12) - Azure Speech Service STT/TTS adapters, MiniMax LLM adapter, call recording playback, Google Calendar delete, security hardening
- **v6.3.1** (2026-02-23) - Local AI Server onboarding + model lifecycle hardening, tool gateway/guardrails, model catalog + UI rebuild flows, CLI verification tooling, expanded docs and audits
- **v6.2.2** (2026-02-20) - Vertex AI credentials auto-management, ADC graceful fallback, secrets dir permissions, install.sh YAML dupe fix, dashboard pipeline variant display
- **v6.2.1** (2026-02-19) - Google Vertex AI Live API Support, credential upload/verify/delete, preflight secrets dir check
- **v6.2.0** (2026-02-14) - Telnyx LLM provider, NumPy resampler, community hardening
- **v5.3.1** (2026-02-01) - Phase Tools (HTTP + webhooks) + Deepgram language + Admin UI + RCA enhancements + stability fixes
- **v5.2.5** (2026-01-28) - Stable Updates improvements + updater image publishing + AudioSocket default
- **v5.2.4** (2026-01-26) - Admin UI Docker Services hardening + remove background update checks
- **v5.2.3** (2026-01-26) - Agent update targets only impacted services on compose changes
- **v5.2.2** (2026-01-26) - Agent update explicit fetch refspec to avoid stale origin branches
- **v5.2.1** (2026-01-25) - Admin UI Updates page (branch preview/run), job history + rollback, safer updater runner
- **v5.1.7** (2026-01-24) - ExternalMedia greeting reliability, upstream squelch, hangup/transcript robustness
- **v5.1.6** (2026-01-20) - Admin UI + RCA improvements, CLI surface alignment, setup wizard fixes
- **v5.0.0** (2026-01-07) - Outbound Campaign Dialer (Alpha), Groq Speech, Ollama improvements, attended transfer
- **v4.6.0** (2025-12-29) - Admin UI config/health improvements, preflight enhancements
- **v4.5.2** (2025-12-16) - Local AI Server UX, MCP tools, Aviation ATIS
- **v4.5.1** (2025-12-13) - Admin UI improvements, wizard fixes, preflight enhancements
- **v4.5.0** (2025-12-11) - Admin UI stability, graceful shutdown, timer logging
- **v4.0.0** (2025-10-29) - Modular pipeline architecture, production monitoring, golden baselines
- **v3.0.0** (2025-09-16) - Modular pipeline architecture, file based playback

[Unreleased]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/compare/v6.4.0...HEAD
[6.4.0]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/compare/v6.3.2...v6.4.0
[6.3.2]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/compare/v6.3.1...v6.3.2
[6.3.1]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/compare/v6.2.2...v6.3.1
[6.2.2]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/compare/v6.2.1...v6.2.2
[6.2.1]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/compare/v6.2.0...v6.2.1
[6.2.0]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/compare/v6.1.1...v6.2.0
[6.1.1]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/tag/v6.1.1
[6.0.0]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/tag/v6.0.0
[5.3.1]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/tag/v5.3.1
[5.2.5]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/tag/v5.2.5
[5.2.4]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/tag/v5.2.4
[5.2.3]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/tag/v5.2.3
[5.2.2]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/tag/v5.2.2
[5.2.1]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/tag/v5.2.1
[5.1.7]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/tag/v5.1.7
[5.1.6]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/tag/v5.1.6
[5.0.0]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/tag/v5.0.0
[4.6.0]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/tag/v4.6.0
[4.5.2]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/tag/v4.5.2
[4.5.1]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/tag/v4.5.1
[4.5.0]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/tag/v4.5.0
[4.0.0]: https://github.com/hkjarral/Asterisk-AI-Voice-Agent/releases/tag/v4.0.0
