import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.audio.audiosocket_protocol import (
    AUDIO_TYPE_TO_FORMAT,
    AudioSocketAudioFrame,
    audio_message_type,
    supports_multirate_audiosocket,
)
from src.audio.audiosocket_server import AudioSocketServer, TYPE_TERMINATE, TYPE_UUID
from src.core.streaming_playback_manager import StreamingPlaybackManager
from src.core.transport_orchestrator import TransportOrchestrator
from src.engine import Engine
from src.providers.base import ProviderCapabilities
from src.providers.local import LocalProvider


class _Writer:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


@pytest.mark.parametrize(
    ("message_type", "encoding", "sample_rate"),
    [(message_type, *audio_format) for message_type, audio_format in AUDIO_TYPE_TO_FORMAT.items()],
)
def test_audio_message_types_cover_all_supported_signed_linear_rates(
    message_type, encoding, sample_rate
):
    assert audio_message_type(encoding, sample_rate) == message_type


def test_linear16_alias_uses_explicit_sample_rate_not_name_suffix():
    assert audio_message_type("linear16", 8000) == 0x10
    assert audio_message_type("linear16", 16000) == 0x12


@pytest.mark.parametrize(
    ("version", "supported"),
    [
        ("Asterisk 18.26.4", False),
        ("20.16.1", False),
        ("20.17.0", True),
        ("21.11.0", False),
        ("21.12.0", True),
        ("22.7.0", True),
        ("23.1.0", True),
        ("24.0.0", True),
        (None, False),
        ("custom-build", False),
    ],
)
def test_multirate_version_gate(version, supported):
    assert supports_multirate_audiosocket(version) is supported


@pytest.mark.asyncio
async def test_server_decodes_slin16_header_metadata_after_fragmented_reads():
    received = []
    on_uuid = AsyncMock(return_value=True)

    async def on_audio(conn_id, frame):
        received.append((conn_id, frame))

    server = AudioSocketServer("127.0.0.1", 0, on_uuid=on_uuid, on_audio=on_audio)
    reader = asyncio.StreamReader()
    writer = _Writer()
    call_uuid = uuid.uuid4()
    payload = b"\x01\x02" * 320
    wire = (
        bytes([TYPE_UUID])
        + (16).to_bytes(2, "big")
        + call_uuid.bytes
        + bytes([0x12])
        + len(payload).to_bytes(2, "big")
        + payload
        + bytes([TYPE_TERMINATE, 0, 0])
    )
    for chunk in (wire[:2], wire[2:11], wire[11:25], wire[25:200], wire[200:]):
        reader.feed_data(chunk)
    reader.feed_eof()

    await server._connection_loop("conn-16k", reader, writer)

    on_uuid.assert_awaited_once_with("conn-16k", str(call_uuid))
    assert received == [
        (
            "conn-16k",
            AudioSocketAudioFrame(payload, 0x12, "slin16", 16000),
        )
    ]


@pytest.mark.asyncio
async def test_server_writes_rate_specific_type_and_rejects_oversized_frame():
    server = AudioSocketServer(
        "127.0.0.1",
        0,
        on_uuid=AsyncMock(return_value=True),
        on_audio=AsyncMock(),
    )
    writer = _Writer()
    server._writers["conn"] = writer
    payload = b"\x00\x01" * 320

    assert await server.send_audio(
        "conn", payload, encoding="slin16", sample_rate=16000
    )
    assert writer.data[:3] == bytes([0x12]) + len(payload).to_bytes(2, "big")
    assert writer.data[3:] == payload
    assert not await server.send_audio("conn", b"x" * 65536)


def _orchestrator_config():
    return {
        "audio_transport": "audiosocket",
        "audiosocket": {"format": "slin"},
        "profiles": {
            "default": "telephony_ulaw_8k",
            "telephony_ulaw_8k": {
                "internal_rate_hz": 8000,
                "transport_out": {"encoding": "ulaw", "sample_rate_hz": 8000},
                "provider_pref": {},
            },
            "wideband_pcm_16k": {
                "internal_rate_hz": 16000,
                "talk_detect_talking_threshold": 1000,
                "transport_out": {"encoding": "slin16", "sample_rate_hz": 16000},
                "provider_pref": {},
            },
        },
    }


