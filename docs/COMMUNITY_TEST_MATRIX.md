# Community Test Matrix — Local AI Server

Help us build the definitive reference for what works best when running AAVA fully local.
Submit your results via [GitHub Issue](https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/issues/new?template=local-ai-test-result.md) or PR to this file.

---

## How to Contribute

### Automated (recommended)

After making a test call with the local provider, run:

```bash
agent rca --local
# or directly:
python3 scripts/local_test_report.py
```

This auto-detects your hardware, queries the Local AI Server for model info, parses docker logs for latency, and outputs a ready-to-paste submission template. Add `--json` for machine-readable output.

### Manual

1. **Run a test call** using a Local AI Server configuration (any STT + TTS + LLM combination).
2. **Record your results** using the template below or the GitHub issue template.
3. **Submit** a PR adding a row to the results table, or open an issue with the `community-test` label.

### What to Measure

- **STT Latency**: Time from end of speech to transcript appearing in logs.
- **LLM Latency**: Time from transcript to first LLM token (check `local_ai_server` logs for `[LLM]` timing).
- **TTS Latency**: Time from LLM response to first audio byte.
- **End-to-End**: Perceived time from user stops speaking to hearing the AI reply.
- **Call Quality**: Subjective 1-5 rating (1 = unusable, 5 = indistinguishable from cloud).

---

## Backend Compatibility Quick Reference

| Backend | Type | CPU | GPU | Build Arg | Approx Size | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| Vosk | STT | Good | No benefit | `INCLUDE_VOSK=true` (default) | 50-200 MB | Best CPU STT; real-time streaming |
| Sherpa-ONNX | STT | Good | No benefit | `INCLUDE_SHERPA=true` (default) | 30-150 MB | Streaming; good multi-language |
| Kroko Cloud | STT | Yes | Yes | N/A | 0 | Requires API key at kroko.ai |
| Kroko Embedded | STT | Yes | Yes | `INCLUDE_KROKO_EMBEDDED=true` | ~100 MB | Self-hosted ONNX server |
| Faster-Whisper | STT | Slow | Recommended | `INCLUDE_FASTER_WHISPER=true` | 75-3000 MB | Auto-downloads from HuggingFace |
| Whisper.cpp | STT | Slow | Good | `INCLUDE_WHISPER_CPP=true` | 75-3000 MB | Manual model download |
| Piper | TTS | Good | No benefit | `INCLUDE_PIPER=true` (default) | 15-60 MB | Best CPU TTS; ONNX voices |
| Kokoro | TTS | OK | Better | `INCLUDE_KOKORO=true` (default) | ~200 MB | Higher quality; multi-voice |
| MeloTTS | TTS | OK | Better | `INCLUDE_MELOTTS=true` | ~500 MB | Multi-accent English |
| llama.cpp | LLM | Not recommended | Required | `INCLUDE_LLAMA=true` (default) | 2-8 GB | CPU: 10-30s/response |

---

## Community Results

### Legend

- **E2E**: End-to-end perceived latency (user stops speaking → hears reply)
- **Quality**: Subjective 1-5 (1=unusable, 3=usable, 5=cloud-quality)
- **Transport**: `em` = ExternalMedia RTP, `as` = AudioSocket

### Results Table

<!-- APPEND YOUR RESULTS HERE — one row per test configuration -->

