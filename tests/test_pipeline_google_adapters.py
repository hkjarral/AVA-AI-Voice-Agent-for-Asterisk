import asyncio
import base64
import io
import json
import wave

import pytest

from src.audio.resampler import convert_pcm16le_to_target_format
from src.config import AppConfig, GoogleProviderConfig
from src.pipelines.google import GoogleLLMAdapter, GoogleSTTAdapter, GoogleTTSAdapter
from src.pipelines.orchestrator import PipelineOrchestrator, PipelineOrchestratorError


def _build_app_config() -> AppConfig:
    providers = {
        "google": {
            "api_key": "test-google-key",
            "project_id": "demo-project",
            "stt_base_url": "https://speech.googleapis.com/v1",
            "tts_base_url": "https://texttospeech.googleapis.com/v1",
            "llm_base_url": "https://generativelanguage.googleapis.com/v1",
            "stt_language_code": "en-US",
            "tts_voice_name": "en-US-Neural2-C",
            "tts_audio_encoding": "MULAW",
            "tts_sample_rate_hz": 8000,
            "llm_model": "models/gemini-1.5-pro-latest",
        }
    }
    pipelines = {
        "google_stack": {
            "stt": "google_stt",
            "llm": "google_llm",
            "tts": "google_tts",
            "options": {
                "stt": {"language_code": "en-US"},
                "llm": {"temperature": 0.3},
                "tts": {"format": {"encoding": "mulaw", "sample_rate": 8000}},
            },
        }
    }
    return AppConfig(
        default_provider="google",
        providers=providers,
        asterisk={"host": "127.0.0.1", "username": "ari", "password": "secret"},
        llm={"initial_greeting": "hi", "prompt": "prompt", "model": "gpt-4o"},
        audio_transport="audiosocket",
        downstream_mode="stream",
        pipelines=pipelines,
        active_pipeline="google_stack",
    )


class _FakeResponse:
    def __init__(self, body: str, status: int = 200):
        self._body = body
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return self._body


