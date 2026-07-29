import asyncio
import sys
import time
import types

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
            "rtp_host": "127.0.0.1",
            "rtp_port": 18080,
            "advertise_host": "127.0.0.1",
            "port_range": "18080-18090",
            "codec": "ulaw",
            "format": "slin16",
            "sample_rate": 16000,
        },
        "audiosocket": {"host": "127.0.0.1", "port": 9092, "format": "ulaw"},
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


def test_session_was_transferred_recognizes_attended_transfer_destination():
    engine = _build_engine({"enabled": True})
    session = CallSession(call_id="call-transfer", caller_channel_id="caller-transfer")

    assert engine._session_was_transferred(session) is False

    session.transfer_destination = "Sales agent"
    assert engine._session_was_transferred(session) is True


@pytest.mark.asyncio
async def test_attended_transfer_ai_briefing_generates_intro_summary_and_prompt(monkeypatch):
    engine = _build_engine(
        {
            "enabled": True,
            "delivery_mode": "stream",
            "stream_fallback_to_file": True,
            "screening_mode": "ai_briefing",
            "ai_briefing_intro_template": "Here is a short summary of the caller.",
            "agent_accept_prompt_template": "Press 1 to accept this transfer, or 2 to decline.",
            "accept_digit": "1",
            "decline_digit": "2",
        }
    )

    session = CallSession(
        call_id="call-screened",
        caller_channel_id="caller-screened",
        caller_name="WIRELESS CALLER",
        caller_number="15551230000",
        context_name="support",
    )
    session.current_action = {"type": "attended_transfer"}
    session.last_transcript = "My name is John and I need help with billing."
    session.conversation_history = [
        {"role": "user", "content": "My name is John."},
        {"role": "user", "content": "I need help with billing."},
    ]
    await engine.session_store.upsert_call(session)

    tts_texts = []
    finalize_calls = []

    async def fake_start_helper(*, call_id, agent_channel_id, attended_cfg=None):
        return {"rtp_session_id": f"attx:{call_id}:{agent_channel_id}"}

    async def fake_generate(*, session, destination_description, timeout_sec, **kwargs):
        return "John needs billing help."

    async def fake_tts(*, call_id, text, timeout_sec):
        tts_texts.append(text)
        return b"\xff" * 320

    async def fake_stream(agent_channel_id, audio_bytes, *, frame_ms=20):
        return True

    async def fake_wait_dtmf(agent_channel_id, *, timeout_sec):
        return "1"

    async def fake_finalize(session_obj, **kwargs):
        finalize_calls.append(kwargs)

    monkeypatch.setattr(engine, "_start_attended_transfer_helper_media", fake_start_helper)
    monkeypatch.setattr(engine, "_generate_attended_transfer_briefing_text", fake_generate)
    monkeypatch.setattr(engine, "_local_ai_server_tts", fake_tts)
    monkeypatch.setattr(engine, "_stream_attended_transfer_audio", fake_stream)
    monkeypatch.setattr(engine, "_wait_for_attended_transfer_dtmf", fake_wait_dtmf)
    monkeypatch.setattr(engine, "_attended_transfer_finalize_bridge", fake_finalize)

    await engine._handle_attended_transfer_answered(
        "agent-screened",
        ["attended-transfer", "call-screened", "support_agent"],
    )

    assert tts_texts[0] == "Here is a short summary of the caller."
    assert tts_texts[1] == "John needs billing help."
    assert tts_texts[2] == "Press 1 to accept this transfer, or 2 to decline."
    updated = await engine.session_store.get_by_call_id("call-screened")
    assert updated is not None
    assert updated.current_action is not None
    assert updated.current_action.get("screening_payload", {}).get("kind") == "ai_briefing"
    assert updated.current_action.get("screening_payload", {}).get("text") == "John needs billing help."
    assert finalize_calls


