from types import SimpleNamespace
from unittest.mock import AsyncMock

import audioop
import asyncio
import json
import pytest

from src.ari_client import ARIClient
from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.core.transport_orchestrator import AudioProfile, TransportOrchestrator
from src.engine import Engine


class _FakeWebSocketServer:
    def __init__(self, binding=None):
        self.binding = binding
        self.registered = []
        self.unregistered = []

    def register_call(self, call_id, channel_id, codec, *, control_format="json"):
        self.registered.append((call_id, channel_id, codec))
        return "safe_nonce"

    async def unregister_call(self, call_id):
        self.unregistered.append(call_id)

    def get_binding(self, call_id):
        return self.binding

    def health(self):
        return {"listening": True}

    def snapshot(self, call_id):
        return {
            "pending": False,
            "output_state": "buffering",
            "queue": {"frames": 7},
        }


def _engine_config(version="20.18.0"):
    return SimpleNamespace(
        audio_transport="websocket",
        asterisk=SimpleNamespace(app_name="aava"),
        websocket_media=SimpleNamespace(
            connection_name="aava_media",
            fallback_format="ulaw",
            media_start_timeout_ms=5000,
        ),
    )


def _bare_engine():
    engine = Engine.__new__(Engine)
    engine.config = _engine_config()
    engine.session_store = SessionStore()
    engine.conversation_coordinator = None
    engine.pending_websocket_channels = {}
    engine.websocket_media_channels = {}
    engine._seen_aux_channels = set()
    engine._websocket_ingress_resample_state = {}
    return engine


@pytest.mark.asyncio
async def test_ari_external_media_websocket_request_keeps_rtp_defaults_optional():
    client = ARIClient.__new__(ARIClient)
    client.send_command = AsyncMock(return_value={"id": "media-1"})

    result = await ARIClient.create_external_media_channel(
        client,
        app="aava",
        external_host="aava_media",
        format="slin16",
        direction="both",
        encapsulation="none",
        transport="websocket",
        connection_type="client",
        transport_data="f(json)v(nonce=abc)",
        channel_id="media-1",
    )

    assert result == {"id": "media-1"}
    payload = client.send_command.await_args.kwargs["data"]
    assert payload == {
        "app": "aava",
        "external_host": "aava_media",
        "format": "slin16",
        "direction": "both",
        "encapsulation": "none",
        "transport": "websocket",
        "connection_type": "client",
        "transport_data": "f(json)v(nonce=abc)",
        "channelId": "media-1",
    }


@pytest.mark.asyncio
async def test_session_store_indexes_neutral_media_channel():
    store = SessionStore()
    session = CallSession(
        call_id="caller-1",
        caller_channel_id="caller-1",
        media_transport_kind="websocket",
        media_channel_id="media-1",
    )
    await store.upsert_call(session)
    assert await store.get_by_channel_id("media-1") is session
    await store.remove_call("caller-1")
    assert await store.get_by_channel_id("media-1") is None


@pytest.mark.parametrize(
    ("encoding", "rate", "expected"),
    [
        ("ulaw", 8000, "ulaw"),
        ("g711_alaw", 8000, "alaw"),
        ("linear16", 8000, "slin"),
        ("linear16", 16000, "slin16"),
    ],
)
def test_websocket_profile_codec_is_call_scoped(encoding, rate, expected):
    session = CallSession(call_id="c", caller_channel_id="c")
    session.transport_profile = SimpleNamespace(
        wire_encoding=encoding,
        wire_sample_rate=rate,
    )
    assert Engine._websocket_profile_codec(session) == expected


