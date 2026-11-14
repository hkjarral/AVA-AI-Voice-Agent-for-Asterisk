# Google Provider Implementation

Technical implementation details for Google AI integration in Asterisk AI Voice Agent.

## Overview

Google AI support includes two modes:
- **Google Live API**: Real-time streaming (monolithic provider)
- **Google Cloud Pipeline**: Modular STT/LLM/TTS components

## Google Live API (Monolithic Provider)

### Architecture

**File**: `src/providers/google.py`

**Key Components**:
- Bidirectional streaming with Google Live API
- WebSocket-like session management
- Built-in tool calling support
- Native audio handling (PCM16@16kHz or PCM16@24kHz)

### Configuration

```yaml
provider_name: google_live
google_live:
  api_key: ${GOOGLE_API_KEY}
  llm_model: gemini-2.5-flash-native-audio-preview-12-2025
  voice:
    name: Puck
  generation_config:
    response_modalities: ["AUDIO"]
    speech_config:
      voice_config:
        prebuilt_voice_config:
          voice_name: Puck
```

### Session Lifecycle

1. **Initialization**:
   ```python
   async def start_session(self, call_context: CallContext):
       self.client = genai.Client(api_key=self.api_key)
       config = {"response_modalities": ["AUDIO"]}
       self.session = await self.client.aio.live.connect(
           model=self.model, config=config
       )
   ```

2. **Audio Streaming**:
   - Accepts PCM16@16kHz or PCM16@24kHz
   - Automatically handles sample rate conversion
   - Sends audio chunks via `session.send(audio_data)`

3. **Response Handling**:
   - `server_content` event: Streamed audio chunks
   - `turn_complete` event: End of agent response
   - `tool_call` event: Tool invocation requests

### Tool Calling

Google Live uses native tool calling:

```python
tools = [
    {
        "name": "transfer",
        "description": "Transfer call to extension/queue",
        "parameters": {
            "type": "object",
            "properties": {
                "destination": {"type": "string"}
            }
        }
    }
]
```

**Tool Format**: Same as OpenAI function calling (nested structure)

### Audio Gating

Google Live has built-in turn detection:
- Server automatically detects end of user speech
- No manual VAD gating required
- `turn_complete` event signals response end

### Error Handling

**Common Errors**:

1. **API Key Invalid**:
   ```
   google.api_core.exceptions.Unauthenticated: Invalid API key
   ```
   Fix: Verify `GOOGLE_API_KEY` in `.env`

2. **Model Not Available**:
   ```
   models/gemini-X is not found for API version v1beta, or is not supported for bidiGenerateContent
   ```
   Fix: Use `gemini-2.5-flash-native-audio-preview-12-2025` (latest native audio model for Live API)

3. **Rate Limiting**:
   ```
   google.api_core.exceptions.ResourceExhausted: Quota exceeded
   ```
   Fix: Request quota increase or implement backoff

### Performance Characteristics

- **Latency**: ~500-1000ms first token
- **Audio Quality**: Excellent (Puck voice recommended)
- **Stability**: High (built-in reconnection)
- **Cost**: Pay-per-use (check Google AI pricing)

### Best Practices

1. **Use PCM16@16kHz**: Matches internal format, no transcoding
2. **Enable tool calling**: Configure tools in session setup
3. **Monitor `turn_complete`**: For response end detection
4. **Handle reconnection**: Implement session recovery on disconnect

## Google Cloud Pipeline (Modular Components)

### Architecture

Uses separate Google Cloud APIs:
- **STT**: Google Speech-to-Text (streaming)
- **LLM**: Google Gemini (REST API)
- **TTS**: Google Text-to-Speech

### STT Implementation

**File**: `src/pipelines/google.py::GoogleSTTAdapter`

```python
async def transcribe(self, audio_chunk: bytes) -> Optional[str]:
    # Send PCM16@16kHz audio
    request = StreamingRecognizeRequest(audio_content=audio_chunk)
    responses = self.client.streaming_recognize(
        config=self.config,
        requests=[request]
    )
    # Return final transcript
    return responses.results[0].alternatives[0].transcript
```

**Configuration**:
```yaml
stt:
  encoding: LINEAR16
  sample_rate: 16000
  language_code: en-US
  enable_automatic_punctuation: true
```