@pytest.mark.asyncio
async def test_attended_transfer_basic_tts_skips_ai_briefing_generation(monkeypatch):
    engine = _build_engine(
        {
            "enabled": True,
            "delivery_mode": "stream",
            "stream_fallback_to_file": True,
            "screening_mode": "basic_tts",
            "accept_digit": "1",
            "decline_digit": "2",
        }
    )

    session = CallSession(
        call_id="call-no-screened",
        caller_channel_id="caller-no-screened",
        caller_name="Bob",
        caller_number="15550001111",
        context_name="support",
    )
    session.current_action = {"type": "attended_transfer"}
    await engine.session_store.upsert_call(session)
    tts_texts = []
    stream_payloads = []
    decisions = []
    finalized = []

    async def fake_start_helper(*, call_id, agent_channel_id, attended_cfg=None):
        return {"rtp_session_id": f"attx:{call_id}:{agent_channel_id}"}

    async def unexpected_generate(*, session, destination_description, timeout_sec):
        raise AssertionError("AI briefing generation should not run for basic_tts")

    async def fake_tts(*, call_id, text, timeout_sec):
        tts_texts.append(text)
        return b"\xff" * 160

    async def fake_stream(agent_channel_id, audio_bytes, *, frame_ms=20):
        stream_payloads.append((agent_channel_id, audio_bytes, frame_ms))
        return True

    async def fake_wait_dtmf(agent_channel_id, *, timeout_sec):
        decisions.append((agent_channel_id, timeout_sec))
        return "1"

    async def fake_finalize(*args, **kwargs):
        finalized.append((args, kwargs))
        return None

    monkeypatch.setattr(engine, "_start_attended_transfer_helper_media", fake_start_helper)
    monkeypatch.setattr(engine, "_generate_attended_transfer_briefing_text", unexpected_generate)
    monkeypatch.setattr(engine, "_local_ai_server_tts", fake_tts)
    monkeypatch.setattr(engine, "_stream_attended_transfer_audio", fake_stream)
    monkeypatch.setattr(engine, "_wait_for_attended_transfer_dtmf", fake_wait_dtmf)
    monkeypatch.setattr(engine, "_attended_transfer_finalize_bridge", fake_finalize)

    await engine._handle_attended_transfer_answered(
        "agent-no-screened",
        ["attended-transfer", "call-no-screened", "support_agent"],
    )

    assert any("Press 1 to accept this transfer" in text for text in tts_texts)
    assert stream_payloads
    assert all(payload == b"\xff" * 160 for _, payload, _ in stream_payloads)
    assert decisions and decisions[0][0] == "agent-no-screened"
    assert finalized
    updated = await engine.session_store.get_by_call_id("call-no-screened")
    assert updated is not None
    assert updated.current_action is not None
    assert updated.current_action.get("decision") == "accepted"


def test_attended_transfer_template_substitution_keeps_unknown_placeholders():
    engine = _build_engine({"enabled": True})
    session = CallSession(
        call_id="call-templates",
        caller_channel_id="caller-templates",
        caller_name="Caller ID Name",
        caller_number="15550112222",
        context_name="support",
    )
    session.current_action = {
        "type": "attended_transfer",
        "screening_payload": {
            "kind": "ai_briefing",
            "text": "Billing issue",
        },
    }

    rendered = engine._apply_prompt_template_substitution(
        "Hi {caller_display} about {screened_reason_display}. Summary={screening_summary}. Unknown={unknown_var}",
        session,
        extra_substitutions=engine._build_attended_transfer_template_vars(
            session,
            destination_description="Support agent",
        ),
    )

    assert rendered == "Hi Caller ID Name about Billing issue. Summary=Billing issue. Unknown={unknown_var}"