@pytest.mark.parametrize(
    ("encoding", "rate", "expected"),
    [
        ("ulaw", 8000, "ulaw"),
        ("alaw", 8000, "alaw"),
        ("slin", 8000, "slin"),
        ("slin16", 16000, "slin16"),
    ],
)
def test_transport_orchestrator_keeps_websocket_wire_codec_per_profile(
    encoding, rate, expected
):
    orchestrator = TransportOrchestrator.__new__(TransportOrchestrator)
    orchestrator.audio_transport = "websocket"
    orchestrator.websocket_fallback_format = "ulaw"
    profile = AudioProfile(
        name=f"profile-{encoding}",
        internal_rate_hz=rate,
        transport_out={"encoding": encoding, "sample_rate_hz": rate},
        provider_pref={
            "input_encoding": "linear16",
            "input_sample_rate_hz": rate,
            "output_encoding": "linear16",
            "output_sample_rate_hz": rate,
        },
    )

    resolved = orchestrator._negotiate_formats(
        profile, "provider", None, provider_config=None
    )

    assert resolved.wire_encoding == expected
    assert resolved.wire_sample_rate == rate


@pytest.mark.asyncio
async def test_websocket_registration_precedes_ari_and_uses_predetermined_id():
    engine = _bare_engine()
    server = _FakeWebSocketServer()
    engine.websocket_server = server
    seen = {}

    async def create_external_media_channel(**kwargs):
        media_id = kwargs["channel_id"]
        seen["registered_before_ari"] = server.registered[-1][1] == media_id
        seen["pending_before_ari"] = engine.pending_websocket_channels[media_id]
        return {"id": media_id}

    engine.ari_client = SimpleNamespace(
        asterisk_version="22.10.1",
        create_external_media_channel=create_external_media_channel
    )
    session = CallSession(call_id="caller-1", caller_channel_id="caller-1")
    session.transport_profile = SimpleNamespace(
        wire_encoding="slin16", wire_sample_rate=16000
    )
    await engine._save_session(session)

    media_id = await engine._start_websocket_media_channel(session)

    assert media_id and media_id.startswith("aava-ws-")
    assert len(media_id) <= 32  # FreePBX CDR/CEL uniqueid and linkedid columns
    assert seen == {
        "registered_before_ari": True,
        "pending_before_ari": "caller-1",
    }
    assert session.media_channel_id == media_id
    assert session.media_channel_pending is True
    assert await engine.session_store.get_by_channel_id(media_id) is session


@pytest.mark.asyncio
async def test_websocket_ulaw_ingress_is_decoded_and_resampled_without_rtp_header_logic():
    engine = _bare_engine()
    binding = SimpleNamespace(codec="ulaw", sample_rate=8000, channel_id="media-1")
    engine.websocket_server = _FakeWebSocketServer(binding)
    engine._on_transport_pcm = AsyncMock()
    session = CallSession(
        call_id="caller-1",
        caller_channel_id="caller-1",
        media_transport_kind="websocket",
        media_channel_id="media-1",
        media_connection_state="ready",
    )
    engine.websocket_media_channels["media-1"] = "caller-1"
    await engine._save_session(session)
    pcm8 = (b"\x10\x00" * 160)
    ulaw = audioop.lin2ulaw(pcm8, 2)

    await engine._websocket_handle_audio("caller-1", ulaw)

    args = engine._on_transport_pcm.await_args.args
    assert args[0] == "caller-1"
    assert len(args[1]) >= len(pcm8) * 2 - 4
    assert args[2] == 16000
    assert engine._on_transport_pcm.await_args.kwargs == {"source": "websocket"}


@pytest.mark.asyncio
async def test_websocket_ingress_drops_audio_before_bridge_ready():
    engine = _bare_engine()
    binding = SimpleNamespace(codec="ulaw", sample_rate=8000, channel_id="media-1")
    engine.websocket_server = _FakeWebSocketServer(binding)
    engine._on_transport_pcm = AsyncMock()
    session = CallSession(
        call_id="caller-1",
        caller_channel_id="caller-1",
        media_transport_kind="websocket",
        media_channel_id="media-1",
        media_connection_state="pending",
    )
    engine.websocket_media_channels["media-1"] = "caller-1"
    await engine._save_session(session)

    await engine._websocket_handle_audio("caller-1", b"\xff" * 160)

    engine._on_transport_pcm.assert_not_awaited()


