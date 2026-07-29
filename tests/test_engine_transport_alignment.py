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
    engine.provider_kinds = {}
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


@pytest.mark.parametrize("provider_name", ["grok", "grok3", "openai_realtime", "deepgram", "elevenlabs_agent"])
def test_externalmedia_forwards_gated_audio_to_native_barge_in_providers(provider_name):
    engine = _make_engine()
    capabilities = types.SimpleNamespace(
        requires_continuous_audio=True,
        has_native_vad=True,
        has_native_barge_in=True,
    )

    assert engine._externalmedia_continuous_input_mode(
        provider_name,
        capabilities,
        audio_capture_enabled=False,
    ) == "forward"


def test_externalmedia_keeps_google_silence_gating_during_output():
    engine = _make_engine()
    capabilities = types.SimpleNamespace(
        requires_continuous_audio=True,
        has_native_vad=True,
        has_native_barge_in=True,
    )

    assert engine._externalmedia_continuous_input_mode(
        "google_live",
        capabilities,
        audio_capture_enabled=False,
    ) == "silence"


def test_externalmedia_drops_gated_audio_without_native_barge_in():
    engine = _make_engine()
    capabilities = types.SimpleNamespace(
        requires_continuous_audio=True,
        has_native_vad=True,
        has_native_barge_in=False,
    )

    assert engine._externalmedia_continuous_input_mode(
        "legacy_full_agent",
        capabilities,
        audio_capture_enabled=False,
    ) == "drop"