def test_attended_transfer_screening_mode_resolution_prefers_explicit_mode():
    engine = _build_engine({"enabled": True})
    assert engine._resolve_attended_transfer_screening_mode({"screening_mode": "caller_recording"}) == "caller_recording"
    assert engine._resolve_attended_transfer_screening_mode({"screening_mode": "ai_briefing"}) == "ai_briefing"
    assert engine._resolve_attended_transfer_screening_mode({"screening_mode": "ai_summary"}) == "ai_briefing"
    assert engine._resolve_attended_transfer_screening_mode({"pass_caller_info_to_context": True}) == "ai_briefing"
    assert engine._resolve_attended_transfer_screening_mode({"screening_mode": "basic_tts"}) == "basic_tts"
    assert engine._resolve_attended_transfer_screening_mode({}) == "basic_tts"


def test_attended_transfer_pending_session_detection():
    engine = _build_engine({"enabled": True})
    session = CallSession(call_id="call-pending", caller_channel_id="caller-pending")

    assert engine._session_has_pending_attended_transfer(session) is False

    session.current_action = {"type": "attended_transfer"}
    assert engine._session_has_pending_attended_transfer(session) is True

    session.current_action["decision"] = "accepted"
    assert engine._session_has_pending_attended_transfer(session) is False

    session.current_action["decision"] = "declined"
    assert engine._session_has_pending_attended_transfer(session) is False


def test_attended_transfer_ai_briefing_rejects_local_ai_fallback_text():
    engine = _build_engine({"enabled": True})
    assert (
        engine._sanitize_attended_transfer_briefing_text(
            "I'm here to help you. How can I assist you today?"
        )
        is None
    )


@pytest.mark.asyncio
async def test_deferred_transfer_commit_waits_for_audio_drain(monkeypatch):
    engine = _build_engine({"enabled": True})
    session = CallSession(
        call_id="call-deferred",
        caller_channel_id="caller-deferred",
        context_name="support",
    )
    session.pending_deferred_transfer = {
        "kind": "transfer",
        "commit_tool": "blind_transfer",
        "transfer_type": "extension",
        "target": "6000",
        "description": "Support agent",
    }
    await engine.session_store.upsert_call(session)

    calls = []

    async def fake_wait(call_id):
        calls.append(("drain", call_id))
        return True

    async def fake_commit(context):
        calls.append(("commit", context.call_id))
        return {"status": "success", "message": "ok"}

    monkeypatch.setattr(engine, "_wait_for_deferred_transfer_audio_drain", fake_wait)
    monkeypatch.setattr(
        "src.tools.telephony.deferred_transfer.commit_pending_deferred_transfer",
        fake_commit,
    )

    result = await engine._commit_pending_deferred_transfer_for_call("call-deferred", session)

    assert result == {"status": "success", "message": "ok"}
    assert calls == [("drain", "call-deferred"), ("commit", "call-deferred")]


@pytest.mark.asyncio
async def test_deferred_transfer_deepgram_plays_local_handoff_before_commit(monkeypatch):
    engine = _build_engine({"enabled": True})
    engine.config.tools["transfer"]["local_handoff_audio_providers"] = ["deepgram"]
    session = CallSession(
        call_id="call-deepgram-handoff",
        caller_channel_id="caller-deepgram-handoff",
        context_name="support",
        provider_name="deepgram",
    )
    session.pending_deferred_transfer = {
        "kind": "transfer",
        "commit_tool": "blind_transfer",
        "transfer_type": "extension",
        "target": "6000",
        "description": "Support agent",
    }
    await engine.session_store.upsert_call(session)

    calls = []

    async def fake_tts(*, call_id, text, timeout_sec):
        calls.append(("tts", call_id, text))
        return b"\xff" * 1600

    async def fake_wait(call_id):
        calls.append(("drain", call_id))
        return True

    async def fake_commit(context):
        calls.append(("commit", context.call_id))
        return {"status": "success", "message": "ok"}

    class _Playback:
        async def play_audio(self, call_id, audio, playback_type):
            calls.append(("play", call_id, playback_type, len(audio)))
            return "pb-handoff"

        async def wait_for_playback_end(self, call_id, playback_id, *, timeout_sec):
            calls.append(("wait-playback", call_id, playback_id))
            return True

    engine.playback_manager = _Playback()
    monkeypatch.setattr(engine, "_local_ai_server_tts", fake_tts)
    monkeypatch.setattr(engine, "_wait_for_deferred_transfer_audio_drain", fake_wait)
    monkeypatch.setattr(
        "src.tools.telephony.deferred_transfer.commit_pending_deferred_transfer",
        fake_commit,
    )

    result = await engine._commit_pending_deferred_transfer_for_call("call-deepgram-handoff", session)

    assert result == {"status": "success", "message": "ok"}
    assert calls == [
        ("tts", "call-deepgram-handoff", "Transferring you to Support agent now."),
        ("play", "call-deepgram-handoff", "transfer-handoff", 1600),
        ("wait-playback", "call-deepgram-handoff", "pb-handoff"),
        ("drain", "call-deepgram-handoff"),
        ("commit", "call-deepgram-handoff"),
    ]


