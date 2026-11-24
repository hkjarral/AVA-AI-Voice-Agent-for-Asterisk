import asyncio
from unittest.mock import AsyncMock

import pytest
import websockets
from websockets.frames import Close

from src.config import AppConfig, GoogleProviderConfig, LocalProviderConfig, OpenAIProviderConfig
from src.pipelines.local import LocalSTTAdapter, LocalTTSAdapter
from src.pipelines.openai import OpenAILLMAdapter, OpenAISTTAdapter, OpenAITTSAdapter
from src.pipelines.orchestrator import PipelineOrchestrator
from src.providers.google_live import GoogleLiveProvider


@pytest.mark.asyncio
async def test_openai_realtime_tts_readiness_requires_model_ack(monkeypatch):
    config = OpenAIProviderConfig(api_key="test", tts_model="gpt-realtime-2.1-mini")
    rejected = AsyncMock()
    rejected.recv.side_effect = websockets.exceptions.ConnectionClosedError(
        Close(3003, "invalid_request_error.model_not_found"), None
    )
    accepted = AsyncMock()
    accepted.recv.side_effect = ['{"type":"session.created"}', '{"type":"session.updated"}']
    monkeypatch.setattr(
        "src.pipelines.openai_realtime_tts.websockets.connect",
        AsyncMock(side_effect=[rejected, accepted]),
    )

    adapter = OpenAITTSAdapter("openai_tts", None, config)
    rejected_result = await adapter.validate_connectivity({})
    assert not rejected_result["healthy"]
    assert "model unavailable" in rejected_result["error"]
    rejected.close.assert_awaited_once()
    result = await adapter.validate_connectivity({})
    assert result["healthy"] and result["details"]["session_acknowledged"]
    accepted.send.assert_awaited_once()
    assert '"type": "session.update"' in accepted.send.await_args.args[0]
    accepted.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_openai_realtime_tts_ignores_audio_received_after_close(monkeypatch):
    from src.pipelines.openai_realtime_tts import RealtimeTTSPool

    ws = AsyncMock()
    receiving = asyncio.Event()
    release = asyncio.Event()

    async def recv():
        if ws.recv.await_count == 1:
            return '{"type":"session.updated"}'
        receiving.set()
        await release.wait()
        return '{"type":"response.output_audio.delta","delta":"AAAAAA=="}'

    ws.recv.side_effect = recv
    ws.close.side_effect = release.set
    monkeypatch.setattr("src.pipelines.openai_realtime_tts.websockets.connect", AsyncMock(return_value=ws))
    pool = RealtimeTTSPool()
    chunks = pool.synthesize("test", "hello", {
        "api_key": "test", "tts_model": "gpt-realtime-2.1-mini", "voice": "marin",
        "tts_realtime_base_url": "wss://api.openai.com/v1/realtime",
        "target_format": {"sample_rate": 24000, "encoding": "linear16"},
    })
    waiting = asyncio.create_task(anext(chunks))
    await receiving.wait()
    await pool.close_call("test")
    with pytest.raises(asyncio.CancelledError):
        await waiting


def test_explicit_token_limit_layer_wins_over_wider_scope():
    config = OpenAIProviderConfig(api_key="test", max_completion_tokens=900)
    adapter = OpenAILLMAdapter("openai_llm", None, config, {"max_tokens": 300})
    options = adapter._compose_options({})
    assert options["max_completion_tokens"] is None and options["max_tokens"] == 300
    options = adapter._compose_options({"max_completion_tokens": 200})
    assert options["max_completion_tokens"] == 200 and options["max_tokens"] is None


@pytest.mark.parametrize(
    ("key", "value", "adapter"),
    [
        ("local_stt", LocalProviderConfig(ws_url="ws://localhost:8765"), LocalSTTAdapter),
        ("local_tts", LocalProviderConfig(ws_url="ws://localhost:8765"), LocalTTSAdapter),
        ("openai_stt", OpenAIProviderConfig(api_key="test"), OpenAISTTAdapter),
        ("openai_tts", OpenAIProviderConfig(api_key="test"), OpenAITTSAdapter),
    ],
)
def test_typed_speech_config_retains_its_adapter(key, value, adapter):
    config = AppConfig(
        default_provider="local",
        asterisk={"host": "127.0.0.1", "username": "ari", "password": "test"},
        llm={"initial_greeting": "hello", "prompt": "test"},
        providers={"local": LocalProviderConfig(), key: value},
    )
    assert isinstance(PipelineOrchestrator(config)._build_component(key, {}), adapter)


@pytest.mark.asyncio
async def test_google_nested_idle_drains_completed_audio_and_arms_current_turn_only():
    on_event = AsyncMock()
    provider = GoogleLiveProvider(GoogleProviderConfig(
        api_key="test", llm_model="gemini-3.8-live-extended-thinking",
        long_audio_playback_enabled=True,
    ), on_event)
    provider._call_id = "test"
    provider._audio_response_id = 3
    provider._generated_audio_turns = [
        {"response_id": i, "assistant": True, "user_text": "hello", "assistant_text": str(i)}
        for i in range(1, 4)
    ]
    provider._maybe_arm_cleanup_after_tts = AsyncMock()

    await provider._handle_server_message(
        {"serverContent": {"turnComplete": True, "interactionStatus": "IN_PROGRESS"}}
    )
    assert len(provider._generated_audio_turns) == 3
    await provider._handle_server_message({"serverContent": {"turnComplete": True, "interactionStatus": "IDLE"}})

    assert not provider._generated_audio_turns
    done = [c.args[0] for c in on_event.await_args_list if c.args[0]["type"] == "AgentAudioDone"]
    assert [event["audio_response_id"] for event in done] == [1, 2, 3]
    provider._maybe_arm_cleanup_after_tts.assert_awaited_once_with(user_text="hello", assistant_text="3")
