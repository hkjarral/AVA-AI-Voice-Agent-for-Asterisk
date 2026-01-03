import types

import pytest

from src.engine import Engine, _CODEC_ALIGNMENT
from src.core.models import CallSession, LegacyTransportProfile


def _make_session(call_id: str, fmt: str, rate: int) -> CallSession:
    session = CallSession(call_id=call_id, caller_channel_id=f"{call_id}-chan")
    # Engine initializes transport_profile in normal runtime; tests create sessions directly.
    session.transport_profile = LegacyTransportProfile(format=fmt, sample_rate=rate)
    return session


def _make_engine() -> Engine:
    engine = Engine.__new__(Engine)
    engine.providers = {}
    engine._call_providers = {}
    engine.call_audio_preferences = {}
    engine._transport_card_logged = set()
    engine.config = types.SimpleNamespace(default_provider="local")
    return engine


@pytest.mark.parametrize(
    "pref_format,pref_rate,transport_format,transport_rate",
    [
        ("slin16", 16000, "ulaw", 8000),
        ("pcm16", 8000, "ulaw", 8000),
        ("ulaw", 8000, "slin16", 16000),
    ],
)
def test_resolve_stream_targets_resets_preferences(pref_format, pref_rate, transport_format, transport_rate):
    engine = _make_engine()
    call_id = "call-pref"
    engine.call_audio_preferences[call_id] = {"format": pref_format, "sample_rate": pref_rate}
    engine.providers["deepgram"] = types.SimpleNamespace(
        config=types.SimpleNamespace(target_encoding=transport_format, target_sample_rate_hz=transport_rate)
    )

    session = _make_session(call_id, transport_format, transport_rate)

    target_fmt, target_rate, remediation = engine._resolve_stream_targets(session, "deepgram")

    assert target_fmt == transport_format
    assert target_rate == transport_rate
    assert engine.call_audio_preferences[call_id]["format"] == transport_format
    assert engine.call_audio_preferences[call_id]["sample_rate"] == transport_rate
    assert remediation is None
    _CODEC_ALIGNMENT.remove("deepgram")


def test_resolve_stream_targets_detects_provider_mismatch():
    engine = _make_engine()
    call_id = "call-mismatch"
    engine.providers["openai_realtime"] = types.SimpleNamespace(
        config=types.SimpleNamespace(target_encoding="slin16", target_sample_rate_hz=16000)
    )

    session = _make_session(call_id, "ulaw", 8000)

    target_fmt, target_rate, remediation = engine._resolve_stream_targets(session, "openai_realtime")

    assert target_fmt == "ulaw"
    assert target_rate == 8000
    assert remediation is not None
    assert "target_encoding" in remediation
    assert "target_sample_rate_hz" in remediation
    assert session.codec_alignment_ok is False
    _CODEC_ALIGNMENT.remove("openai_realtime")


def test_resolve_stream_targets_pass_through_when_aligned():
    engine = _make_engine()
    call_id = "call-aligned"
    engine.providers["openai_realtime"] = types.SimpleNamespace(
        config=types.SimpleNamespace(target_encoding="ulaw", target_sample_rate_hz=8000)
    )

    session = _make_session(call_id, "ulaw", 8000)

    target_fmt, target_rate, remediation = engine._resolve_stream_targets(session, "openai_realtime")

    assert target_fmt == "ulaw"
    assert target_rate == 8000
    assert remediation is None
    assert session.codec_alignment_ok is True
    _CODEC_ALIGNMENT.remove("openai_realtime")