@pytest.mark.asyncio
async def test_deferred_transfer_audio_drain_waits_for_streaming_buffer():
    engine = _build_engine({"enabled": True})
    engine.config.tools["transfer"]["deferred_audio_drain_timeout_sec"] = 1.0
    engine.config.tools["transfer"]["deferred_audio_drain_quiet_ms"] = 60

    call_id = "call-drain"
    engine.streaming_playback_manager.active_streams[call_id] = {
        "buffered_bytes": 160,
        "jitter_depth": 0,
        "last_real_emit_ts": time.time(),
    }
    engine.streaming_playback_manager.frame_remainders[call_id] = b""
    observations = []

    async def clear_buffer():
        await asyncio.sleep(0.05)
        observations.append(("before_clear", engine.streaming_playback_manager.active_streams[call_id]["buffered_bytes"]))
        engine.streaming_playback_manager.active_streams[call_id]["buffered_bytes"] = 0
        engine.streaming_playback_manager.active_streams[call_id]["last_real_emit_ts"] = time.time()

    clear_task = asyncio.create_task(clear_buffer())
    drained = await engine._wait_for_deferred_transfer_audio_drain(call_id)
    await clear_task

    assert drained is True
    assert observations == [("before_clear", 160)]
    assert engine.streaming_playback_manager.active_streams[call_id]["buffered_bytes"] == 0


@pytest.mark.asyncio
async def test_predial_transfer_finalize_removes_ai_media_and_bridges_destination():
    engine = _build_engine({"enabled": True})
    session = CallSession(
        call_id="call-predial",
        caller_channel_id="caller-predial",
        bridge_id="bridge-predial",
        audiosocket_channel_id="audiosocket-predial",
        external_media_id="rtp-predial",
    )
    session.current_action = {
        "type": "predial_transfer",
        "target": "6000",
        "target_name": "Support agent",
        "answered": True,
        "predial_channel_id": "SIP/6000-00000001",
    }
    await engine.session_store.upsert_call(session)
    engine.ari_client.remove_channel_from_bridge = types.MethodType(
        lambda self, bridge_id, channel_id: asyncio.sleep(0, result=True),
        engine.ari_client,
    )
    engine.ari_client.add_channel_to_bridge = types.MethodType(
        lambda self, bridge_id, channel_id: asyncio.sleep(0, result=True),
        engine.ari_client,
    )
    engine.ari_client.send_command = types.MethodType(
        lambda self, **kwargs: asyncio.sleep(0, result={"status": 204}),
        engine.ari_client,
    )

    ok = await engine._finalize_predial_transfer_bridge(session, "SIP/6000-00000001")

    assert ok is True
    updated = await engine.session_store.get_by_call_id("call-predial")
    assert updated.current_action["bridged"] is True
    assert updated.current_action["channel_id"] == "SIP/6000-00000001"
    assert updated.transfer_state == "bridged"
    assert updated.transfer_destination == "Support agent"