| Date | Contributor | Hardware | GPU | STT Backend | STT Model | TTS Backend | TTS Voice | LLM Model | LLM Context | Transport | E2E Latency | Quality | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2025-07-14 | @maintainer | Vast.ai A100 40GB | A100 | vosk | en-us-0.22 | piper | lessac-medium | phi-3-mini Q4_K_M | 2048 | em | ~2s | 3 | Baseline GPU test |
| 2025-07-14 | @maintainer | Vast.ai A100 40GB | A100 | faster_whisper | base | kokoro | af_heart | phi-3-mini Q4_K_M | 2048 | em | ~1.5s | 4 | Whisper + Kokoro combo |
| 2026-02-22 | @hkjarral | AMD EPYC 7443P, 66GB RAM | RTX 4090 24GB | faster_whisper | base | kokoro | af_heart | phi-3-mini-4k-instruct.Q4_K_M.gguf | 4096 | em | ~665ms | 4 | Phi-3 tool calls can be malformed/truncated; use `LOCAL_TOOL_CALL_POLICY=auto` and keep `LOCAL_TOOL_GATEWAY_ENABLED=true` for structured full-local tool normalization |
| 2026-02-23 | @hkjarral | AMD EPYC 7443P, 66GB RAM | RTX 4090 24GB | faster_whisper | base | kokoro | af_heart | Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf | 4096 | em | ~1.0s | 5 | Call `1771817082.317`: structured gateway + repair cleanly executed `hangup_call` on polite close (`Thank you.`), no tool-chatter leaked to spoken output, and post-call webhook executed successfully |
| 2026-02-27 | @hkjarral | AMD EPYC 7713, 98GB RAM | RTX 4090 24GB | kroko | embedded (en-US) | kokoro | af_heart | Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf | 2048 | em | ~1.2s | 5 | Call `1772234505.97`: end-to-end local + GPU offload; barge-in and `hangup_call` succeeded (tool gateway `tool_path=heuristic`) |
| 2026-02-27 | @hkjarral | AMD EPYC 7713, 98GB RAM | RTX 4090 24GB | whisper_cpp | unknown | kokoro | af_heart | Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf | 2048 | em | ~1.1s | 2 | Call `1772235703.109`: low coherence (telephony STT felt “not hearing”); transcripts arrived as short fragments; call ended without `hangup_call` |
| 2026-07-13 | @hkjarral | Intel Xeon Gold 6226R, 43GB RAM | Tesla V100S 32GB | faster_whisper | base (en), CUDA float16 | kokoro | af_heart | Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf | 2048 | as | ~640ms | 5 | Call `1783917838.10`: clean full-local GPU call; exact Kokoro terminal farewell played once, partial-frame drain completed in 619ms, and ARI recorded `agent_hangup` |
| 2026-07-13 | @hkjarral | Intel Xeon Gold 6226R, 43GB RAM | Tesla V100S 32GB | faster_whisper | base (en), CUDA float16 | kokoro | af_heart | Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf | 2048 | em | ~640ms | 5 | Call `1783918235.12`: clean and clear full-local RTP call; media RX, barge-in, one exact Kokoro farewell, 805ms terminal tail drain, and `agent_hangup` all passed |

---

### Detailed Submissions

```
**Date**: 2026-07-13
**Hardware**: Intel(R) Xeon(R) Gold 6226R CPU @ 2.90GHz, 43GB RAM
**GPU**: Tesla V100S-PCIE-32GB 32GB
**OS**: Debian GNU/Linux 13 (trixie)
**Docker**: 29.6.1
**STT**: faster_whisper / Faster-Whisper (base, en)
**STT Runtime**: device=cuda, compute=float16
**TTS**: kokoro / Kokoro (af_heart, mode=local)
**LLM**: Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf / n_ctx=2048 / max_tokens=96
**LLM GPU Layers**: 50
**LLM Tool Capability**: strict
**Transport**: AudioSocket
**Pipeline**: local
**Runtime Mode**: full
**Runtime Flags**: filler_audio=false, llm_tts_overlap=true
**E2E Latency**: ~640ms (local report estimate)
**LLM Latency**: ~264ms avg (2 samples, last=240ms)
**Call History Turn Latency**: 319ms avg, 345ms max
**STT Transcripts (last session)**: 2
**TTS Responses (last session)**: 4
**Quality (1-5)**: 5
**Notes**: Call `1783917838.10` was reported as working perfectly. The Local AI Server synthesized the exact `Goodbye.` tool result with Kokoro; only one farewell was stored and heard. An 80-byte partial AudioSocket frame drained in 619ms after the low-water re-arm fix, versus 6.257s before the fix. No Asterisk goodbye fallback or terminal timeout fired, and `RCA_CALL_END` recorded `agent_hangup`.
**Tool Calls**:
  ✅ hangup_call: 1 executed [local_llm]
```

```
**Date**: 2026-07-13
**Hardware**: Intel(R) Xeon(R) Gold 6226R CPU @ 2.90GHz, 43GB RAM
**GPU**: Tesla V100S-PCIE-32GB 32GB
**OS**: Debian GNU/Linux 13 (trixie)
**Docker**: 29.6.1
**STT**: faster_whisper / Faster-Whisper (base, en)
**STT Runtime**: device=cuda, compute=float16
**TTS**: kokoro / Kokoro (af_heart, mode=local)
**LLM**: Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf / n_ctx=2048 / max_tokens=96
**LLM GPU Layers**: 50
**LLM Tool Capability**: strict
**Transport**: ExternalMedia RTP (mu-law, 8kHz)
**Pipeline**: local
**Runtime Mode**: full
**Runtime Flags**: filler_audio=false, llm_tts_overlap=true
**E2E Latency**: ~640ms (local report estimate)
**LLM Latency**: ~427ms avg (5 samples, last=240ms)
**Call History Turn Latency**: 376ms avg, 504ms max
**STT Transcripts (last session)**: 5
**TTS Responses (last session)**: 5
**Quality (1-5)**: 5
**Notes**: Call `1783918235.12` was reported as clean and clear. RTP was established bidirectionally, Local VAD and Asterisk TalkDetect corroborated barge-in, one exact `Goodbye.` farewell played, a 40-byte terminal remainder drained in 805ms, and `RCA_CALL_END` recorded `agent_hangup`. The host was restored to its AudioSocket baseline after validation.
**Tool Calls**:
  ✅ hangup_call: 1 executed [local_llm]
```