def test_audiosocket_profile_selects_wideband_without_changing_legacy_profile():
    orchestrator = TransportOrchestrator(_orchestrator_config())

    legacy = orchestrator.resolve_transport("test", None, {})
    wideband = orchestrator.resolve_transport(
        "test", None, {"AI_AUDIO_PROFILE": "wideband_pcm_16k"}
    )

    assert (legacy.wire_encoding, legacy.wire_sample_rate) == ("slin", 8000)
    assert (wideband.wire_encoding, wideband.wire_sample_rate) == ("slin16", 16000)
    assert legacy.talk_detect_talking_threshold is None
    assert wideband.talk_detect_talking_threshold == 1000


def test_companded_profile_does_not_inherit_global_wideband_format():
    config = _orchestrator_config()
    config["audiosocket"] = {"format": "slin16", "sample_rate": 16000}
    orchestrator = TransportOrchestrator(config)

    legacy = orchestrator.resolve_transport("test", None, {})

    assert (legacy.wire_encoding, legacy.wire_sample_rate) == ("slin", 8000)


def test_externalmedia_does_not_select_provider_wideband_capabilities():
    config = _orchestrator_config()
    config["audio_transport"] = "externalmedia"
    orchestrator = TransportOrchestrator(config)
    caps = ProviderCapabilities(
        input_encodings=["linear16", "ulaw"],
        input_sample_rates_hz=[24000, 8000],
        output_encodings=["pcm16", "ulaw"],
        output_sample_rates_hz=[24000, 8000],
        wideband_input_encoding="linear16",
        wideband_input_sample_rate_hz=24000,
        wideband_output_encoding="pcm16",
        wideband_output_sample_rate_hz=24000,
    )
    configured = SimpleNamespace(
        provider_input_encoding="ulaw",
        provider_input_sample_rate_hz=8000,
        output_encoding="ulaw",
        output_sample_rate_hz=8000,
    )

    resolved = orchestrator.resolve_transport(
        "test",
        caps,
        {"AI_AUDIO_PROFILE": "wideband_pcm_16k"},
        provider_config=configured,
    )

    assert resolved.provider_input_encoding == "ulaw"
    assert resolved.provider_input_sample_rate == 8000
    assert resolved.provider_output_encoding == "ulaw"
    assert resolved.provider_output_sample_rate == 8000


def test_asterisk_parenthesized_g722_format_is_detected_as_wideband_pcm():
    assert Engine._normalize_audio_format("(g722)") == (
        "slin16",
        16000,
        "(g722)",
    )


@pytest.mark.asyncio
async def test_pipeline_talk_detect_prefers_per_profile_threshold():
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(
        barge_in=SimpleNamespace(
            pipeline_talk_detect_enabled=True,
            pipeline_talk_detect_silence_ms=1200,
            pipeline_talk_detect_talking_threshold=256,
        )
    )
    engine.ari_client = SimpleNamespace(set_channel_var=AsyncMock(return_value=True))
    engine._save_session = AsyncMock()
    engine._pipeline_forced = {"call-wideband": True}
    session = SimpleNamespace(
        call_id="call-wideband",
        caller_channel_id="channel-wideband",
        vad_state={},
        transport_profile=SimpleNamespace(
            profile_name="wideband_pcm_16k",
            talk_detect_talking_threshold=1000,
        ),
    )

    await engine._enable_pipeline_talk_detect(session)

    engine.ari_client.set_channel_var.assert_awaited_once_with(
        "channel-wideband", "TALK_DETECT(set)", "1200,1000"
    )
    assert session.vad_state["pipeline_talk_detect"]["talking_threshold"] == 1000


