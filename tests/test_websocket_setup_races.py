"""Regression coverage for the greeting-only attachment race seen on the development PBX."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.engine import Engine
from src.audio.transports.legacy import WebSocketRuntime
from src.audio.transports.lifecycle import CallMediaLifecycle


@pytest.fixture(params=["json", "plain"])
def rig(request):
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(
        audio_transport="websocket",
        websocket_media=SimpleNamespace(media_start_timeout_ms=100),
    )
    engine.session_store = SessionStore()
    engine.conversation_coordinator = None
    engine.websocket_media_channels = {"media": "caller"}
    engine.pending_websocket_channels = {"media": "caller"}
    engine._websocket_ingress_resample_state = {}
    engine._on_transport_pcm = AsyncMock()
    session = CallSession(
        call_id="caller", caller_channel_id="caller", bridge_id="bridge",
        media_channel_id="media", media_connection_state="pending",
    )
    binding = SimpleNamespace(
        channel_id="media", connection_id="connection", codec="slin16",
        sample_rate=16000, ptime=20, optimal_frame_size=640,
        control_format=request.param,
    )
    engine.websocket_server = SimpleNamespace(
        wait_ready=AsyncMock(return_value=binding),
        get_binding=lambda call_id: binding, health=lambda: {"metrics": {"input_drops": 0}},
    )
    engine.ari_client = SimpleNamespace(
        add_channel_to_bridge=AsyncMock(return_value=True), hangup_channel=AsyncMock(),
    )
    return engine, session


@pytest.mark.asyncio
@pytest.mark.parametrize("aux_first", [False, True])
async def test_duplicate_attach_and_readiness_never_downgrade(rig, aux_first):
    engine, session = rig
    await engine._save_session(session)
    entered, release = asyncio.Event(), asyncio.Event()

    async def add(*args):
        entered.set()
        await release.wait()
        return True

    engine.ari_client.add_channel_to_bridge.side_effect = add

    async def main_setup():
        assert await engine._attach_websocket_media_channel(session, "media")
        assert await engine._await_websocket_media_ready(session)

    async def aux():
        await engine._handle_websocket_media_stasis_start("media", {})

    async with asyncio.timeout(1):
        first = asyncio.create_task(aux() if aux_first else main_setup())
        await entered.wait()
        second = asyncio.create_task(main_setup() if aux_first else aux())
        await asyncio.sleep(0)
        assert engine.ari_client.add_channel_to_bridge.await_count == 1
        release.set()
        await asyncio.gather(first, second)
        # Explicitly exercise another event arriving after finalization.
        await aux()
    assert session.media_connection_state == "ready"
    engine.ari_client.add_channel_to_bridge.assert_awaited_once()
    await engine._websocket_handle_audio("caller", b"\x00\x20" * 320)
    engine._on_transport_pcm.assert_awaited_once()
    assert session.websocket_input_rejections == {}
    assert not engine._websocket_setup_locks


@pytest.mark.asyncio
async def test_auxiliary_failure_leaves_main_retry_owner(rig):
    engine, session = rig
    await engine._save_session(session)
    engine.ari_client.add_channel_to_bridge.side_effect = [False, True]
    await engine._handle_websocket_media_stasis_start("media", {})
    assert session.media_connection_state == "pending"
    assert await engine._attach_websocket_media_channel(session, "media")
    assert await engine._await_websocket_media_ready(session)
    assert session.media_connection_state == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["closed", "failed", "cleanup", "removed", "replaced", "bridge", "channel", "mapping"])
@pytest.mark.parametrize("phase", ["attach", "ready"])
async def test_setup_revalidates_identity_and_terminal_state_after_await(rig, mutation, phase):
    engine, session = rig
    await engine._save_session(session)
    entered, release = asyncio.Event(), asyncio.Event()
    binding = engine.websocket_server.get_binding("caller")

    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        return True if phase == "attach" else binding

    if phase == "attach":
        engine.ari_client.add_channel_to_bridge.side_effect = delayed
        operation = engine._attach_websocket_media_channel(session, "media")
    else:
        session.media_connection_state = "bridge_attached"
        engine.websocket_server.wait_ready.side_effect = delayed
        operation = engine._await_websocket_media_ready(session)

    async with asyncio.timeout(1):
        task = asyncio.create_task(operation)
        await entered.wait()
        if mutation in {"closed", "failed"}:
            session.media_connection_state = mutation
        elif mutation == "cleanup":
            session.cleanup_in_progress = True
        elif mutation == "removed":
            await engine.session_store.remove_call("caller")
        elif mutation == "replaced":
            await engine.session_store.upsert_call(CallSession(call_id="caller", caller_channel_id="replacement"))
        elif mutation == "bridge":
            session.bridge_id = "replacement-bridge"
        elif mutation == "channel":
            session.media_channel_id = "replacement-media"
        else:
            engine.websocket_media_channels["media"] = "different-caller"
        old_state = session.media_connection_state
        release.set()
        assert await task is False
    assert session.media_connection_state == old_state
    if mutation == "removed":
        assert await engine.session_store.get_by_call_id("caller") is None
    if mutation == "replaced":
        assert await engine.session_store.get_by_call_id("caller") is not session
    engine.ari_client.hangup_channel.assert_not_awaited()
    assert not engine._websocket_setup_locks


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_split_setup_lock(rig):
    engine, session = rig
    await engine._save_session(session)
    entered, release = asyncio.Event(), asyncio.Event()

    async def add(*args):
        entered.set()
        await release.wait()
        return True

    engine.ari_client.add_channel_to_bridge.side_effect = add
    async with asyncio.timeout(1):
        owner = asyncio.create_task(engine._attach_websocket_media_channel(session, "media"))
        await entered.wait()
        waiter = asyncio.create_task(engine._attach_websocket_media_channel(session, "media"))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        late = asyncio.create_task(engine._attach_websocket_media_channel(session, "media"))
        await asyncio.sleep(0)
        engine.ari_client.add_channel_to_bridge.assert_awaited_once()
        release.set()
        assert all(await asyncio.gather(owner, late))


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, TimeoutError("MEDIA_START timeout")])
async def test_ready_timeout_cannot_be_revived_by_auxiliary_event(rig, result):
    engine, session = rig
    session.media_connection_state = "bridge_attached"
    await engine._save_session(session)
    if isinstance(result, Exception):
        engine.websocket_server.wait_ready.side_effect = result
    else:
        engine.websocket_server.wait_ready.return_value = result
    assert await engine._await_websocket_media_ready(session) is False
    await engine._handle_websocket_media_stasis_start("media", {})
    assert session.media_connection_state == "failed"
    engine.ari_client.add_channel_to_bridge.assert_not_awaited()


@pytest.mark.asyncio
async def test_different_calls_have_independent_setup_locks(rig):
    engine, session = rig
    await engine._save_session(session)
    other = CallSession(call_id="other", caller_channel_id="other", bridge_id="other-bridge", media_channel_id="other-media", media_connection_state="pending")
    engine.websocket_media_channels["other-media"] = "other"
    await engine._save_session(other)
    entered, release = asyncio.Event(), asyncio.Event()

    async def add(bridge_id, media_id):
        if media_id == "media":
            entered.set()
            await release.wait()
        return True

    engine.ari_client.add_channel_to_bridge.side_effect = add
    async with asyncio.timeout(1):
        pending = asyncio.create_task(engine._attach_websocket_media_channel(session, "media"))
        await entered.wait()
        assert await engine._attach_websocket_media_channel(other, "other-media")
        release.set()
        assert await pending


@pytest.mark.asyncio
async def test_engine_rejections_are_separate_from_queue_overflow_and_call_scoped(rig):
    engine, session = rig
    await engine._save_session(session)
    await engine._websocket_handle_audio("caller", b"\0" * 640)
    await engine._websocket_handle_audio("caller", b"\0" * 640)
    assert session.websocket_input_rejections == {"not_ready": 2}
    session.media_connection_state = "ready"
    engine.websocket_media_channels["media"] = "other"
    await engine._websocket_handle_audio("caller", b"\0" * 640)
    engine._websocket_module_health = lambda: {}
    health = engine._selected_transport_health()
    assert health["metrics"]["input_drops"] == 0
    assert health["engine_input_rejections"] == {"not_ready": 2, "ownership_mismatch": 1}
    health["engine_input_rejections"].clear()
    assert engine._websocket_input_rejections["not_ready"] == 2
    assert CallSession(call_id="other", caller_channel_id="other").websocket_input_rejections == {}
    engine._on_transport_pcm.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["removed", "replaced", "cleanup"])
async def test_guarded_session_save_rechecks_under_store_lock(rig, mutation):
    engine, session = rig
    await engine._save_session(session)
    store = engine.session_store
    async with store._lock:
        saving = asyncio.create_task(engine._save_session(session, require_current=True))
        await asyncio.sleep(0)
        if mutation == "removed":
            store._sessions_by_call_id.pop("caller")
            store._sessions_by_channel_id.clear()
        elif mutation == "replaced":
            store._sessions_by_call_id["caller"] = CallSession(call_id="caller", caller_channel_id="replacement")
        else:
            session.cleanup_in_progress = True
    await asyncio.wait_for(saving, 1)
    current = await store.get_by_call_id("caller")
    if mutation == "removed":
        assert current is None
    elif mutation == "replaced":
        assert current is not session
    else:
        assert await store.upsert_call(session, require_current=True) is False


@pytest.mark.asyncio
async def test_runtime_finalization_rejects_close_after_readiness(rig):
    engine, session = rig
    await engine._save_session(session)
    session.media_connection_state = "closed"
    runtime = WebSocketRuntime.__new__(WebSocketRuntime)
    assert await runtime.finalize_call_ready(engine, session, SimpleNamespace(channel_id="media"), True) is False
    assert session.status == "initializing"
    assert engine.pending_websocket_channels["media"] == "caller"


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["inactive_session", "missing_binding", "decode_or_ingress_error"])
async def test_additional_ingress_rejections_are_visible(rig, reason):
    engine, session = rig
    session.media_connection_state = "ready"
    await engine._save_session(session)
    if reason == "inactive_session":
        session.cleanup_completed = True
    elif reason == "missing_binding":
        engine.websocket_server.get_binding = lambda call_id: None
    else:
        engine._on_transport_pcm.side_effect = ValueError("fixture failure")
    await engine._websocket_handle_audio("caller", b"\0" * 640)
    assert session.websocket_input_rejections == {reason: 1}
    assert engine._websocket_input_rejections == {reason: 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("aux_first", [False, True])
async def test_actual_selected_lifecycle_admits_provider_only_after_shared_setup(rig, aux_first):
    engine, session = rig
    binding = engine.websocket_server.get_binding("caller")
    engine.config.asterisk = SimpleNamespace(app_name="fixture")
    engine.config.websocket_media.control_format = binding.control_format
    engine.config.websocket_media.fallback_format = "slin16"
    engine._seen_aux_channels = set()
    engine.pending_websocket_channels.clear()
    engine.websocket_media_channels.clear()
    engine._enable_pipeline_talk_detect = AsyncMock()
    engine._cleanup_call = AsyncMock()
    engine._stop_connection_audio = AsyncMock()
    await engine._save_session(session)
    runtime = WebSocketRuntime.__new__(WebSocketRuntime)
    runtime._config = engine.config
    runtime._section = engine.config.websocket_media
    runtime._asterisk_version = lambda: "20.17.0" if binding.control_format == "plain" else "22.10.1"
    runtime.server = engine.websocket_server
    runtime.server.register_call = lambda *args, **kwargs: "fixture-nonce"
    runtime.server.unregister_call = AsyncMock()
    engine.call_media_lifecycle = CallMediaLifecycle(runtime, setup_timeout_seconds=1)
    entered, release = asyncio.Event(), asyncio.Event()
    aux_tasks = []

    async def create(**kwargs):
        media_id = kwargs.get("channel_id") or kwargs["params"]["channelId"]
        binding.channel_id = media_id
        if aux_first:
            aux_tasks.append(asyncio.create_task(engine._handle_websocket_media_stasis_start(media_id, {})))
            await entered.wait()
        return {"id": media_id}

    async def add(*args):
        entered.set()
        await release.wait()
        return True

    async def start_provider(call_id):
        assert session.media_connection_state == "ready"
        await engine._websocket_handle_audio(call_id, b"\0\x20" * 320)

    engine.ari_client.create_external_media_channel = AsyncMock(side_effect=create)
    engine.ari_client.send_command = AsyncMock(side_effect=lambda *args, **kwargs: create(**kwargs))
    # AsyncMock does not await a coroutine returned by a synchronous side effect.
    async def originate(*args, **kwargs):
        return await create(**kwargs)
    engine.ari_client.send_command.side_effect = originate
    engine.ari_client.add_channel_to_bridge.side_effect = add
    engine._ensure_provider_session_started = AsyncMock(side_effect=start_provider)
    async with asyncio.timeout(2):
        main = asyncio.create_task(engine._setup_selected_call_media(session))
        await entered.wait()
        if not aux_first:
            aux_tasks.append(asyncio.create_task(engine._handle_websocket_media_stasis_start(session.media_channel_id, {})))
        await asyncio.sleep(0)
        release.set()
        result = await main
        await asyncio.gather(*aux_tasks)
    assert result.ready and result.failure_reason is None
    assert session.media_connection_state == "ready"
    engine.ari_client.add_channel_to_bridge.assert_awaited_once()
    engine._ensure_provider_session_started.assert_awaited_once_with("caller")
    engine._on_transport_pcm.assert_awaited_once()
    engine._cleanup_call.assert_not_awaited()
