from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.call_diagnostics import build_call_diagnostics_snapshot
from src.engine import Engine


def test_snapshot_captures_effective_provider_audio_and_runtime_without_secrets():
    config = SimpleNamespace(
        audio_transport="audiosocket",
        downstream_mode="stream",
        providers={
            "google_live": {
                "type": "google_live",
                "model": "gemini-live",
                "input_encoding": "pcm16",
                "input_sample_rate_hz": 16000,
                "api_key": "must-not-be-copied",
                "instructions": "must-not-be-copied",
            }
        },
        streaming=SimpleNamespace(chunk_size_ms=20, provider_grace_ms=400),
        vad=SimpleNamespace(vad_mode="provider", energy_threshold=300),
        barge_in=SimpleNamespace(enabled=True, min_ms=160),
    )
    transport = SimpleNamespace(
        profile_name="telephony_ulaw_8k",
        wire_encoding="ulaw",
        wire_sample_rate=8000,
        provider_input_encoding="pcm16",
        provider_input_sample_rate=16000,
        provider_output_encoding="pcm16",
        provider_output_sample_rate=24000,
        internal_rate=16000,
        chunk_ms=20,
        idle_cutoff_ms=1200,
        output_resampler="soxr",
        output_resampler_source="profile",
    )
    session = SimpleNamespace(
        diagnostics_snapshot={},
        provider_name="google_live",
        provider_kind="realtime",
        context_name="support",
        routing_method="ai_agent",
        pipeline_name=None,
        pipeline_components={},
        transport_profile=transport,
        session_voice="Aoede",
        voice_source="agent",
        tool_generation_id=3,
        tool_config_hash="safe-config-hash",
        no_input_policy={"enabled": True, "initial_timeout_sec": 8, "secret": "no"},
        media_transport_kind="audiosocket",
        media_connection_state="connected",
        negotiated_encoding="ulaw",
        negotiated_sample_rate=8000,
        media_packetization_ms=20,
        media_optimal_frame_size=160,
        media_rx_confirmed=True,
        provider_session_active=True,
        codec_alignment_ok=True,
        caller_audio_format="ulaw",
        caller_sample_rate=8000,
        streaming_started=True,
        streaming_bytes_sent=2048,
        streaming_fallback_count=0,
        streaming_keepalive_timeouts=0,
        websocket_input_rejections={},
    )

    snapshot = build_call_diagnostics_snapshot(config, session)

    assert snapshot["resolved"]["audio_profile"] == "telephony_ulaw_8k"
    assert snapshot["resolved"]["transport_profile"]["wire_sample_rate"] == 8000
    assert snapshot["configured"]["provider"]["model"] == "gemini-live"
    assert snapshot["configured"]["vad"]["vad_mode"] == "provider"
    assert snapshot["runtime"]["streaming_bytes_sent"] == 2048
    rendered = repr(snapshot)
    assert "must-not-be-copied" not in rendered
    assert "api_key" not in rendered
    assert "instructions" not in rendered


def test_snapshot_freezes_configured_values_but_refreshes_runtime():
    config = SimpleNamespace(
        audio_transport="audiosocket", downstream_mode="stream", providers={},
        streaming=None, vad=None, barge_in=None,
    )
    session = SimpleNamespace(
        diagnostics_snapshot={}, provider_name="local", provider_kind="local",
        context_name="agent", routing_method="ai_agent", pipeline_name="local_hybrid",
        pipeline_components={}, transport_profile=SimpleNamespace(profile_name="first"),
        session_voice=None, voice_source="provider-default", websocket_input_rejections={},
    )
    first = build_call_diagnostics_snapshot(config, session)
    session.diagnostics_snapshot = first
    config.audio_transport = "externalmedia"
    session.transport_profile.profile_name = "changed"
    session.streaming_bytes_sent = 99

    final = build_call_diagnostics_snapshot(config, session)

    assert final["configured"]["audio_transport"] == "audiosocket"
    assert final["resolved"]["audio_profile"] == "first"
    assert final["runtime"]["streaming_bytes_sent"] == 99


