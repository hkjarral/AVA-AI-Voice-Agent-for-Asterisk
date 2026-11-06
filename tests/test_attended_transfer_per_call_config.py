"""Attended transfer reads the call's own tool settings.

The attended_transfer tool originates the destination leg from the session's
tool snapshot. The answer handler and the recovery path must read that same
snapshot, so a config reload during the call cannot change the rules of a
transfer that is already ringing or screening.
"""
from unittest.mock import AsyncMock

import pytest

import src.engine as engine_module
from src.core.models import CallSession
from tests.test_attended_transfer_streaming import _build_engine


SNAPSHOT_DESTINATIONS = {
    "support_agent": {
        "type": "extension",
        "target": "6000",
        "description": "Snapshot desk",
        "attended_allowed": True,
    }
}


def _snapshot(attended, destinations=SNAPSHOT_DESTINATIONS):
    return {"tools": {"attended_transfer": attended, "transfer": {"destinations": destinations}}}


async def _ringing_session(engine, call_id, *, snapshot=None):
    session = CallSession(call_id=call_id, caller_channel_id=f"{call_id}-caller")
    if snapshot is not None:
        session.tool_runtime_config = snapshot
    session.current_action = {"type": "attended_transfer", "agent_channel_id": f"{call_id}-ringing"}
    await engine.session_store.upsert_call(session)
    engine.register_attended_transfer_agent_channel(call_id, f"{call_id}-ringing")
    return session


class _Fakes:
    """Record what the handler does at each media/TTS/DTMF step."""

    def __init__(self, engine, monkeypatch, *, digit):
        self.tts = []
        self.plays = []
        self.dtmf_timeouts = []
        self.finalized = []
        self.aborts = []
        self.hangups = []
        self.digit = digit
        monkeypatch.setattr(engine, "_local_ai_server_tts", self._tts)
        monkeypatch.setattr(engine, "_play_ulaw_bytes_on_channel_and_wait", self._play)
        monkeypatch.setattr(engine, "_wait_for_attended_transfer_dtmf", self._wait)
        monkeypatch.setattr(engine, "_attended_transfer_finalize_bridge", self._finalize)
        monkeypatch.setattr(engine, "_attended_transfer_abort_and_resume", self._abort)
        monkeypatch.setattr(engine.ari_client, "hangup_channel", self._hangup)

    async def _tts(self, *, call_id, text, timeout_sec):
        self.tts.append((text, timeout_sec))
        return b"\xff" * 160

    async def _play(self, *, channel_id, audio_bytes, playback_id_prefix, timeout_sec):
        self.plays.append((channel_id, playback_id_prefix, timeout_sec))
        return f"{playback_id_prefix}-ok"

    async def _wait(self, agent_channel_id, *, timeout_sec):
        self.dtmf_timeouts.append(timeout_sec)
        return self.digit

    async def _finalize(self, session_obj, **kwargs):
        self.finalized.append(kwargs)

    async def _abort(self, session_obj, agent_channel_id, *, reason):
        self.aborts.append(reason)

    async def _hangup(self, channel_id):
        self.hangups.append(channel_id)


async def _answer(engine, call_id, destination="support_agent"):
    await engine._handle_attended_transfer_answered(
        f"{call_id}-agent", ["attended-transfer", call_id, destination]
    )


@pytest.mark.asyncio
async def test_snapshot_enabled_proceeds_when_global_disabled(monkeypatch):
    # A reload that disables attended transfer must not hang up a leg that
    # this call's own settings originated.
    engine = _build_engine({"enabled": False})
    session = await _ringing_session(engine, "call-snap-on", snapshot=_snapshot({"enabled": True}))
    fakes = _Fakes(engine, monkeypatch, digit="1")

    await _answer(engine, session.call_id)

    assert fakes.hangups == []
    assert len(fakes.finalized) == 1
    assert session.current_action["decision"] == "accepted"


