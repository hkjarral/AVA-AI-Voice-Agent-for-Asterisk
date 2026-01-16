# Transport & Playback Mode Compatibility Guide

**Last Updated**: January 7, 2026  
**Issue**: Linear AAVA-28, AAVA-85

## Overview

This document defines the **validated and supported** combinations of audio transport, provider mode, and playback methods.

For **v5.1.4+**: both **AudioSocket** and **ExternalMedia RTP** are validated options for pipeline deployments and full-agent deployments. Choose based on what fits your Asterisk environment and network constraints (TCP `8090` for AudioSocket vs UDP `18080` for ExternalMedia RTP), and confirm the combination you’re running matches the matrix below.

Note: AudioSocket is currently validated with `audiosocket.format: slin`.

---

## Key Concepts

### `downstream_mode` (v5.1.4+)

`downstream_mode` controls how the **ai-engine** delivers TTS back to the caller:

- `downstream_mode: file`  
  Always uses file playback (Asterisk Playback via Announcer). This is the **most validated** option for modular pipelines.
- `downstream_mode: stream`  
  Enables streaming playback when possible. In pipeline mode, the engine will **attempt streaming playback** and **fall back to file playback on errors**.

The engine emits a startup warning when pipelines are configured and `downstream_mode` is `stream` so operators understand this is a streaming-first path with fallback.

## Validated Configurations

### ✅ Configuration 1: ExternalMedia RTP + Hybrid Pipelines + File Playback

**Use Case**: Modular STT → LLM → TTS pipelines

**Configuration**:
```yaml
audio_transport: externalmedia
active_pipeline: hybrid_support  # or any pipeline
downstream_mode: file  # recommended + most validated for pipelines
```

**Technical Details**:
- **Transport**: ExternalMedia RTP (direct UDP audio stream)
- **Provider Mode**: Pipeline (modular adapters)
- **Playback Method**: File-based (PlaybackManager)
- **Audio Flow**:
  - Caller audio → RTP Server → ai-engine → Pipeline STT
  - TTS bytes → File → Asterisk Announcer channel → Caller
  - **No bridge conflict**: RTP ingestion separate from file playback

**Status**: ✅ **VALIDATED**
- Clean two-way conversation
- Proper gating (no feedback loop)
- No audio routing issues

**Why This Works**:
- RTP audio ingestion doesn't use Asterisk bridge
- File playback uses Announcer channel in bridge
- No routing conflict between ingestion and playback

**Optional (v5.1.4+)**: If you set `downstream_mode: stream`, the pipeline runner will attempt streaming playback first and fall back to file playback on errors. For GA stability, keep `file` unless you specifically want to test streaming behavior.

---

### ✅ Configuration 2: AudioSocket + Full Agent + Streaming Playback

**Use Case**: Monolithic providers with integrated STT/LLM/TTS (Deepgram Voice Agent, OpenAI Realtime)

**Configuration**:
```yaml
audio_transport: audiosocket
active_pipeline: ""  # Disable pipelines
default_provider: deepgram  # or openai_realtime
downstream_mode: stream
```

**Technical Details**:
- **Transport**: AudioSocket (Asterisk channel in bridge)
- **Provider Mode**: Full Agent (monolithic)
- **Playback Method**: Streaming (StreamingPlaybackManager)
- **Audio Flow**:
  - Caller audio → AudioSocket channel → ai-engine → Provider
  - Provider TTS stream → StreamingPlaybackManager → AudioSocket → Caller
  - **No Announcer**: Streaming playback doesn't create extra channels

**Status**: ✅ **VALIDATED**
- Clean audio routing
- No bridge conflicts
- Real-time streaming

**Why This Works**:
- AudioSocket channel in bridge for bidirectional audio
- StreamingPlaybackManager sends audio directly to AudioSocket
- No Announcer channel needed

---

### ✅ Configuration 3: AudioSocket + Pipelines + File Playback

> **Note**: AudioSocket + pipelines was previously unstable due to audio routing issues; validated as stable as of **v4.0 (November 2025)** after fixes in commits `fbbe5b9`, `181b210`, and `fbaaf2e`.

**Use Case**: Modular STT → LLM → TTS pipelines with AudioSocket transport

**Configuration**:
```yaml
audio_transport: audiosocket
active_pipeline: local_hybrid  # or any pipeline
downstream_mode: file  # recommended + most validated for pipelines
```

**Technical Details**:
- **Transport**: AudioSocket (Asterisk channel in bridge)
- **Provider Mode**: Pipeline (modular adapters)
- **Playback Method**: File-based (PlaybackManager)
- **Audio Flow**:
  - Caller audio → AudioSocket channel → ai-engine → Pipeline STT
  - TTS bytes → File → Asterisk Announcer channel → Caller
  - **Bridge coexistence**: Both AudioSocket and Announcer channels work together