@pytest.mark.asyncio
async def test_predial_transfer_bridges_before_slow_provider_shutdown():
    engine = _build_engine({"enabled": True})
    session = CallSession(
        call_id="call-predial-fast",
        caller_channel_id="caller-predial-fast",
        bridge_id="bridge-predial-fast",
        audiosocket_channel_id="audiosocket-predial-fast",
        external_media_id="rtp-predial-fast",
        provider_name="google_live",
    )
    session.current_action = {
        "type": "predial_transfer",
        "target": "6000",
        "target_name": "Support agent",
        "answered": True,
        "predial_channel_id": "SIP/6000-00000002",
    }
    await engine.session_store.upsert_call(session)

    order = []
    stop_started = asyncio.Event()
    stop_release = asyncio.Event()
    stop_done = asyncio.Event()

    class SlowProvider:
        async def stop_session(self):
            order.append("provider-stop-start")
            stop_started.set()
            await stop_release.wait()
            order.append("provider-stop-done")
            stop_done.set()

    engine._call_providers["call-predial-fast"] = SlowProvider()

    async def fake_remove(self, bridge_id, channel_id):
        order.append(f"remove:{channel_id}")
        return True

    async def fake_add(self, bridge_id, channel_id):
        order.append(f"add:{channel_id}")
        return True

    engine.ari_client.remove_channel_from_bridge = types.MethodType(fake_remove, engine.ari_client)
    engine.ari_client.add_channel_to_bridge = types.MethodType(fake_add, engine.ari_client)
    engine.ari_client.send_command = types.MethodType(
        lambda self, **kwargs: asyncio.sleep(0, result={"status": 204}),
        engine.ari_client,
    )

    ok = await engine._finalize_predial_transfer_bridge(session, "SIP/6000-00000002")

    assert ok is True
    assert order[:3] == [
        "remove:rtp-predial-fast",
        "remove:audiosocket-predial-fast",
        "add:SIP/6000-00000002",
    ]
    assert "provider-stop-done" not in order

    await asyncio.wait_for(stop_started.wait(), timeout=1)
    stop_release.set()
    await asyncio.wait_for(stop_done.wait(), timeout=1)
    assert order[-1] == "provider-stop-done"


@pytest.mark.asyncio
async def test_predial_transfer_bridge_failure_cleans_destination_leg_and_provider():
    engine = _build_engine({"enabled": True})
    session = CallSession(
        call_id="call-predial-fail",
        caller_channel_id="caller-predial-fail",
        bridge_id="bridge-predial-fail",
        provider_name="google_live",
    )
    session.current_action = {
        "type": "predial_transfer",
        "target": "6000",
        "target_name": "Support agent",
        "answered": True,
        "predial_channel_id": "SIP/6000-00000003",
    }
    await engine.session_store.upsert_call(session)
    engine.register_predial_transfer_channel("call-predial-fail", "SIP/6000-00000003")

    stopped = asyncio.Event()
    hung_up = []
    scheduled = []

    class Provider:
        async def stop_session(self):
            stopped.set()

    engine._call_providers["call-predial-fail"] = Provider()
    engine.ari_client.add_channel_to_bridge = types.MethodType(
        lambda self, bridge_id, channel_id: asyncio.sleep(0, result=False),
        engine.ari_client,
    )
    engine.ari_client.send_command = types.MethodType(
        lambda self, **kwargs: asyncio.sleep(0, result={"status": 204}),
        engine.ari_client,
    )

    async def fake_hangup(channel_id):
        hung_up.append(channel_id)
        return True

    def fake_fire_and_forget(coro, *, name=None):
        scheduled.append(name)
        return asyncio.create_task(coro)

    engine.ari_client.hangup_channel = fake_hangup
    engine._fire_and_forget = fake_fire_and_forget

    ok = await engine._finalize_predial_transfer_bridge(session, "SIP/6000-00000003")

    assert ok is False
    assert "SIP/6000-00000003" not in engine._predial_transfer_channel_to_call_id
    assert hung_up == ["SIP/6000-00000003"]
    assert scheduled == ["predial-provider-stop-failed-call-predial-fail"]
    await asyncio.wait_for(stopped.wait(), timeout=1)
    updated = await engine.session_store.get_by_call_id("call-predial-fail")
    assert updated.current_action is None


