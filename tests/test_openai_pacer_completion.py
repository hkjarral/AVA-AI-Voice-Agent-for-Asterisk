"""Issue 808: completion must seal the audio actually handed downstream."""
import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config import OpenAIRealtimeProviderConfig
from src.providers.openai_realtime import OpenAIRealtimeProvider


def provider(callback, *, paced=True):
    obj = OpenAIRealtimeProvider(OpenAIRealtimeProviderConfig(
        api_key="test", target_encoding="mulaw", target_sample_rate_hz=8000,
        egress_pacer_enabled=paced, egress_pacer_warmup_ms=320,
    ), on_event=callback)
    obj._call_id = "call"
    obj._current_response_id = "response-1"
    obj._greeting_completed = True
    obj.websocket = SimpleNamespace(state=SimpleNamespace(name="OPEN"), close=AsyncMock())
    obj._send_json = AsyncMock()
    obj._outfmt_acknowledged = True
    obj._provider_output_format = "g711_ulaw"
    obj._active_output_sample_rate_hz = 8000
    return obj


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [1, 159, 160, 2561, 8000])
@pytest.mark.parametrize("done_type", ["response.output_audio.done", "response.audio.done", "response.delta", "response.done"])
async def test_natural_completion_emits_exact_tail_before_boundary(size, done_type):
    events = []
    async def receive(event):
        events.append(event)
    obj = provider(receive)
    payload = bytes((i % 120) + 1 for i in range(size))
    await obj._handle_output_audio(base64.b64encode(payload).decode())
    await asyncio.sleep(0)  # Let a long clip start pacing; short clips stay in warm-up.
    done = {"type": done_type, "response_id": "response-1"}
    if done_type == "response.delta":
        done["delta"] = {"type": "output_audio.done"}
    if done_type == "response.done":
        done["response"] = {"id": "response-1", "status": "completed"}
    await obj._handle_event(done)
    assert b"".join(e["data"] for e in events if e["type"] == "AgentAudio") == payload
    assert [e["type"] for e in events].count("AgentAudioDone") == 1
    assert events[-1]["type"] == "AgentAudioDone"
    assert not obj._outbuf
    assert not obj._pacer_running
    assert obj._pacer_task.done()
    await obj._handle_event(done)
    assert [e["type"] for e in events].count("AgentAudioDone") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["cancel", "failed", "disconnect", "stop"])
async def test_interrupted_completion_discards_tail(reason):
    events = []
    async def receive(event):
        events.append(event)
    obj = provider(receive)
    await obj._handle_output_audio(base64.b64encode(b"pending-tail").decode())
    if reason == "cancel":
        await obj._cancel_response("response-1")
    elif reason == "failed":
        await obj._handle_event({"type": "response.done", "response": {"id": "response-1", "status": "failed"}})
    elif reason == "stop":
        await obj.stop_session()
    else:
        await obj._emit_audio_done(drain=False)
    assert not any(e["type"] == "AgentAudio" for e in events)
    assert not obj._outbuf
    assert not obj._pacer_running


@pytest.mark.asyncio
async def test_inflight_frame_is_admitted_before_tail_and_done():
    entered, release = asyncio.Event(), asyncio.Event()
    events = []
    async def receive(event):
        if event["type"] == "AgentAudio" and not entered.is_set():
            entered.set()
            await release.wait()
        events.append(event)
    obj = provider(receive)
    payload = b"\x12" * 4000
    await obj._handle_output_audio(base64.b64encode(payload).decode())
    await asyncio.wait_for(entered.wait(), 1)
    done = asyncio.create_task(obj._emit_audio_done())
    await asyncio.sleep(0)
    assert not done.done()
    release.set()
    await asyncio.wait_for(done, 1)
    assert b"".join(e["data"] for e in events if e["type"] == "AgentAudio") == payload
    assert events[-1]["type"] == "AgentAudioDone"


@pytest.mark.asyncio
async def test_late_done_cannot_seal_new_response():
    receive = AsyncMock()
    obj = provider(receive)
    obj._current_response_id = "response-2"
    obj._outbuf.extend(b"new-response")
    await obj._handle_event({"type": "response.output_audio.done", "response_id": "response-1"})
    receive.assert_not_awaited()
    assert obj._outbuf == b"new-response"