**Status**: ✅ **VALIDATED** (November 2025)
- Clean two-way conversation
- Continuous audio frame flow over full call duration
- Multiple playback cycles (greeting + responses + farewell)
- Tool execution functional (hangup with farewell)

**Why This Now Works**:
1. **Pipeline audio routing fix** (commit `fbbe5b9`, Oct 27):
   - Pipeline mode check added BEFORE continuous_input provider routing
   - Audio correctly routed to pipeline queues
2. **Pipeline gating enforcement** (commit `181b210`, Oct 28):
   - Gating checks added to prevent feedback loop
   - Agent doesn't hear own TTS playback
3. **AudioSocket stability improvements**:
   - Single-frame issue resolved
   - Asterisk now continuously sends frames to AudioSocket even with Announcer present

**Historical Context** (archived):
- **Pre-October 2025**: AudioSocket + Pipeline was unstable
- **Issue**: Bridge routing conflict, single-frame reception
- **Resolution**: Series of fixes in October 2025
- **Current Status**: Fully functional and production-ready

---

## Configuration Matrix

| Transport | Provider Mode | Playback Method | Gating | Status |
|-----------|--------------|-----------------|--------|--------|
| **ExternalMedia RTP** | Pipeline | File (PlaybackManager) | ✅ Working | ✅ **VALIDATED** |
| **AudioSocket** | Full Agent | Streaming (StreamingPlaybackManager) | ✅ Working | ✅ **VALIDATED** |
| **AudioSocket** | Pipeline | File (PlaybackManager) | ✅ Working | ✅ **VALIDATED** (v4.0+) |
| **ExternalMedia RTP** | Pipeline | Streaming-first (fallback to file) | ✅ Working | ⚠️ **SUPPORTED (v5.1.4+)** |
| **AudioSocket** | Pipeline | Streaming-first (fallback to file) | ✅ Working | ⚠️ **SUPPORTED (v5.1.4+)** |

---

## Decision Guide

### Use ExternalMedia RTP When:
- ✅ Running hybrid pipelines (modular STT/LLM/TTS)
- ✅ Need file-based playback (most validated)
- ✅ Want clean audio routing (no bridge conflicts)
- ✅ Modern deployment

### Use AudioSocket When:
- ✅ Running full agent providers (Deepgram Voice Agent, OpenAI Realtime)
- ✅ Running pipelines (validated as of v4.0)
- ✅ Need streaming playback (full agents) or file playback (pipelines)
- ✅ Legacy compatibility requirements

---

## Configuration Examples

### Example 1: Production Pipeline (Recommended)

```yaml
# config/ai-agent.yaml
audio_transport: externalmedia
active_pipeline: hybrid_support
downstream_mode: file  # recommended for pipelines

pipelines:
  hybrid_support:
    stt: deepgram_stt
    llm: openai_llm
    tts: deepgram_tts
    options:
      stt:
        streaming: true
        encoding: linear16
        sample_rate: 16000
```

**Result**: Clean two-way conversation with proper gating ✅

---

### Example 2: Full Agent (Streaming)

```yaml
# config/ai-agent.yaml
audio_transport: audiosocket
active_pipeline: ""  # Disable pipelines
default_provider: deepgram
downstream_mode: stream

providers:
  deepgram:
    enabled: true
    continuous_input: true
    # ... provider config
```

**Result**: Real-time streaming conversation ✅

---

## Troubleshooting

### Symptom: Only hear greeting, nothing after

**Possible Causes**:
1. Using pre-v4.0 version with AudioSocket + Pipeline
2. Audio gating misconfiguration
3. Pipeline STT not receiving audio

**Solutions**:
1. Upgrade to v4.0 or later (includes AudioSocket + Pipeline fixes)
2. Check gating logs for feedback loop
3. Verify pipeline audio routing in logs
4. If issues persist, use `audio_transport: externalmedia` as fallback

### Symptom: No audio frames after initial connection

**Check**:
1. Verify transport mode in logs
2. Check for Announcer channel in bridge
3. Confirm downstream_mode being honored

**Fix**: Use validated configuration from this document

---

## Implementation Notes

### Pipeline Playback Behavior (v5.1.4+)

Pipelines can use:
- **File playback** when `downstream_mode: file` (always file)
- **Streaming-first** when `downstream_mode: stream` (stream if possible; fallback to file on errors)

```python
# Pipeline runner gating (simplified)
use_streaming_playback = self.config.downstream_mode != "file"
```

