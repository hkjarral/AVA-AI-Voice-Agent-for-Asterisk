import base64

import pytest

from src.config import (
    GoogleProviderConfig,
    GrokProviderConfig,
    OpenAIRealtimeProviderConfig,
)
from src.providers.elevenlabs_config import ElevenLabsAgentConfig
import src.providers.elevenlabs_agent as elevenlabs_module
import src.providers.google_live as google_module
import src.providers.grok as grok_module
import src.providers.openai_realtime as openai_module
from src.providers.elevenlabs_agent import ElevenLabsAgentProvider
from src.providers.google_live import GoogleLiveProvider
from src.providers.grok import GrokProvider
from src.providers.openai_realtime import OpenAIRealtimeProvider


def _record_resampler_mode(monkeypatch, module):
    calls = []
    real_resample_audio = module.resample_audio

    def recording_resample_audio(*args, **kwargs):
        calls.append(kwargs.copy())
        return real_resample_audio(*args, **kwargs)

    monkeypatch.setattr(module, "resample_audio", recording_resample_audio)
    return calls


def _pcm_b64(sample_count=480):
    return base64.b64encode(b"\x00\x00" * sample_count).decode("ascii")


@pytest.mark.asyncio
async def test_openai_output_uses_explicit_bandlimited_policy(monkeypatch):
    monkeypatch.delenv("AAVA_OPENAI_OUTPUT_RESAMPLER", raising=False)
    calls = _record_resampler_mode(monkeypatch, openai_module)
    events = []

    async def on_event(event):
        events.append(event)

    config = OpenAIRealtimeProviderConfig(
        api_key="test",
        output_sample_rate_hz=24000,
        target_sample_rate_hz=8000,
        target_encoding="mulaw",
        output_resampler="bandlimited",
    )
    provider = OpenAIRealtimeProvider(config, on_event=on_event)
    provider._call_id = "openai-bandlimited"
    provider._outfmt_acknowledged = True
    provider._provider_output_format = "pcm16"
    provider._active_output_sample_rate_hz = 24000

    await provider._handle_output_audio(_pcm_b64())

    assert calls[0]["mode"] == "bandlimited"
    assert events[0]["type"] == "AgentAudio"
    assert len(events[0]["data"]) == 160


@pytest.mark.asyncio
async def test_openai_preserves_filter_history_until_response_done():
    events = []

    async def on_event(event):
        events.append(event)

    provider = OpenAIRealtimeProvider(OpenAIRealtimeProviderConfig(), on_event)
    provider._call_id = "openai-response-boundary"
    provider._current_response_id = "response-1"
    provider._in_audio_burst = True
    provider._output_resample_state = (b"fir-history",)
    provider._output_resampler_logged = True

    await provider._emit_audio_done()

    assert provider._output_resample_state == (b"fir-history",)
    assert provider._output_resampler_logged is True

    await provider._handle_event(
        {"type": "response.done", "response": {"id": "response-1", "output": []}}
    )

    assert provider._output_resample_state is None
    assert provider._output_resampler_logged is False
    assert events[0]["type"] == "AgentAudioDone"


@pytest.mark.asyncio
async def test_google_output_uses_explicit_bandlimited_policy(monkeypatch):
    monkeypatch.delenv("AAVA_GOOGLE_OUTPUT_RESAMPLER", raising=False)
    calls = _record_resampler_mode(monkeypatch, google_module)
    events = []

    async def on_event(event):
        events.append(event)

    config = GoogleProviderConfig(
        output_sample_rate_hz=24000,
        target_sample_rate_hz=8000,
        target_encoding="ulaw",
        output_resampler="bandlimited",
    )
    provider = GoogleLiveProvider(config, on_event=on_event)
    provider._call_id = "google-bandlimited"

    await provider._handle_audio_output(_pcm_b64(), mime_type="audio/pcm;rate=24000")

    assert calls[0]["mode"] == "bandlimited"
    assert calls[0]["state"] is None
    assert events[0]["type"] == "AgentAudio"
    assert len(events[0]["data"]) == 160


