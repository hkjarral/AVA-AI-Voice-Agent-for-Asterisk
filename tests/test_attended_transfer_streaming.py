import pytest

from src.config import AppConfig
from src.core.models import CallSession
from src.engine import Engine


def _build_engine(attended_transfer_cfg: dict) -> Engine:
    config_data = {
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {
            "host": "127.0.0.1",
            "port": 8088,
            "username": "u",
            "password": "p",
            "app_name": "ai-voice-agent",
        },
        "llm": {"initial_greeting": "hi", "prompt": "You are helpful", "model": "gpt-4o"},
        "pipelines": {"local_only": {}},
        "active_pipeline": "local_only",
        "audio_transport": "audiosocket",
        "external_media": {
            "rtp_host": "0.0.0.0",
            "rtp_port": 18080,
            "advertise_host": "127.0.0.1",
            "port_range": "18080-18090",
            "codec": "ulaw",
            "format": "slin16",
            "sample_rate": 16000,
        },
        "audiosocket": {"host": "0.0.0.0", "port": 9092, "format": "ulaw"},
        "tools": {
            "attended_transfer": attended_transfer_cfg,
            "transfer": {
                "destinations": {
                    "support_agent": {
                        "type": "extension",
                        "target": "6000",
                        "description": "Support agent",
                        "attended_allowed": True,
                    }
                }
            },
        },
    }
    return Engine(AppConfig(**config_data))


@pytest.mark.asyncio
async def test_attended_transfer_stream_mode_uses_helper_media(monkeypatch):
    engine = _build_engine(
        {
            "enabled": True,
            "delivery_mode": "stream",
            "stream_fallback_to_file": True,
            "accept_digit": "1",
            "decline_digit": "2",
        }
    )

    session = CallSession(
        call_id="call-stream",
        caller_channel_id="caller-stream",
        caller_name="Bob",
        caller_number="15551234567",
        context_name="support",
    )
    session.current_action = {"type": "attended_transfer"}
    await engine.session_store.upsert_call(session)

    streamed_chunks = []
    finalize_calls = []

    async def fake_start_helper(*, call_id, agent_channel_id, attended_cfg=None):
        return {"rtp_session_id": f"attx:{call_id}:{agent_channel_id}"}

    async def fake_tts(*, call_id, text, timeout_sec):
        return b"\xff" * 320

    async def fake_stream(agent_channel_id, audio_bytes, *, frame_ms=20):
        streamed_chunks.append((agent_channel_id, len(audio_bytes), frame_ms))
        return True

    async def fake_wait_dtmf(agent_channel_id, *, timeout_sec):
        return "1"

    async def fake_finalize(session_obj, **kwargs):
        finalize_calls.append((session_obj.call_id, kwargs))

    async def unexpected_abort(*args, **kwargs):
        raise AssertionError("abort path should not run in accepted stream test")

    async def unexpected_file_play(*args, **kwargs):
        raise AssertionError("file playback should not run when helper streaming succeeds")

    monkeypatch.setattr(engine, "_start_attended_transfer_helper_media", fake_start_helper)
    monkeypatch.setattr(engine, "_local_ai_server_tts", fake_tts)
    monkeypatch.setattr(engine, "_stream_attended_transfer_audio", fake_stream)
    monkeypatch.setattr(engine, "_wait_for_attended_transfer_dtmf", fake_wait_dtmf)
    monkeypatch.setattr(engine, "_attended_transfer_finalize_bridge", fake_finalize)
    monkeypatch.setattr(engine, "_attended_transfer_abort_and_resume", unexpected_abort)
    monkeypatch.setattr(engine, "_play_ulaw_bytes_on_channel_and_wait", unexpected_file_play)

    await engine._handle_attended_transfer_answered(
        "agent-stream",
        ["attended-transfer", "call-stream", "support_agent"],
    )

    assert len(streamed_chunks) == 2
    assert streamed_chunks[0][0] == "agent-stream"
    assert streamed_chunks[1][0] == "agent-stream"
    assert finalize_calls
    updated = await engine.session_store.get_by_call_id("call-stream")
    assert updated is not None
    assert updated.current_action is not None
    assert updated.current_action.get("decision") == "accepted"


@pytest.mark.asyncio
async def test_attended_transfer_stream_falls_back_to_file_playback(monkeypatch):
    engine = _build_engine(
        {
            "enabled": True,
            "delivery_mode": "stream",
            "stream_fallback_to_file": True,
            "accept_digit": "1",
            "decline_digit": "2",
        }
    )

    session = CallSession(
        call_id="call-fallback",
        caller_channel_id="caller-fallback",
        caller_name="Bob",
        caller_number="15557654321",
        context_name="support",
    )
    session.current_action = {"type": "attended_transfer"}
    await engine.session_store.upsert_call(session)

    played = []
    abort_reasons = []

    async def fake_start_helper(*, call_id, agent_channel_id, attended_cfg=None):
        return None

    async def fake_tts(*, call_id, text, timeout_sec):
        return b"\xff" * 160

    async def fake_file_play(*, channel_id, audio_bytes, playback_id_prefix, timeout_sec):
        played.append((channel_id, playback_id_prefix, len(audio_bytes)))
        return f"{playback_id_prefix}-ok"

    async def fake_wait_dtmf(agent_channel_id, *, timeout_sec):
        return "2"

    async def fake_abort(session_obj, agent_channel_id, *, reason):
        abort_reasons.append((session_obj.call_id, agent_channel_id, reason))

    async def unexpected_finalize(*args, **kwargs):
        raise AssertionError("finalize path should not run when the agent declines")

    monkeypatch.setattr(engine, "_start_attended_transfer_helper_media", fake_start_helper)
    monkeypatch.setattr(engine, "_local_ai_server_tts", fake_tts)
    monkeypatch.setattr(engine, "_play_ulaw_bytes_on_channel_and_wait", fake_file_play)
    monkeypatch.setattr(engine, "_wait_for_attended_transfer_dtmf", fake_wait_dtmf)
    monkeypatch.setattr(engine, "_attended_transfer_abort_and_resume", fake_abort)
    monkeypatch.setattr(engine, "_attended_transfer_finalize_bridge", unexpected_finalize)

    await engine._handle_attended_transfer_answered(
        "agent-fallback",
        ["attended-transfer", "call-fallback", "support_agent"],
    )

    assert [item[1] for item in played] == ["attx-ann", "attx-prompt"]
    assert abort_reasons == [("call-fallback", "agent-fallback", "declined")]


def test_attended_transfer_helper_defaults_use_offset_port_range():
    engine = _build_engine(
        {
            "enabled": True,
            "delivery_mode": "stream",
            "stream_fallback_to_file": True,
        }
    )

    helper = engine._get_attended_transfer_helper_settings()

    assert helper["rtp_port"] == 18180
    assert helper["port_range"] == (18180, 18190)
