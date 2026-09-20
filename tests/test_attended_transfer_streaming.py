import asyncio
import sys
import time
import types

import pytest

from src.config import AppConfig
from src.core.models import CallSession
from src.engine import Engine
from src.tools.execution_history import record_in_call_tool_result


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
    session.current_action = {
        "type": "attended_transfer",
        "agent_channel_id": "agent-ringing",
    }
    await engine.session_store.upsert_call(session)
    engine.register_attended_transfer_agent_channel("call-stream", "agent-ringing")

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
    assert updated.current_action.get("agent_channel_id") == "agent-stream"
    assert "agent-ringing" not in engine._attended_transfer_agent_channel_to_call_id
    assert engine._attended_transfer_agent_channel_to_call_id["agent-stream"] == "call-stream"


@pytest.mark.asyncio
async def test_attended_transfer_pickup_replaces_ringing_leg_before_cleanup():
    engine = _build_engine({"enabled": True})
    session = CallSession(
        call_id="call-pickup",
        caller_channel_id="caller-pickup",
    )
    session.current_action = {
        "type": "attended_transfer",
        "agent_channel_id": "agent-ringing",
    }
    await engine.session_store.upsert_call(session)
    engine.register_attended_transfer_agent_channel("call-pickup", "agent-ringing")

    superseded = engine._activate_attended_transfer_answered_channel(
        "call-pickup", "agent-pickup"
    )

    assert superseded == ("agent-ringing",)
    assert "agent-ringing" not in engine._attended_transfer_agent_channel_to_call_id
    assert engine._attended_transfer_agent_channel_to_call_id["agent-pickup"] == "call-pickup"

    # Asterisk destroys the original ringing channel after pickup. It no longer
    # owns the transfer, so that event must not tear down the caller session.
    await engine._cleanup_call("agent-ringing")

    assert await engine.session_store.get_by_call_id("call-pickup") is session


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
@pytest.mark.parametrize("cleanup_field", ["cleanup_in_progress", "cleanup_completed"])
async def test_deferred_transfer_commit_skips_calls_already_in_cleanup(
    monkeypatch,
    cleanup_field,
):
    engine = _build_engine({"enabled": True})
    session = CallSession(
        call_id=f"call-deferred-{cleanup_field}",
        caller_channel_id=f"caller-deferred-{cleanup_field}",
    )
    session.pending_deferred_transfer = {
        "id": f"action-{cleanup_field}",
        "kind": "transfer",
        "commit_tool": "blind_transfer",
        "target": "6000",
    }
    setattr(session, cleanup_field, True)
    await engine.session_store.upsert_call(session)

    async def unexpected(*args, **kwargs):
        raise AssertionError("cleanup must prevent deferred transfer work")

    monkeypatch.setattr(engine, "_play_deferred_transfer_local_handoff", unexpected)
    monkeypatch.setattr(engine, "_wait_for_deferred_transfer_audio_drain", unexpected)

    result = await engine._commit_pending_deferred_transfer_for_call(
        session.call_id,
        session,
    )

    assert result is None


@pytest.mark.asyncio
async def test_deferred_transfer_commit_rechecks_cleanup_after_audio_drain(monkeypatch):
    engine = _build_engine({"enabled": True})
    session = CallSession(
        call_id="call-deferred-cleanup-during-drain",
        caller_channel_id="caller-deferred-cleanup-during-drain",
    )
    session.pending_deferred_transfer = {
        "id": "action-cleanup-during-drain",
        "kind": "transfer",
        "commit_tool": "blind_transfer",
        "target": "6000",
    }
    await engine.session_store.upsert_call(session)

    async def fake_handoff(*args, **kwargs):
        return False

    async def fake_wait(call_id):
        session.cleanup_in_progress = True
        return True

    async def unexpected_commit(context):
        raise AssertionError(f"cleanup must prevent transfer for {context.call_id}")

    monkeypatch.setattr(engine, "_play_deferred_transfer_local_handoff", fake_handoff)
    monkeypatch.setattr(engine, "_wait_for_deferred_transfer_audio_drain", fake_wait)
    monkeypatch.setattr(
        "src.tools.telephony.deferred_transfer.commit_pending_deferred_transfer",
        unexpected_commit,
    )

    result = await engine._commit_pending_deferred_transfer_for_call(
        session.call_id,
        session,
    )

    assert result is None


