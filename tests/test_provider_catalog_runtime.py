"""Provider catalog migrations exercise actual request/lifecycle contracts."""
import asyncio
import base64
import json
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest

from src.config import DeepgramProviderConfig, GoogleProviderConfig, LLMConfig, OpenAIProviderConfig
from src.pipelines.deepgram import DeepgramTTSAdapter
from src.pipelines.deepgram_flux import DeepgramFluxSTTAdapter
from src.pipelines.openai import OpenAILLMAdapter, OpenAITTSAdapter
from src.pipelines.openai_realtime_tts import RealtimeTTSPool
from src.providers.deepgram import DeepgramProvider, build_speak_provider_block, validate_agent_language_configuration
from src.providers.google_live import GoogleLiveProvider


class Socket:
    def __init__(self, events=()):
        self.events = asyncio.Queue()
        for event in events:
            self.events.put_nowait(json.dumps(event))
        self.sent = []
        self.closed = False

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def recv(self):
        result = await self.events.get()
        if result is None:
            raise RuntimeError("socket closed")
        return result

    async def close(self):
        self.closed = True
        self.events.put_nowait(None)


def realtime_options(**overrides):
    return {
        "tts_realtime_base_url": "wss://api.openai.com/v1/realtime",
        "tts_model": "gpt-realtime-2.1-mini", "voice": "marin", "api_key": "synthetic",
        "target_format": {"encoding": "linear16", "sample_rate": 24000},
        "output_resampler": "linear", "timeout_sec": 1, **overrides,
    }