def test_snapshot_deep_copies_mutable_allowlisted_values():
    response_modalities = ["audio", "text"]
    fallback_providers = ["local"]
    fallback_thresholds = {"google_live": 180}
    allowed_hosts = ["127.0.0.1"]
    config = SimpleNamespace(
        audio_transport="externalmedia",
        downstream_mode="stream",
        providers={"google_live": {"response_modalities": response_modalities}},
        streaming=None,
        vad=None,
        barge_in=SimpleNamespace(
            provider_fallback_providers=fallback_providers,
            provider_fallback_min_ms_by_provider=fallback_thresholds,
        ),
        external_media=SimpleNamespace(allowed_remote_hosts=allowed_hosts),
    )
    session = SimpleNamespace(
        diagnostics_snapshot={},
        provider_name="google_live",
        pipeline_components={"stt": {"name": "local_stt"}},
        transport_profile=None,
        websocket_input_rejections={},
    )

    snapshot = build_call_diagnostics_snapshot(config, session)
    response_modalities.append("image")
    fallback_providers.append("deepgram")
    fallback_thresholds["google_live"] = 999
    allowed_hosts.append("10.0.0.1")
    session.pipeline_components["stt"]["name"] = "changed"

    assert snapshot["configured"]["provider"]["response_modalities"] == ["audio", "text"]
    assert snapshot["configured"]["barge_in"]["provider_fallback_providers"] == ["local"]
    assert snapshot["configured"]["barge_in"]["provider_fallback_min_ms_by_provider"] == {"google_live": 180}
    assert snapshot["configured"]["selected_transport"]["allowed_remote_hosts"] == ["127.0.0.1"]
    assert snapshot["resolved"]["pipeline_components"] == {"stt": {"name": "local_stt"}}


def test_setup_finalizer_recaptures_effective_provider_and_audio_profile():
    config = SimpleNamespace(
        audio_transport="audiosocket",
        downstream_mode="stream",
        providers={
            "default": {"model": "default-model"},
            "override": {"model": "effective-model"},
        },
        streaming=None,
        vad=None,
        barge_in=None,
        audiosocket=SimpleNamespace(format="slin", port=8090),
    )
    session = SimpleNamespace(
        diagnostics_snapshot={},
        provider_name="default",
        pipeline_components={},
        transport_profile=SimpleNamespace(profile_name="initial"),
        websocket_input_rejections={},
    )
    session.diagnostics_snapshot = build_call_diagnostics_snapshot(config, session)
    session.provider_name = "override"
    session.transport_profile = SimpleNamespace(profile_name="telephony_ulaw_8k")

    finalized = build_call_diagnostics_snapshot(
        config,
        session,
        replace_configured=True,
    )

    assert finalized["configured"]["provider"]["model"] == "effective-model"
    assert finalized["resolved"]["provider_name"] == "override"
    assert finalized["resolved"]["audio_profile"] == "telephony_ulaw_8k"


@pytest.mark.asyncio
async def test_stasis_setup_failure_has_an_initial_diagnostics_snapshot():
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(
        default_provider="google_live",
        audio_transport="audiosocket",
        downstream_mode="stream",
        providers={"google_live": {"model": "gemini-live"}},
        streaming=None,
        vad=None,
        barge_in=None,
        audiosocket=SimpleNamespace(format="slin", port=8090),
    )
    engine.ari_client = SimpleNamespace(
        send_command=AsyncMock(return_value={}),
        answer_channel=AsyncMock(),
        create_bridge=AsyncMock(return_value="bridge-1"),
        add_channel_to_bridge=AsyncMock(return_value=True),
    )
    engine.session_store = SimpleNamespace(get_by_call_id=AsyncMock(return_value=None))
    engine.bridges = {}
    engine._called_number_cache = {}
    engine._get_provider_kind = lambda provider: provider
    engine._tool_generation = None
    engine._resolve_session_tool_runtime = lambda _session: None
    engine._should_use_local_vad = lambda _provider: False
    engine.vad_manager = None
    engine._cleanup_call = AsyncMock()
    captured = []

    async def save(session, *, new=False):
        if new:
            captured.append(deepcopy(session.diagnostics_snapshot))
            return
        raise RuntimeError("fail after initial session registration")

    engine._save_session = save

    await Engine._handle_caller_stasis_start_hybrid(
        engine,
        "setup-failure-call",
        {"caller": {"name": "Test", "number": "anonymous"}},
    )

    assert captured[0]["configured"]["provider"]["model"] == "gemini-live"
    assert captured[0]["resolved"]["provider_name"] == "google_live"
    engine._cleanup_call.assert_awaited_once_with(
        "setup-failure-call",
        force_caller_hangup=True,
    )