### LLM Implementation

**File**: `src/pipelines/google.py::GoogleLLMAdapter`

Uses Gemini API for conversational responses:

```python
async def generate(self, messages: List[Dict]) -> LLMResponse:
    response = await self.model.generate_content_async(
        contents=messages,
        generation_config=self.config
    )
    return LLMResponse(
        text=response.text,
        tool_calls=self._extract_tool_calls(response)
    )
```

### TTS Implementation

**File**: `src/pipelines/google.py::GoogleTTSAdapter`

```python
async def synthesize(self, text: str) -> bytes:
    synthesis_input = texttospeech.SynthesisInput(text=text)
    response = await self.client.synthesize_speech(
        input=synthesis_input,
        voice=self.voice_config,
        audio_config=self.audio_config
    )
    return response.audio_content  # Returns PCM16 or MULAW
```

## Credentials & Authentication

### API Key (Google Live)

Set in `.env`:
```bash
GOOGLE_API_KEY=AIzaSy...
```

Enable APIs:
1. Visit [Google AI Studio](https://aistudio.google.com/apikey)
2. Create API key
3. Enable Gemini API access

### Service Account (Google Cloud Pipeline)

1. **Create Service Account**:
   ```bash
   gcloud iam service-accounts create asterisk-ai-agent \
     --display-name="Asterisk AI Voice Agent"
   ```

2. **Grant Permissions**:
   ```bash
   gcloud projects add-iam-policy-binding PROJECT_ID \
     --member="serviceAccount:asterisk-ai-agent@PROJECT_ID.iam.gserviceaccount.com" \
     --role="roles/speech.client"
   ```

   Default pipeline endpoints are `generativelanguage.googleapis.com` for LLM,
   `speech.googleapis.com` for STT, and `texttospeech.googleapis.com` for TTS.
   These adapters need no Vertex AI project role. For Cloud TTS authentication
   and quota permissions, follow
   [Google's authentication guide](https://docs.cloud.google.com/text-to-speech/docs/authentication).
   The AVA adapter credential precedence is documented in the
   [configuration reference](../../Configuration-Reference.md#google-pipelines).

3. **Download Key**:
   ```bash
   gcloud iam service-accounts keys create key.json \
     --iam-account=asterisk-ai-agent@PROJECT_ID.iam.gserviceaccount.com
   ```

4. **Set Environment**:
   ```bash
   export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
   ```

## Debugging

### Enable Debug Logging

```yaml
logging:
  level: DEBUG
  google_debug: true
```

### Common Issues

**Google Live**:
- Check API key validity
- Verify model availability (`gemini-2.5-flash-native-audio-preview-12-2025`)
- Monitor session state transitions
- Check audio format (PCM16@16kHz recommended)

**Google Cloud Pipeline**:
- Verify service account permissions
- Check quota limits (STT/TTS minutes)
- Monitor API response times
- Validate audio encoding matches config

### Log Patterns

**Success**:
```
[info] Google Live session started
[info] Audio streaming active
[info] Received server_content: 3200 bytes
[info] turn_complete received
```

**Errors**:
```
[error] Google API error: UNAUTHENTICATED
[error] Model not found: gemini-2.5-flash-native-audio-preview-12-2025
[error] Quota exceeded for project
```

## Testing

### Test Google Live

```bash
# Make test call
# Expected: Full conversation with natural responses
# Audio quality: Clear, natural voice
# Latency: <1s first response
```

### Test Google Cloud Pipeline

```bash
# Configure pipeline
provider_name: custom_pipeline
pipeline: google_cloud

# Make test call
# Expected: Transcription → LLM → TTS chain works
# All three APIs responding correctly
```

## Related Documentation

- User Setup: `docs/Provider-Google-Setup.md`
- Architecture: `docs/contributing/architecture-deep-dive.md`
- Tool Calling: `docs/TOOL_CALLING_GUIDE.md`

## References

- [Google AI Studio](https://aistudio.google.com/)
- [Gemini API Docs](https://ai.google.dev/docs)
- [Google Cloud Speech](https://cloud.google.com/speech-to-text)
- [Google Cloud Text-to-Speech](https://cloud.google.com/text-to-speech)