@pytest.mark.asyncio
async def test_predial_transfer_finalize_is_serialized():
    engine = _build_engine({"enabled": True})
    session = CallSession(
        call_id="call-predial-race",
        caller_channel_id="caller-predial-race",
        bridge_id="bridge-predial-race",
    )
    session.current_action = {
        "type": "predial_transfer",
        "target": "6000",
        "target_name": "Support agent",
        "answered": True,
        "predial_channel_id": "SIP/6000-00000004",
    }
    await engine.session_store.upsert_call(session)

    add_started = asyncio.Event()
    release_add = asyncio.Event()
    add_calls = []

    async def fake_add(self, bridge_id, channel_id):
        add_calls.append((bridge_id, channel_id))
        add_started.set()
        await release_add.wait()
        return True

    engine.ari_client.add_channel_to_bridge = types.MethodType(fake_add, engine.ari_client)
    engine.ari_client.send_command = types.MethodType(
        lambda self, **kwargs: asyncio.sleep(0, result={"status": 204}),
        engine.ari_client,
    )

    first = asyncio.create_task(engine._finalize_predial_transfer_bridge(session, "SIP/6000-00000004"))
    await asyncio.wait_for(add_started.wait(), timeout=1)
    second = asyncio.create_task(engine._finalize_predial_transfer_bridge(session, "SIP/6000-00000004"))
    await asyncio.sleep(0)
    release_add.set()

    assert await asyncio.wait_for(first, timeout=1) is True
    assert await asyncio.wait_for(second, timeout=1) is True
    assert add_calls == [("bridge-predial-race", "SIP/6000-00000004")]
    assert "call-predial-race" not in engine._predial_bridge_in_progress


@pytest.mark.asyncio
async def test_predial_transfer_finalize_in_progress_does_not_report_success(monkeypatch):
    engine = _build_engine({"enabled": True})
    session = CallSession(
        call_id="call-predial-in-flight",
        caller_channel_id="caller-predial-in-flight",
        bridge_id="bridge-predial-in-flight",
    )
    session.current_action = {
        "type": "predial_transfer",
        "target": "6000",
        "target_name": "Support agent",
        "answered": True,
        "bridged": False,
        "predial_channel_id": "SIP/6000-00000006",
    }
    await engine.session_store.upsert_call(session)
    engine._predial_bridge_in_progress.add("call-predial-in-flight")

    ticks = iter([0.0, 10.0])
    monkeypatch.setattr("src.engine.time.time", lambda: next(ticks, 10.0))

    ok = await engine._finalize_predial_transfer_bridge(session, "SIP/6000-00000006")

    assert ok is False
    assert "call-predial-in-flight" in engine._predial_bridge_in_progress
    updated = await engine.session_store.get_by_call_id("call-predial-in-flight")
    assert updated.current_action["bridged"] is False


@pytest.mark.asyncio
async def test_unbridged_predial_leg_cleanup_does_not_cleanup_caller():
    engine = _build_engine({"enabled": True})
    session = CallSession(
        call_id="call-predial-unbridged",
        caller_channel_id="caller-predial-unbridged",
        bridge_id="bridge-predial-unbridged",
    )
    session.current_action = {
        "type": "predial_transfer",
        "target": "6000",
        "target_name": "Support agent",
        "answered": True,
        "ready_to_bridge": False,
        "bridged": False,
        "predial_channel_id": "SIP/6000-00000005",
    }
    await engine.session_store.upsert_call(session)
    engine.register_predial_transfer_channel("call-predial-unbridged", "SIP/6000-00000005")

    destroyed_bridges = []

    async def fake_destroy_bridge(bridge_id):
        destroyed_bridges.append(bridge_id)
        return True

    engine.ari_client.destroy_bridge = fake_destroy_bridge

    await engine._cleanup_call("SIP/6000-00000005")

    assert destroyed_bridges == []
    assert "SIP/6000-00000005" not in engine._predial_transfer_channel_to_call_id
    updated = await engine.session_store.get_by_call_id("call-predial-unbridged")
    assert updated is not None
    assert updated.current_action is None