@pytest.mark.asyncio
async def test_realtime_tts_ga_request_streams_split_pcm_and_closes(monkeypatch):
    ws = Socket([
        {"type": "session.created"}, {"type": "session.updated"},
        {"type": "response.output_audio.delta", "delta": base64.b64encode(b"\x01").decode()},
        {"type": "response.output_audio.delta", "delta": base64.b64encode(b"\x00\x02\x00").decode()},
        {"type": "response.done", "response": {"status": "completed"}},
    ])
    connect = AsyncMock(return_value=ws)
    monkeypatch.setattr("src.pipelines.openai_realtime_tts.websockets.connect", connect)
    pool = RealtimeTTSPool()
    result = b"".join([x async for x in pool.synthesize("a", "Hello", realtime_options())])
    assert result == b"\x01\x00\x02\x00"
    assert ws.closed and pool._requests == {}
    assert parse_qs(urlparse(connect.call_args.args[0]).query)["model"] == ["gpt-realtime-2.1-mini"]
    session = ws.sent[0]["session"]
    assert session["type"] == "realtime" and session["tools"] == []
    assert session["audio"]["output"] == {"format": {"type": "audio/pcm", "rate": 24000}, "voice": "marin"}
    assert ws.sent[1]["response"]["input"][0]["content"] == [{"type": "input_text", "text": "Hello"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("events,error", [
    ([{"type": "session.updated"}, {"type": "response.done", "response": {"status": "failed"}}], "did not complete"),
    ([{"type": "session.updated"}, {"type": "response.done", "response": {"status": "completed"}}], "without audio"),
    ([{"type": "error", "error": {"code": "invalid_voice", "message": "private caller text"}}], "invalid_voice"),
])
async def test_realtime_tts_failures_close_without_reflecting_private_text(monkeypatch, events, error):
    ws = Socket(events)
    monkeypatch.setattr("src.pipelines.openai_realtime_tts.websockets.connect", AsyncMock(return_value=ws))
    pool = RealtimeTTSPool()
    with pytest.raises(RuntimeError, match=error) as exc:
        _ = [x async for x in pool.synthesize("a", "Hello", realtime_options())]
    assert "private caller text" not in str(exc.value)
    assert ws.closed and not pool._requests


@pytest.mark.asyncio
async def test_realtime_tts_timeout_cleans_stream(monkeypatch):
    ws = Socket()
    monkeypatch.setattr("src.pipelines.openai_realtime_tts.websockets.connect", AsyncMock(return_value=ws))
    pool = RealtimeTTSPool()
    with pytest.raises(asyncio.TimeoutError):
        _ = [x async for x in pool.synthesize("a", "Hello", realtime_options(timeout_sec=0.01))]
    assert ws.closed and not pool._requests


@pytest.mark.asyncio
async def test_realtime_adapter_restart_resets_pool_and_invalid_format_does_not_register_request(monkeypatch):
    pool = RealtimeTTSPool()
    with pytest.raises(ValueError, match="sample rate must be positive"):
        _ = [x async for x in pool.synthesize("a", "Hello", realtime_options(target_format={"encoding": "linear16", "sample_rate": 0}))]
    assert not pool._requests
    cfg = OpenAIProviderConfig(api_key="synthetic")
    adapter = OpenAITTSAdapter("openai_tts", None, cfg)
    await adapter.stop()
    assert adapter._realtime_tts._stopped
    await adapter.start()
    assert not adapter._realtime_tts._stopped


@pytest.mark.asyncio
async def test_realtime_close_call_invalidates_handshake_and_isolates_other_calls(monkeypatch):
    pending = asyncio.Event()
    release = asyncio.Event()
    ws = Socket()

    async def connect(*args, **kwargs):
        pending.set()
        await release.wait()
        return ws

    monkeypatch.setattr("src.pipelines.openai_realtime_tts.websockets.connect", connect)
    pool = RealtimeTTSPool()

    async def consume():
        return [x async for x in pool.synthesize("a", "Hello", realtime_options())]

    task = asyncio.create_task(consume())
    await pending.wait()
    await pool.close_call("unrelated")
    assert not task.done()
    await pool.close_call("a")
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ws.closed and pool._requests == {}


@pytest.mark.asyncio
async def test_tts_auto_uses_realtime_only_for_realtime_model(monkeypatch):
    cfg = OpenAIProviderConfig(api_key="synthetic", tts_model="gpt-realtime-2.1-mini", tts_transport="auto", voice="marin")
    adapter = OpenAITTSAdapter("openai_tts", None, cfg)
    monkeypatch.setattr(adapter, "_ensure_session", AsyncMock())
    adapter._session = object()
    calls = []

    async def synth(call_id, text, merged):
        calls.append(merged)
        yield b"ok"

    monkeypatch.setattr(adapter._realtime_tts, "synthesize", synth)
    assert [x async for x in adapter.synthesize("a", "Hello", {})] == [b"ok"]
    assert calls[0]["tts_model"] == "gpt-realtime-2.1-mini"
    assert calls[0]["timeout_sec"] == 30.0
    assert adapter._compose_options({})["timeout_sec"] == cfg.response_timeout_sec
    assert adapter._compose_options({"tts_model": "tts-1"})["tts_model"] == "tts-1"
    # Pipeline provider swaps can leave a Groq model/voice behind. The fallback
    # must be dispatched through its own transport, never /audio/speech.
    assert [x async for x in adapter.synthesize("a", "Hello", {"model": "canopylabs/orpheus-v1-english", "voice": "hannah"})] == [b"ok"]
    assert calls[-1]["tts_model"] == "gpt-realtime-2.1-mini" and calls[-1]["voice"] == "marin"


@pytest.mark.asyncio
async def test_realtime_tts_budget_allows_generation_longer_than_legacy_response_timeout(monkeypatch):
    class DelayedSocket(Socket):
        async def recv(self):
            await asyncio.sleep(0.02)
            return await super().recv()
    ws = DelayedSocket([
        {"type": "session.updated"},
        {"type": "response.output_audio.delta", "delta": base64.b64encode(b"\x00\x00" * 480).decode()},
        {"type": "response.done", "response": {"status": "completed"}},
    ])
    monkeypatch.setattr("src.pipelines.openai_realtime_tts.websockets.connect", AsyncMock(return_value=ws))
    cfg = OpenAIProviderConfig(api_key="synthetic", response_timeout_sec=0.01, tts_realtime_timeout_sec=1)
    adapter = OpenAITTSAdapter("openai_tts", None, cfg)
    try:
        audio = b"".join([x async for x in adapter.synthesize("a", "Hello", {})])
        assert audio and ws.closed
        assert adapter._compose_options({})["timeout_sec"] == 0.01
    finally:
        await adapter.stop()


def test_modern_openai_chat_uses_completion_limit_and_none_reasoning_for_luna_tools():
    adapter = OpenAILLMAdapter("openai_llm", None, OpenAIProviderConfig(api_key="synthetic", chat_model="gpt-6-luna"))
    merged = adapter._compose_options({"max_tokens": 256, "tools": ["hangup_call"]})
    payload = adapter._build_chat_payload("hi", {}, merged)
    assert payload["max_completion_tokens"] == 256
    assert payload["reasoning_effort"] == "none"
    assert "max_tokens" not in payload and "temperature" not in payload
    merged["chat_model"] = "gpt-6.2-sol"
    with pytest.raises(ValueError, match="Responses API"):
        adapter._build_chat_payload("hi", {}, merged)
    merged["tools"] = []
    with pytest.raises(ValueError, match="does not support reasoning_effort none"):
        adapter._build_chat_payload("hi", {}, merged)
    merged["reasoning_effort"] = "low"
    assert adapter._build_chat_payload("hi", {}, merged)["reasoning_effort"] == "low"


def test_compatible_endpoint_keeps_legacy_chat_parameter_contract():
    adapter = OpenAILLMAdapter("custom_llm", None, OpenAIProviderConfig(api_key="synthetic", chat_base_url="https://api.deepseek.com", chat_model="deepseek-flash"))
    payload = adapter._build_chat_payload("hi", {}, adapter._compose_options({"max_tokens": 200, "temperature": 0.3}))
    assert payload["max_tokens"] == 200 and payload["temperature"] == 0.3


def test_legacy_openai_chat_omits_reasoning_even_after_config_dump():
    cfg = OpenAIProviderConfig(api_key="synthetic", chat_model="gpt-4o-mini")
    adapter = OpenAILLMAdapter("openai_llm", None, cfg)
    options = adapter._compose_options({"reasoning_effort": "none", "max_tokens": 200})
    payload = adapter._build_chat_payload("hi", {}, options)
    assert "reasoning_effort" not in payload
    assert payload["max_tokens"] == 200


def test_aura_rest_speed_is_sent_and_flux_only_settings_rejected():
    adapter = DeepgramTTSAdapter("deepgram_tts", None, DeepgramProviderConfig(api_key="synthetic"))
    options = adapter._compose_options({"tts_speed": 0.9})
    url, query = adapter._build_tts_request(options, "mulaw", 8000)
    assert url.endswith("/v1/speak") and query["speed"] == 0.9
    with pytest.raises(ValueError, match="Aura TTS speed"):
        adapter._build_tts_request({**options, "tts_speed": 0.5}, "mulaw", 8000)
    with pytest.raises(ValueError, match="expressivity requires"):
        adapter._build_tts_request({**options, "tts_expressivity": 1}, "mulaw", 8000)


@pytest.mark.asyncio
@pytest.mark.parametrize("model,expected", [
    ("eleven_v3", {"stability": 0.5}),
    ("eleven_v4", {"stability": 0.5, "similarity_boost": 0.75}),
    ("eleven_v4_turbo", {"stability": 0.5, "similarity_boost": 0.75}),
])
async def test_eleven_modern_models_send_only_supported_voice_controls(model, expected):
    from src.config import ElevenLabsProviderConfig
    from src.pipelines.elevenlabs import ElevenLabsTTSAdapter

    class Response:
        status = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def read(self):
            return b"\x00\x00" * 160

    class Session:
        closed = False
        def post(self, url, **kwargs):
            self.request = kwargs
            return Response()
        async def close(self):
            self.closed = True

    session = Session()
    cfg = ElevenLabsProviderConfig(api_key="synthetic", model_id=model, voice_id="synthetic")
    adapter = ElevenLabsTTSAdapter("elevenlabs_tts", None, cfg, session_factory=lambda: session)
    try:
        audio = [chunk async for chunk in adapter.synthesize("a", "Hello", {})]
        assert audio and session.request["json"]["voice_settings"] == expected
        if model == "eleven_v3":
            with pytest.raises(ValueError, match="stability must"):
                _ = [chunk async for chunk in adapter.synthesize("a", "Hello", {"stability": 0.3})]
    finally:
        await adapter.stop()
    assert session.closed


def test_flux_tts_rest_uses_v2_and_only_documented_provider_fields():
    cfg = DeepgramProviderConfig(api_key="synthetic", tts_model="flux-haley-en", tts_version="v2", tts_speed=1.05, tts_expressivity=1)
    adapter = DeepgramTTSAdapter("deepgram_tts", None, cfg)
    options = adapter._compose_options({})
    url, query = adapter._build_tts_request(options, "mulaw", 8000)
    assert url == "https://api.deepgram.com/v2/speak"
    assert query == {"model": "flux-haley-en", "encoding": "linear16", "sample_rate": 8000, "container": "none", "speed": 1.05, "expressivity": 1}
    options["source_format"]["container"] = "wav"
    with pytest.raises(ValueError, match="raw"):
        adapter._build_tts_request(options, "mulaw", 8000)


def test_flux_full_agent_language_and_voice_family_fail_closed():
    assert validate_agent_language_configuration(listen_model="flux-general-multi", agent_language="pt-BR", speak_model="flux-bianca-pt", tts_version="v2") == "pt"
    with pytest.raises(ValueError, match="tts_version"):
        validate_agent_language_configuration(listen_model="nova-3", agent_language="en", speak_model="flux-haley-en")
    with pytest.raises(ValueError, match="English-only"):
        validate_agent_language_configuration(listen_model="flux-general-en", agent_language="pt", speak_model="flux-bianca-pt", tts_version="v2")
    assert build_speak_provider_block(model="flux-haley-en", version="v2", speed=1.05)["version"] == "v2"


@pytest.mark.asyncio
async def test_flux_full_agent_primary_and_retry_share_v2_speak():
    provider = DeepgramProvider({"model": "flux-general-en", "agent_language": "en", "tts_model": "flux-haley-en", "tts_version": "v2", "tts_speed": 1.05}, LLMConfig(), None)
    provider.call_id = "synthetic"
    provider.websocket = Socket()
    provider._allowed_tools = []
    await provider._configure_agent()
    primary = provider.websocket.sent[0]
    expected = {"type": "deepgram", "model": "flux-haley-en", "version": "v2", "speed": 1.05}
    assert primary["agent"]["speak"]["provider"] == expected
    assert provider._last_settings_minimal["agent"]["speak"]["provider"] == expected


@pytest.mark.asyncio
async def test_flux_modular_multilingual_handshake_uses_configured_model_and_hints(monkeypatch):
    cfg = DeepgramProviderConfig(api_key="synthetic", model="flux-general-multi", language_hints=["en", "es"])
    adapter = DeepgramFluxSTTAdapter("deepgram_flux_stt", None, cfg)
    captured = {}

    async def validate(url, api_key, timeout=5.0):
        captured["url"] = url
        return {"healthy": True}

    monkeypatch.setattr(adapter, "_test_websocket_connection", validate)
    await adapter.validate_connectivity({})
    query = parse_qs(urlparse(captured["url"]).query)
    assert query["model"] == ["flux-general-multi"]
    assert query["language_hint"] == ["en", "es"]
    assert "language_hints" not in query
    assert "language" not in query
    assert "channels" not in query

    socket = AsyncMock()
    connect = AsyncMock(return_value=socket)
    monkeypatch.setattr("src.pipelines.deepgram_flux.websockets.connect", connect)
    try:
        await adapter.open_call("synthetic", {})
        url = connect.await_args.args[0]
        call_query = parse_qs(urlparse(url).query)
        assert call_query["language_hint"] == ["en", "es"]
        assert "language_hints" not in call_query
        assert "channels" not in call_query
    finally:
        await adapter.close_call("synthetic")


@pytest.mark.asyncio
async def test_deepgram_tts_readiness_uses_instance_endpoint_and_key(monkeypatch):
    cfg = DeepgramProviderConfig(api_key="instance-key", base_url="https://api.eu.deepgram.com",
                                 tts_model="flux-haley-en", tts_version="v2")
    adapter = DeepgramTTSAdapter("deepgram_customer_tts", None, cfg)
    probe = AsyncMock(return_value={"healthy": True})
    monkeypatch.setattr(adapter, "_test_http_connection", probe)

    assert (await adapter.validate_connectivity({}))["healthy"]
    probe.assert_awaited_once_with("https://api.eu.deepgram.com", "instance-key")


@pytest.mark.asyncio
async def test_google_thinking_uses_idle_not_intermediate_utterance_boundary(monkeypatch):
    provider = GoogleLiveProvider(GoogleProviderConfig(llm_model="gemini-3.8-live-extended-thinking", api_key="synthetic"), AsyncMock())
    complete = AsyncMock()
    monkeypatch.setattr(provider, "_handle_turn_complete", complete)
    await provider._handle_server_message({"serverContent": {"turnComplete": True}, "interactionStatus": "IN_PROGRESS"})
    complete.assert_not_awaited()
    await provider._handle_server_message({"interactionStatus": "IDLE"})
    complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_google_thinking_setup_and_blocking_tool_validation(monkeypatch):
    provider = GoogleLiveProvider(GoogleProviderConfig(llm_model="gemini-3.8-live-extended-thinking", api_key="synthetic", thinking_level="medium"), AsyncMock())
    send = AsyncMock()
    monkeypatch.setattr(provider, "_send_message", send)
    await provider._send_setup({"tools": []})
    assert send.call_args.args[0]["setup"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "MEDIUM"}
    assert provider.uses_full_duplex_barge_in()
    with pytest.raises(ValueError, match="non-blocking"):
        await provider._send_setup({"tools": ["hangup_call"]})


@pytest.mark.asyncio
@pytest.mark.parametrize("transcription", ["gpt-transcribe", "whisper-1"])
async def test_full_openai_realtime_projects_input_model_and_reasoning(transcription):
    from src.config import OpenAIRealtimeProviderConfig
    from src.providers.openai_realtime import OpenAIRealtimeProvider
    provider = OpenAIRealtimeProvider(OpenAIRealtimeProviderConfig(input_transcription_model=transcription, model="gpt-realtime-2.1", reasoning_effort="low"), None)
    send = AsyncMock()
    provider._send_json = send
    await provider._send_session_update()
    session = send.call_args.args[0]["session"]
    assert session["audio"]["input"]["transcription"]["model"] == transcription
    assert session["reasoning"] == {"effort": "low"}


@pytest.mark.asyncio
async def test_full_openai_legacy_non_reasoning_model_omits_effort():
    from src.config import OpenAIRealtimeProviderConfig
    from src.providers.openai_realtime import OpenAIRealtimeProvider
    provider = OpenAIRealtimeProvider(OpenAIRealtimeProviderConfig(model="gpt-realtime-1.5", reasoning_effort="low"), None)
    send = AsyncMock()
    provider._send_json = send
    await provider._send_session_update()
    assert "reasoning" not in send.call_args.args[0]["session"]


@pytest.mark.asyncio
async def test_realtime_tts_cancellation_and_call_close_release_connected_socket(monkeypatch):
    ws = Socket([{"type": "session.updated"}])
    monkeypatch.setattr("src.pipelines.openai_realtime_tts.websockets.connect", AsyncMock(return_value=ws))
    pool = RealtimeTTSPool()
    async def consume():
        return [x async for x in pool.synthesize("a", "Hello", realtime_options())]
    task = asyncio.create_task(consume())
    for _ in range(50):
        if len(ws.sent) >= 2:
            break
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ws.closed and pool._requests == {}


@pytest.mark.asyncio
async def test_realtime_tts_resampling_odd_chunks_matches_combined_pcm(monkeypatch):
    from src.audio import resample_audio, convert_pcm16le_to_target_format
    pcm = b"\x10\x00\x20\x00\x30\x00" * 40
    parts = [pcm[:5], pcm[5:33], pcm[33:]]
    ws = Socket([{"type": "session.updated"}, *[
        {"type": "response.output_audio.delta", "delta": base64.b64encode(p).decode()} for p in parts
    ], {"type": "response.done", "response": {"status": "completed"}}])
    monkeypatch.setattr("src.pipelines.openai_realtime_tts.websockets.connect", AsyncMock(return_value=ws))
    pool = RealtimeTTSPool()
    audio = b"".join([x async for x in pool.synthesize("a", "Hello", realtime_options(target_format={"encoding": "mulaw", "sample_rate": 8000}))])
    expected, _ = resample_audio(pcm, 24000, 8000)
    assert audio == convert_pcm16le_to_target_format(expected, "mulaw")


def test_groq_tts_validates_voice_against_model_without_changing_explicit_voice():
    from src.config import GroqTTSProviderConfig
    from src.pipelines.groq import GroqTTSAdapter
    config = GroqTTSProviderConfig(tts_model="canopylabs/orpheus-arabic-saudi", voice="abdullah")
    adapter = GroqTTSAdapter("groq_tts", None, config)
    assert adapter._compose_options({})["voice"] == "abdullah"
    with pytest.raises(ValueError, match="model language"):
        adapter._compose_options({"voice": "hannah"})


@pytest.mark.asyncio
async def test_think_model_preserves_historical_explicit_override():
    from types import SimpleNamespace
    provider = DeepgramProvider(DeepgramProviderConfig(model="nova-3", tts_model="aura-2-thalia-en"), SimpleNamespace(model="gpt-4o", prompt="synthetic", initial_greeting="hello"), None)
    provider.call_id = "synthetic"
    provider.websocket = Socket()
    provider._allowed_tools = []
    await provider._configure_agent()
    expected = {"type": "open_ai", "model": "gpt-4o", "temperature": 0.7}
    assert provider.websocket.sent[0]["agent"]["think"]["provider"] == expected
    assert provider._last_settings_minimal["agent"]["think"]["provider"] == expected


@pytest.mark.asyncio
async def test_explicit_think_model_wins_over_historical_override():
    from types import SimpleNamespace
    provider = DeepgramProvider(DeepgramProviderConfig(model="nova-3", tts_model="aura-2-thalia-en", think_model="gpt-5.6-luna"), SimpleNamespace(model="gpt-4o", prompt="synthetic", initial_greeting="hello"), None)
    provider.call_id = "synthetic"
    provider.websocket = Socket()
    provider._allowed_tools = []
    await provider._configure_agent()
    expected = {"type": "open_ai", "model": "gpt-5.6-luna"}
    assert provider.websocket.sent[0]["agent"]["think"]["provider"] == expected
    assert provider._last_settings_minimal["agent"]["think"]["provider"] == expected


@pytest.mark.parametrize("model", ["gpt-5", "gpt-5.4-mini", "gpt-5.6-luna", "gpt-5.6-terra", "o3-mini"])
def test_deepgram_reasoning_think_models_do_not_receive_legacy_temperature(model):
    from src.providers.deepgram import build_think_provider_block
    assert build_think_provider_block(model) == {"type": "open_ai", "model": model}


@pytest.mark.asyncio
async def test_full_flux_tts_rejects_alaw_before_connecting(monkeypatch):
    connect = AsyncMock()
    monkeypatch.setattr("src.providers.deepgram.connect_with_recovery", connect)
    provider = DeepgramProvider(DeepgramProviderConfig(api_key="synthetic", tts_model="flux-haley-en", tts_version="v2", output_encoding="alaw", output_sample_rate=8000), LLMConfig(), None)
    with pytest.raises(ValueError, match="A-law output is not supported"):
        await provider.start_session("alaw-rejected", {"tools": []})
    connect.assert_not_awaited()