@pytest.mark.asyncio
async def test_interrupt_preempts_natural_completion_waiting_for_inflight_write():
    entered = asyncio.Event()
    events = []
    async def receive(event):
        if event["type"] == "AgentAudio":
            entered.set()
            await asyncio.Event().wait()
        events.append(event)
    obj = provider(receive)
    await obj._handle_output_audio(base64.b64encode(b"\x12" * 4000).decode())
    await asyncio.wait_for(entered.wait(), 1)
    done = asyncio.create_task(obj._emit_audio_done())
    await asyncio.sleep(0)
    await obj._cancel_response("response-1")
    await asyncio.wait_for(done, 1)
    assert [e["type"] for e in events] == ["AgentAudioDone"]
    assert not obj._outbuf
    # In-flight old deltas cannot restart the cancelled pacer.
    await obj._handle_output_audio(base64.b64encode(b"stale").decode())
    assert not obj._outbuf
    await obj._handle_event({"type": "response.created", "response": {"id": "response-2"}})
    await obj._handle_output_audio(base64.b64encode(b"fresh").decode())
    # Replace the callback for the new response so it can complete.
    obj.on_event = AsyncMock()
    await obj._emit_audio_done()
    assert obj.on_event.call_args_list[0].args[0]["data"] == b"fresh"


@pytest.mark.asyncio
async def test_stuck_pacer_admission_fails_without_publishing_success():
    entered = asyncio.Event()
    async def receive(event):
        if event["type"] == "AgentAudio":
            entered.set()
            await asyncio.Event().wait()
        else:
            raise AssertionError("A failed admission must not publish completion")
    obj = provider(receive)
    await obj._handle_output_audio(base64.b64encode(b"\x12" * 4000).decode())
    await asyncio.wait_for(entered.wait(), 1)
    with pytest.raises(RuntimeError, match="did not quiesce"):
        await obj._emit_audio_done()
    assert obj._pacer_task.done()
    assert not obj._outbuf


@pytest.mark.asyncio
async def test_failed_response_does_not_flush_handoff_tail():
    obj = provider(AsyncMock())
    obj._outbuf.extend(b"failed-tail")
    obj.flush_handoff_pacer = AsyncMock(return_value=True)
    await obj._handle_event({"type": "response.done", "response": {"id": "response-1", "status": "cancelled"}})
    obj.flush_handoff_pacer.assert_not_awaited()
    assert not obj._outbuf


@pytest.mark.asyncio
async def test_failed_inflight_callback_cannot_silently_complete_remaining_audio():
    async def receive(event):
        raise RuntimeError("downstream admission failed")
    obj = provider(receive)
    await obj._handle_output_audio(base64.b64encode(b"\x12" * 4000).decode())
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="audio admission failed"):
        await obj._emit_audio_done()
    assert not obj._outbuf
    assert not obj._pacer_running
    obj.on_event = AsyncMock()
    await obj._handle_event({"type": "response.created", "response": {"id": "response-2"}})
    await obj._handle_output_audio(base64.b64encode(b"fresh").decode())
    await obj._emit_audio_done()
    assert obj.on_event.call_args_list[0].args[0]["data"] == b"fresh"