```
**Date**: 2026-02-22
**Hardware**: AMD EPYC 7443P 24-Core Processor, 66GB RAM
**GPU**: NVIDIA GeForce RTX 4090 24GB
**OS**: Ubuntu 22.04.5 LTS
**Docker**: 29.2.1
**STT**: faster_whisper / Faster-Whisper (base)
**TTS**: kokoro / Kokoro (af_heart, mode=hf)
**LLM**: phi-3-mini-4k-instruct.Q4_K_M.gguf / n_ctx=4096
**LLM GPU Layers**: -1
**Transport**: ExternalMedia RTP
**Pipeline**: local
**Runtime Mode**: full
**E2E Latency**: ~665ms
**LLM Latency**: ~261ms avg (8 samples, last=265ms)
**STT Transcripts (last session)**: 9
**TTS Responses (last session)**: 9
**Quality (1-5)**: 4
**Notes**: Tool calls do not work reliably; use heuristic-based hangup for phi-3 (malformed/truncated tool-call markup observed).
**Tool Calls**:
  ⚠️ hangup_call: 2 attempted (not executed) [llm_markup]
  ✅ demo_post_call_webhook: 1 executed [post_call]
```

```
**Date**: 2026-02-23
**Hardware**: AMD EPYC 7443P 24-Core Processor, 66GB RAM
**GPU**: NVIDIA GeForce RTX 4090 24GB
**OS**: Ubuntu 22.04.5 LTS
**Docker**: 29.2.1
**STT**: faster_whisper / Faster-Whisper (base)
**TTS**: kokoro / Kokoro (af_heart, mode=hf)
**LLM**: Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf / n_ctx=4096
**LLM GPU Layers**: -1
**Transport**: ExternalMedia RTP
**Pipeline**: local
**Runtime Mode**: full
**E2E Latency**: ~1.0s
**LLM Latency**: ~534ms avg (8 samples, last=612ms)
**STT Transcripts (last session)**: 8
**TTS Responses (last session)**: 10
**Quality (1-5)**: 5
**Notes**: Call `1771817082.317` was clean end-to-end. Local logs show strict structured tool gateway with a repair-path handoff (`tool_path=repair`) produced a valid `hangup_call` on user close intent (`Thank you.`), the engine executed a single hangup path, and no tool execution chatter leaked into final spoken text.
**Tool Calls**:
  ✅ hangup_call: 1 executed [local_llm]
  ✅ demo_post_call_webhook: 1 executed [post_call]
```

```
**Date**: 2026-02-23
**Hardware**: AMD EPYC 7443P 24-Core Processor, 66GB RAM
**GPU**: NVIDIA GeForce RTX 4090 24GB
**OS**: Ubuntu 22.04.5 LTS
**Docker**: 29.2.1
**STT**: kroko / Kroko (embedded, port 6006)
**TTS**: kokoro / Kokoro (af_heart, mode=hf)
**LLM**: Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf / n_ctx=4096
**LLM GPU Layers**: -1
**Transport**: ExternalMedia RTP
**Pipeline**: local
**Runtime Mode**: full
**E2E Latency**: ~1.0s
**LLM Latency**: ~541ms avg (16 samples, last=625ms)
**STT Transcripts (last session)**: 35
**TTS Responses (last session)**: 19
**Quality (1-5)**: <your rating>
**Notes**: Natural voice quality
**Tool Calls**:
  ⚠️ hangup_call: 2 executed, 1 blocked [guardrail, local_llm]
  ✅ demo_post_call_webhook: 2 executed [post_call]
```

```
**Date**: 2026-02-23
**Hardware**: AMD EPYC 7443P 24-Core Processor, 66GB RAM
**GPU**: NVIDIA GeForce RTX 4090 24GB
**OS**: Ubuntu 22.04.5 LTS
**Docker**: 29.2.1
**STT**: kroko / Kroko (embedded, port 6006)
**TTS**: melotts / MeloTTS (EN-US)
**LLM**: Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf / n_ctx=4096
**LLM GPU Layers**: -1
**Transport**: ExternalMedia RTP
**Pipeline**: local
**Runtime Mode**: full
**E2E Latency**: ~1.0s
**LLM Latency**: ~546ms avg (18 samples, last=608ms)
**STT Transcripts (last session)**: 52
**TTS Responses (last session)**: 22
**Quality (1-5)**: <your rating>
**Notes**: Start of the conversation is slow but then it picks up
**Tool Calls**:
  ✅ hangup_call: 2 executed [local_llm]
  ✅ demo_post_call_webhook: 2 executed [post_call]
```