@pytest.mark.asyncio
async def test_snapshot_templates_digits_and_timeouts_win_over_global(monkeypatch):
    engine = _build_engine({
        "enabled": True,
        "accept_digit": "1",
        "decline_digit": "2",
        "announcement_template": "Global {destination_description}.",
        "agent_accept_prompt_template": "Global press 1.",
        "accept_timeout_seconds": 15,
        "tts_timeout_seconds": 8,
        "caller_connected_prompt": "Global connecting.",
    })
    session = await _ringing_session(engine, "call-snap-values", snapshot=_snapshot({
        "enabled": True,
        "accept_digit": "7",
        "decline_digit": "8",
        "announcement_template": "Snapshot call for {destination_description}.",
        "agent_accept_prompt_template": "Press 7 to take it.",
        "accept_timeout_seconds": 4,
        "tts_timeout_seconds": 3,
        "caller_connected_prompt": "Snapshot connecting.",
    }))
    # "7" is a decline under the global digits and an accept under the snapshot.
    fakes = _Fakes(engine, monkeypatch, digit="7")

    await _answer(engine, session.call_id)

    assert fakes.tts == [("Snapshot call for Snapshot desk.", 3.0), ("Press 7 to take it.", 3.0)]
    assert [(p[1], p[2]) for p in fakes.plays] == [("attx-ann", 12.0), ("attx-prompt", 12.0)]
    assert fakes.dtmf_timeouts == [4.0]
    assert fakes.aborts == []
    assert len(fakes.finalized) == 1
    assert fakes.finalized[0]["destination_description"] == "Snapshot desk"
    assert fakes.finalized[0]["caller_connected_prompt"] == "Snapshot connecting."
    assert fakes.finalized[0]["tts_timeout"] == 3.0


@pytest.mark.asyncio
async def test_destination_is_described_from_snapshot(monkeypatch):
    # The agent's transfer policy can narrow or reshape destinations; the
    # global config does not know this key at all.
    engine = _build_engine({"enabled": True})
    session = await _ringing_session(engine, "call-snap-dest", snapshot=_snapshot(
        {"enabled": True},
        destinations={"night_desk": {"type": "extension", "target": "6100",
                                     "description": "Night desk", "attended_allowed": True}},
    ))
    fakes = _Fakes(engine, monkeypatch, digit="1")

    await _answer(engine, session.call_id, destination="night_desk")

    assert fakes.finalized[0]["destination_description"] == "Night desk"


@pytest.mark.asyncio
async def test_empty_snapshot_falls_back_to_global(monkeypatch):
    engine = _build_engine({"enabled": True, "announcement_template": "Global {destination_description}."})
    session = await _ringing_session(engine, "call-no-snap")
    assert session.tool_runtime_config == {}
    fakes = _Fakes(engine, monkeypatch, digit="1")

    await _answer(engine, session.call_id)

    assert fakes.tts[0] == ("Global Support agent.", 8.0)
    assert len(fakes.finalized) == 1


@pytest.mark.asyncio
async def test_empty_snapshot_with_global_disabled_still_hangs_up(monkeypatch):
    engine = _build_engine({"enabled": False})
    session = await _ringing_session(engine, "call-no-snap-off")
    fakes = _Fakes(engine, monkeypatch, digit="1")

    await _answer(engine, session.call_id)

    assert fakes.hangups == ["call-no-snap-off-agent"]
    assert fakes.finalized == []
    assert "call-no-snap-off-agent" not in engine._attended_transfer_agent_channel_to_call_id


@pytest.mark.asyncio
async def test_snapshot_stream_mode_uses_helper_with_snapshot_settings(monkeypatch):
    engine = _build_engine({"enabled": True, "delivery_mode": "file"})
    session = await _ringing_session(engine, "call-snap-stream", snapshot=_snapshot(
        {"enabled": True, "delivery_mode": "stream", "stream_fallback_to_file": True}
    ))
    fakes = _Fakes(engine, monkeypatch, digit="1")
    helper_cfgs = []
    streamed = []

    async def fake_start_helper(*, call_id, agent_channel_id, attended_cfg=None):
        helper_cfgs.append(attended_cfg)
        return {"rtp_session_id": f"attx:{call_id}:{agent_channel_id}"}

    async def fake_stream(agent_channel_id, audio_bytes, *, frame_ms=20):
        streamed.append(agent_channel_id)
        return True

    monkeypatch.setattr(engine, "_start_attended_transfer_helper_media", fake_start_helper)
    monkeypatch.setattr(engine, "_stream_attended_transfer_audio", fake_stream)

    await _answer(engine, session.call_id)

    assert helper_cfgs[0]["delivery_mode"] == "stream"
    assert streamed == ["call-snap-stream-agent", "call-snap-stream-agent"]
    assert fakes.plays == []


