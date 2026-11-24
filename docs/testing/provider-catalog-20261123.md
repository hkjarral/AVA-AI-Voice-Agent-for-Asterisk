# Provider catalog validation — 2026-11-23

Scope: the sourced catalog and runtime/UI/configuration/CI changes described in
[the provider comparison and migration guide](../Provider-Catalog-Updates.md).
Existing operator model pins and provider YAML are preserved. API success below
is not telephone-call acceptance.

## Automated checks

- Core Python suite, using CI's exclusions and 26% coverage gate: **5,716 passed,
  23 skipped, 178 deselected; 65.20% coverage**.
- Admin backend: **1,228 passed**.
- Admin frontend under Node 22: **873 passed across 100 files**; build and
  typecheck passed, lint had zero errors (802 warnings, below the 817 baseline).
- Generated catalog freshness, deterministic model/voice/default/sample drift
  checker, negative drift cases, source compilation and committed-secrets guard
  passed. Optional upstream local-catalog verification passed for Piper, Vosk
  and Kokoro.

## voiprnd API qualification

Tests used configured credentials, synthetic text/audio and bounded requests.
They did not place telephone calls or save provider configuration. The matrix
records the latest result for each distinct preset.

| Test | Result |
| --- | --- |
| OpenAI Realtime 2.1-mini modular TTS | PASS; repeated from the final deployed engine, including 8.2-second synthesis. |
| OpenAI gpt-transcribe | PASS after project model access was enabled; repeated from the final engine. |
| OpenAI GPT-6 Luna LLM | PASS |
| OpenAI GPT-6.2 Sol / Astra, tool-free | PASS / PASS |
| OpenAI Realtime 2.1 full-provider setup | PASS; setup acknowledged, no conversational/tool call acceptance implied. |
| xAI Grok full-provider setup | PASS; setup acknowledged. |
| Deepgram Flux TTS | PASS; nonempty PCM. |
| Deepgram Voice Agent, managed GPT-5.6 Luna + Flux TTS | PASS; settings acknowledged and greeting audio returned. |
| Google Live 3.8 standard / Extended Thinking, tool-free | PASS / PASS; setup acknowledged. |
| Google Gemini 3.8 Flash modular LLM | PASS |
| Groq Arabic Orpheus / Abdullah | PASS; synthesis returned audio; native-language quality needs a real call. |
| Telnyx GLM-5.3-Flash | PASS |
| ElevenLabs Flash 2.5 / v3 / v4 / v4 Turbo | PASS / PASS / PASS / PASS with configured voice. |
| MiniMax M3 | HTTP 402; billing/account availability remains unverified. |
| Fish Audio S2.1 Pro | HTTP 401; configured credential/access remains unverified. |
| Google Cloud TTS | HTTP 401; Cloud speech authentication remains unverified. A working Gemini key does not establish Cloud TTS authorization. |
| DeepSeek / CAMB / Azure | SKIP; usable credentials unavailable. |

Latest distinct presets: **18 passed, 3 failed for account/auth/billing responses,
3 skipped**. These are representative model/API checks, not all voices or
provider/account entitlement combinations.

## Deployment and remaining acceptance

The candidate uses separate source and images with the existing config/data
mounts. The pre-deployment source checksum manifest was verified. Existing
operator YAML was compared byte-for-byte after deployment. Prior images and
composition were retained for rollback. Runtime archives remain local under
`logs/archived/`; they include private operator state and are not committed.

Local AI retained its installed compiled backends and models; application code
was layered over its existing CPU image for this test deployment. Sherpa
SenseVoice, the existing local LLM and Piper initialized. Kokoro is disabled in
this host's installed backend set, so its 54 choices were tested in schema,
control-plane and UI flows; non-English synthesis/G2P installation was not
qualified here.

Use the [real-call acceptance matrix](../Provider-Catalog-Updates.md#real-call-acceptance)
before moving existing Agents to new models or workflows. Priority: modular
Realtime TTS cancellation and pronunciation, Flux turn-taking and multilingual
recognition, Google extended-thinking completion/interruption, new LLM tool
calling, and multilingual/new-model voice quality. No real-call IDs are claimed
for this validation because telephone tests are left to the operator.