class _FakeSession:
    def __init__(self, body: str, status: int = 200):
        self._body = body
        self._status = status
        self.requests = []
        self.closed = False

    def post(self, url, json=None, params=None, headers=None, timeout=None):
        self.requests.append(
            {
                "url": url,
                "json": json,
                "params": params,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return _FakeResponse(self._body, status=self._status)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_google_stt_adapter_transcribes(monkeypatch):
    app_config = _build_app_config()
    provider_config = GoogleProviderConfig(**app_config.providers["google"])
    payload = json.dumps(
        {"results": [{"alternatives": [{"transcript": "hello google"}]}]}
    )
    fake_session = _FakeSession(payload)

    adapter = GoogleSTTAdapter(
        "google_stt",
        app_config,
        provider_config,
        {"encoding": "LINEAR16"},
        session_factory=lambda: fake_session,
    )

    await adapter.start()
    await adapter.open_call("call-1", {})

    audio = b"\x00\x10" * 160  # 20 ms of PCM16 @ 8 kHz
    transcript = await adapter.transcribe("call-1", audio, 8000, {})
    assert transcript == "hello google"

    request = fake_session.requests[0]
    assert request["params"]["key"] == "test-google-key"
    assert request["json"]["config"]["languageCode"] == "en-US"


@pytest.mark.asyncio
async def test_google_llm_adapter_generate(monkeypatch):
    app_config = _build_app_config()
    provider_config = GoogleProviderConfig(**app_config.providers["google"])
    payload = json.dumps(
        {
            "candidates": [
                {"finishReason": "STOP", "content": {"parts": [{"text": "response from gemini"}]}}
            ]
        }
    )
    fake_session = _FakeSession(payload)

    adapter = GoogleLLMAdapter(
        "google_llm",
        app_config,
        provider_config,
        {"temperature": 0.1},
        session_factory=lambda: fake_session,
    )

    await adapter.start()
    summary_prompt = "Summarize the call. Do not use the agent persona."
    reply = await adapter.generate(
        "call-1",
        "hello there",
        {"system_prompt": summary_prompt},
        {
            "temperature": 0.2,
            "system_prompt": summary_prompt,
            "instructions": summary_prompt,
        },
    )
    assert reply.text == "response from gemini"

    request = fake_session.requests[0]
    assert request["params"]["key"] == "test-google-key"
    assert request["json"]["generationConfig"]["temperature"] == 0.2
    assert request["json"]["systemInstruction"] == {
        "parts": [{"text": summary_prompt}],
    }
    assert request["json"]["contents"][0]["parts"][0]["text"] == "hello there"


@pytest.mark.asyncio
async def test_google_tts_adapter_synthesizes_chunks():
    app_config = _build_app_config()
    provider_config = GoogleProviderConfig(**app_config.providers["google"])

    pcm_audio = b"\x00\x10" * 160
    mulaw_audio = convert_pcm16le_to_target_format(pcm_audio, "mulaw")
    payload = json.dumps({"audioContent": base64.b64encode(mulaw_audio).decode("ascii")})
    fake_session = _FakeSession(payload)

    adapter = GoogleTTSAdapter(
        "google_tts",
        app_config,
        provider_config,
        {"format": {"encoding": "mulaw", "sample_rate": 8000}},
        session_factory=lambda: fake_session,
    )

    await adapter.start()
    await adapter.open_call("call-1", {})

    chunks = [chunk async for chunk in adapter.synthesize("call-1", "Hello caller", {})]
    synthesized = b"".join(chunks)
    assert synthesized == mulaw_audio

    request = fake_session.requests[0]
    assert request["params"]["key"] == "test-google-key"
    assert request["json"]["voice"]["name"] == "en-US-Neural2-C"


def test_google_tts_strips_linear16_wav_container_before_playback():
    pcm_audio = b"\x00\x10" * 320
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(pcm_audio)

    converted = GoogleTTSAdapter._convert_audio(
        wav_buffer.getvalue(),
        "LINEAR16",
        16000,
        "linear16",
        16000,
    )

    assert converted == pcm_audio


@pytest.mark.asyncio
async def test_google_orchestrator_falls_back_without_credentials(monkeypatch):
    app_config = _build_app_config()
    app_config.providers["google"].pop("api_key", None)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    orchestrator = PipelineOrchestrator(app_config)
    with pytest.raises(PipelineOrchestratorError) as exc_info:
        await orchestrator.start()
    assert "google_stack" in str(exc_info.value)
    assert "cannot resolve" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_pipeline_orchestrator_registers_google_adapters():
    app_config = _build_app_config()
    orchestrator = PipelineOrchestrator(app_config)
    await orchestrator.start()

    resolution = orchestrator.get_pipeline("call-1")
    assert isinstance(resolution.stt_adapter, GoogleSTTAdapter)
    assert isinstance(resolution.llm_adapter, GoogleLLMAdapter)
    assert isinstance(resolution.tts_adapter, GoogleTTSAdapter)
    assert resolution.stt_options["language_code"] == "en-US"


def _google_llm_payload(model: str, options: dict) -> dict:
    app_config = _build_app_config()
    provider_config = GoogleProviderConfig(**app_config.providers["google"])
    adapter = GoogleLLMAdapter("google_llm", app_config, provider_config, {})
    merged = {"model": model, "temperature": 0.2, "top_p": 0.9, "max_output_tokens": 64, **options}
    return adapter._build_payload("hello", {}, merged)["generationConfig"]


@pytest.mark.parametrize("model", ["models/gemini-3.8-flash", "gemini-3.6-flash", "gemini-4-argon", "gemini-3.5-flash", "models/gemini-3.5-flash-lite"])
def test_google_llm_payload_omits_sampling_params_for_gemini_3_6_plus(model):
    config = _google_llm_payload(model, {})

    assert "temperature" not in config
    assert "topP" not in config
    assert config["maxOutputTokens"] == 64


@pytest.mark.parametrize("model", ["models/gemini-1.5-pro-latest", "gemini-2.5-flash", "gemini-3.1-flash-lite", "custom-gemini-3.8"])
def test_google_llm_payload_keeps_sampling_params_for_older_models(model):
    config = _google_llm_payload(model, {})

    assert config["temperature"] == 0.2
    assert config["topP"] == 0.9


@pytest.mark.parametrize("model", ["gemini-3-flash-preview", "models/gemini-3.8-flash", "gemini-4-argon"])
def test_google_multiple_candidates_omitted_for_gemini_3_plus(model):
    assert "candidateCount" not in _google_llm_payload(model, {"candidate_count": 2})


def test_google_multiple_candidates_preserved_for_older_models():
    assert _google_llm_payload("gemini-2.5-flash", {"candidate_count": 2})["candidateCount"] == 2


@pytest.mark.asyncio
async def test_google_unsupported_options_removed_after_request_overrides():
    fake = _FakeSession(json.dumps({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "Done"}]}}]}))
    adapter = GoogleLLMAdapter("google_llm", _build_app_config(), GoogleProviderConfig(api_key="test-key"), session_factory=lambda: fake)
    try:
        await adapter.generate("call", "hello", {}, {
            "candidate_count": 2,
            "request_overrides": {"generationConfig": {"candidateCount": 2, "temperature": 0.2, "topP": 0.9, "topK": 10, "maxOutputTokens": 128}},
        })
        assert fake.requests[0]["json"]["generationConfig"] == {"maxOutputTokens": 128}
    finally:
        await adapter.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("model,expected_config", [
    ("models/gemini-3.8-flash", {"maxOutputTokens": 4096, "thinkingConfig": {"thinkingLevel": "low"}}),
    ("gemini-3.5-flash", {"maxOutputTokens": 4096, "thinkingConfig": {"thinkingLevel": "low"}}),
    ("gemini-3.5-flash-lite", {"maxOutputTokens": 4096, "thinkingConfig": {"thinkingLevel": "low"}}),
    ("gemini-2.5-flash", {"temperature": 0.3, "maxOutputTokens": 256}),
    ("custom-model", {"temperature": 0.3, "maxOutputTokens": 256}),
])
async def test_google_summary_uses_model_budget_and_closes_session(monkeypatch, model, expected_config):
    from src.post_call_summary import PostCallSummaryService
    fake = _FakeSession(json.dumps({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "The caller asked about office hours."}]}}]}))
    monkeypatch.setattr("src.pipelines.google.aiohttp.ClientSession", lambda: fake)
    app = _build_app_config()
    app.providers["google"]["llm_model"] = model
    service = PostCallSummaryService(PipelineOrchestrator(app))
    result = await service.generate(provider="google_llm", call_id="budget", conversation_history=[{"role": "user", "content": "What are your hours?"}], system_prompt="Summarize in 50 words.", max_words=50, timeout_ms=1000)
    assert result.status == "ok"
    assert result.text == "The caller asked about office hours."
    request = fake.requests[0]
    assert request["json"]["generationConfig"] == expected_config
    assert request["json"]["systemInstruction"] == {"parts": [{"text": "Summarize in 50 words."}]}
    assert request["url"].endswith(model.removeprefix("models/") + ":generateContent")
    assert len(fake.requests) == 1
    assert fake.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", ["", "The caller requested a"])
