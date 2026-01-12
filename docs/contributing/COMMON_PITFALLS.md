# Common Pitfalls and Solutions

This document contains real issues encountered during development, their root causes, and how to avoid them. Learn from these mistakes so you don't repeat them!

## Table of Contents

- [Tool Execution Issues](#tool-execution-issues)
- [State Management](#state-management)
- [Audio & Codec Issues](#audio--codec-issues)
- [Configuration Errors](#configuration-errors)
- [Provider-Specific Issues](#provider-specific-issues)

---

## Tool Execution Issues

### Pitfall #1: Tool Schema Format Mismatch (OpenAI Realtime)

**Symptom**: AI says "goodbye" but call doesn't hang up. Tools configured but never executed.

**Error Message**:

```
Missing required parameter: 'session.tools[0].name'.
```

**Root Cause**: Using Chat Completions schema format (nested) instead of Realtime API format (flat).

The OpenAI Realtime API requires a different schema format than the Chat Completions API:

```python
# ❌ WRONG (Chat Completions format - nested):
{
  "type": "function",
  "function": {              # Nested under "function" key
    "name": "hangup_call",
    "description": "...",
    "parameters": {...}
  }
}

# ✅ CORRECT (Realtime API format - flat):
{
  "type": "function",
  "name": "hangup_call",     # Flat structure
  "description": "...",
  "parameters": {...}
}
```

**Solution**:

- Use `to_openai_realtime_schema()` for OpenAI Realtime provider
- Use `to_openai_schema()` for Chat Completions (pipelines)
- Use `to_deepgram_schema()` for Deepgram Voice Agent

**Implementation**: See `src/tools/base.py::to_openai_realtime_schema()`

**Prevention**:

- Always verify schema format for your provider
- Test with `test_schema_format.py` if unsure
- Look for "missing_required_parameter" errors in logs

**Reference**: AAVA-85 regression fix (commit b1c92f1, Nov 19, 2025)

---

### Pitfall #2: Deepgram Function Calling Field Names

**Symptom**: Tools configured but Deepgram never calls them.

**Root Cause**: Using OpenAI field names (`tools`) instead of Deepgram field names (`functions`).

```python
# ❌ WRONG (OpenAI naming):
agent.think.tools = [...]

# ✅ CORRECT (Deepgram naming):
agent.think.functions = [...]
```

**Also**: Event type must match exactly:

```python
# ❌ WRONG:
elif event_type == "function_call"

# ✅ CORRECT:
elif event_type == "FunctionCallRequest"
```

**Solution**: Use Deepgram-specific naming and event types.

**Reference**: Deepgram function calling implementation (commit c8d994b, 2163e2f)

---

## State Management

### Pitfall #3: Conversation History Not Preserved

**Symptom**: Email transcript missing initial greeting.

**Root Cause**: Reinitializing `conversation_history` as empty list, overwriting session data.

```python
# ❌ WRONG:
conversation_history = []

# ✅ CORRECT:
conversation_history = list(session.conversation_history or [])
```

**Impact**: Lost greeting message, incomplete email transcripts.

**Solution**: Always initialize from session state, never overwrite without reading first.

**Reference**: AAVA-85 bug #1 (commit dd5bc5a)

---

### Pitfall #4: Using Wrong Config Attribute

**Symptom**: `AttributeError: 'Engine' object has no attribute 'app_config'`

**Root Cause**: Engine uses `self.config`, not `self.app_config`.

```python
# ❌ WRONG:
context = ToolExecutionContext(
    config=self.app_config.dict()  # AttributeError!
)

# ✅ CORRECT:
context = ToolExecutionContext(
    config=self.config.dict()
)
```

**Impact**: Tool execution crashes completely.

**Solution**: Always use `self.config` in Engine context.

**Reference**: AAVA-85 bug #2 (commit a007241)

---

### Pitfall #5: Wrong ARI Method Name

**Symptom**: Tool executes but call doesn't hang up.

**Root Cause**: Using `delete_channel()` instead of `hangup_channel()`.

```python
# ❌ WRONG:
await self.ari_client.delete_channel(channel_id)

# ✅ CORRECT:
await self.ari_client.hangup_channel(channel_id)
```

**Solution**: Use the correct ARI client method. Check `src/ari_client.py` for available methods.

**Reference**: AAVA-85 bug #3 (commit cc125fd)

---

## Audio & Codec Issues

### Pitfall #6: Farewell Audio Cut Off

**Symptom**: Farewell message partially played before call hangs up.

**Root Cause**: Fixed sleep duration (2 seconds) doesn't match actual audio length.

```python
# ❌ WRONG:
await asyncio.sleep(2.0)  # May be too short or too long

# ✅ CORRECT:
duration_sec = len(audio_bytes) / 8000.0  # mulaw 8kHz
await asyncio.sleep(duration_sec + 0.5)   # Add buffer
```

**Solution**: Calculate sleep duration from actual audio byte count.

**Reference**: AAVA-85 bug #5 (commit 8058dab)

---

### Pitfall #7: AudioSocket Format Override

**Symptom**: Severe audio garble/distortion on calls.

**Root Cause**: Code overrode YAML config with detected caller codec, causing format mismatch.

**Example**:

- Caller uses μ-law (160 bytes/frame)
- Asterisk channel expects PCM16 slin (320 bytes/frame per dialplan)
- Override forced μ-law → frame size mismatch → garble

**Solution**: AudioSocket format must always match YAML config, never caller codec.

```yaml
# YAML config
audiosocket:
  format: "slin"  # This is the source of truth
```

**Lesson**: AudioSocket wire leg is separate from caller-side trunk codec.

**Reference**: AudioSocket wire format fix (commit 1a049ce, Oct 25, 2025)

---

## Configuration Errors

### Pitfall #8: Pipeline Audio Codec Mismatch

**Symptom**: Pipeline STT fails, no transcripts generated.

**Root Cause**: Pipeline adapters sent PCM16@16kHz (internal format) but config specified mulaw@8kHz.

**Why It Happens**:

- Both AudioSocket and ExternalMedia RTP standardize to PCM16@16kHz internally
- Pipeline adapters are "thin wrappers" - send audio as-is without transcoding
- Monolithic providers have internal encoding logic

**Solution**: Match pipeline config to internal format for zero transcoding:

```yaml
pipelines:
  hybrid_support:
    options:
      stt:
        encoding: linear16    # Matches internal PCM16
        sample_rate: 16000    # Matches internal 16kHz
```

**Benefit**: Best quality, lowest latency, no overhead.

**Reference**: Pipeline audio codec management (commit 0f71c74, AAVA-28)

---

### Pitfall #9: Pipeline Audio Routing (AudioSocket)

**Symptom**: Pipeline STT receives zero audio. No transcriptions after greeting.

**Root Cause**: Code checked monolithic provider conditions first, returned early, never reached pipeline routing.

**The Bug**:

```python
# Checked continuous_input providers FIRST
if provider_name == "deepgram":
    provider.send_audio(audio)  # Wrong destination!
    return  # Early return - pipeline code never reached
```

**Solution**: Check pipeline mode BEFORE provider-specific routing.

**Reference**: AudioSocket pipeline routing fix (commit fbbe5b9, Oct 27, 2025)

---

## Provider-Specific Issues

### Pitfall #10: OpenAI Realtime Modality Constraints

**Symptom**: Complete audio failure with error.

**Error Message**:

```
Invalid modalities: ['audio'].
Supported combinations are: ['text'] and ['audio', 'text'].
```

**Root Cause**: OpenAI Realtime API does NOT support audio-only modality.

**Attempted**: Force audio generation by using `modalities: ['audio']`  
**Reality**: API strictly requires `['audio', 'text']` for voice

**Known Limitation**: OpenAI may occasionally generate text-only or partial audio. This cannot be prevented.

**Solution**: Always use `['audio', 'text']` and handle gracefully when no audio is generated.

**Reference**: OpenAI modality constraints (commit 6dbd51e, Nov 10, 2025)

---

### Pitfall #11: OpenAI Realtime VAD Sensitivity

**Symptom**: Agent self-interrupts, echo loops, audio gate fluttering.

**Root Cause**: `webrtc_aggressiveness: 0` was too sensitive, detected echo as "speech".

**Impact**:

- Gate opened/closed 50+ times per call
- Echo leaked through gaps
- OpenAI detected own audio
- Self-interruption loop

**Solution**:

```yaml
vad:
  webrtc_aggressiveness: 1  # CRITICAL for OpenAI Realtime
```

**Why**: OpenAI has sophisticated server-side echo cancellation. Local VAD level 0 fights it. Level 1 ignores echo.

**Reference**: OpenAI Realtime golden baseline (commit 937b4a4, Oct 26, 2025)

---

## General Best Practices

### Testing Tool Execution

**Always verify these log patterns**:

```
✅ SUCCESS:
- "OpenAI session configured with 6 tools"
- "OpenAI function call detected: hangup_call"
- "Hangup tool executed: success"

❌ FAILURE:
- "missing_required_parameter"
- "AI used farewell phrase without invoking hangup_call tool"
- AttributeError in tool execution
```

### Debugging Workflow

1. **Check schema format** first (most common issue)
2. **Verify tool registration** in logs
3. **Check provider-specific requirements** (naming, event types)
4. **Test with known-good config** to isolate issue
5. **Use `agent rca`** for detailed analysis

### State Management Rules

1. **Always read before write** - Initialize from session state
2. **Use correct attribute names** - Check class definitions
3. **Verify ARI method names** - Check `ari_client.py`
4. **Calculate timing dynamically** - Don't use fixed sleeps for audio

### Configuration Validation

1. **Match internal audio formats** - Use linear16@16kHz for pipelines
2. **Test ALL providers** when changing shared components
3. **Verify schema formats** for each provider type
4. **Check logs for warnings** - They often indicate misconfigurations

---

## Need Help?

- **Tool issues?** See [Tool Development Guide](tool-development.md)
- **Provider issues?** See [Provider Development Guide](provider-development.md)
- **Audio issues?** See [Architecture Deep Dive](architecture-deep-dive.md)
- **Still stuck?** Check [Debugging Guide](debugging-guide.md)

---

**Remember**: Every bug is a learning opportunity. When you fix a bug, add it here to help future contributors! 🚀