@pytest.mark.asyncio
async def test_deferred_transfer_drain_timeout_cancels_instead_of_committing(monkeypatch):
    engine = _build_engine({"enabled": True})
    session = CallSession(
        call_id="call-deferred-timeout",
        caller_channel_id="caller-deferred-timeout",
        context_name="support",
    )
    session.pending_deferred_transfer = {
        "id": "action-timeout",
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
        return False

    async def fake_abort(call_id, *, action_id):
        calls.append(("abort", call_id, action_id))
        return {"status": "failed", "error_code": "deferred_audio_drain_timeout"}

    async def unexpected_commit(context):
        raise AssertionError(f"deferred transfer must not commit for {context.call_id}")

    monkeypatch.setattr(engine, "_wait_for_deferred_transfer_audio_drain", fake_wait)
    monkeypatch.setattr(engine, "_abort_deferred_transfer_after_drain_timeout", fake_abort)
    monkeypatch.setattr(
        "src.tools.telephony.deferred_transfer.commit_pending_deferred_transfer",
        unexpected_commit,
    )

    result = await engine._commit_pending_deferred_transfer_for_call(
        "call-deferred-timeout",
        session,
    )

    assert result == {"status": "failed", "error_code": "deferred_audio_drain_timeout"}
    assert calls == [
        ("drain", "call-deferred-timeout"),
        ("abort", "call-deferred-timeout", "action-timeout"),
    ]


@pytest.mark.asyncio
async def test_deferred_transfer_timeout_history_canonicalizes_tool_alias():
    engine = _build_engine({"enabled": True})
    session = CallSession(
        call_id="call-deferred-timeout-alias",
        caller_channel_id="caller-deferred-timeout-alias",
    )
    action = {
        "id": "action-timeout-alias",
        "kind": "transfer",
        "source_tool": "transfer_call",
        "target": "6000",
        "created_at": time.time(),
        "_tool_history_origin": {
            "tool_call_id": "provider-transfer-alias",
            "name": "transfer_call",
            "params": {"destination": "support_agent"},
        },
    }
    session.pending_deferred_transfer = action
    await engine.session_store.upsert_call(session)

    await engine._record_deferred_transfer_timeout_tool_result(session, action)

    assert session.tool_calls[-1]["name"] == "blind_transfer"
    assert session.tool_calls[-1]["tool_call_id"] == "provider-transfer-alias"
    assert session.tool_calls[-1]["result"] == "cancelled"


@pytest.mark.asyncio
async def test_deferred_transfer_timeout_cleans_predial_and_resumes_ai(monkeypatch):
    engine = _build_engine({"enabled": True})
    call_id = "call-predial-timeout"
    channel_id = "predial-channel-timeout"
    session = CallSession(
        call_id=call_id,
        caller_channel_id="caller-predial-timeout",
        context_name="support",
    )
    session.audio_capture_enabled = False
    session.pending_deferred_transfer = {
        "id": "action-predial-timeout",
        "kind": "transfer",
        "commit_tool": "blind_transfer",
        "transfer_type": "extension",
        "target": "6000",
        "description": "Support agent",
        "payload": {"predial": {"channel_id": channel_id}},
    }
    session.current_action = {
        "type": "predial_transfer",
        "deferred_action_id": "action-predial-timeout",
        "predial_channel_id": channel_id,
    }
    await engine.session_store.upsert_call(session)
    await record_in_call_tool_result(
        session_store=engine.session_store,
        call_id=call_id,
        tool_call_id="tool-call-predial-timeout",
        tool_name="blind_transfer",
        parameters={"destination": "support_agent"},
        result={
            "status": "success",
            "message": "Transferring you to Support agent now.",
            "destination": "6000",
            "deferred_transfer": dict(session.pending_deferred_transfer),
        },
    )
    engine.register_predial_transfer_channel(call_id, channel_id)

    events = []

    class _Provider:
        async def cancel_response(self):
            events.append(("cancel-provider", call_id))

    async def fake_hangup(self, target_channel_id):
        events.append(("hangup", target_channel_id))
        return True

    async def fake_command(self, *, method, resource, **kwargs):
        events.append(("command", method, resource))
        return True

    async def fake_stop_streaming(target_call_id):
        events.append(("stop-stream", target_call_id))
        return True

    async def fake_speak(target_call_id, text, kind):
        current = await engine.session_store.get_by_call_id(target_call_id)
        events.append(
            (
                "speak",
                target_call_id,
                text,
                kind,
                current.pending_deferred_transfer,
            )
        )
        return True

    engine._call_providers[call_id] = _Provider()
    engine.ari_client.hangup_channel = types.MethodType(fake_hangup, engine.ari_client)
    engine.ari_client.send_command = types.MethodType(fake_command, engine.ari_client)
    monkeypatch.setattr(engine.streaming_playback_manager, "stop_streaming_playback", fake_stop_streaming)
    monkeypatch.setattr(engine, "_speak_no_input_announcement", fake_speak)

    result = await engine._abort_deferred_transfer_after_drain_timeout(
        call_id,
        action_id="action-predial-timeout",
    )

    updated = await engine.session_store.get_by_call_id(call_id)
    assert result == {
        "status": "failed",
        "message": "Deferred transfer cancelled because caller-facing audio did not drain before the safety timeout.",
        "error_code": "deferred_audio_drain_timeout",
        "transfer_cancelled": True,
        "apology_spoken": True,
    }
    assert updated.pending_deferred_transfer is None
    assert updated.current_action is None
    assert updated.audio_capture_enabled is True
    assert [item["status"] for item in updated.tool_calls] == [
        "success",
        "failure",
    ]
    assert [item["tool_call_id"] for item in updated.tool_calls] == [
        "tool-call-predial-timeout",
        "tool-call-predial-timeout",
    ]
    assert updated.tool_calls[-1]["action"] == "deferred_transfer_timeout"
    assert updated.tool_calls[-1]["result"] == "cancelled"
    assert channel_id not in engine._predial_transfer_channel_to_call_id
    assert engine._deferred_predial_forced_hangup_tasks == {}
    assert events == [
        ("hangup", channel_id),
        ("command", "DELETE", "channels/caller-predial-timeout/moh"),
        ("cancel-provider", call_id),
        ("stop-stream", call_id),
        (
            "speak",
            call_id,
            "I'm sorry, I couldn't complete that transfer without interrupting you. How else can I help?",
            "deferred_transfer_timeout",
            None,
        ),
    ]


@pytest.mark.asyncio
async def test_deferred_transfer_timeout_retains_predial_owner_until_hangup_accepted(monkeypatch):
    engine = _build_engine({"enabled": True})
    call_id = "call-predial-hangup-retry"
    channel_id = "predial-channel-hangup-retry"
    session = CallSession(
        call_id=call_id,
        caller_channel_id="caller-predial-hangup-retry",
        context_name="support",
    )
    session.pending_deferred_transfer = {
        "id": "action-predial-hangup-retry",
        "kind": "transfer",
        "commit_tool": "blind_transfer",
        "transfer_type": "extension",
        "target": "6000",
        "payload": {"predial": {"channel_id": channel_id}},
    }
    session.current_action = {
        "type": "predial_transfer",
        "deferred_action_id": "action-predial-hangup-retry",
        "predial_channel_id": channel_id,
    }
    await engine.session_store.upsert_call(session)
    engine.register_predial_transfer_channel(call_id, channel_id)

    hangup_results = iter((False, True))
    retry_started = asyncio.Event()
    allow_retry = asyncio.Event()

    async def fake_hangup(self, target_channel_id):
        assert target_channel_id == channel_id
        return next(hangup_results)

    async def fake_command(self, **kwargs):
        return True

    async def fake_stop_streaming(target_call_id):
        return True

    async def fake_speak(target_call_id, text, kind):
        return True

    async def controlled_sleep(_seconds):
        retry_started.set()
        await allow_retry.wait()

    engine.ari_client.hangup_channel = types.MethodType(fake_hangup, engine.ari_client)
    engine.ari_client.send_command = types.MethodType(fake_command, engine.ari_client)
    monkeypatch.setattr(engine.streaming_playback_manager, "stop_streaming_playback", fake_stop_streaming)
    monkeypatch.setattr(engine, "_speak_no_input_announcement", fake_speak)
    monkeypatch.setattr("src.engine.asyncio.sleep", controlled_sleep)

    result = await engine._abort_deferred_transfer_after_drain_timeout(
        call_id,
        action_id="action-predial-hangup-retry",
    )
    await retry_started.wait()

    assert result["error_code"] == "deferred_audio_drain_timeout"
    assert engine._predial_transfer_channel_to_call_id[channel_id] == call_id
    retry_task = engine._deferred_predial_forced_hangup_tasks[channel_id]

    allow_retry.set()
    await retry_task

    assert channel_id not in engine._predial_transfer_channel_to_call_id
    assert engine._deferred_predial_forced_hangup_tasks == {}


@pytest.mark.asyncio
async def test_deferred_transfer_timeout_owns_predial_before_initial_hangup_await(monkeypatch):
    engine = _build_engine({"enabled": True})
    call_id = "call-predial-cancel-window"
    channel_id = "predial-channel-cancel-window"
    session = CallSession(
        call_id=call_id,
        caller_channel_id="caller-predial-cancel-window",
        context_name="support",
    )
    session.pending_deferred_transfer = {
        "id": "action-predial-cancel-window",
        "kind": "transfer",
        "commit_tool": "blind_transfer",
        "transfer_type": "extension",
        "target": "6000",
        "payload": {"predial": {"channel_id": channel_id}},
    }
    session.current_action = {
        "type": "predial_transfer",
        "deferred_action_id": "action-predial-cancel-window",
        "predial_channel_id": channel_id,
    }
    await engine.session_store.upsert_call(session)
    engine.register_predial_transfer_channel(call_id, channel_id)
    direct_hangup_started = asyncio.Event()
    retry_sleep_started = asyncio.Event()
    allow_retry = asyncio.Event()
    hangup_calls = 0

    async def fake_hangup(self, target_channel_id):
        nonlocal hangup_calls
        assert target_channel_id == channel_id
        hangup_calls += 1
        if hangup_calls == 1:
            direct_hangup_started.set()
            await asyncio.Event().wait()
        return True

    async def controlled_sleep(_seconds):
        retry_sleep_started.set()
        await allow_retry.wait()

    engine.ari_client.hangup_channel = types.MethodType(fake_hangup, engine.ari_client)
    monkeypatch.setattr("src.engine.asyncio.sleep", controlled_sleep)

    recovery_task = asyncio.create_task(
        engine._abort_deferred_transfer_after_drain_timeout(
            call_id,
            action_id="action-predial-cancel-window",
        )
    )
    await asyncio.wait_for(direct_hangup_started.wait(), timeout=0.25)
    await asyncio.wait_for(retry_sleep_started.wait(), timeout=0.25)

    recovery_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await recovery_task

    assert engine._predial_transfer_channel_to_call_id[channel_id] == call_id
    retry_task = engine._deferred_predial_forced_hangup_tasks[channel_id]
    allow_retry.set()
    await retry_task

    assert hangup_calls == 2
    assert channel_id not in engine._predial_transfer_channel_to_call_id
    assert engine._deferred_predial_forced_hangup_tasks == {}


@pytest.mark.asyncio
async def test_deferred_predial_shutdown_finalizes_retained_hangup_owner():
    engine = _build_engine({"enabled": True})
    call_id = "call-predial-shutdown"
    channel_id = "predial-channel-shutdown"
    owner_blocker = asyncio.Event()

    async def retry_owner():
        await owner_blocker.wait()

    retry_task = asyncio.create_task(retry_owner())
    engine._deferred_predial_forced_hangup_tasks[channel_id] = retry_task
    engine.register_predial_transfer_channel(call_id, channel_id)
    hangups = []

    async def fake_hangup(self, target_channel_id):
        hangups.append(target_channel_id)
        return True

    engine.ari_client.hangup_channel = types.MethodType(fake_hangup, engine.ari_client)

    await engine._finalize_deferred_predial_hangups_for_shutdown()
    await asyncio.gather(retry_task, return_exceptions=True)

    assert hangups == [channel_id]
    assert retry_task.cancelled()
    assert engine._deferred_predial_forced_hangup_tasks == {}
    assert channel_id not in engine._predial_transfer_channel_to_call_id


@pytest.mark.asyncio
async def test_agent_audio_done_defers_transfer_commit_outside_provider_callback(monkeypatch):
    engine = _build_engine({"enabled": True})
    call_id = "call-deferred-provider-callback"
    session = CallSession(
        call_id=call_id,
        caller_channel_id="caller-deferred-provider-callback",
        context_name="support",
    )
    session.pending_deferred_transfer = {
        "id": "action-provider-callback",
        "kind": "transfer",
        "commit_tool": "blind_transfer",
        "transfer_type": "extension",
        "target": "6000",
    }
    await engine.session_store.upsert_call(session)
    engine.streaming_playback_manager.continuous_stream = False
    commit_started = asyncio.Event()
    allow_commit = asyncio.Event()

    async def fake_note_output_end(*args, **kwargs):
        return None

    async def controlled_commit(target_call_id, target_session):
        assert target_call_id == call_id
        assert target_session.pending_deferred_transfer["id"] == "action-provider-callback"
        commit_started.set()
        await allow_commit.wait()

    monkeypatch.setattr(engine, "_note_provider_output_end", fake_note_output_end)
    monkeypatch.setattr(engine, "_commit_pending_deferred_transfer_for_call", controlled_commit)

    await asyncio.wait_for(
        engine.on_provider_event(
            {
                "type": "AgentAudioDone",
                "call_id": call_id,
                "streaming_done": True,
            }
        ),
        timeout=0.25,
    )
    await asyncio.wait_for(commit_started.wait(), timeout=0.25)

    task = next(iter(engine._call_bg_tasks[call_id]))
    assert not task.done()
    allow_commit.set()
    await task


@pytest.mark.asyncio
async def test_deferred_transfer_timeout_retries_rejected_playback_stop_before_apology(monkeypatch):
    engine = _build_engine({"enabled": True})
    call_id = "call-playback-stop-retry"
    playback_id = "playback-stop-retry"
    session = CallSession(
        call_id=call_id,
        caller_channel_id="caller-playback-stop-retry",
        context_name="support",
    )
    session.pending_deferred_transfer = {
        "id": "action-playback-stop-retry",
        "kind": "transfer",
        "commit_tool": "blind_transfer",
        "transfer_type": "extension",
        "target": "6000",
    }
    await engine.session_store.upsert_call(session)
    playback_active = True
    stop_results = iter((False, True))
    events = []

    async def fake_stop_streaming(target_call_id):
        events.append(("stop-stream", target_call_id))
        return True

    async def fake_list_playbacks(target_call_id):
        assert target_call_id == call_id
        return [playback_id] if playback_active else []

    async def fake_get_playback(target_playback_id):
        assert target_playback_id == playback_id
        return object() if playback_active else None

    async def fake_stop_playback(target_playback_id):
        assert target_playback_id == playback_id
        result = next(stop_results)
        events.append(("stop-playback", result))
        return result

    async def fake_wait_for_playback_end(target_call_id, target_playback_id, *, timeout_sec):
        events.append(("wait-playback", target_call_id, target_playback_id, timeout_sec))
        return False

    async def fake_playback_finished(target_playback_id):
        nonlocal playback_active
        events.append(("finish-playback", target_playback_id))
        playback_active = False
        return True

    async def fake_sleep(seconds):
        events.append(("retry-sleep", seconds))

    async def fake_speak(target_call_id, text, kind):
        events.append(("speak", target_call_id, kind))
        return True

    monkeypatch.setattr(engine.streaming_playback_manager, "stop_streaming_playback", fake_stop_streaming)
    monkeypatch.setattr(engine.session_store, "list_playbacks_for_call", fake_list_playbacks)
    monkeypatch.setattr(engine.session_store, "get_playback", fake_get_playback)
    monkeypatch.setattr(engine.ari_client, "stop_playback", fake_stop_playback)
    monkeypatch.setattr(engine.playback_manager, "wait_for_playback_end", fake_wait_for_playback_end)
    monkeypatch.setattr(engine.playback_manager, "on_playback_finished", fake_playback_finished)
    monkeypatch.setattr(engine, "_speak_no_input_announcement", fake_speak)
    monkeypatch.setattr("src.engine.asyncio.sleep", fake_sleep)

    result = await engine._abort_deferred_transfer_after_drain_timeout(
        call_id,
        action_id="action-playback-stop-retry",
    )

    assert result["apology_spoken"] is True
    assert events == [
        ("stop-stream", call_id),
        ("stop-playback", False),
        ("retry-sleep", 0.1),
        ("stop-playback", True),
        ("wait-playback", call_id, playback_id, 0.5),
        ("finish-playback", playback_id),
        ("speak", call_id, "deferred_transfer_timeout"),
    ]


@pytest.mark.asyncio
async def test_deferred_transfer_playback_stop_retry_limit_is_bounded(monkeypatch):
    engine = _build_engine({"enabled": True})
    call_id = "call-playback-stop-limit"
    playback_id = "playback-stop-limit"
    session = CallSession(
        call_id=call_id,
        caller_channel_id="caller-playback-stop-limit",
        context_name="support",
    )
    await engine.session_store.upsert_call(session)
    stop_attempts = []
    retry_delays = []

    async def fake_list_playbacks(target_call_id):
        assert target_call_id == call_id
        return [playback_id]

    async def fake_stop_playback(target_playback_id):
        assert target_playback_id == playback_id
        stop_attempts.append(target_playback_id)
        return False

    async def fake_sleep(seconds):
        retry_delays.append(seconds)

    monkeypatch.setattr(engine.session_store, "list_playbacks_for_call", fake_list_playbacks)
    monkeypatch.setattr(engine.ari_client, "stop_playback", fake_stop_playback)
    monkeypatch.setattr("src.engine.asyncio.sleep", fake_sleep)

    stopped = await engine._stop_deferred_transfer_playbacks_before_recovery(call_id)

    assert stopped is False
    assert stop_attempts == [playback_id] * 5
    assert retry_delays == [0.1, 0.2, 0.4, 0.8]


@pytest.mark.asyncio
async def test_deferred_transfer_playback_cleanup_failure_suppresses_apology(monkeypatch):
    engine = _build_engine({"enabled": True})
    call_id = "call-playback-cleanup-failed"
    session = CallSession(
        call_id=call_id,
        caller_channel_id="caller-playback-cleanup-failed",
        context_name="support",
    )
    session.pending_deferred_transfer = {
        "id": "action-playback-cleanup-failed",
        "kind": "transfer",
        "commit_tool": "blind_transfer",
        "transfer_type": "extension",
        "target": "6000",
    }
    await engine.session_store.upsert_call(session)

    async def fake_stop_streaming(target_call_id):
        assert target_call_id == call_id
        return True

    async def failed_playback_cleanup(target_call_id):
        assert target_call_id == call_id
        return False

    async def unexpected_speak(*args, **kwargs):
        raise AssertionError("apology must remain suppressed while playback cleanup failed")

    scheduled = []

    def fake_schedule_recovery(**kwargs):
        scheduled.append(kwargs)

    monkeypatch.setattr(engine.streaming_playback_manager, "stop_streaming_playback", fake_stop_streaming)
    monkeypatch.setattr(engine, "_stop_deferred_transfer_playbacks_before_recovery", failed_playback_cleanup)
    monkeypatch.setattr(engine, "_speak_no_input_announcement", unexpected_speak)
    monkeypatch.setattr(engine, "_schedule_deferred_transfer_timeout_recovery", fake_schedule_recovery)

    result = await engine._abort_deferred_transfer_after_drain_timeout(
        call_id,
        action_id="action-playback-cleanup-failed",
    )

    assert result["error_code"] == "deferred_audio_drain_timeout"
    assert result["transfer_cancelled"] is True
    assert result["apology_spoken"] is False
    assert result["recovery_pending"] is True
    assert scheduled == [
        {
            "call_id": call_id,
            "action_id": "action-playback-cleanup-failed",
            "predial_channel_id": "",
            "apology": "I'm sorry, I couldn't complete that transfer without interrupting you. How else can I help?",
        }
    ]
    updated = await engine.session_store.get_by_call_id(call_id)
    assert updated.audio_capture_enabled is False


@pytest.mark.asyncio
async def test_deferred_transfer_websocket_abort_failure_retains_recovery_owner(monkeypatch):
    engine = _build_engine({"enabled": True})
    engine.config.audio_transport = "websocket"
    call_id = "call-websocket-abort-failed"
    session = CallSession(
        call_id=call_id,
        caller_channel_id="caller-websocket-abort-failed",
        context_name="support",
    )
    await engine.session_store.upsert_call(session)

    async def failed_stream_abort(target_call_id):
        assert target_call_id == call_id
        return False

    monkeypatch.setattr(
        engine.streaming_playback_manager,
        "stop_streaming_playback",
        failed_stream_abort,
    )

    assert not await engine._stop_deferred_transfer_stream_before_recovery(call_id)


@pytest.mark.asyncio
async def test_deferred_transfer_active_audiosocket_stop_failure_retains_recovery_owner(monkeypatch):
    engine = _build_engine({"enabled": True})
    call_id = "call-audiosocket-stop-failed"
    engine.streaming_playback_manager.active_streams[call_id] = {
        "stream_id": "active-audiosocket-stream"
    }

    async def failed_stream_stop(target_call_id):
        assert target_call_id == call_id
        return False

    monkeypatch.setattr(
        engine.streaming_playback_manager,
        "stop_streaming_playback",
        failed_stream_stop,
    )

    assert not await engine._stop_deferred_transfer_stream_before_recovery(call_id)


@pytest.mark.asyncio
async def test_deferred_transfer_recovery_owner_retries_until_cleanup_then_apologizes(monkeypatch):
    engine = _build_engine({"enabled": True})
    call_id = "call-deferred-recovery-owner"
    session = CallSession(
        call_id=call_id,
        caller_channel_id="caller-deferred-recovery-owner",
        context_name="support",
    )
    await engine.session_store.upsert_call(session)

    stream_results = iter((False, True))
    playback_results = iter((False, True))
    events = []

    async def fake_stop_stream(target_call_id):
        assert target_call_id == call_id
        result = next(stream_results)
        events.append(("stream", result))
        return result

    async def fake_stop_playbacks(target_call_id):
        assert target_call_id == call_id
        result = next(playback_results)
        events.append(("playbacks", result))
        return result

    async def fake_complete(**kwargs):
        events.append(("complete", kwargs))
        return True

    async def fake_sleep(seconds):
        events.append(("sleep", seconds))

    monkeypatch.setattr(engine, "_stop_deferred_transfer_stream_before_recovery", fake_stop_stream)
    monkeypatch.setattr(engine, "_stop_deferred_transfer_playbacks_before_recovery", fake_stop_playbacks)
    monkeypatch.setattr(engine, "_complete_deferred_transfer_timeout_recovery", fake_complete)
    monkeypatch.setattr("src.engine.asyncio.sleep", fake_sleep)

    engine._schedule_deferred_transfer_timeout_recovery(
        call_id=call_id,
        action_id="action-recovery-owner",
        predial_channel_id="predial-recovery-owner",
        apology="Please hold while I recover.",
    )
    task = engine._deferred_transfer_recovery_tasks[call_id]
    await task

    assert events == [
        ("stream", False),
        ("playbacks", False),
        ("sleep", 0.5),
        ("stream", True),
        ("playbacks", True),
        (
            "complete",
            {
                "call_id": call_id,
                "action_id": "action-recovery-owner",
                "predial_channel_id": "predial-recovery-owner",
                "apology": "Please hold while I recover.",
            },
        ),
    ]
    assert engine._deferred_transfer_recovery_tasks == {}


@pytest.mark.asyncio
async def test_deferred_transfer_audio_drain_defaults_to_fifteen_seconds(monkeypatch):
    engine = _build_engine({"enabled": True})
    session = CallSession(call_id="call-default-drain", caller_channel_id="caller-default-drain")
    await engine.session_store.upsert_call(session)
    captured = {}

    async def fake_wait(call_id, *, timeout_sec, quiet_sec, reason):
        captured.update(
            call_id=call_id,
            timeout_sec=timeout_sec,
            quiet_sec=quiet_sec,
            reason=reason,
        )
        return True

    monkeypatch.setattr(engine, "_wait_for_call_audio_drain", fake_wait)

    assert await engine._wait_for_deferred_transfer_audio_drain("call-default-drain") is True
    assert captured == {
        "call_id": "call-default-drain",
        "timeout_sec": 15.0,
        "quiet_sec": 0.5,
        "reason": "deferred_transfer",
    }


@pytest.mark.asyncio
async def test_deferred_transfer_zero_timeout_still_fails_closed_with_pending_audio():
    engine = _build_engine({"enabled": True})
    engine.config.tools["transfer"]["deferred_audio_drain_timeout_sec"] = 0
    engine.config.tools["transfer"]["deferred_audio_drain_quiet_ms"] = 500
    call_id = "call-zero-drain"
    session = CallSession(call_id=call_id, caller_channel_id="caller-zero-drain")
    await engine.session_store.upsert_call(session)
    engine.streaming_playback_manager.active_streams[call_id] = {
        "buffered_bytes": 160,
        "jitter_depth": 0,
        "last_real_emit_ts": time.time(),
    }

    assert await engine._wait_for_deferred_transfer_audio_drain(call_id) is False

    engine.streaming_playback_manager.active_streams[call_id]["buffered_bytes"] = 0
    engine.streaming_playback_manager.active_streams[call_id]["last_real_emit_ts"] = time.time() - 1.0

    assert await engine._wait_for_deferred_transfer_audio_drain(call_id) is True


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
