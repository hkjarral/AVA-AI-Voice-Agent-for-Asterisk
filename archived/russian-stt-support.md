# Russian STT Support — Feature Analysis Report

> Archived analysis note:
> This document started as a gap-analysis report and some sections below describe
> missing functionality that has since been implemented on the
> `russian-stt-support` branch. Treat the detailed findings below as historical
> context unless they are reaffirmed in the status update immediately after this note.

## Implementation Status Update — 2026-03-08

The branch has moved beyond analysis and now implements the core Russian-STT-enabling work for Local AI Server.

### Implemented

- **Faster-Whisper Russian wiring**
  - `FASTER_WHISPER_LANGUAGE` is now persisted, surfaced in Admin UI, exposed through the Local AI switch API, and applied at runtime.
- **Whisper.cpp Russian wiring**
  - `WHISPER_CPP_LANGUAGE` is now actually passed into runtime transcription and exposed in Admin UI / Local AI switch flows.
- **Sherpa offline transducer mode**
  - `SHERPA_MODEL_TYPE=offline` is implemented.
  - `SHERPA_VAD_MODEL_PATH` is implemented.
  - Offline Sherpa rejects streaming models instead of trying to run them incorrectly.
  - Per-session Silero VAD is implemented to avoid cross-call contamination.
  - Offline segment extraction now deep-copies and validates samples before decode.
- **Sherpa telephony tuning**
  - Implemented env-driven tuning knobs:
    - `SHERPA_VAD_THRESHOLD`
    - `SHERPA_VAD_MIN_SILENCE_MS`
    - `SHERPA_VAD_MIN_SPEECH_MS`
    - `SHERPA_OFFLINE_PREROLL_MS`
    - `SHERPA_OFFLINE_DEBUG_SEGMENTS`
- **UI / control-plane / status**
  - STT language switching for Faster-Whisper and Whisper.cpp is exposed in:
    - `System -> Models`
    - Dashboard Local AI health widget
    - Local provider configuration form
    - `System -> Environment`
  - Sherpa online/offline mode and VAD model path are exposed in:
    - `System -> Models`
    - Dashboard Local AI health widget
    - Local provider configuration form
    - `System -> Environment`
  - Local AI status/protocol surfaces now expose STT language and Sherpa model type.
- **T-one STT backend**
  - T-one recognition pipeline is wired end-to-end: `TONE_MODEL_PATH`, `TONE_DECODER_TYPE`, `TONE_KENLM_PATH` are exposed in Admin UI switch API, persisted in env, and applied at runtime.
  - Trailing-chunk flush ensures no audio is silently dropped at session close.
- **Silero TTS backend**
  - `SILERO_SPEAKER`, `SILERO_LANGUAGE`, `SILERO_MODEL_ID`, `SILERO_SAMPLE_RATE`, and `SILERO_MODEL_PATH` are wired through Admin UI, switch API, and runtime.
  - Model-path override allows custom Silero model files outside the default catalog location.
- **Model catalog**
  - Added:
    - `sherpa-onnx-zipformer-en-2023-06-26`
    - `sherpa-onnx-zipformer-gigaspeech-2023-12-12`
    - `sherpa-onnx-zipformer-ru-2024-09-18`
- **Documentation**
  - Updated:
    - `.env.example`
    - `docs/LOCAL_ONLY_SETUP.md`
    - `docs/local-ai-server/PROTOCOL.md`

### Implemented but still operationally evolving

- **Sherpa offline English validation**
  - The offline Sherpa path is now stable and materially improved for telephony.
  - Barge-in works overall.
  - Accuracy under interrupted/short utterances still needs tuning and should not be overstated as “production-complete.”

### Not implemented in this branch

- **NeMo CTC / GigaAM-specific adapter support**
  - This branch does **not** add direct NeMo CTC / GigaAM runtime support.
  - Current Sherpa Russian testing target is the **Sherpa offline transducer** path, not a dedicated NeMo CTC adapter.

### Recommended interpretation of this archived report

- Use this file for:
  - original architecture analysis
  - rationale for why Sherpa offline was needed
  - rationale for why Faster-Whisper / Whisper.cpp also mattered
