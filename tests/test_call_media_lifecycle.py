import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.audio.transports.base import (
    CallMediaRequest,
    CompletionQuality,
    TransportCapabilities,
)
from src.audio.transports.lifecycle import CallMediaLifecycle
from src.audio.transports.legacy import (
    AudioSocketRuntime,
    ExternalMediaRuntime,
    WebSocketRuntime,
)
from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.engine import Engine


class _Runtime:
    kind = "test"
    capabilities = TransportCapabilities(CompletionQuality.QUIET_TAIL)

    def __init__(self, request, *, attach_results=(True,), wait_result=True):
        self.request = request
        self.attach_results = iter(attach_results)
        self.wait_result = wait_result
        self.calls = []
        self.closed = []

    async def prepare_call(self, session):
        self.calls.append(("prepare", session.call_id))
        return self.request

    async def create_call(self, owner, session, request):
        self.calls.append(("create", request))
        return "media-1"

    async def attach_call(self, owner, session, channel_id):
        self.calls.append(("attach", channel_id))
        return next(self.attach_results)

    async def wait_call_ready(self, owner, session, request):
        self.calls.append(("wait", request))
        return self.wait_result

    async def finalize_call_ready(self, owner, session, request, binding):
        self.calls.append(("finalize", binding))
        return True

    async def close_call(self, call_id, *, connection_id=None):
        self.closed.append(call_id)

    def is_aux_channel(self, channel, *, known_channel_ids):
        return False

    async def handle_aux_channel(self, channel_id, channel):
        return None


def _request(**overrides):
    values = {
        "kind": "test",
        "call_id": "call-1",
        "codec": "ulaw",
        "sample_rate": 8000,
        "operation": "external_media",
        "ari_params": {},
    }
    values.update(overrides)
    return CallMediaRequest(**values)


@pytest.mark.asyncio
async def test_immediate_lifecycle_retries_attach_before_readiness_and_finalize():
    runtime = _Runtime(_request(), attach_results=(False, True), wait_result="binding")
    lifecycle = CallMediaLifecycle(
        runtime,
        attach_attempts=2,
        attach_retry_seconds=0,
    )
    session = SimpleNamespace(call_id="call-1", bridge_id="bridge-1")

    result = await lifecycle.setup(SimpleNamespace(), session)

    assert result.ready is True
    assert result.channel_id == "media-1"
    assert [call[0] for call in runtime.calls] == [
        "prepare",
        "create",
        "attach",
        "attach",
        "wait",
        "finalize",
    ]


@pytest.mark.asyncio
async def test_audiosocket_style_aux_event_defers_attach_and_readiness():
    runtime = _Runtime(
        _request(attachment="aux_event", readiness="aux_event")
    )
    lifecycle = CallMediaLifecycle(runtime)
    session = SimpleNamespace(call_id="call-1", bridge_id="bridge-1")

    result = await lifecycle.setup(SimpleNamespace(), session)

    assert result.created is True
    assert result.ready is False
    assert result.failure_reason is None
    assert [call[0] for call in runtime.calls] == ["prepare", "create"]


@pytest.mark.asyncio
async def test_failed_create_releases_resources_prepared_before_ari():
    class MissingChannelRuntime(_Runtime):
        async def create_call(self, owner, session, request):
            self.calls.append(("create", request))
            return None

    runtime = MissingChannelRuntime(_request())

    result = await CallMediaLifecycle(runtime).setup(
        SimpleNamespace(),
        SimpleNamespace(call_id="call-1", bridge_id="bridge-1"),
    )

    assert result.failure_reason == "test-media-start-failed"
    assert runtime.closed == ["call-1"]