@pytest.mark.asyncio
async def test_rtp_callback_delegates_to_common_pcm_ingress_with_server_rate():
    engine = Engine.__new__(Engine)
    engine.rtp_server = SimpleNamespace(sample_rate=8000)
    engine._on_transport_pcm = AsyncMock()

    await engine._on_rtp_audio("caller-1", 42, b"\x00\x00")

    engine._on_transport_pcm.assert_awaited_once_with(
        "caller-1",
        b"\x00\x00",
        8000,
        source="externalmedia",
        ssrc=42,
    )


def test_websocket_admission_uses_real_ari_version_and_listener_state():
    engine = _bare_engine()
    engine.websocket_server = _FakeWebSocketServer()
    capability = SimpleNamespace(
        ready=True, inventory_available=True, missing_required_modules=(),
        non_running_required_modules=(), running_timing_modules=("res_timing_timerfd",), reason=None,
    )
    engine.ari_client = SimpleNamespace(
        asterisk_version="Asterisk 20.18.0",
        websocket_media_module_capability=lambda: capability,
    )
    assert engine._websocket_admission_error() is None
    capability.ready = False
    capability.reason = "Missing module chan_websocket"
    assert "chan_websocket" in engine._websocket_admission_error()
    assert engine._selected_transport_ready() is False
    capability.ready = True
    engine.ari_client.asterisk_version = "Asterisk 20.17.0"
    assert "20.18+" in engine._websocket_admission_error()


@pytest.mark.asyncio
@pytest.mark.parametrize("modules_ready", [True, False, None])
async def test_websocket_ready_endpoint_requires_engine_module_inventory(modules_ready):
    engine = _bare_engine()
    engine.websocket_server = _FakeWebSocketServer()
    engine.config.default_provider = "fixture"
    engine.providers = {"fixture": SimpleNamespace(is_ready=lambda: True)}
    engine._get_provider_kind = lambda name: "fixture"
    capability = SimpleNamespace(
        ready=bool(modules_ready), inventory_available=modules_ready is not None,
        missing_required_modules=(), non_running_required_modules=(),
        running_timing_modules=("res_timing_timerfd",) if modules_ready else (),
        reason=None if modules_ready else "Inventory or timing unavailable",
    )
    refresh = AsyncMock()
    engine.ari_client = SimpleNamespace(
        is_connected=True, asterisk_version="22.10.1",
        module_inventory=None, refresh_module_inventory=refresh,
        websocket_media_module_capability=lambda: capability,
    )
    response = await engine._ready_handler(None)
    assert response.status == (200 if modules_ready else 503)
    assert json.loads(response.text)["transport_ok"] is bool(modules_ready)
    refresh.assert_awaited_once_with(timeout_sec=3.0)
    assert engine._selected_transport_health()["modules_ready"] is bool(modules_ready)


@pytest.mark.asyncio
async def test_websocket_module_refresh_cache_never_skips_forced_admission():
    import time

    engine = _bare_engine()
    refresh = AsyncMock()
    engine.ari_client = SimpleNamespace(
        module_inventory=SimpleNamespace(captured_at_monotonic=time.monotonic()),
        refresh_module_inventory=refresh,
    )
    await engine._refresh_websocket_modules()
    refresh.assert_not_awaited()
    await engine._refresh_websocket_modules(force=True)
    refresh.assert_awaited_once_with(timeout_sec=3.0)
    refresh.reset_mock()
    engine.config.audio_transport = "audiosocket"
    await engine._refresh_websocket_modules(force=True)
    refresh.assert_not_awaited()


@pytest.mark.parametrize("wire_codec", ["ulaw", "alaw", "slin", "slin16"])
def test_websocket_provider_input_mode_matches_canonical_ingress(wire_codec):
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(audio_transport="websocket")
    session = CallSession(call_id="caller-1", caller_channel_id="caller-1")
    session.transport_profile = SimpleNamespace(
        wire_encoding=wire_codec,
        wire_sample_rate=16000 if wire_codec == "slin16" else 8000,
    )
    assert engine._provider_input_mode_for_transport(session) == "pcm16_16k"