- Do **not** use this file alone as the current implementation checklist without cross-checking the branch state and updated docs listed above.

**Issue**: [#258 — How to connect AVA with T-one (russian language best choice for STT)](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/issues/258)
**Date**: 2026-03-03 (updated 2026-03-07)
**Branch**: [`russian-stt-support`](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/tree/russian-stt-support)
**Reporter**: @dowletos

---

## 1. Executive Summary

Community member @dowletos wants high-accuracy Russian STT in AVA. The request evolved from **T-one** to **GigaAM** (Sherpa-ONNX NeMo transducer), which provides ~95% accuracy for Russian telephony audio.

**Critical Findings**:

1. The user's error (`'window_size' does not exist in the metadata`) occurs because GigaAM v3 NeMo transducer models are **offline-only** and cannot be used with AVA's current streaming `OnlineRecognizer.from_transducer()` implementation.
2. **There are NO streaming/online Russian models in sherpa-onnx** ([confirmed by k2-fsa#2435](https://github.com/k2-fsa/sherpa-onnx/issues/2435)). All Russian models — including all GigaAM variants (v1, v2, v3, transducer, CTC) — are offline only.
3. **Faster-Whisper already supports Russian at the runtime level** — set `FASTER_WHISPER_LANGUAGE=ru` with a multilingual model. However, this is env-var-only; the admin UI, wizard, and control-plane do not expose language switching for non-Kroko backends.
4. **Whisper.cpp Russian is broken** — `WHISPER_CPP_LANGUAGE` is accepted as config but never passed to `model.transcribe()` (`stt_backends.py` lines 674, 720, 755). The default model is also English-only (`ggml-base.en.bin`).
5. **STT language switching is Kroko-specific** in the control-plane — `_STT_CONFIG_MAP` maps `"language"` → `"kroko_language"` only (`control_plane.py:20`). `SwitchModelRequest.language` is documented "For Kroko STT" (`local_ai.py:147`).

**What already works today**:

- **Faster-Whisper Russian via env var**: `FASTER_WHISPER_LANGUAGE=ru` + multilingual model works at the STT runtime level. The gap is orchestration/UI, not transcription.
- **Vosk Russian via model swap**: Swapping the Vosk model path to a Russian model works out of the box. Lower accuracy (~80–85%).
- **Cloud providers**: Google Live, OpenAI Realtime, and Deepgram pipeline adapters all support Russian natively through their own APIs.

**Recommended Path**: Phase 1 is Faster-Whisper with `FASTER_WHISPER_LANGUAGE=ru` (env-var-configurable today). Phase 2 makes multilingual STT first-class in control-plane/UI. Phase 3 adds Sherpa `OfflineRecognizer` + VAD for GigaAM v3.

---

## 2. Severity-Tagged Findings

### HIGH

| # | Finding | Evidence |
|---|---------|----------|
| 1 | **Whisper.cpp language bug**: `WHISPER_CPP_LANGUAGE` stored but never passed to `transcribe()` | `stt_backends.py` lines 674, 720, 755 — `self.model.transcribe(self._audio_buffer)` with no `language=` arg |
| 2 | **Control-plane language is Kroko-only**: `_STT_CONFIG_MAP` maps `"language"` → `"kroko_language"` | `control_plane.py:20`; `local_ai.py:147` documents `language` as "For Kroko STT" |
| 3 | **Config shape mismatch**: Previous report proposed nested YAML that doesn't match AVA's architecture | AVA uses env vars → `LocalAIConfig.from_env()` (startup) and websocket `switch_model` → `control_plane` (runtime) |

### MEDIUM

| # | Finding | Evidence |
|---|---------|----------|
| 4 | **"Zero-code" overstated**: Faster-Whisper `ru` works at runtime but UI/wizard/control-plane don't expose it | `LocalProviderForm.tsx`, `ModelsPage.tsx`, `wizard.py`, `HealthWidget.tsx` — no language selector for non-Kroko |
| 5 | **Sherpa offline scope understated**: Requires echo-suppression, status/protocol updates, catalog entries, VAD buffering | Echo-suppression: `server.py:4091`; Status: `status_builder.py` — no language in non-Kroko STT display |
| 6 | **Unverified GigaAM claims**: "v1/v2 may have streaming" is false; "CTC variant may stream" is false | All confirmed offline-only per [k2-fsa#2435](https://github.com/k2-fsa/sherpa-onnx/issues/2435) |

### LOW

| # | Finding | Evidence |
|---|---------|----------|
| 7 | **Effort estimates conflate code-only with shipped-change**: "15-23 hours" doesn't account for tests, docs, UI | See revised estimates in Section 9 |

---

## 3. User Request Timeline

| Date | Comment | Key Points |
|------|---------|------------|
| Mar 2 | Initial request | T-one integration, replace Vosk for Russian |
| Mar 2 | Our response | Provided integration guidance for T-one |
| Mar 3 | User update | Pivoted to **GigaAM** via Sherpa-ONNX (better accuracy) |
| Mar 3 | Error reported | `'window_size' does not exist in the metadata` |
| Mar 3 | User clarification | Wants `sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian-2025-12-16` |

### User's Error Log

```text
/project/sherpa-onnx/csrc/online-transducer-nemo-model.cc:InitEncoder:323
'window_size' does not exist in the metadata
```

---

## 4. Model Options Analyzed

### 4.1 GigaAM (Recommended by User)

All GigaAM variants are **offline-only**. None support streaming.

| Model | Type | Streaming | Accuracy | Notes |
|-------|------|-----------|----------|-------|
| `sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian` | NeMo Transducer | ❌ Offline | ~95% | **User's preferred** |
| `sherpa-onnx-nemo-ctc-giga-am-v3-russian` | NeMo CTC | ❌ Offline | ~94% | CTC variant, also offline |
| `sherpa-onnx-nemo-transducer-giga-am-v2-russian` | NeMo Transducer | ❌ Offline | ~93% | Older, also offline |

**Source**: [salute-developers/GigaAM](https://github.com/salute-developers/GigaAM) (Sberbank AI)

### 4.2 Other Local Russian STT Options

| Model | Type | Streaming | Accuracy | Effort | Notes |
|-------|------|-----------|----------|--------|-------|
| **Faster-Whisper** (multilingual) | CTranslate2 | Pseudo (chunked) | ~90–95% | Env-var today; UI in Phase 2 | Runtime works; orchestration gap |
| **Whisper.cpp** (multilingual) | ggml | Pseudo (chunked) | ~90–95% | Bug fix (Phase 1a) + UI | Language param not wired |
| **Vosk Russian** | Kaldi | ✅ True streaming | ~80–85% | Model swap only | Lower accuracy |
| **T-one** | Conformer | ✅ WebSocket streaming | ~93% | High (new adapter) | 8kHz telephony-optimized |

### 4.3 Cloud Providers (Already Support Russian)

Google Live, OpenAI Realtime, and Deepgram pipeline adapters support Russian natively through their APIs — no AVA changes needed.

---

## 5. Technical Root Cause Analysis

### Current AVA Sherpa Implementation

**File**: `local_ai_server/stt_backends.py` (lines 174–334)

```python
# Current implementation uses OnlineRecognizer (streaming only)
self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
    tokens=tokens_file,
    encoder=encoder_file,
    decoder=decoder_file,
    joiner=joiner_file,
    num_threads=2,
    sample_rate=self.sample_rate,
    enable_endpoint_detection=True,
    decoding_method="greedy_search",
)
```

### Why GigaAM v3 Fails

1. **GigaAM v3 is an OFFLINE model** — uses `EncDecRNNTBPEModel` architecture which doesn't support streaming
2. **Missing metadata**: The `window_size` field is required for online (streaming) NeMo transducers but is absent in offline models
3. **Architecture mismatch**: `OnlineRecognizer.from_transducer()` expects streaming-capable models with windowed attention metadata

### Additional Requirements for Offline Sherpa

Beyond the recognizer swap, offline Sherpa needs:

- **VAD integration**: Silero VAD to segment audio into speech chunks before sending to the offline recognizer
- **Echo-suppression parity**: Whisper-family backends already use `_arm_whisper_stt_suppression()` (`server.py:4091`) to hold STT during TTS playback. Offline Sherpa needs equivalent treatment.
- **Session buffering**: Online Sherpa processes audio frame-by-frame; offline Sherpa accumulates VAD-segmented speech and transcribes in batches, changing the transcript delivery pattern.

---

## 6. AVA Architecture Analysis

### 6a. Startup Config Path

```text
Environment variables
  → LocalAIConfig.from_env()        [config.py]
    → server.__init__()             [server.py]
      → server._load_stt_model()   [server.py:1121]
        → selects backend by stt_backend value
```

Relevant env vars for multilingual STT:

| Env Var | Status | Notes |
|---------|--------|-------|
| `FASTER_WHISPER_LANGUAGE` | ✅ Works | Passed to `model.transcribe(language=...)` |
| `WHISPER_CPP_LANGUAGE` | ❌ Bug | Stored in config but never passed to `transcribe()` |
| `SHERPA_MODEL_PATH` | ✅ Works | For online models only |
| `SHERPA_MODEL_TYPE` | ❌ Missing | Needed for online vs offline selection |
| `SHERPA_VAD_MODEL_PATH` | ❌ Missing | Needed for offline VAD |

### 6b. Runtime Switch Path

```text
Admin UI / websocket client
  → sends { "type": "switch_model", ... }
    → ws_protocol.py:163
      → model_manager.switch_model()     [model_manager.py:15]
        → control_plane.apply_switch_model_request()  [control_plane.py:90]
          → _apply_config_dict() with _STT_CONFIG_MAP
            → config update + server.reload_models()
```

Current `_STT_CONFIG_MAP` (`control_plane.py:10–26`):

```python
_STT_CONFIG_MAP = {
    "model_path": "stt_model_path",
    "sherpa_model_path": "sherpa_model_path",
    "kroko_model_path": "kroko_model_path",
    "whisper_cpp_model_path": "whisper_cpp_model_path",
    "kroko_url": "kroko_url",
    "kroko_language": "kroko_language",
    "kroko_port": "kroko_port",
    "kroko_embedded": "kroko_embedded",
    "url": "kroko_url",
    "language": "kroko_language",          # ← Kroko-only!
    "port": "kroko_port",
    "embedded": "kroko_embedded",
    "model": "faster_whisper_model",
    "device": "faster_whisper_device",
    "compute_type": "faster_whisper_compute",
}
```

**Missing entries**: `faster_whisper_language`, `whisper_cpp_language`, `sherpa_model_type`, `sherpa_vad_model_path`.

---

## 7. Protocol / Status / API Surface Gaps

### 7a. Status Builder (`status_builder.py`)

`_stt_status()` reports language only for Kroko:

- Kroko: `f"Kroko ({server.kroko_language})"` (line 20)
- Faster-Whisper: `f"Faster-Whisper ({server.faster_whisper_model})"` (line 31) — no language
- Whisper.cpp: `"Whisper.cpp"` (line 36) — no language
- Sherpa: `os.path.basename(server.sherpa_model_path)` (line 26) — no language or mode

**Needed**: Add language to STT status for all backends. Add model type (online/offline) for Sherpa.

### 7b. Protocol Contract (`protocol_contract.py`)

`SwitchModelRequest` schema (lines 164–188) includes `kroko_language` but no:

- `faster_whisper_language`
- `whisper_cpp_language`
- `sherpa_model_type`
- `sherpa_vad_model_path`

`StatusResponse` schema does not require language in the STT model status object.

**Note**: All schemas use `additionalProperties: true`, so new fields won't break existing callers. But they should be documented in the schema for tooling/UI consumers.

### 7c. PROTOCOL.md (`docs/local-ai-server/PROTOCOL.md`)

Model Switching section (line 427+) shows `switch_model` examples only for Kroko language switching. Needs examples for:

- Faster-Whisper language: `{ "type": "switch_model", "stt_backend": "faster_whisper", "stt_config": { "language": "ru", "model": "medium" } }`
- Sherpa offline mode: `{ "type": "switch_model", "stt_backend": "sherpa", "sherpa_model_path": "...", "sherpa_model_type": "offline", "sherpa_vad_model_path": "..." }`

### 7d. Admin API (`admin_ui/backend/api/local_ai.py`)

`SwitchModelRequest.language` (line 147) is documented "For Kroko STT". Needs generalization or per-backend language fields:

```python
# Current
language: Optional[str] = None  # For Kroko STT

# Needed
faster_whisper_language: Optional[str] = None
whisper_cpp_language: Optional[str] = None
sherpa_model_type: Optional[str] = None    # online | offline
sherpa_vad_model_path: Optional[str] = None
```

---

## 8. Configuration Schema (Corrected)

**Previous report proposed a nested YAML schema. This is wrong.** AVA uses env vars + websocket control-plane, not a new YAML config layer.

### Startup (env vars in `.env`)

```bash
# Faster-Whisper Russian (works today)
LOCAL_STT_BACKEND=faster_whisper
FASTER_WHISPER_MODEL=medium
FASTER_WHISPER_LANGUAGE=ru

# Whisper.cpp Russian (requires bug fix first)
LOCAL_STT_BACKEND=whisper_cpp
WHISPER_CPP_MODEL_PATH=/app/models/stt/ggml-medium.bin   # Must be multilingual, NOT .en
WHISPER_CPP_LANGUAGE=ru

# Sherpa offline GigaAM (requires Phase 3 implementation)
LOCAL_STT_BACKEND=sherpa
SHERPA_MODEL_PATH=/app/models/stt/sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian
SHERPA_MODEL_TYPE=offline           # NEW: online (default) | offline
SHERPA_VAD_MODEL_PATH=/app/models/vad/silero_vad.onnx  # NEW
```

### Runtime (control-plane additions to `_STT_CONFIG_MAP`)

```python
# Additions needed in control_plane.py _STT_CONFIG_MAP:
"faster_whisper_language": "faster_whisper_language",
"whisper_cpp_language": "whisper_cpp_language",
"sherpa_model_type": "sherpa_model_type",
"sherpa_vad_model_path": "sherpa_vad_model_path",
```

### Model Compatibility Guardrails

When `language` is set to a non-English value (e.g. `ru`):

- **Reject/warn** if the Whisper model name contains `.en` (e.g. `ggml-base.en.bin`, `base.en`)
- **Warn** if using `tiny` or `base` models (lower multilingual accuracy)
- **Recommend** `medium` or `large-v3` for non-English languages

---

## 9. Options Evaluation Matrix (Corrected)

| Criteria | Faster-Whisper `ru` | Whisper.cpp `ru` | Vosk Russian | GigaAM v3 (Sherpa Offline) | T-one |
|----------|---------------------|------------------|--------------|---------------------------|-------|
| **Runtime works today?** | ✅ Yes (env var) | ❌ Bug — lang not wired | ✅ Yes (model swap) | ❌ Needs offline mode | ❌ Needs adapter |
| **First-class in UI?** | ❌ No (Kroko-only lang) | ❌ No | ❌ No lang selector | ❌ No | ❌ No |
| **Code changes (code-only)** | 0 lines | ~3 lines (bug fix) | 0 lines | ~300–500 lines | ~400+ lines |
| **Shipped effort (incl. tests, docs, UI)** | ~1 hr (docs) | ~2 hrs | ~1 hr (docs) | ~28–36 hrs | ~30+ hrs |
| **Regression risk** | None (runtime) | Low (bug fix) | None | Medium | Medium |
| **Russian accuracy** | ~90–95% (large-v3) | ~90–95% (large-v3) | ~80–85% | ~95% (best) | ~93% |
| **Streaming** | Pseudo (chunked) | Pseudo (chunked) | True streaming | Pseudo (VAD-gated) | True streaming |
| **Latency** | ~1.5s per utterance | ~1.5s per utterance | Real-time | ~0.5–1s (VAD-gated) | ~0.3s |
| **Model size** | 1.5GB (large-v3) | 1.5GB (large-v3) | ~50MB | ~450MB | ~500MB |

---

## 10. Recommended Path Forward

### Phase 1a: Fix Whisper.cpp Language Bug

Pass `self.language` to `model.transcribe()` at three call sites in `stt_backends.py` (lines 674, 720, 755).

| Metric | Value |
|--------|-------|
| Code-only | ~1 hr |
| Shipped (incl. test) | ~2 hrs |
| Risk | Low |

### Phase 1b: Document Faster-Whisper Russian (Already Works)

Faster-Whisper `FASTER_WHISPER_LANGUAGE=ru` works at the runtime level today. Document it in `.env.example` and user-facing guides. Note the orchestration/UI gap.

| Metric | Value |
|--------|-------|
| Code-only | 0 hrs |
| Shipped (docs) | ~1 hr |
| Risk | None |

### Phase 2: Language-General Control-Plane + UI + Guardrails

Make multilingual STT first-class across the orchestration surface:

- Add `faster_whisper_language`, `whisper_cpp_language` to `_STT_CONFIG_MAP` and `SwitchModelRequest`
- Add language to `_stt_status()` display for all backends
- Update `protocol_contract.py` schema and `PROTOCOL.md` documentation
- Add language selector in `LocalProviderForm.tsx`, `ModelsPage.tsx`, `HealthWidget.tsx`
- Add `.en` model guardrails (reject/warn when language is non-English with English-only model)
- Update `EnvPage.tsx` for new env vars
- Update wizard for multilingual presets

| Metric | Value |
|--------|-------|
| Code-only | ~8–12 hrs |
| Shipped (incl. tests, docs, UI) | ~16–20 hrs |
| Risk | Low–Medium |

### Phase 3: Sherpa Offline Mode + VAD for GigaAM

Add `OfflineRecognizer` support behind the existing `sherpa` backend, default-off:

- Explicit `SHERPA_MODEL_TYPE=offline` mode flag (default: `online`, preserving current behavior)
- `OfflineRecognizer.from_transducer()` + Silero VAD integration
- VAD session buffering (accumulate speech segments, batch transcribe)
- Echo-suppression parity with `_arm_whisper_stt_suppression()`
- Status/schema exposure (model type, VAD state in `status_builder.py` and `protocol_contract.py`)
- Model catalog entries for GigaAM assets

```python
# Sketch: OfflineRecognizer + VAD integration
import sherpa_onnx

# Create VAD for speech segmentation
vad = sherpa_onnx.VoiceActivityDetector(
    model="/path/to/silero_vad.onnx",
    sample_rate=16000,
    min_silence_duration=0.25,
    min_speech_duration=0.25,
)

# Create OFFLINE recognizer for GigaAM v3
recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
    tokens=tokens_file,
    encoder=encoder_file,
    decoder=decoder_file,
    joiner=joiner_file,
    num_threads=4,
    sample_rate=16000,
    decoding_method="greedy_search",
)

# Process audio with VAD-gated transcription
def process_audio(audio_chunk):
    vad.accept_waveform(audio_chunk)
    if vad.is_speech_finished():
        speech_segment = vad.get_speech()
        stream = recognizer.create_stream()
        stream.accept_waveform(16000, speech_segment)
        recognizer.decode_stream(stream)
        return stream.result.text
    return None
```

| Metric | Value |
|--------|-------|
| Code-only | ~16–24 hrs |
| Shipped (incl. tests, docs, catalog) | ~28–36 hrs |
| Risk | Medium |

### Phase 4: Russian Presets, Model Catalog, Auto-Download

- Russian-capable model catalog entries in `models_catalog.py`
- Auto-download hints for Vosk Russian, Faster-Whisper multilingual, GigaAM assets
- Wizard Russian presets
- Deployment profile for Russian-language operation

| Metric | Value |
|--------|-------|
| Code-only | ~8–12 hrs |
| Shipped (incl. tests, docs, UI) | ~14–18 hrs |
| Risk | Low |

### NOT Recommended

- **Vosk Russian**: Accuracy too low (~80–85%) for production
- **T-one**: High effort (new adapter), requires separate server, accuracy not better than Faster-Whisper
- **Hacking GigaAM into OnlineRecognizer**: Fundamentally impossible — model architecture doesn't support streaming
- **New nested YAML config layer**: Conflicts with AVA's env-var + control-plane architecture

---

## 11. Effort Summary

| Phase | Scope | Code-Only | Shipped Change | Risk |
|-------|-------|-----------|----------------|------|
| 1a | Fix Whisper.cpp language bug | ~1 hr | ~2 hrs | Low |
| 1b | Document Faster-Whisper `ru` | 0 hrs | ~1 hr | None |
| 2 | Language-general control-plane + UI + guardrails | ~8–12 hrs | ~16–20 hrs | Low–Medium |
| 3 | Sherpa offline + VAD + echo-suppression + status | ~16–24 hrs | ~28–36 hrs | Medium |
| 4 | Russian presets, catalog, auto-download, wizard | ~8–12 hrs | ~14–18 hrs | Low |
| **Total** | | **~33–49 hrs** | **~61–77 hrs** | |

---

## 12. Regression Risk Assessment

### Impact by Phase

| Phase | Component | Risk | Mitigation |
|-------|-----------|------|------------|
| 1a | Whisper.cpp transcription | Low | 3-line bug fix; existing English behavior unchanged if `language=en` (default) |
| 2 | Control-plane field additions | Low | `additionalProperties: true` in protocol schema; existing `switch_model` payloads unaffected |
| 2 | UI form changes | Low–Medium | Language selector is additive; existing provider form state preserved |
| 2 | `.en` model guardrails | Low | Warning only; doesn't block operation |
| 3 | Existing Sherpa online users | Medium | `SHERPA_MODEL_TYPE` defaults to `online`; existing behavior unchanged unless explicitly set to `offline` |
| 3 | Echo-suppression for offline Sherpa | Medium | Must implement `_arm_whisper_stt_suppression()` parity; without it, self-echo during TTS |
| 3 | VAD-gated transcript delivery | Medium | Changes transcript timing pattern (batch vs frame-by-frame); may affect turn-taking and barge-in |

### Telephony Risks

- **CPU-only deployments**: Larger multilingual Whisper models (`medium`, `large-v3`) are significantly slower on CPU. May need GPU recommendation for production Russian STT.
- **Sherpa offline + VAD latency**: VAD-gated recognition changes from token-streaming to utterance-batched, potentially delaying turn-taking. VAD thresholds need careful tuning.
- **Self-echo during full-local operation**: Whisper-family backends already suppress STT during TTS playback (`server.py:4091`). Offline Sherpa needs equivalent treatment or TTS audio will be transcribed as caller speech.

---

## 13. Testing Plan

### Unit Tests

- Control-plane field mapping for `faster_whisper_language`, `whisper_cpp_language`, `sherpa_model_type`
- Model/language validation: `.en` model rejection when `language=ru`
- Whisper.cpp language pass-through to `transcribe()`
- Sherpa online/offline mode selection
- Status builder language display for all backends

### Integration Tests

- `switch_model` + `status` round-trip with new language fields
- Faster-Whisper Russian end-to-end transcription
- Sherpa online regression (existing English models still work)
- Sherpa offline Russian transcription path
- Vosk Russian model swap

### Telephony Tests

- AudioSocket and ExternalMedia call tests with Russian audio fixtures
- Barge-in during Sherpa offline VAD buffering
- First-transcript and final-transcript latency measurements
- TTS echo-leak tests (verify STT suppression works for offline Sherpa)
- CPU-only and GPU test runs

### Failure Cases

- Missing model file
- Missing VAD asset for offline Sherpa
- Online/offline Sherpa model type mismatch
- `.en` Whisper model with `language=ru`
- Hot-switch during active call sessions
- Empty/duplicate final transcripts

---

## 14. Files Affected (Complete Inventory)

### Production Files

| File | Phase | Change |
|------|-------|--------|
| `local_ai_server/stt_backends.py` | 1a, 3 | Fix Whisper.cpp language bug; add Sherpa offline mode + VAD |
| `local_ai_server/config.py` | 2, 3 | Add `sherpa_model_type`, `sherpa_vad_model_path` env vars |
| `local_ai_server/control_plane.py` | 2 | Add `faster_whisper_language`, `whisper_cpp_language`, Sherpa fields to `_STT_CONFIG_MAP` |
| `local_ai_server/status_builder.py` | 2 | Add language to STT status display for all backends |
| `local_ai_server/protocol_contract.py` | 2 | Add new fields to `SwitchModelRequest` and `StatusResponse` schemas |
| `local_ai_server/server.py` | 3 | Sherpa offline loading, echo-suppression parity, VAD session buffering |
| `admin_ui/backend/api/local_ai.py` | 2 | Generalize `SwitchModelRequest.language` beyond Kroko |
| `admin_ui/backend/api/wizard.py` | 4 | Russian preset in wizard flow |
| `admin_ui/backend/api/models_catalog.py` | 4 | Russian model catalog entries |
| `admin_ui/frontend/.../LocalProviderForm.tsx` | 2 | Language selector for Faster-Whisper/Whisper.cpp |
| `admin_ui/frontend/.../ModelsPage.tsx` | 2, 4 | Language display; Russian model entries |
| `admin_ui/frontend/.../HealthWidget.tsx` | 2 | Show STT language in health display |
| `admin_ui/frontend/.../EnvPage.tsx` | 2 | Env-var settings page for new STT language/Sherpa vars |
| `docs/local-ai-server/PROTOCOL.md` | 2, 3 | Document new `switch_model` fields and examples |
| `.env.example` | 2, 3 | Document new env vars |

### Test Files

| File | Phase | Coverage |
|------|-------|----------|
| `tests/test_control_plane.py` | 2 | New language field mapping |
| `tests/test_stt_backends.py` | 1a, 3 | Whisper.cpp language pass-through; Sherpa mode selection |
| `tests/test_status_builder.py` | 2 | Language in STT status |
| `tests/test_protocol_contract.py` | 2 | Schema validation with new fields |
| `tests/test_sherpa_offline.py` | 3 | New: offline recognizer + VAD integration |
| Russian telephony fixtures | 3 | New: Russian audio samples for E2E testing |

---

## 15. Dependencies & Prerequisites

### Required Downloads (Phase 3)

```bash
# GigaAM v3 Transducer (user's preferred)
wget https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian-2025-12-16.tar.bz2

# Silero VAD (required for offline models)
wget https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx
```

### Python Dependencies

```text
sherpa-onnx>=1.10.0  # Already in requirements
onnxruntime>=1.16.0  # Already in requirements
```

### Disk Space

| Model | Size |
|-------|------|
| GigaAM v3 Transducer | ~450 MB |
| Silero VAD | ~2 MB |
| Faster-Whisper medium | ~1.5 GB |
| Faster-Whisper large-v3 | ~3 GB |

---

## 16. References

- [sherpa-onnx NeMo Models](https://k2-fsa.github.io/sherpa/onnx/nemo/index.html)
- [GigaAM GitHub](https://github.com/salute-developers/GigaAM)
- [GigaAM v3 HuggingFace](https://huggingface.co/csukuangfj/sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian-2025-12-16)
- [k2-fsa/sherpa-onnx#2435](https://github.com/k2-fsa/sherpa-onnx/issues/2435) — Confirms no streaming Russian models
- [sherpa-onnx Issue #2216](https://github.com/k2-fsa/sherpa-onnx/issues/2216) — `window_size` metadata error
- [T-one STT](https://github.com/voicekit-team/T-one)
- [k2-fsa ASR Models](https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models)
- [AVA Protocol Docs](docs/local-ai-server/PROTOCOL.md)

---

## Appendix: User Comments Summary

### @dowletos (Mar 3, 2026)

> "Could someone help me with integration GigaAM STT to AVA? Exactly this model provides the best productivity and accuracy."

> "I have already installed Sherpa_English language STT availiable at AVA and runned it. All is ok. But when i switched the Sherpa_English to the russian model downloaded i got an error..."

> "Me personally not a software engineer and it could take months till i will get into the AVA project deeply. Appreciate your help it would help a thousands of people."

### Key User Needs

1. High accuracy Russian STT (~95%)
2. Full offline/local operation
3. Compatible with existing AVA infrastructure
4. Non-technical user — needs turnkey solution