```
**Date**: 2026-02-27
**Hardware**: AMD EPYC 7713 64-Core Processor, 98GB RAM
**GPU**: NVIDIA GeForce RTX 4090 24GB
**OS**: Ubuntu 22.04.5 LTS
**Docker**: 29.2.1 (Compose v5.1.0)
**STT**: kroko / Kroko (embedded, en-US)
**TTS**: kokoro / Kokoro (af_heart, mode=local)
**LLM**: Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf / n_ctx=2048
**LLM GPU Layers**: 50 (runtime-selected)
**Transport**: ExternalMedia RTP
**Pipeline**: local
**Runtime Mode**: full
**E2E Latency**: ~1.2s (avg_turn_latency_ms=1172, max_turn_latency_ms=1846)
**Quality (1-5)**: 5
**Notes**: Call `1772234505.97` was clean end-to-end; `hangup_call` executed successfully via tool gateway fast-path heuristic.
**Tool Calls**:
  ✅ hangup_call: 1 executed [tool_path=heuristic]
```

```
**Date**: 2026-02-27
**Hardware**: AMD EPYC 7713 64-Core Processor, 98GB RAM
**GPU**: NVIDIA GeForce RTX 4090 24GB
**OS**: Ubuntu 22.04.5 LTS
**Docker**: 29.2.1
**STT**: whisper_cpp / Whisper.cpp
**TTS**: kokoro / Kokoro (af_heart, mode=local)
**LLM**: Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf / n_ctx=2048
**LLM GPU Layers**: -1
**Transport**: ExternalMedia RTP
**Pipeline**: local
**Runtime Mode**: full
**E2E Latency**: ~1.1s (avg_turn_latency_ms=1077, max_turn_latency_ms=1888)
**Quality (1-5)**: 2
**Notes**: Call `1772235703.109`: low coherence; Whisper.cpp emitted short transcript fragments (e.g., split utterances) and did not feel as robust as Faster-Whisper in this telephony setup.
**Tool Calls**:
  ✅ demo_post_call_webhook: 1 executed [post_call]
```

#### Comparative Summary (2026-02-23 RTX 4090)

- **LLM latency stability**: Both runs are tightly clustered (~541ms vs ~546ms avg) with similar tails (last=625ms vs 608ms).
- **TTS behavior**: Kokoro notes “Natural voice quality”; MeloTTS notes “Start of the conversation is slow but then it picks up” (suggesting warm-up/caching or first-utterance overhead).
- **Guardrails**: One extra `hangup_call` was blocked in the Kokoro run (`[guardrail, local_llm]`), while the MeloTTS run had only executed tool calls.
- **Throughput**: MeloTTS run processed more transcripts (52 vs 35) and more TTS responses (22 vs 19) within the logged session, implying good steady-state performance under longer sessions.

## Submission Template

Use this when adding a row or opening an issue:

```
**Date**: YYYY-MM-DD
**Hardware**: e.g., "Ryzen 7 5800X, 32GB RAM" or "Vast.ai RTX 4090 24GB"
**GPU**: e.g., "RTX 4090 24GB" or "None (CPU only)"
**STT**: Backend + model (e.g., "vosk / en-us-0.22" or "faster_whisper / base")
**TTS**: Backend + voice (e.g., "piper / lessac-medium" or "kokoro / af_heart")
**LLM**: Model + context (e.g., "phi-3-mini Q4_K_M / n_ctx=2048") or "Cloud (GPT-4o)"
**Transport**: ExternalMedia RTP or AudioSocket
**Pipeline**: local_only / local_hybrid / other
**E2E Latency**: Approximate (e.g., "~2s", "3-5s")
**Quality (1-5)**: Your rating
**Notes**: Any observations (echo issues, model switching behavior, etc.)
```

---

## FAQ

**Q: How do I measure latency?**
Set `LOCAL_LOG_LEVEL=DEBUG` and check timestamps in `docker compose logs local_ai_server`. Look for:
- `STT result` → transcript timestamp
- `LLM response` → first token timestamp
- `TTS audio` → first byte timestamp

**Q: What pipeline should I use?**
- `local_only`: All local (STT + LLM + TTS). Requires GPU for usable LLM latency.
- `local_hybrid`: Local STT + TTS, cloud LLM (e.g., GPT-4o). Best quality on CPU.

**Q: Can I test from a different machine?**
Yes — set up split-host mode. See `docs/LOCAL_ONLY_SETUP.md` for details on configuring `LOCAL_WS_HOST=0.0.0.0` with `LOCAL_WS_AUTH_TOKEN`.