@pytest.mark.asyncio
async def test_setup_timeout_cancels_work_and_releases_transport_registration():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class SlowRuntime(_Runtime):
        async def create_call(self, owner, session, request):
            started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

    runtime = SlowRuntime(
        _request(
            channel_id="assigned-before-http",
            setup_failure_reason="test-media-setup-failed",
            force_cleanup_on_failure=True,
        )
    )
    lifecycle = CallMediaLifecycle(runtime, setup_timeout_seconds=0.01)
    session = SimpleNamespace(call_id="call-1", bridge_id="bridge-1")

    hangup = AsyncMock()
    result = await lifecycle.setup(SimpleNamespace(ari_client=SimpleNamespace(hangup_channel=hangup)), session)
    hangup.assert_awaited_once_with("assigned-before-http")

    assert started.is_set()
    assert cancelled.is_set()
    assert result.failure_reason == "test-media-setup-failed"
    assert result.force_cleanup is True
    assert result.ready is False
    assert runtime.closed == ["call-1"]
    assert all(call[0] != "finalize" for call in runtime.calls)


@pytest.mark.asyncio
async def test_setup_cancellation_releases_transport_state_and_propagates():
    started = asyncio.Event()

    class SlowRuntime(_Runtime):
        async def create_call(self, owner, session, request):
            started.set()
            await asyncio.Future()

    runtime = SlowRuntime(_request(channel_id="assigned-before-http"))
    lifecycle = CallMediaLifecycle(runtime, setup_timeout_seconds=None)
    session = SimpleNamespace(call_id="call-1", bridge_id="bridge-1")
    hangup = AsyncMock()
    task = asyncio.create_task(lifecycle.setup(SimpleNamespace(ari_client=SimpleNamespace(hangup_channel=hangup)), session))
    await started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runtime.closed == ["call-1"]
    hangup.assert_awaited_once_with("assigned-before-http")
    assert all(call[0] != "finalize" for call in runtime.calls)


@pytest.mark.asyncio
async def test_externalmedia_prepare_failure_preserves_legacy_caller_ownership():
    class FailedRtpRuntime(_Runtime):
        kind = "externalmedia"
        force_cleanup_on_prepare_failure = False

        async def prepare_call(self, session):
            raise RuntimeError("no RTP port")

    runtime = FailedRtpRuntime(_request(kind="externalmedia"))

    result = await CallMediaLifecycle(runtime).setup(
        SimpleNamespace(),
        SimpleNamespace(call_id="call-1", bridge_id="bridge-1"),
    )

    assert result.failure_reason == "externalmedia-media-start-failed"
    assert result.force_cleanup is False


@pytest.mark.asyncio
async def test_initial_inactive_session_is_rejected_before_transport_prepare():
    runtime = _Runtime(_request())
    owner = SimpleNamespace(_media_session_is_active=AsyncMock(return_value=False))

    result = await CallMediaLifecycle(runtime).setup(
        owner,
        SimpleNamespace(call_id="call-1", bridge_id="bridge-1"),
    )

    assert result.failure_reason == "test-media-start-failed"
    assert runtime.calls == []
    assert runtime.closed == []