@pytest.mark.asyncio
async def test_terminal_snapshot_includes_remote_websocket_queue():
    engine = _bare_engine()
    engine.websocket_server = _FakeWebSocketServer()
    engine._provider_stream_queues = {}
    engine._provider_coalesce_buf = {}
    engine.streaming_playback_manager = SimpleNamespace(
        active_streams={}, jitter_buffers={}, frame_remainders={}
    )

    snapshot = await engine._call_audio_drain_snapshot("caller-1")

    assert snapshot["pending_transport_output"] == 1
    assert snapshot["pending_transport_frames"] == 7


@pytest.mark.asyncio
async def test_reload_rejects_transport_change_before_mutating_running_config(monkeypatch):
    engine = Engine.__new__(Engine)
    running = {"audio_transport": "audiosocket", "tools": {"enabled": True}}
    saved = {"audio_transport": "websocket", "tools": {"enabled": False}}
    engine.config = running
    engine._tool_reload_lock = asyncio.Lock()
    engine._restart_required_after_reload = False
    engine._is_request_authorized = lambda request: True
    monkeypatch.setattr("src.config.load_config", lambda: saved)

    response = await engine._reload_handler(object())

    assert response.status == 409
    assert json.loads(response.text)["restart_required"] is True
    assert engine.config is running


@pytest.mark.asyncio
async def test_websocket_agent_audio_done_arms_correlated_boundary_gating():
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()
    call_id = "call-boundary"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    await engine.session_store.upsert_call(session)
    manager = SimpleNamespace(
        continuous_stream=True,
        mark_segment_boundary=AsyncMock(),
        end_segment_gating=AsyncMock(),
        record_provider_bytes=lambda *args: None,
    )
    engine.streaming_playback_manager = manager
    engine.config = SimpleNamespace(
        audio_transport="websocket",
        streaming=SimpleNamespace(coalesce_enabled=False),
    )
    engine.conversation_coordinator = SimpleNamespace(
        on_tts_end=AsyncMock(side_effect=AssertionError("must wait for boundary"))
    )
    engine.provider_kinds = {}
    engine._provider_stream_queues = {call_id: asyncio.Queue()}
    engine._provider_stream_formats = {}
    engine._provider_segment_start_ts = {}
    engine._provider_bytes = {}
    engine._enqueued_bytes = {}
    engine._provider_chunk_seq = {}
    engine._downstream_file_audio_events = {}
    engine._downstream_file_streaming_logged = set()
    engine._provider_coalesce_buf = {}
    engine._segment_tts_active = set()
    engine._note_provider_output_end = AsyncMock()

    await engine.on_provider_event({"type": "AgentAudioDone", "call_id": call_id})

    manager.mark_segment_boundary.assert_awaited_once_with(call_id)
    manager.end_segment_gating.assert_awaited_once_with(
        call_id, notify_no_input=False
    )
    engine.conversation_coordinator.on_tts_end.assert_not_awaited()


@pytest.mark.asyncio
async def test_hangup_while_waiting_for_media_ready_cannot_resurrect_session():
    engine = _bare_engine()
    session = CallSession(
        call_id="caller-1",
        caller_channel_id="caller-1",
        media_transport_kind="websocket",
        media_channel_id="media-1",
        media_connection_state="bridge_attached",
        bridge_id="bridge-1",
    )
    engine.websocket_media_channels["media-1"] = "caller-1"
    await engine._save_session(session)

    async def wait_ready(call_id, timeout=None):
        session.cleanup_in_progress = True
        await engine.session_store.remove_call(call_id)
        return SimpleNamespace(
            channel_id="media-1",
            connection_id="conn-1",
            codec="ulaw",
            sample_rate=8000,
            ptime=20,
            optimal_frame_size=160,
        )

    engine.websocket_server = SimpleNamespace(wait_ready=wait_ready)

    assert await engine._await_websocket_media_ready(session) is False
    assert await engine.session_store.get_by_call_id("caller-1") is None