async def test_google_truncated_summary_is_error_and_session_closes(monkeypatch, partial):
    from src.post_call_summary import PostCallSummaryService
    fake = _FakeSession(json.dumps({"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": partial}]}}]}))
    monkeypatch.setattr("src.pipelines.google.aiohttp.ClientSession", lambda: fake)
    app = _build_app_config()
    app.providers["google"]["llm_model"] = "models/gemini-3.8-flash"
    result = await PostCallSummaryService(PipelineOrchestrator(app)).generate(provider="google_llm", call_id="truncated", conversation_history=[{"role": "user", "content": "Office hours?"}], system_prompt="Summarize.", max_words=50, timeout_ms=1000)
    assert result.status == "error"
    assert result.error_code == "GoogleGenerationIncompleteError"
    assert result.text == ""
    assert len(fake.requests) == 1  # No unbounded retry or provider fallback.
    assert fake.closed


@pytest.mark.asyncio
async def test_google_summary_cancellation_closes_session(monkeypatch):
    from src.post_call_summary import PostCallSummaryService
    fake = _FakeSession("{}")
    async def delayed_text(self):
        await asyncio.sleep(1)
        return self._body
    monkeypatch.setattr(_FakeResponse, "text", delayed_text)
    monkeypatch.setattr("src.pipelines.google.aiohttp.ClientSession", lambda: fake)
    result = await PostCallSummaryService(PipelineOrchestrator(_build_app_config())).generate(provider="google_llm", call_id="timeout", conversation_history=[{"role": "user", "content": "Office hours?"}], system_prompt="Summarize.", max_words=50, timeout_ms=10)
    assert result.status == "timeout"
    assert result.text == ""
    assert fake.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_google_complete_candidate_survives_truncated_alternative(reverse):
    candidates = [
        {"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "Partial secret"}]}},
        {"finishReason": "STOP", "content": {"parts": [
            {"text": "Internal reasoning", "thought": True},
            {"text": "Complete "}, {"text": "answer."},
        ]}},
    ]
    fake = _FakeSession(json.dumps({"candidates": candidates[::-1] if reverse else candidates}))
    adapter = GoogleLLMAdapter("google_llm", _build_app_config(), GoogleProviderConfig(api_key="test-key"), session_factory=lambda: fake)
    try:
        result = await adapter.generate("mixed", "hello", {}, {"model": "gemini-2.5-flash", "candidate_count": 2})
        assert result.text == "Complete answer."
        assert len(fake.requests) == 1
        assert fake.requests[0]["json"]["generationConfig"]["candidateCount"] == 2
    finally:
        await adapter.stop()
    assert fake.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("other", [
    {"finishReason": "STOP", "content": {"parts": [{"text": "  "}]}},
    {"finishReason": "STOP", "content": {"parts": [{"text": "Thinking", "thought": True}]}},
    {"finishReason": "SAFETY", "content": {"parts": [{"text": "Blocked"}]}},
    {"content": {"parts": [{"text": "Unfinished"}]}},
])
async def test_google_no_usable_complete_candidate_still_fails_summary(monkeypatch, other):
    from src.post_call_summary import PostCallSummaryService
    fake = _FakeSession(json.dumps({"candidates": [other, {"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "Partial"}]}}]}))
    monkeypatch.setattr("src.pipelines.google.aiohttp.ClientSession", lambda: fake)
    result = await PostCallSummaryService(PipelineOrchestrator(_build_app_config())).generate(
        provider="google_llm", call_id="mixed-summary", conversation_history=[{"role": "user", "content": "Hours?"}],
        system_prompt="Summarize.", max_words=50, timeout_ms=1000,
    )
    assert result.status == "error"
    assert result.error_code == "GoogleGenerationIncompleteError"
    assert result.text == ""
    assert len(fake.requests) == 1
    assert fake.closed