@pytest.mark.asyncio
async def test_engine_failure_path_does_not_save_connection_audio_on_stale_session():
    request = _request(kind="externalmedia", force_cleanup_on_failure=False)
    result = SimpleNamespace(
        request=request,
        channel_id=None,
        failure_reason="external-media-start-failed",
        force_cleanup=False,
        ready=False,
    )
    engine = Engine.__new__(Engine)
    engine.call_media_lifecycle = SimpleNamespace(
        runtime=SimpleNamespace(kind="externalmedia"),
        setup=AsyncMock(return_value=result),
    )
    engine._media_session_is_active = AsyncMock(return_value=False)
    engine._stop_connection_audio = AsyncMock()
    engine._cleanup_call = AsyncMock()
    session = CallSession(call_id="call-1", caller_channel_id="call-1")
    session.connection_audio_playback_id = "connection-audio-1"

    await engine._setup_selected_call_media(session)

    engine._stop_connection_audio.assert_not_awaited()
    engine._cleanup_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_externalmedia_attach_cleanup_race_hangs_up_late_aux_leg():
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()
    engine._save_session = AsyncMock()
    session = CallSession(call_id="call-1", caller_channel_id="call-1")
    session.bridge_id = "bridge-1"
    session.pending_external_media_id = "media-1"
    await engine.session_store.upsert_call(session)

    async def add_channel_to_bridge(bridge_id, channel_id):
        assert (bridge_id, channel_id) == ("bridge-1", "media-1")
        session.cleanup_in_progress = True
        await engine.session_store.remove_call(session.call_id)
        return True

    engine.ari_client = SimpleNamespace(
        add_channel_to_bridge=add_channel_to_bridge,
        hangup_channel=AsyncMock(return_value=True),
    )

    assert await engine._attach_external_media_channel_direct(session, "media-1") is False
    engine.ari_client.hangup_channel.assert_awaited_once_with("media-1")
    engine._save_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_audiosocket_aux_attach_cannot_resurrect_cleaned_session():
    engine = Engine.__new__(Engine)
    engine.session_store = SessionStore()
    engine._save_session = AsyncMock(side_effect=engine.session_store.upsert_call)
    engine._ensure_provider_session_started = AsyncMock()
    engine._stop_connection_audio = AsyncMock()
    engine.pending_audiosocket_channels = {"as-media": "call-1"}
    engine.uuidext_to_channel = {"uuid-1": "call-1"}
    engine.audiosocket_channels = {}
    engine.bridges = {}
    session = CallSession(call_id="call-1", caller_channel_id="call-1")
    session.bridge_id = "bridge-1"
    session.audiosocket_uuid = "uuid-1"
    await engine.session_store.upsert_call(session)

    async def add_channel_to_bridge(bridge_id, channel_id):
        session.cleanup_in_progress = True
        await engine.session_store.remove_call(session.call_id)
        return True

    engine.ari_client = SimpleNamespace(
        add_channel_to_bridge=add_channel_to_bridge,
        hangup_channel=AsyncMock(return_value=True),
    )

    await engine._handle_audiosocket_channel_stasis_start(
        "as-media", {"id": "as-media", "name": "AudioSocket/test"}
    )

    engine.ari_client.hangup_channel.assert_awaited_once_with("as-media")
    engine._ensure_provider_session_started.assert_not_awaited()
    assert await engine.session_store.get_by_call_id("call-1") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("neutral_id", "external_id", "audiosocket_id", "expected"),
    [
        ("ws-media", None, None, ["ws-media"]),
        ("rtp-media", "rtp-media", None, ["rtp-media"]),
        ("as-media", None, "as-media", ["as-media"]),
    ],
)
async def test_transfer_detach_uses_neutral_media_id_without_legacy_duplicates(
    neutral_id, external_id, audiosocket_id, expected
):
    engine = Engine.__new__(Engine)
    engine.ari_client = SimpleNamespace(
        remove_channel_from_bridge=AsyncMock(return_value=True)
    )
    session = CallSession(call_id="call-1", caller_channel_id="call-1")
    session.bridge_id = "bridge-1"
    session.media_channel_id = neutral_id
    session.external_media_id = external_id
    session.audiosocket_channel_id = audiosocket_id

    await engine._remove_primary_media_from_bridge(
        session, reason="test-transfer"
    )

    assert [
        call.args[1]
        for call in engine.ari_client.remove_channel_from_bridge.await_args_list
    ] == expected


