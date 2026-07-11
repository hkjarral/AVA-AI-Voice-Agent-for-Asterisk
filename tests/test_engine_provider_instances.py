from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.core.models import CallSession
from src.engine import Engine


@pytest.mark.asyncio
async def test_call_history_persists_provider_instance_key(monkeypatch):
    captured = {}

    class FakeCallHistoryStore:
        _enabled = True

        async def save(self, record):
            captured["record"] = record
            return True

        async def get_by_call_id(self, call_id):
            return captured["record"]

    monkeypatch.setattr(
        "src.core.call_history.get_call_history_store",
        lambda: FakeCallHistoryStore(),
    )

    engine = Engine.__new__(Engine)
    session = CallSession(
        call_id="call-1",
        caller_channel_id="call-1",
        provider_name="acme_google_live",
        provider_kind="google_live",
        start_time=datetime.now(timezone.utc),
        conversation_history=[{"role": "user", "content": "hello"}],
    )

    await engine._persist_call_history(session, "call-1")

    assert captured["record"].provider_name == "acme_google_live"


def test_provider_fallback_allowlist_matches_named_instance_by_kind():
    engine = Engine.__new__(Engine)
    engine.provider_kinds = {"grok3": "grok"}

    assert engine._provider_fallback_is_allowed("grok3", {"grok"}) is True
    assert engine._provider_fallback_is_allowed("grok3", {"deepgram"}) is False
    assert engine._provider_fallback_is_allowed("grok3", set()) is True


@pytest.mark.asyncio
async def test_named_grok_instance_triggers_local_vad_fallback():
    engine = Engine.__new__(Engine)
    engine.provider_kinds = {"grok3": "grok"}
    engine.config = SimpleNamespace(
        default_provider="grok",
        barge_in=SimpleNamespace(
            enabled=True,
            provider_fallback_enabled=True,
            provider_fallback_providers=["grok"],
            energy_threshold=1000,
            min_ms=20,
            cooldown_ms=500,
        ),
    )
    engine.streaming_playback_manager = SimpleNamespace(is_stream_active=lambda call_id: True)
    engine.vad_manager = None
    applied = []

    async def apply(call_id, *, source, reason):
        applied.append((call_id, source, reason))

    engine._apply_barge_in_action = apply
    session = SimpleNamespace(
        call_id="call-grok3",
        provider_name="grok3",
        media_rx_confirmed=True,
        vad_state={},
        barge_in_candidate_ms=0,
        barge_start_ts=0.0,
        last_barge_in_ts=0.0,
    )

    await engine._maybe_provider_barge_in_fallback(
        session,
        pcm16=b"\xff\x7f" * 160,
        pcm_rate_hz=16000,
        audiosocket_wire=None,
        source="externalmedia",
    )

    assert applied == [("call-grok3", "local_vad_fallback", "grok3:externalmedia")]


def test_named_grok_instance_inherits_base_runtime_config():
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(
        providers={
            "grok": {
                "api_key": "base-key",
                "voice": "ara",
                "turn_detection": {
                    "type": "server_vad",
                    "silence_duration_ms": 1000,
                    "prefix_padding_ms": 300,
                    "threshold": 0.5,
                },
            }
        },
        llm=SimpleNamespace(prompt="fallback prompt", initial_greeting="fallback greeting"),
    )

    cfg = engine._build_grok_config(
        {
            "type": "grok",
            "api_key": "instance-key",
            "display_name": "Grok Three",
        },
        "grok3",
    )

    assert cfg is not None
    assert cfg.api_key == "instance-key"
    assert cfg.voice == "ara"
    assert cfg.turn_detection is not None
    assert cfg.turn_detection.silence_duration_ms == 1000
    assert cfg.turn_detection.prefix_padding_ms == 300
    assert cfg.display_name == "Grok Three"