@pytest.mark.asyncio
async def test_attended_transfer_ai_briefing_falls_back_to_basic_tts_when_generation_unavailable(monkeypatch):
    engine = _build_engine(
        {
            "enabled": True,
            "delivery_mode": "stream",
            "stream_fallback_to_file": True,
            "screening_mode": "ai_briefing",
            "announcement_template": "Transfer {caller_display} regarding {context_name}.",
            "agent_accept_prompt_template": "Press 1 to accept this transfer, or 2 to decline.",
            "accept_digit": "1",
            "decline_digit": "2",
        }
    )

    session = CallSession(
        call_id="call-ai-briefing-fallback",
        caller_channel_id="caller-ai-briefing-fallback",
        caller_name="Caller ID Name",
        caller_number="15550112222",
        context_name="support",
    )
    session.current_action = {"type": "attended_transfer"}
    await engine.session_store.upsert_call(session)

    tts_texts = []

    async def fake_start_helper(*, call_id, agent_channel_id, attended_cfg=None):
        return {"rtp_session_id": f"attx:{call_id}:{agent_channel_id}"}

    async def fake_generate(*, session, destination_description, timeout_sec, **kwargs):
        return None

    async def fake_tts(*, call_id, text, timeout_sec):
        tts_texts.append(text)
        return b"\xff" * 320

    async def fake_stream(agent_channel_id, audio_bytes, *, frame_ms=20):
        return True

    async def fake_wait_dtmf(agent_channel_id, *, timeout_sec):
        return "1"

    async def fake_finalize(*args, **kwargs):
        return None

    monkeypatch.setattr(engine, "_start_attended_transfer_helper_media", fake_start_helper)
    monkeypatch.setattr(engine, "_generate_attended_transfer_briefing_text", fake_generate)
    monkeypatch.setattr(engine, "_local_ai_server_tts", fake_tts)
    monkeypatch.setattr(engine, "_stream_attended_transfer_audio", fake_stream)
    monkeypatch.setattr(engine, "_wait_for_attended_transfer_dtmf", fake_wait_dtmf)
    monkeypatch.setattr(engine, "_attended_transfer_finalize_bridge", fake_finalize)

    await engine._handle_attended_transfer_answered(
        "agent-ai-briefing-fallback",
        ["attended-transfer", "call-ai-briefing-fallback", "support_agent"],
    )

    assert tts_texts[0] == "Transfer Caller ID Name regarding support."
    assert tts_texts[1] == "Press 1 to accept this transfer, or 2 to decline."


@pytest.mark.asyncio
async def test_local_ai_server_llm_request_waits_for_auth_success(monkeypatch):
    engine = _build_engine({"enabled": True})
    engine.config.providers["local"]["base_url"] = "ws://local-ai.test/ws"
    engine.config.providers["local"]["auth_token"] = "FAKE_TEST_TOKEN"  # noqa: S105 - test-only token

    class FakeWebSocket:
        def __init__(self):
            self.sent = []
            self._responses = [
                '{"type":"auth_response","status":"ok"}',
                '{"type":"llm_response","text":"Short caller summary."}',
            ]

        async def send(self, message):
            self.sent.append(message)

        async def recv(self):
            return self._responses.pop(0)

    class FakeConnect:
        def __init__(self, ws):
            self.ws = ws

        async def __aenter__(self):
            return self.ws

        async def __aexit__(self, exc_type, exc, tb):
            return False

    fake_ws = FakeWebSocket()
    monkeypatch.setitem(sys.modules, "websockets", types.SimpleNamespace(connect=lambda *args, **kwargs: FakeConnect(fake_ws)))

    result = await engine._local_ai_server_llm_request(
        call_id="call-auth-ok",
        text="summarize",
        timeout_sec=1.0,
    )

    assert result == "Short caller summary."
    assert len(fake_ws.sent) == 2
    assert '"type": "auth"' in fake_ws.sent[0]
    assert '"type": "llm_request"' in fake_ws.sent[1]