@pytest.mark.parametrize(
    (
        "provider_name",
        "wideband_input_rate",
        "wideband_output_rate",
        "expected_input_rate",
        "expected_output_rate",
    ),
    [
        ("elevenlabs_agent", 16000, 16000, 16000, 16000),
        ("google_live", 16000, 24000, 16000, 24000),
        ("openai_realtime", 24000, 24000, 24000, 24000),
        ("grok", 16000, 16000, 16000, 16000),
        ("deepgram", 16000, 16000, 16000, 16000),
        ("local", 16000, 8000, 16000, 8000),
    ],
)
def test_wideband_profile_uses_provider_native_boundary(
    provider_name,
    wideband_input_rate,
    wideband_output_rate,
    expected_input_rate,
    expected_output_rate,
):
    orchestrator = TransportOrchestrator(_orchestrator_config())
    caps = ProviderCapabilities(
        input_encodings=["linear16", "ulaw"],
        input_sample_rates_hz=[wideband_input_rate, 8000],
        output_encodings=["pcm16", "ulaw"],
        output_sample_rates_hz=[wideband_output_rate, 8000],
        wideband_input_encoding="linear16",
        wideband_input_sample_rate_hz=wideband_input_rate,
        wideband_output_encoding="pcm16" if wideband_output_rate >= 16000 else "ulaw",
        wideband_output_sample_rate_hz=wideband_output_rate,
    )
    configured = SimpleNamespace(
        provider_input_encoding="ulaw",
        provider_input_sample_rate_hz=8000,
        output_encoding="ulaw",
        output_sample_rate_hz=8000,
    )

    resolved = orchestrator.resolve_transport(
        provider_name,
        caps,
        {"AI_AUDIO_PROFILE": "wideband_pcm_16k"},
        provider_config=configured,
    )

    assert resolved.provider_input_sample_rate == expected_input_rate
    assert resolved.provider_output_sample_rate == expected_output_rate


def test_legacy_profile_keeps_configured_provider_boundary():
    orchestrator = TransportOrchestrator(_orchestrator_config())
    caps = ProviderCapabilities(
        input_encodings=["linear16", "ulaw"],
        input_sample_rates_hz=[24000, 8000],
        output_encodings=["pcm16", "ulaw"],
        output_sample_rates_hz=[24000, 8000],
        wideband_input_encoding="linear16",
        wideband_input_sample_rate_hz=24000,
        wideband_output_encoding="pcm16",
        wideband_output_sample_rate_hz=24000,
    )
    configured = SimpleNamespace(
        provider_input_encoding="ulaw",
        provider_input_sample_rate_hz=8000,
        output_encoding="ulaw",
        output_sample_rate_hz=8000,
    )

    resolved = orchestrator.resolve_transport(
        "test", caps, {}, provider_config=configured
    )

    assert resolved.provider_input_encoding == "ulaw"
    assert resolved.provider_input_sample_rate == 8000
    assert resolved.provider_output_encoding == "ulaw"
    assert resolved.provider_output_sample_rate == 8000


def test_engine_applies_wideband_provider_formats_without_mutating_template():
    engine = Engine.__new__(Engine)
    provider = SimpleNamespace(
        config=SimpleNamespace(
            input_encoding="ulaw",
            input_sample_rate_hz=8000,
            provider_input_encoding="ulaw",
            provider_input_sample_rate_hz=8000,
            output_encoding="ulaw",
            output_sample_rate_hz=8000,
            target_encoding="ulaw",
            target_sample_rate_hz=8000,
            output_resampler="inherit",
        )
    )
    session = SimpleNamespace(
        call_id="call-wideband",
        transport_profile=SimpleNamespace(
            profile_name="wideband_pcm_16k",
            wire_encoding="slin16",
            wire_sample_rate=16000,
            output_resampler="linear",
        ),
        provider_overrides={
            "target_encoding": "slin16",
            "target_sample_rate_hz": 16000,
            "provider_input_encoding": "linear16",
            "provider_input_sample_rate_hz": 24000,
            "provider_output_encoding": "pcm16",
            "provider_output_sample_rate_hz": 24000,
        },
    )

    engine._apply_provider_overrides(provider, session)

    assert (provider.config.input_encoding, provider.config.input_sample_rate_hz) == (
        "slin16",
        16000,
    )
    assert (
        provider.config.provider_input_encoding,
        provider.config.provider_input_sample_rate_hz,
    ) == ("linear16", 24000)
    assert (provider.config.output_encoding, provider.config.output_sample_rate_hz) == (
        "pcm16",
        24000,
    )
    assert (provider.config.target_encoding, provider.config.target_sample_rate_hz) == (
        "slin16",
        16000,
    )


