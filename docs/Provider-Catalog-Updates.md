# Provider model and voice catalog updates

Verified against the official sources recorded in
[`src/utils/provider_catalog.json`](../src/utils/provider_catalog.json) on
2026-11-23. The catalog describes implemented choices, not account entitlement
or proof of telephone-call quality.

## Defaults and existing installations

New-provider defaults now use OpenAI Realtime `gpt-realtime-2.1`, OpenAI modular
LLM `gpt-6-luna` with `reasoning_effort: none`, STT `gpt-transcribe`, Google Live
`gemini-3.8-live`, DeepSeek `deepseek-flash`, Telnyx
`zai-org/GLM-5.3-Flash`, and ElevenLabs `eleven_flash_v2_5`. Google modular LLM
uses `gemini-3.8-flash`. Deepgram keeps Nova-3 and Aura v1 by default, with
`aura-2-thalia-en` and the managed OpenAI Think model `gpt-5.6-luna`.

Explicit saved model IDs, voices, endpoints and credential references are not
rewritten. Deprecated model choices remain visible with lifecycle labels where
they still accept requests. Retired suggestions are removed; custom configured
values remain visible for correction. Missing values use the refreshed runtime
defaults, so review configurations that deliberately relied on omitted fields.
The sample YAML is a fresh-install example, not a replacement for operator YAML.

Provider changes require the normal engine restart and affect newly started
calls. Agent voice changes retain their existing next-call behavior. Back up
operator YAML and use a separate test Agent/pipeline before selecting a new API
workflow. Do not replace existing provider blocks simply to refresh a catalog.

## OpenAI modular TTS migration