@pytest.mark.asyncio
@pytest.mark.parametrize("done_type", ["response.done", "response.output_audio.done", "response.audio.done", "response.delta"])
@pytest.mark.parametrize("failure", ["tail", "pacer", "blocked", "handoff", "immediate"])
async def test_receive_loop_survives_failed_response_and_accepts_next(done_type, failure):
    """Exercise actual event dispatch, not only the low-level seal helper."""
    events = []
    entered = asyncio.Event()

    async def receive(e):
        if e["type"] == "AgentAudio" and e["data"] != b"fresh":
            if failure == "blocked":
                entered.set()
                await asyncio.Event().wait()
            raise RuntimeError("admission failed")
        events.append(e)

    obj = provider(receive)
    obj._outbuf.extend(b"tail")
    obj._in_audio_burst = True
    obj.complete_handoff_audio = AsyncMock()
    if failure == "immediate":
        obj._egress_pacer_enabled = False
        obj._outbuf.clear()
        await obj._handle_output_audio(base64.b64encode(b"old").decode())
    if failure in {"pacer", "blocked"}:
        obj._outbuf.clear()
        await obj._handle_output_audio(base64.b64encode(b"\x12" * 4000).decode())
        await asyncio.sleep(0)
        if failure == "blocked":
            await asyncio.wait_for(entered.wait(), 1)
    if failure == "handoff":
        obj.flush_handoff_pacer = AsyncMock(side_effect=RuntimeError("handoff callback failed"))
        # Handoff flushing belongs to the complete response event.
        done_type = "response.done"
    done = {"type": done_type, "response_id": "response-1"}
    if done_type == "response.done":
        done["response"] = {"id": "response-1", "status": "completed"}
    if done_type == "response.delta":
        done["delta"] = {"type": "output_audio.done"}

    import json
    class Socket:
        async def __aiter__(self):
            yield json.dumps(done)
            yield json.dumps(done)  # Duplicate failure cannot publish success.
            assert [e["type"] for e in events] == ["AgentAudioFailed"]
            obj.complete_handoff_audio.assert_not_awaited()
            assert obj._discarding_output and not obj._outbuf
            obj.flush_handoff_pacer = AsyncMock(return_value=True)
            yield json.dumps({"type": "response.created", "response": {"id": "response-2"}})
            yield json.dumps({"type": "response.output_audio.delta", "delta": base64.b64encode(b"fresh").decode()})
            yield json.dumps({"type": "response.done", "response": {"id": "response-2", "status": "completed"}})
            obj._closing = True

    obj.websocket = Socket()
    obj._reconnect_with_backoff = AsyncMock()
    await obj._receive_loop()
    assert [e["type"] for e in events] == ["AgentAudioFailed", "AgentAudio", "AgentAudioDone"]
    assert events[1]["data"] == b"fresh"
    obj._reconnect_with_backoff.assert_not_awaited()
    assert obj._reconnect_task is None


@pytest.mark.asyncio
async def test_failed_farewell_has_one_explicit_failure_terminal_notification():
    events = []
    async def receive(e):
        if e["type"] == "AgentAudio":
            raise RuntimeError("failed farewell")
        events.append(e)
    obj = provider(receive)
    obj._outbuf.extend(b"tail")
    obj._in_audio_burst = True
    obj._farewell_response_id = "response-1"
    obj._farewell_waiting_for_audio_done = True
    obj._audio_seen_response_ids.add("response-1")
    obj._start_farewell_timeout()
    await obj._handle_event({"type": "response.output_audio.done"})
    await obj._handle_event({"type": "response.output_audio.done"})
    assert [e["type"] for e in events] == ["AgentAudioFailed", "HangupReady"]
    assert events[-1]["reason"] == "farewell_audio_failed"
    assert obj._farewell_timeout_task is None


@pytest.mark.asyncio
async def test_failed_greeting_releases_transport_input_guard():
    async def fail_audio(e):
        if e["type"] == "AgentAudio":
            raise RuntimeError("admission failed")
    obj = provider(fail_audio)
    obj._greeting_response_id = "response-1"
    obj._greeting_transport_guard_active = True
    obj._outbuf.extend(b"tail")
    assert not await obj._finish_audio_output()
    assert not obj._greeting_transport_guard_active


@pytest.mark.asyncio
async def test_failed_downstream_boundary_is_not_handoff_success():
    events = []
    async def receive(e):
        if e["type"] == "AgentAudioDone":
            raise RuntimeError("boundary admission failed")
        events.append(e)
    obj = provider(receive)
    obj.complete_handoff_audio = AsyncMock()
    obj._outbuf.extend(b"tail")
    obj._in_audio_burst = True
    obj._audio_seen_response_ids.add("response-1")
    await obj._handle_event({"type": "response.done", "response": {"id": "response-1", "status": "completed"}})
    assert [e["type"] for e in events] == ["AgentAudio", "AgentAudioFailed"]
    obj.complete_handoff_audio.assert_not_awaited()