@pytest.mark.asyncio
async def test_externalmedia_runtime_allocates_once_and_uses_prepared_request(
    monkeypatch,
):
    class Server:
        def __init__(self, **kwargs):
            self.sample_rate = kwargs["sample_rate"]
            self.allocate_calls = []

        async def allocate_session(self, call_id):
            self.allocate_calls.append(call_id)
            return 18084

        async def cleanup_session(self, call_id):
            return None

    monkeypatch.setattr("src.audio.transports.legacy.RTPServer", Server)
    config = SimpleNamespace(
        asterisk=SimpleNamespace(app_name="aava"),
        external_media=SimpleNamespace(
            rtp_host="0.0.0.0",
            advertise_host="10.0.0.4",
            rtp_port=18080,
            codec="ulaw",
            format="slin16",
            sample_rate=16000,
            direction="both",
            lock_remote_endpoint=True,
        ),
    )
    runtime = ExternalMediaRuntime(
        config,
        on_pcm=AsyncMock(),
        on_aux=AsyncMock(),
        port_range=(18080, 18090),
        allowed_remote_hosts=["127.0.0.1"],
    )
    engine = Engine.__new__(Engine)
    engine.config = config
    engine.rtp_server = runtime.server
    engine.session_store = SessionStore()
    engine.conversation_coordinator = None
    engine._save_session = AsyncMock(side_effect=engine.session_store.upsert_call)
    engine.ari_client = SimpleNamespace(
        create_external_media_channel=AsyncMock(return_value={"id": "rtp-media"}),
        add_channel_to_bridge=AsyncMock(return_value=True),
        hangup_channel=AsyncMock(return_value=True),
    )
    session = CallSession(call_id="call-1", caller_channel_id="call-1")
    session.bridge_id = "bridge-1"
    await engine.session_store.upsert_call(session)

    result = await CallMediaLifecycle(runtime).setup(engine, session)

    assert result.ready is True
    assert runtime.server.allocate_calls == ["call-1"]
    engine.ari_client.create_external_media_channel.assert_awaited_once_with(
        app="aava",
        external_host="10.0.0.4:18084",
        format="ulaw",
        direction="both",
        encapsulation="rtp",
    )
    assert session.external_media_id == "rtp-media"
    assert session.pending_external_media_id is None
    assert session.media_connection_state == "bridge_attached"


@pytest.mark.asyncio
async def test_audiosocket_runtime_keeps_readiness_deferred_and_preregisters_uuid(
    monkeypatch,
):
    class Server:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("src.audio.transports.legacy.AudioSocketServer", Server)
    config = SimpleNamespace(
        asterisk=SimpleNamespace(app_name="aava"),
        audiosocket=SimpleNamespace(
            host="127.0.0.1",
            advertise_host=None,
            port=9092,
            format="slin",
        ),
    )
    runtime = AudioSocketRuntime(
        config,
        on_uuid=AsyncMock(),
        on_audio=AsyncMock(),
        on_disconnect=AsyncMock(),
        on_dtmf=AsyncMock(),
        on_aux=AsyncMock(),
    )
    engine = Engine.__new__(Engine)
    engine.config = config
    engine.pending_audiosocket_channels = {}
    engine.uuidext_to_channel = {}
    engine.session_store = SessionStore()
    engine.conversation_coordinator = None
    engine._save_session = AsyncMock(side_effect=engine.session_store.upsert_call)

    async def send_command(method, resource, *, data, params):
        audio_uuid = data["variables"]["AUDIOSOCKET_UUID"]
        assert engine.uuidext_to_channel[audio_uuid] == "call-1"
        return {"id": "audiosocket-media"}

    engine.ari_client = SimpleNamespace(send_command=send_command)
    session = CallSession(call_id="call-1", caller_channel_id="call-1")
    session.bridge_id = "bridge-1"
    await engine.session_store.upsert_call(session)

    result = await CallMediaLifecycle(runtime).setup(engine, session)

    assert result.created is True
    assert result.ready is False
    assert result.failure_reason is None
    assert engine.pending_audiosocket_channels == {"audiosocket-media": "call-1"}
    assert session.audiosocket_uuid
    assert session.media_connection_state == "pending"