def test_engine_preserves_dict_provider_input_when_override_is_absent():
    engine = Engine.__new__(Engine)
    provider = SimpleNamespace(
        config={
            "input_encoding": "ulaw",
            "input_sample_rate_hz": 8000,
            "provider_input_encoding": "ulaw",
            "provider_input_sample_rate_hz": 8000,
            "target_encoding": "ulaw",
            "target_sample_rate_hz": 8000,
            "output_resampler": "inherit",
        }
    )
    session = SimpleNamespace(
        call_id="call-legacy-dict",
        transport_profile=SimpleNamespace(
            profile_name="telephony_ulaw_8k",
            wire_encoding="slin",
            wire_sample_rate=8000,
            output_resampler="linear",
        ),
        provider_overrides={
            "target_encoding": "slin",
            "target_sample_rate_hz": 8000,
        },
    )

    engine._apply_provider_overrides(provider, session)

    assert provider.config["provider_input_encoding"] == "ulaw"
    assert provider.config["provider_input_sample_rate_hz"] == 8000


@pytest.mark.parametrize(
    ("configured_format", "expected_mode"),
    [
        ("ulaw", "mulaw8k"),
        ("slin", "pcm16_8k"),
        ("slin16", "pcm16_16k"),
    ],
)
def test_provider_input_mode_falls_back_to_legacy_audiosocket_config(
    configured_format, expected_mode
):
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(
        audio_transport="audiosocket",
        audiosocket=SimpleNamespace(format=configured_format),
    )
    session = SimpleNamespace(transport_profile=None)

    assert engine._provider_input_mode_for_transport(session) == expected_mode


def test_provider_input_mode_prefers_resolved_wideband_transport():
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(
        audio_transport="audiosocket",
        audiosocket=SimpleNamespace(format="ulaw"),
    )
    session = SimpleNamespace(
        transport_profile=SimpleNamespace(
            wire_encoding="slin16", wire_sample_rate=16000
        )
    )

    assert engine._provider_input_mode_for_transport(session) == "pcm16_16k"


def test_local_tts_preferences_use_supported_contract_for_each_wire_rate():
    provider = LocalProvider.__new__(LocalProvider)

    provider.config = SimpleNamespace(
        target_encoding="slin",
        target_sample_rate_hz=8000,
    )
    assert provider._tts_output_preferences() == {
        "output_encoding": "mulaw",
        "output_sample_rate_hz": 8000,
    }

    provider.config = SimpleNamespace(
        target_encoding="slin16",
        target_sample_rate_hz=16000,
    )
    assert provider._tts_output_preferences() == {
        "output_encoding": "linear16",
        "output_sample_rate_hz": 16000,
    }


@pytest.mark.asyncio
async def test_streaming_manager_keeps_audiosocket_framing_per_call():
    sessions = {
        "call-8k": SimpleNamespace(audiosocket_conn_id="conn-8k"),
        "call-16k": SimpleNamespace(audiosocket_conn_id="conn-16k"),
    }
    session_store = SimpleNamespace(
        get_by_call_id=AsyncMock(side_effect=lambda call_id: sessions[call_id])
    )
    audio_server = SimpleNamespace(send_audio=AsyncMock(return_value=True))
    manager = StreamingPlaybackManager(
        session_store,
        ari_client=SimpleNamespace(),
        streaming_config={"sample_rate": 8000},
        audio_transport="audiosocket",
        audiosocket_server=audio_server,
    )

    assert await manager._send_audio_chunk(
        "call-8k", "stream-8k", b"a" * 320, target_fmt="slin", target_rate=8000
    )
    assert await manager._send_audio_chunk(
        "call-16k", "stream-16k", b"b" * 640, target_fmt="slin16", target_rate=16000
    )

    assert audio_server.send_audio.await_args_list[0].kwargs == {
        "encoding": "slin",
        "sample_rate": 8000,
    }
    assert audio_server.send_audio.await_args_list[1].kwargs == {
        "encoding": "slin16",
        "sample_rate": 16000,
    }


@pytest.mark.asyncio
async def test_audiosocket_mulaw_remainder_uses_mulaw_silence_padding():
    call_id = "call-mulaw-tail"
    stream_id = "stream-mulaw-tail"
    session_store = SimpleNamespace(clear_gating_token=AsyncMock(return_value=True))
    manager = StreamingPlaybackManager(
        session_store,
        ari_client=SimpleNamespace(),
        streaming_config={"sample_rate": 8000, "chunk_size_ms": 20},
        audio_transport="audiosocket",
    )
    manager.provider_grace_ms = 0
    manager._send_audio_chunk = AsyncMock(return_value=True)
    manager.active_streams[call_id] = {
        "stream_id": stream_id,
        "target_format": "ulaw",
        "target_sample_rate": 8000,
        "chunk_size_ms": 20,
        "diag_enabled": False,
        "end_reason": "completed",
    }
    manager.frame_remainders[call_id] = b"\x7f"

    await manager._cleanup_stream(call_id, stream_id)

    sent = manager._send_audio_chunk.await_args.args[2]
    assert sent == b"\x7f" + (b"\xFF" * 159)
    assert manager._send_audio_chunk.await_args.kwargs == {
        "target_fmt": "ulaw",
        "target_rate": 8000,
    }