@pytest.mark.asyncio
async def test_google_interruption_resets_output_filter_history():
    events = []

    async def on_event(event):
        events.append(event)

    provider = GoogleLiveProvider(GoogleProviderConfig(), on_event=on_event)
    provider._call_id = "google-interrupted"
    provider._in_audio_burst = True
    provider._output_resample_state = ("old-response-history",)
    provider._output_resampler_logged = True

    await provider._handle_server_content({"serverContent": {"interrupted": True}})

    assert provider._output_resample_state is None
    assert provider._output_resampler_logged is False
    assert events[0]["type"] == "ProviderBargeIn"


def test_elevenlabs_output_uses_explicit_bandlimited_policy(monkeypatch):
    monkeypatch.delenv("AAVA_ELEVENLABS_OUTPUT_RESAMPLER", raising=False)
    calls = _record_resampler_mode(monkeypatch, elevenlabs_module)
    config = ElevenLabsAgentConfig(
        api_key="test",
        agent_id="agent-test",
        output_sample_rate_hz=16000,
        target_sample_rate_hz=8000,
        target_encoding="ulaw",
        output_resampler="bandlimited",
    )
    provider = ElevenLabsAgentProvider(config, on_event=None)
    provider._call_id = "elevenlabs-bandlimited"

    output = provider._convert_output_audio(b"\x00\x00" * 320)

    assert calls[0]["mode"] == "bandlimited"
    assert len(output) == 160


@pytest.mark.asyncio
async def test_grok_output_uses_explicit_bandlimited_policy(monkeypatch):
    monkeypatch.delenv("AAVA_GROK_OUTPUT_RESAMPLER", raising=False)
    calls = _record_resampler_mode(monkeypatch, grok_module)
    events = []

    async def on_event(event):
        events.append(event)

    config = GrokProviderConfig(
        api_key="test",
        output_encoding="linear16",
        output_sample_rate_hz=24000,
        target_encoding="ulaw",
        target_sample_rate_hz=8000,
        output_resampler="bandlimited",
    )
    provider = GrokProvider(config, on_event=on_event)
    provider._call_id = "grok-bandlimited"
    provider._outfmt_acknowledged = True
    provider._provider_output_format = "pcm16"
    provider._active_output_sample_rate_hz = 24000

    await provider._handle_output_audio(_pcm_b64())

    assert calls[0]["mode"] == "bandlimited"
    assert events[0]["type"] == "AgentAudio"
    assert len(events[0]["data"]) == 160


@pytest.mark.asyncio
async def test_grok_local_barge_in_resets_output_filter_history():
    provider = GrokProvider(GrokProviderConfig(api_key="test"), on_event=None)
    provider._call_id = "grok-interrupted"
    provider._output_resample_state = ("old-response-history",)
    provider._output_resampler_logged = True

    await provider.handle_local_barge_in()

    assert provider._output_resample_state is None
    assert provider._output_resampler_logged is False


@pytest.mark.parametrize(
    ("env_name", "factory"),
    [
        (
            "AAVA_OPENAI_OUTPUT_RESAMPLER",
            lambda: OpenAIRealtimeProvider(OpenAIRealtimeProviderConfig(), None),
        ),
        (
            "AAVA_GOOGLE_OUTPUT_RESAMPLER",
            lambda: GoogleLiveProvider(GoogleProviderConfig(), None),
        ),
        (
            "AAVA_GROK_OUTPUT_RESAMPLER",
            lambda: GrokProvider(GrokProviderConfig(), None),
        ),
        (
            "AAVA_ELEVENLABS_OUTPUT_RESAMPLER",
            lambda: ElevenLabsAgentProvider(
                ElevenLabsAgentConfig(agent_id="agent-test"), None
            ),
        ),
    ],
)
def test_invalid_environment_policy_fails_back_to_linear(
    monkeypatch, env_name, factory
):
    monkeypatch.setenv(env_name, "not-a-mode")
    provider = factory()
    assert provider._output_resampler_mode == "linear"


def test_elevenlabs_null_output_resampler_fails_closed(monkeypatch):
    monkeypatch.delenv("AAVA_ELEVENLABS_OUTPUT_RESAMPLER", raising=False)
    config = ElevenLabsAgentConfig(agent_id="agent-test", output_resampler=None)

    provider = ElevenLabsAgentProvider(config, None)

    assert provider._output_resampler_mode == "linear"
    assert provider._output_resampler_source == "profile"