@pytest.mark.asyncio
async def test_websocket_runtime_registers_once_before_ari_then_waits_for_protocol(
    monkeypatch,
):
    class Server:
        def __init__(self, config, **kwargs):
            self.registered = []
            self.ready_timeouts = []
            self.unregistered = []

        def register_call(self, call_id, channel_id, codec, *, control_format="json"):
            self.registered.append((call_id, channel_id, codec))
            return "nonce-1"

        async def wait_ready(self, call_id, timeout):
            self.ready_timeouts.append(timeout)
            return SimpleNamespace(
                channel_id=self.registered[0][1],
                connection_id="conn-1",
                codec="ulaw",
                sample_rate=8000,
                ptime=20,
                optimal_frame_size=160,
            )

        async def unregister_call(self, call_id):
            self.unregistered.append(call_id)

        def health(self):
            return {"listening": True}

    monkeypatch.setattr(
        "src.audio.transports.legacy.WebSocketMediaServer", Server
    )
    config = SimpleNamespace(
        asterisk=SimpleNamespace(app_name="aava"),
        websocket_media=SimpleNamespace(
            connection_name="aava_media",
            fallback_format="ulaw",
            media_start_timeout_ms=12500,
        ),
    )
    runtime = WebSocketRuntime(
        config,
        on_audio=AsyncMock(),
        on_disconnect=AsyncMock(),
        on_dtmf=AsyncMock(),
        on_aux=AsyncMock(),
    )
    engine = Engine.__new__(Engine)
    engine.config = config
    engine.websocket_server = runtime.server
    engine.pending_websocket_channels = {}
    engine.websocket_media_channels = {}
    engine._seen_aux_channels = set()
    engine.session_store = SessionStore()
    engine.conversation_coordinator = None
    engine._save_session = AsyncMock(side_effect=engine.session_store.upsert_call)

    async def create_external_media_channel(**kwargs):
        media_id = kwargs["channel_id"]
        assert engine.pending_websocket_channels[media_id] == "call-1"
        assert len(runtime.server.registered) == 1
        return {"id": media_id}

    engine.ari_client = SimpleNamespace(
        create_external_media_channel=create_external_media_channel,
        add_channel_to_bridge=AsyncMock(return_value=True),
        hangup_channel=AsyncMock(return_value=True),
    )
    session = CallSession(call_id="call-1", caller_channel_id="call-1")
    session.bridge_id = "bridge-1"
    await engine.session_store.upsert_call(session)

    result = await CallMediaLifecycle(runtime).setup(engine, session)

    assert result.ready is True
    assert len(runtime.server.registered) == 1
    assert runtime.server.ready_timeouts == [12.5]
    assert session.media_connection_state == "ready"
    assert session.media_connection_id == "conn-1"
    assert engine.pending_websocket_channels == {}


@pytest.mark.asyncio
async def test_websocket_prepare_is_rolled_back_when_cleanup_wins_before_create(
    monkeypatch,
):
    class Server:
        def __init__(self, config, **kwargs):
            self.registered = []
            self.unregistered = []

        def register_call(self, call_id, channel_id, codec, *, control_format="json"):
            self.registered.append((call_id, channel_id, codec))
            return "nonce-1"

        async def unregister_call(self, call_id):
            self.unregistered.append(call_id)

    monkeypatch.setattr(
        "src.audio.transports.legacy.WebSocketMediaServer", Server
    )
    config = SimpleNamespace(
        asterisk=SimpleNamespace(app_name="aava"),
        websocket_media=SimpleNamespace(
            connection_name="aava_media",
            fallback_format="ulaw",
        ),
    )
    runtime = WebSocketRuntime(
        config,
        on_audio=AsyncMock(),
        on_disconnect=AsyncMock(),
        on_dtmf=AsyncMock(),
        on_aux=AsyncMock(),
    )
    engine = Engine.__new__(Engine)
    engine.config = config
    engine.websocket_server = runtime.server
    # Lifecycle's initial guard passes; its post-prepare guard then observes
    # that caller cleanup removed the session in the intervening await boundary.
    engine._media_session_is_active = AsyncMock(side_effect=[True, False])
    session = CallSession(call_id="call-1", caller_channel_id="call-1")

    result = await CallMediaLifecycle(runtime).setup(engine, session)

    assert result.failure_reason == "websocket-media-start-failed"
    assert len(runtime.server.registered) == 1
    assert runtime.server.unregistered == ["call-1"]