Relevant logic lives in the pipeline runner in `src/engine.py` (search for `use_streaming_playback` and `Pipeline streaming playback failed; falling back to file playback`).

### Why Full Agents Respect downstream_mode

Relevant logic lives in `src/engine.py` (search for `use_streaming = self.config.downstream_mode != "file"`).

```python
# Full agents check downstream_mode
use_streaming = self.config.downstream_mode != "file"

if use_streaming:
    await self.streaming_playback_manager.start_streaming_playback(...)
else:
    await self.playback_manager.play_audio(...)
```

**Reason**: Full agents were designed for continuous streaming with optional file fallback.

---

## Remote Host Deployments (NAT/VPN)

> **⚠️ Important: When AI Engine and Asterisk are on different machines**

### AudioSocket Transport ✅ Recommended for Remote Deployments

AudioSocket works seamlessly with remote Asterisk because:
- Audio is streamed bidirectionally over TCP
- No file-based playback required for full agent providers
- Streaming playback sends audio directly through the socket

### ExternalMedia (RTP) Transport ⚠️ Requires Shared Storage

ExternalMedia with pipeline providers uses **file-based playback** via ARI, which requires:
- AI engine generates audio files in `audio/ai-generated/`
- Asterisk must access these files for playback via Announcer channel
- **Without shared storage, Asterisk returns "File does not exist" errors**

**Solutions for ExternalMedia with remote Asterisk:**
1. **NFS/Shared mount**: Mount AI engine's `audio/` directory on Asterisk server at the same path
2. **Same machine**: Run AI engine on the same server as Asterisk
3. **Use AudioSocket instead**: Switch to AudioSocket transport for remote deployments

### Transport Selection Guide for Remote Hosts

| Scenario | Recommended Transport |
|----------|----------------------|
| AI engine & Asterisk same machine | Either works |
| AI engine remote, full agent provider | AudioSocket ✅ |
| AI engine remote, pipeline provider | AudioSocket ✅ or ExternalMedia + shared storage |
| Lowest latency priority | ExternalMedia (if shared storage available) |

### NAT Configuration

For NAT/VPN scenarios, configure `advertise_host` to tell Asterisk where to connect:

```yaml
audiosocket:
  host: "0.0.0.0"              # Bind address
  advertise_host: "10.10.10.3" # Address Asterisk connects to
  port: 8090

external_media:
  rtp_host: "0.0.0.0"
  advertise_host: "10.10.10.3"
  rtp_port: 18080
```

See [Milestone 23: NAT/Hybrid Network Support](./contributing/milestones/milestone-23-nat-advertise-host.md) for details.

---

## Related Issues

- **Linear AAVA-28**: Pipeline STT streaming implementation & gating fixes
- **Commits**:
  - `181b210`: Pipeline gating enforcement
  - `fbaaf2e`: Fallback safety margin increase
  - `294e55e`: Deepgram STT streaming support

---

## Validation History

| Date | Transport | Mode | Result | Notes |
|------|-----------|------|--------|-------|
| 2025-10-28 | RTP | Pipeline | ✅ Pass | Clean two-way, no feedback |
| 2025-10-28 | AudioSocket | Pipeline | ❌ Fail | Pre-fix: Only greeting heard |
| 2025-10-28 | AudioSocket | Full Agent | ✅ Pass | Streaming playback |
| 2025-11-19 | AudioSocket | Full Agent (Google Live) | ✅ Pass | Clean conversation |
| 2025-11-19 | AudioSocket | Full Agent (Deepgram) | ✅ Pass | Clean conversation |
| 2025-11-19 | AudioSocket | Full Agent (OpenAI) | ✅ Pass | Tool execution validated |
| 2025-11-19 | AudioSocket | Pipeline (local_hybrid) | ✅ Pass | Post-fix validation |

---

## Recommendations

1. **Production (v4.0+)**: Both **ExternalMedia RTP** and **AudioSocket** are validated for pipeline deployments
2. **Transport Selection**:
   - **AudioSocket**: Simpler configuration, single transport mechanism for all modes
   - **ExternalMedia RTP**: Separate ingestion path, proven in production longer
3. **Pre-v4.0 Systems**: Use **ExternalMedia RTP** for pipelines (AudioSocket + Pipeline had known issues)
4. **Monitoring**: Always check transport logs during deployment validation
5. **Upgrades**: When upgrading from pre-v4.0, AudioSocket + Pipeline becomes a supported option

---

**For questions or issues, see**:
- [Architecture Deep Dive](./contributing/architecture-deep-dive.md)
- [ROADMAP.md](./ROADMAP.md)
- Linear issue AAVA-28