@pytest.mark.asyncio
async def test_helper_server_starts_on_demand_with_global_bind(monkeypatch):
    engine = _build_engine({"enabled": True, "delivery_mode": "file"})
    started = []

    class FakeRTPServer:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.running = False

        async def start(self):
            self.running = True
            started.append((self.kwargs["host"], self.kwargs["port"]))

    monkeypatch.setattr(engine_module, "RTPServer", FakeRTPServer)

    # Global delivery_mode is file: startup-style call stays off.
    assert await engine._ensure_attended_transfer_helper_rtp_server_started() is None
    server = await engine._ensure_attended_transfer_helper_rtp_server_started(streaming_requested=True)

    assert isinstance(server, FakeRTPServer)
    # external_media.rtp_host / rtp_port + 100 from the global config.
    assert started == [("127.0.0.1", 18180)]
    assert engine.attended_transfer_rtp_server is server


@pytest.mark.asyncio
async def test_helper_server_start_failure_returns_none(monkeypatch):
    engine = _build_engine({"enabled": True, "delivery_mode": "file"})

    def malformed_settings(attended_cfg=None):
        raise ValueError("invalid literal for int() with base 10: 'abc'")

    monkeypatch.setattr(engine, "_get_attended_transfer_helper_settings", malformed_settings)

    result = await engine._start_attended_transfer_helper_media(
        call_id="call-start-fail",
        agent_channel_id="agent-start-fail",
        attended_cfg={"enabled": True, "delivery_mode": "stream"},
    )

    assert result is None
    assert engine.attended_transfer_rtp_server is None


@pytest.mark.asyncio
async def test_abort_plays_snapshot_declined_prompt_with_snapshot_timeout(monkeypatch):
    engine = _build_engine({"enabled": True, "caller_declined_prompt": "Global prompt.", "tts_timeout_seconds": 8})
    session = CallSession(call_id="call-snap-abort", caller_channel_id="caller-snap-abort")
    session.tool_runtime_config = _snapshot({
        "enabled": True,
        "caller_declined_prompt": "Sorry, {destination_description} is busy.",
        "tts_timeout_seconds": 2.5,
    })
    session.current_action = {
        "type": "attended_transfer",
        "agent_channel_id": "agent-snap-abort",
        "target_name": "Snapshot desk",
    }
    session.audio_capture_enabled = False
    await engine.session_store.upsert_call(session)
    engine.register_attended_transfer_agent_channel(session.call_id, "agent-snap-abort")
    tts = AsyncMock(return_value=b"\xff" * 160)
    play = AsyncMock(return_value="attx-decline-ok")
    monkeypatch.setattr(engine, "_local_ai_server_tts", tts)
    monkeypatch.setattr(engine, "_play_ulaw_bytes_on_channel_and_wait", play)
    monkeypatch.setattr(engine.ari_client, "hangup_channel", AsyncMock())
    monkeypatch.setattr(engine.ari_client, "send_command", AsyncMock())

    await engine._attended_transfer_abort_and_resume(session, "agent-snap-abort", reason="declined")

    tts.assert_awaited_once_with(call_id=session.call_id, text="Sorry, Snapshot desk is busy.", timeout_sec=2.5)
    assert play.call_args.kwargs["channel_id"] == "caller-snap-abort"
    assert play.call_args.kwargs["timeout_sec"] == 10.0
    assert session.current_action is None
    assert session.audio_capture_enabled is True


def test_google_disconnect_budget_reads_call_snapshot():
    engine = _build_engine({"enabled": True})
    session = CallSession(call_id="call-budget", caller_channel_id="caller-budget")
    session.tool_runtime_config = {"tools": {"attended_transfer": {
        "dial_timeout_seconds": 60, "caller_screening_max_seconds": 12,
        "ai_briefing_timeout_seconds": 4, "agent_accept_timeout_seconds": 30,
        "tts_timeout_seconds": 10,
    }}}

    # 60 + 12 + 4 + 30 + 20 * 10 + 10
    assert engine._google_disconnect_transfer_wait_sec(session) == 316
    # Global defaults: 30 + 6 + 2 + 15 + 20 * 8 + 10
    assert engine._google_disconnect_transfer_wait_sec() == 223