The Speech API models `tts-1`, `tts-1-hd`, and `gpt-4o-mini-tts` retire on
January 6, 2027. The documented replacement is a different workflow:
`gpt-realtime-2.1-mini` is used through Realtime, not `/v1/audio/speech`.
See the [official migration notice](https://developers.openai.com/api/docs/guides/text-to-speech).

```yaml
openai_tts:
  type: openai
  capabilities: [tts]
  enabled: true
  tts_model: gpt-realtime-2.1-mini
  tts_transport: auto
  tts_realtime_base_url: wss://api.openai.com/v1/realtime
  tts_realtime_timeout_sec: 30
  voice: marin
```

`auto` selects Realtime for `gpt-realtime*` model IDs. Explicit legacy or custom
Speech model IDs continue to use the configured Speech endpoint. An explicit
`speech`/`realtime` transport must agree with its model. Realtime synthesis owns
a tools-disabled, bounded session per text fragment; cancellation/hangup closes
its sockets. It converts native PCM to the negotiated telephone output format.
`tts_realtime_timeout_sec` bounds each fragment (30 seconds by default) without
changing the existing Speech/LLM/STT timeout. `tts_instructions` optionally controls delivery; the text is requested verbatim.

Chat-completion request parameters also depend on the model. Modern OpenAI
models use `max_completion_tokens`; unsupported temperature settings are omitted.
The default Luna tool path uses `reasoning_effort: none`. GPT-6.2 tool calling
requires the Responses API and is rejected rather than silently losing tools.
OpenAI-compatible gateways retain their own request parameters and model IDs.
Model access must be enabled for the API key/project; a documented model can still
return `403 model_not_found` until the account permits it.

## Deepgram v2 and per-provider isolation

Aura uses `tts_version: v1`. Flux TTS is an explicit opt-in:

```yaml
deepgram_flux_tts:
  type: deepgram
  capabilities: [tts]
  enabled: false
  tts_version: v2
  tts_model: flux-kit-en
  tts_speed: 1.05
  tts_expressivity: 0
```

Full-agent Settings and minimal retry payloads use the same selected TTS
version/model. Modular Flux synthesis uses `/v2/speak`. Flux speed is 0.5–1.5
in 0.05 increments and expressivity is an integer from -2 to 2; Aura speed
starts at 0.7 and does not accept Flux expressivity. See
[Deepgram TTS contracts](https://developers.deepgram.com/docs/voice-agent-tts-models).

The shared catalogs contain 103 Aura voices and 266 Flux voices. The Agent
picker filters by the selected provider's TTS version and language. Stored
mismatches remain visible and fail closed; a voice selection never silently
changes its language or API family.

Modular Flux STT supports `flux-general-en` and `flux-general-multi`. The latter
accepts `language_hints: [en, es]`; the adapter uses the v2 query contract.
Explicit modular speech blocks now own their configuration and credentials,
including custom instance keys. A disabled, invalid or uncredentialed role block
does not inherit a full-agent provider's factory or another customer's settings.

## Google Extended Thinking

`gemini-3.8-live-extended-thinking` is a separate, explicit selection. Its
Developer API connection uses the documented alpha endpoint and
`thinking_level: low|medium|high` when supplied. Intermediate utterance
`turnComplete` events do not finish the background interaction; `IDLE` does.
See [Live model contracts](https://aistudio.google.com/docs/live-api/capabilities).

Extended Thinking requires non-blocking tool declarations. AVA deliberately
keeps telephony and state-changing tools blocking. Configurations containing
those tools are rejected before setup; this option is for tool-free or explicitly
compatible read-only configurations. Vertex Extended Thinking is not advertised
as qualified. Standard Gemini 3.8 Live retains normal telephony tool behavior.
The verified standard 3.8 Vertex region list includes `asia-south1` (Mumbai).

## Other catalogs and local voices

- Groq Arabic Orpheus has six Arabic voices. A model-only Arabic configuration
  chooses `abdullah`; an explicitly incompatible English voice is rejected.
- Fish Audio exposes `s2.1-pro`, `s2.1-pro-free`, `s2-pro`, `s1`, `drama-3`, and
  `s3-pro`. S1 is deprecated with a December 31, 2026 retirement. The new IDs
  are qualified choices, not an assertion of access on every account.
- CAMB includes the MARS 8.1 beta choices; parameter support is model-dependent.
- ElevenLabs removes the retired v1 suggestion, recommends Flash over Turbo,
  and offers only audio output formats that the adapter can actually decode.
- Piper retains its exact 142-entry pinned upstream catalog. Kokoro exposes
  all 54 upstream voices with language metadata. Local AI voice/language are
  server-wide settings; select them together in System → Environment or through
  the model-switch API. Do not mutate global models in response to a live call.
  New non-English voices require their language/G2P dependencies and voice files.
- Local STT/TTS backend suggestions include T-one and Matcha. Local model weights
  are not retired by hosted-API lifecycle notices.

## Baseline → implemented provider coverage

The baseline below is the task branch's original `origin/main` revision
(`329661ea`, v7.7.4). “Retained” means the existing choices were checked and
kept; it does not imply that every model in a provider's product portfolio is
implemented. Voice IDs supplied by an account or hosted Agent remain editable
instead of being replaced with a supposedly exhaustive static list.

| Provider / supported role | Baseline | Implemented result and operator action | Official reference |
| --- | --- | --- | --- |
| OpenAI Realtime — full | `gpt-realtime`, 1.5, 2 and mini; 10 voices; input transcription fixed to Whisper. | Add 2.1 and 2.1-mini; new default 2.1. Retain the 10 voices. Expose input transcription (new default `gpt-transcribe`) and optional reasoning effort for the 2 family. Old realtime/mini IDs carry the January 20, 2027 deadline. | [Realtime models and conversation contract](https://developers.openai.com/api/docs/guides/realtime-conversations), [deprecations](https://developers.openai.com/api/docs/deprecations) |
| Deepgram Voice Agent — full | Nova-3/Nova-2 phonecall/Flux recognition, 103 Aura voices; legacy Asteria default; no TTS-version control. | Retain recognition choices and Aura catalog; default to Aura-2 Thalia. Add explicit Flux TTS v2 with 266 voices/42 languages, speed/expressivity controls and family/language validation. Expose managed Think choices with `gpt-5.6-luna` default. Flux TTS requires an explicit selection and a real-call test. | [Agent STT](https://developers.deepgram.com/docs/voice-agent-stt-models), [Agent LLMs](https://developers.deepgram.com/docs/voice-agent-llm-models), [Agent TTS](https://developers.deepgram.com/docs/voice-agent-tts-models), [Flux voices](https://developers.deepgram.com/docs/flux-tts/voices) |
| Google Live — full | Standard 3.8 already selectable; 2.5 latest default; 30 voices; 3.8 Vertex regions US/EU/Iowa. | Make standard 3.8 the new default; retain all 30 voices and compatible legacy model choices. Add Mumbai for standard 3.8 Vertex. Add Developer-only 3.8 Extended Thinking with optional thinking level and enforced tool restrictions; do not select it for ordinary telephony-tool Agents. | [Live capabilities](https://aistudio.google.com/docs/live-api/capabilities), [3.8 Live Vertex locations](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-8-live) |
| xAI Grok — full | Latest and versioned 3.0, 28 built-in voices and reasoning controls already supported. | Retain these choices/defaults and synchronize the shared catalog; preserve account-specific cloned voice IDs. No additional transport migration is needed. | [Voice Agent](https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech), [voices](https://docs.x.ai/developers/model-capabilities/audio/text-to-speech) |
| ElevenLabs Conversational Agent — full | Hosted Agent selected by `agent_id`; its model, voices and tools are managed in ElevenLabs. | Retain hosted-Agent behavior and configured IDs. Modular ElevenLabs TTS model changes below do not rewrite the hosted Agent. | [Hosted Agent configuration](https://elevenlabs.io/docs/eleven-agents/overview) |
| OpenAI / compatible gateways — modular LLM | Suggestions centered on 4o, 4 Turbo and 3.5 Turbo; runtime default 4o-mini. | New OpenAI default `gpt-6-luna` with `reasoning_effort: none`; add 6.2 Sol/Astra as tool-free Chat Completions choices. Reject their unsupported tool path. Preserve gateway/custom IDs and parameters; label retained 4o choices with the January 14, 2027 deadline. | [Luna](https://developers.openai.com/api/docs/models/gpt-6-luna), [Sol](https://developers.openai.com/api/docs/models/gpt-6.2-sol), [Astra](https://developers.openai.com/api/docs/models/gpt-6.2-astra) |
| OpenAI / compatible gateways — modular STT | Whisper-1 default and two 4o transcription suggestions. | Default to `gpt-transcribe`; include legacy/dated transcription choices with their February 26, 2027 deadline. Preserve explicit gateway models. | [Model catalog](https://developers.openai.com/api/docs/models/all), [transcription retirements](https://developers.openai.com/api/docs/deprecations) |
| OpenAI / compatible gateways — modular TTS | Speech API only; `tts-1` default, three model suggestions and one shared voice list. | Add bounded Realtime synthesis with new default 2.1-mini and explicit auto/speech/realtime controls. Filter voices by model (9 legacy Speech, 13 mini-TTS, 10 Realtime); include missing Verse/Marin/Cedar where supported. Existing Speech selections remain Speech and show the January 6, 2027 deadline. | [TTS and migration](https://developers.openai.com/api/docs/guides/text-to-speech) |
| Deepgram — modular STT | Runtime adapters existed but Nova/Flux presets were missing from the modular registry. | Add Nova and Flux presets using the real adapter type, including multilingual Flux and `language_hints`. Custom role instances own their credentials/configuration. | [Recognition model choices](https://developers.deepgram.com/docs/voice-agent-stt-models) |
| Deepgram — modular TTS | Free-text Aura model; Asteria default; v1 synthesis. | Share the 103 Aura/266 Flux voice catalogs; default Aura-2 Thalia. Add opt-in v2 synthesis, version/model agreement, speed and Flux expressivity controls. | [Aura models](https://developers.deepgram.com/docs/tts-models), [Flux voices](https://developers.deepgram.com/docs/flux-tts/voices) |
| Google — modular LLM | 3.8 Flash and 3.6 Flash suggestions. | Retain both; add 3.5 Flash-Lite and share the 3.8 default. Retain model-dependent temperature handling. | [Google model lifecycle](https://aistudio.google.com/docs/deprecations) |
| Google Cloud — modular STT / TTS | Cloud speech adapters; no STT registry preset; free-text TTS voice default `en-US-Neural2-C`. | Add Cloud STT preset/role wiring. Retain editable Cloud TTS voice IDs, encoding/rate controls and Neural2-C default; no static claim to list every regional Cloud voice. | [Cloud voices and supported types](https://docs.cloud.google.com/text-to-speech/docs/list-voices-and-types) |
| DeepSeek — modular LLM | `deepseek-v4-flash` and `deepseek-v4-pro` suggestions. | Replace the Flash suggestion/default with the current official `deepseek-flash`; retain V4 Pro and existing explicit IDs. | [Current model IDs](https://api-docs.deepseek.com/quick_start/pricing/) |
| Telnyx — modular LLM | Older Qwen3-235B and Llama 3.1 suggestions. | Replace suggestions with seven current catalog models across GLM, Kimi, MiniMax, DeepSeek, Qwen and Gemma; new default `zai-org/GLM-5.3-Flash`. Existing custom IDs remain visible. | [Inference models](https://developers.telnyx.com/docs/inference/models) |
| MiniMax — modular LLM | M3 plus M2.7/M2.7-highspeed suggestions. | Retain M3 default; replace stale suggestions with current `MiniMax-M3.1-Flash`. | [OpenAI-compatible model contract](https://platform.minimax.io/docs/api-reference/text-openai-api) |
| Groq — modular STT / TTS | Two Whisper models, English/Arabic Orpheus model choices, but six English voice suggestions only. | Retain both STT models and both TTS models; add six Arabic voices and model-aware voice filtering. A model-only Arabic config receives Abdullah; an explicit incompatible voice is rejected. | [Whisper](https://console.groq.com/docs/speech-to-text), [Orpheus voices](https://console.groq.com/docs/text-to-speech/orpheus) |
| ElevenLabs — modular TTS | Turbo 2.5 default; Multilingual v2 and retired Monolingual v1 suggestions; MP3 offered despite decoder limits. | Default Flash 2.5; add v3, v4 and v4 Turbo; retain Multilingual v2 and mark Turbo deprecated. Remove v1/unsupported output suggestions. Restrict v3 to discrete stability; v4 sends stability/similarity only. Preserve account voice IDs. | [Models](https://elevenlabs.io/docs/overview/models), [TTS API](https://elevenlabs.io/docs/api-reference/text-to-speech/convert), [model-dependent controls](https://elevenlabs.io/docs/eleven-creative/playground/text-to-speech) |
| Fish Audio — modular TTS | S2.1 Pro/free, S2 Pro, S1 and `drama-3-preview`; account voice reference. | Retain S2 choices/default; add `drama-3` and `s3-pro`; replace the stale preview suggestion and mark S1's December 31, 2026 retirement. Voice references remain account supplied. | [Models](https://docs.fish.audio/developer-guide/models-pricing/models-overview), [TTS contract](https://docs.fish.audio/api-reference/endpoint/openapi-v1/text-to-speech.md) |
| CAMB AI — modular TTS | MARS Flash/Pro/Instruct; numeric voice ID. | Retain those defaults/choices and editable voice IDs; add 8.1 Flash/Pro beta with qualified PCM output settings. Access and telephone quality still need account/call acceptance. | [Streaming TTS contract](https://docs.camb.ai/api-reference/endpoint/create-tts-stream) |
| Azure Speech — modular STT / TTS | Realtime/fast recognition; Jenny/Guy/Sonia voice suggestions and custom IDs. | Retain supported variants and the three curated voice suggestions; update fast-transcription API version to `2025-10-15`. Custom regional voices remain editable. | [Fast transcription](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/fast-transcription-create), [language/voice support](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support) |
| Ollama — modular LLM | Editable installed-model ID with six curated suggestions. | Retain suggestions/default and dynamic installed-model selection. Hosted provider retirements do not remove local weights. | [Installed-model API](https://docs.ollama.com/api/tags) |
| Local AI Server — full / modular STT / LLM / TTS | Existing local adapters/weights; STT backend picker omitted T-one and TTS picker omitted Matcha. | Add the missing backend choices while retaining Vosk/Piper defaults and existing LLM controls. No automatic weight download or global model switch occurs on voice selection. | [Local speech backends](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/index.html) |
| Local Piper / Kokoro — TTS voice catalogs | Pinned Piper catalog of 142 entries; 11 Kokoro voice files. | Keep the exact 142 Piper entries; expose all 54 Kokoro voices with language metadata and server-global voice/language controls. Generated UI data and CI validate both catalogs. Install required voice/G2P files before non-English calls. | [Pinned Piper voices](https://huggingface.co/rhasspy/piper-voices/tree/v1.0.0), [Kokoro voice files](https://huggingface.co/hexgrad/Kokoro-82M/tree/main/voices) |

## Keeping catalogs synchronized

Cloud model/voice data, defaults, lifecycle labels, and official source links
have one canonical JSON source. Python reads it directly; frontend JSON is
generated and checked into Git for the independent frontend Docker build.
Local Kokoro UI data is generated from the backend model catalog.

```bash
python scripts/generate_provider_catalog.py
python scripts/check_provider_models.py
pytest tests/test_provider_model_catalog_checks.py -q
```

The normal CI gate is offline and deterministic. It checks generated data,
runtime defaults, sample configuration, model/voice compatibility, lifecycle
metadata and local catalog integrity. Negative tests deliberately introduce
drift and require the checker to fail. `--check-upstream` is an explicit
maintainer check for public local-model catalogs, not a flaky network dependency
of every pull request. Account-authenticated smoke tests are separate.

## Real-call acceptance

API smoke success does not establish latency, interruption quality or telephony
cleanup. Test with a separate Agent/pipeline; keep the normal provider unchanged
until acceptance. Archive raw evidence before analyzing failures.

| Change | Telephone test and expected result |
| --- | --- |
| OpenAI Realtime 2.1 and new input transcription | Greeting, recognition of names/numbers, interruption, one permitted tool action, farewell/hangup; no leftover session. |
| OpenAI modular Realtime TTS | Several short and long replies, exact script pronunciation, interrupt during synthesis, caller hangup during a reply, then a second call; no old audio or socket retained. Compare 8 kHz and the deployment's wideband profile. |
| Deepgram Flux TTS, full and modular | Select matching language/version/voice, verify greeting and several turns, barge-in, farewell and cleanup; include one non-English call where available. |
| Deepgram multilingual Flux STT | Switch between configured hint languages during one call; confirm final transcript and end-of-turn behavior. |
| Google 3.8 standard | Conversation, greeting drain, interruption, transfer/tool flow and hangup on the actual transport. |
| Google Extended Thinking | Tool-free test with intermediate speech, a slower answer and interruption; no premature turn completion. Separately verify blocking-tool configuration is rejected before a call is admitted. |
| New LLM/default Think model | At least one permitted tool call and multi-turn history; no reasoning text is spoken. |
| Groq Arabic / multilingual Kokoro | Native-language greeting and response, correct voice and phonemization, then restore the previous global Local AI settings. |

Record exact revision, provider/model/voice, transport, call ID, observed result,
post-call health and archived evidence. Unavailable credentials, denied model
access and provider-side errors remain unverified rather than passing.

Representative API, automated and deployment evidence is recorded in the
[validation report](testing/provider-catalog-20261123.md).