@pytest.mark.asyncio
async def test_enhanced_vad_uses_audiosocket_frame_rate(monkeypatch):
    resample_calls = []

    def fake_resample(data, source_rate, target_rate, *, state=None):
        resample_calls.append((data, source_rate, target_rate, state))
        return b"\x00\x00" * 160, None

    monkeypatch.setattr("src.engine.resample_audio", fake_resample)
    vad_result = SimpleNamespace(is_speech=False, confidence=0.0, energy_level=0)
    engine = Engine.__new__(Engine)
    engine.vad_manager = SimpleNamespace(
        process_frame=AsyncMock(return_value=vad_result)
    )
    engine._resample_state_vad8k = {}
    engine.config = SimpleNamespace(audiosocket=SimpleNamespace(format="ulaw"))
    session = SimpleNamespace(
        call_id="call-vad-wideband",
        transport_profile=SimpleNamespace(
            wire_encoding="slin16", wire_sample_rate=16000
        ),
        vad_state={},
    )

    result = await engine._run_enhanced_vad(
        session,
        b"\x01\x00" * 320,
        wire_encoding="slin16",
        wire_sample_rate=16000,
    )

    assert result is vad_result
    assert resample_calls[0][1:3] == (16000, 8000)
    engine.vad_manager.process_frame.assert_awaited_once()


@pytest.mark.asyncio
async def test_streaming_start_honors_wideband_target_over_global_fallback():
    session = SimpleNamespace(
        provider_name="elevenlabs_agent",
        transport_profile=SimpleNamespace(
            wire_encoding="slin16",
            wire_sample_rate=16000,
            provider_output_sample_rate=16000,
        ),
        streaming_started=False,
        current_stream_id=None,
    )
    session_store = SimpleNamespace(
        get_by_call_id=AsyncMock(return_value=session),
        upsert_call=AsyncMock(),
        set_gating_token=AsyncMock(return_value=True),
        clear_gating_token=AsyncMock(return_value=True),
    )
    manager = StreamingPlaybackManager(
        session_store,
        ari_client=SimpleNamespace(),
        streaming_config={"sample_rate": 8000},
        audio_transport="audiosocket",
    )
    manager.audiosocket_format = "slin"

    stream_id = await manager.start_streaming_playback(
        "call-wideband",
        asyncio.Queue(),
        source_encoding="slin16",
        source_sample_rate=16000,
        target_encoding="slin16",
        target_sample_rate=16000,
    )
    try:
        assert stream_id
        assert manager.active_streams["call-wideband"]["target_format"] == "slin16"
        assert manager.active_streams["call-wideband"]["target_sample_rate"] == 16000
    finally:
        if stream_id:
            await manager.stop_streaming_playback("call-wideband")


@pytest.mark.asyncio
async def test_streaming_start_inherits_wideband_target_from_call_profile():
    """Pipeline callers may omit explicit targets after profile resolution."""
    session = SimpleNamespace(
        provider_name="pipeline",
        transport_profile=SimpleNamespace(
            wire_encoding="slin16",
            wire_sample_rate=16000,
            provider_output_sample_rate=16000,
        ),
        streaming_started=False,
        current_stream_id=None,
    )
    session_store = SimpleNamespace(
        get_by_call_id=AsyncMock(return_value=session),
        upsert_call=AsyncMock(),
        set_gating_token=AsyncMock(return_value=True),
        clear_gating_token=AsyncMock(return_value=True),
    )
    manager = StreamingPlaybackManager(
        session_store,
        ari_client=SimpleNamespace(),
        streaming_config={"sample_rate": 8000},
        audio_transport="audiosocket",
    )
    manager.audiosocket_format = "slin"

    stream_id = await manager.start_streaming_playback(
        "call-pipeline-wideband",
        asyncio.Queue(),
        source_encoding="linear16",
        source_sample_rate=16000,
    )
    try:
        assert stream_id
        info = manager.active_streams["call-pipeline-wideband"]
        assert info["target_format"] == "slin16"
        assert info["target_sample_rate"] == 16000
    finally:
        if stream_id:
            await manager.stop_streaming_playback("call-pipeline-wideband")