@pytest.mark.asyncio
async def test_selected_auxiliary_stasis_is_classified_before_action_args():
    runtime = _Runtime(_request())
    runtime.is_aux_channel = Mock(return_value=True)
    runtime.handle_aux_channel = AsyncMock()
    engine = Engine.__new__(Engine)
    engine.call_media_lifecycle = CallMediaLifecycle(runtime)
    engine._pre_stasis_channels = {"media-1"}
    engine._seen_aux_channels = set()
    engine.pending_audiosocket_channels = {}
    engine.pending_websocket_channels = {"media-1": "call-1"}
    engine.websocket_media_channels = {}
    engine._handle_outbound_stasis = AsyncMock()

    channel = {"id": "media-1", "name": "WebSocket/aava"}
    await engine._handle_stasis_start(
        {"channel": channel, "args": ["outbound", "should-not-route"]}
    )

    runtime.handle_aux_channel.assert_awaited_once_with("media-1", channel)
    engine._handle_outbound_stasis.assert_not_awaited()
    assert "media-1" in engine._seen_aux_channels


@pytest.mark.asyncio
async def test_destroyed_before_stasis_marks_selected_leg_as_auxiliary():
    runtime = _Runtime(_request())
    runtime.is_aux_channel = Mock(return_value=True)
    engine = Engine.__new__(Engine)
    engine.call_media_lifecycle = CallMediaLifecycle(runtime)
    engine._pre_stasis_channels = {"media-1"}
    engine._seen_aux_channels = set()
    engine.pending_audiosocket_channels = {}
    engine.pending_websocket_channels = {}
    engine.websocket_media_channels = {}
    engine._handle_outbound_channel_destroyed = AsyncMock()

    async def cleanup(channel_id):
        assert channel_id in engine._seen_aux_channels

    engine._cleanup_call = AsyncMock(side_effect=cleanup)

    await engine._handle_channel_destroyed(
        {"channel": {"id": "media-1", "name": "AudioSocket/127.0.0.1"}}
    )

    engine._cleanup_call.assert_awaited_once_with("media-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_raises", [False, True])
async def test_common_pcm_bus_captures_raw_caller_audio_without_blocking(
    capture_raises,
):
    engine = Engine.__new__(Engine)
    session = CallSession(call_id="call-1", caller_channel_id="call-1")
    engine.session_store = SimpleNamespace(
        get_by_call_id=AsyncMock(return_value=session)
    )
    capture = Mock()
    if capture_raises:
        capture.side_effect = RuntimeError("capture unavailable")
    engine.audio_capture = SimpleNamespace(append_pcm16=capture)
    engine._save_session = AsyncMock()
    engine._observe_no_input_audio = AsyncMock()
    engine._consume_attended_transfer_screening_audio = Mock(return_value=True)

    payload = b"\x01\x00" * 160
    await engine._on_transport_pcm(
        session.call_id,
        payload,
        8000,
        source="test-transport",
    )

    capture.assert_called_once_with(
        "call-1", "caller_inbound", payload, 8000
    )
    engine._observe_no_input_audio.assert_awaited_once_with(
        session,
        payload,
        8000,
        source="test-transport",
    )


@pytest.mark.asyncio
async def test_audiosocket_runtime_falls_back_to_slin_for_non_slin_format(monkeypatch):
    """`audiosocket.format: ulaw` deployments always originated c(slin); keep that."""

    class Server:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("src.audio.transports.legacy.AudioSocketServer", Server)
    config = SimpleNamespace(
        asterisk=SimpleNamespace(app_name="aava"),
        audiosocket=SimpleNamespace(
            host="127.0.0.1", advertise_host=None, port=9092, format="ulaw"
        ),
    )
    runtime = AudioSocketRuntime(
        config,
        on_uuid=AsyncMock(),
        on_audio=AsyncMock(),
        on_disconnect=AsyncMock(),
        on_dtmf=AsyncMock(),
        on_aux=AsyncMock(),
    )
    session = CallSession(call_id="call-1", caller_channel_id="call-1")

    request = await runtime.prepare_call(session)

    assert request.codec == "slin"
    assert request.ari_params["endpoint"].endswith("/c(slin)")