@pytest.mark.asyncio
async def test_local_ai_server_llm_request_stops_on_auth_failure(monkeypatch):
    engine = _build_engine({"enabled": True})
    engine.config.providers["local"]["base_url"] = "ws://local-ai.test/ws"
    engine.config.providers["local"]["auth_token"] = "FAKE_TEST_TOKEN"  # noqa: S105 - test-only token

    class FakeWebSocket:
        def __init__(self):
            self.sent = []
            self._responses = [
                '{"type":"auth_response","status":"error","message":"invalid_auth_token"}',
            ]

        async def send(self, message):
            self.sent.append(message)

        async def recv(self):
            return self._responses.pop(0)

    class FakeConnect:
        def __init__(self, ws):
            self.ws = ws

        async def __aenter__(self):
            return self.ws

        async def __aexit__(self, exc_type, exc, tb):
            return False

    fake_ws = FakeWebSocket()
    monkeypatch.setitem(sys.modules, "websockets", types.SimpleNamespace(connect=lambda *args, **kwargs: FakeConnect(fake_ws)))

    result = await engine._local_ai_server_llm_request(
        call_id="call-auth-failed",
        text="summarize",
        timeout_sec=1.0,
    )

    assert result is None
    assert len(fake_ws.sent) == 1
    assert '"type": "auth"' in fake_ws.sent[0]


@pytest.mark.asyncio
async def test_attended_transfer_caller_recording_mode_streams_intro_clip_and_prompt(monkeypatch):
    engine = _build_engine(
        {
            "enabled": True,
            "delivery_mode": "stream",
            "stream_fallback_to_file": True,
            "screening_mode": "caller_recording",
            "accept_digit": "1",
            "decline_digit": "2",
        }
    )

    session = CallSession(
        call_id="call-recording-mode",
        caller_channel_id="caller-recording-mode",
        caller_name="Caller ID",
        caller_number="15550009999",
        context_name="support",
    )
    session.current_action = {
        "type": "attended_transfer",
        "screening_mode": "caller_recording",
        "screening_payload": {
            "kind": "caller_recording",
            "audio_ulaw": b"\xff" * 1600,
            "duration_ms": 200,
        },
    }
    await engine.session_store.upsert_call(session)

    tts_texts = []
    stream_lengths = []

    async def fake_start_helper(*, call_id, agent_channel_id, attended_cfg=None):
        return {"rtp_session_id": f"attx:{call_id}:{agent_channel_id}"}

    async def fake_tts(*, call_id, text, timeout_sec):
        tts_texts.append(text)
        return b"\xff" * 320

    async def fake_stream(agent_channel_id, audio_bytes, *, frame_ms=20):
        stream_lengths.append(len(audio_bytes))
        return True

    async def fake_wait_dtmf(agent_channel_id, *, timeout_sec):
        return "1"

    async def fake_finalize(*args, **kwargs):
        return None

    monkeypatch.setattr(engine, "_start_attended_transfer_helper_media", fake_start_helper)
    monkeypatch.setattr(engine, "_local_ai_server_tts", fake_tts)
    monkeypatch.setattr(engine, "_stream_attended_transfer_audio", fake_stream)
    monkeypatch.setattr(engine, "_wait_for_attended_transfer_dtmf", fake_wait_dtmf)
    monkeypatch.setattr(engine, "_attended_transfer_finalize_bridge", fake_finalize)

    await engine._handle_attended_transfer_answered(
        "agent-recording-mode",
        ["attended-transfer", "call-recording-mode", "support_agent"],
    )

    assert tts_texts[0] == "Hi, this is Ava. Here is the caller's screening."
    assert tts_texts[1] == "Press 1 to accept this transfer, or 2 to decline."
    assert stream_lengths == [320, 1600, 320]