@pytest.mark.asyncio
async def test_stream_timing_is_isolated_per_call_profile():
    sessions = {
        "call-40ms": SimpleNamespace(
            provider_name="test",
            transport_profile=SimpleNamespace(
                wire_encoding="slin16",
                wire_sample_rate=16000,
                provider_output_sample_rate=16000,
                chunk_ms=40,
                idle_cutoff_ms=600,
            ),
            streaming_started=False,
            current_stream_id=None,
        ),
        "call-20ms": SimpleNamespace(
            provider_name="test",
            transport_profile=SimpleNamespace(
                wire_encoding="slin",
                wire_sample_rate=8000,
                provider_output_sample_rate=8000,
                chunk_ms=20,
                idle_cutoff_ms=1200,
            ),
            streaming_started=False,
            current_stream_id=None,
        ),
    }
    session_store = SimpleNamespace(
        get_by_call_id=AsyncMock(side_effect=lambda call_id: sessions[call_id]),
        upsert_call=AsyncMock(),
        set_gating_token=AsyncMock(return_value=True),
        clear_gating_token=AsyncMock(return_value=True),
    )
    manager = StreamingPlaybackManager(
        session_store,
        ari_client=SimpleNamespace(),
        streaming_config={"chunk_size_ms": 20},
        audio_transport="audiosocket",
    )

    stream_40 = await manager.start_streaming_playback("call-40ms", asyncio.Queue())
    stream_20 = await manager.start_streaming_playback("call-20ms", asyncio.Queue())
    try:
        assert stream_40 and stream_20
        assert manager.active_streams["call-40ms"]["chunk_size_ms"] == 40
        assert manager.active_streams["call-40ms"]["idle_cutoff_ms"] == 600
        assert "low_watermark_chunks" in manager.active_streams["call-40ms"]
        assert manager._get_low_watermark_frames("call-40ms") == (
            manager.active_streams["call-40ms"]["low_watermark_chunks"]
        )
        assert manager._frame_size_bytes("call-40ms") == 1280
        assert manager.active_streams["call-20ms"]["chunk_size_ms"] == 20
        assert manager.active_streams["call-20ms"]["idle_cutoff_ms"] == 1200
        assert "low_watermark_chunks" in manager.active_streams["call-20ms"]
        assert manager._get_low_watermark_frames("call-20ms") == (
            manager.active_streams["call-20ms"]["low_watermark_chunks"]
        )
        assert manager._frame_size_bytes("call-20ms") == 320
        assert manager.chunk_size_ms == 20
    finally:
        if stream_40:
            await manager.stop_streaming_playback("call-40ms")
        if stream_20:
            await manager.stop_streaming_playback("call-20ms")


@pytest.mark.asyncio
async def test_engine_originates_wideband_channel_from_call_profile():
    session = SimpleNamespace(
        transport_profile=SimpleNamespace(
            wire_encoding="slin16", wire_sample_rate=16000
        ),
        audiosocket_uuid=None,
    )
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(
        audiosocket=SimpleNamespace(
            host="127.0.0.1", advertise_host=None, port=8090, format="slin"
        ),
        asterisk=SimpleNamespace(app_name="asterisk-ai-voice-agent"),
    )
    engine.session_store = SimpleNamespace(get_by_call_id=AsyncMock(return_value=session))
    engine.ari_client = SimpleNamespace(
        send_command=AsyncMock(return_value={"id": "audiosocket-channel"})
    )
    engine.pending_audiosocket_channels = {}
    engine.uuidext_to_channel = {}
    engine._save_session = AsyncMock()

    await Engine._originate_audiosocket_channel_hybrid(engine, "caller-channel")

    params = engine.ari_client.send_command.await_args.kwargs["params"]
    assert params["endpoint"].startswith("AudioSocket/127.0.0.1:8090/")
    assert params["endpoint"].endswith("/c(slin16)")
    assert engine.pending_audiosocket_channels["audiosocket-channel"] == "caller-channel"
