from types import SimpleNamespace

from src.core.call_diagnostics import build_call_diagnostics_snapshot


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
