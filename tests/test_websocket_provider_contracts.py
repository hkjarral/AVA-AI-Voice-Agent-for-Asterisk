"""Contract coverage for the shared canonical transport PCM ingress bus.

These tests intentionally enter :meth:`Engine._on_transport_pcm` after a
transport has decoded its wire format.  That is the boundary shared by
WebSocket and ExternalMedia: providers and modular pipelines must only see
call-scoped canonical PCM16, never a transport-specific codec or another
call's audio.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.engine import Engine


class _Provider:
    """Network-free full-agent stand-in with explicit capability metadata."""

    def __init__(self, capabilities):
        self._capabilities = capabilities
        self.config = SimpleNamespace(
            provider_input_encoding="linear16",
            provider_input_sample_rate_hz=16000,
        )
        self.send_audio = AsyncMock()

    def get_capabilities(self):
        return self._capabilities


def _capabilities(*, native_vad=True, native_barge_in=True):
    return SimpleNamespace(
        requires_continuous_audio=True,
        has_native_vad=native_vad,
        has_native_barge_in=native_barge_in,
    )


def _engine() -> Engine:
    """Small Engine surface required by _on_transport_pcm, without I/O."""
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(
        audio_transport="websocket",
        default_provider="default-provider",
        barge_in=SimpleNamespace(enabled=True),
    )
    engine.session_store = SessionStore()
    engine.conversation_coordinator = None
    engine.no_input_watchdog = None
    engine._agent_output_active_calls = set()
    engine._attended_transfer_screening_state_by_call = {}
    engine._pipeline_forced = {}
    engine._pipeline_queues = {}
    engine._provider_start_tasks = {}
    engine._call_providers = {}
    engine.providers = {}
    engine.provider_kinds = {}
    engine._resample_state_provider_in = {}
    engine.audio_capture = SimpleNamespace(append_encoded=lambda *_args, **_kwargs: None)
    engine._maybe_provider_barge_in_fallback = AsyncMock()
    return engine


async def _register_session(
    engine: Engine,
    call_id: str,
    *,
    provider_name: str,
    capture_enabled: bool,
    wire_encoding: str = "slin16",
    wire_rate: int = 16000,
) -> CallSession:
    session = CallSession(
        call_id=call_id,
        caller_channel_id=call_id,
        provider_name=provider_name,
        provider_session_active=True,
        audio_capture_enabled=capture_enabled,
    )
    # This is deliberately distinct for each call in the queue-isolation test.
    # It must not affect the canonical PCM data entering this shared method.
    session.transport_profile = SimpleNamespace(
        wire_encoding=wire_encoding,
        wire_sample_rate=wire_rate,
    )
    await engine.session_store.upsert_call(session)
    return session


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_name",
    ["openai_realtime", "deepgram", "elevenlabs_agent", "grok"],
)
async def test_websocket_common_ingress_forwards_gated_caller_pcm_to_native_full_agents(
    provider_name,
):
    """Native VAD/barge-in agents retain caller audio while output is gated."""
    engine = _engine()
    provider = _Provider(_capabilities())
    engine._call_providers["call-1"] = provider
    engine.provider_kinds[provider_name] = provider_name
    await _register_session(
        engine,
        "call-1",
        provider_name=provider_name,
        capture_enabled=False,
    )
    pcm16 = b"\x34\x12" * 160

    await engine._on_transport_pcm("call-1", pcm16, 16000, source="websocket")

    provider.send_audio.assert_awaited_once_with(
        pcm16, sample_rate=16000, encoding="slin16"
    )


@pytest.mark.asyncio
async def test_websocket_common_ingress_sends_google_silence_but_locally_gates_non_native_provider():
    """Google keeps its stream alive with silence; local ownership keeps audio gated."""
    engine = _engine()
    google = _Provider(_capabilities())
    local = _Provider(_capabilities(native_vad=False, native_barge_in=False))
    engine._call_providers.update({"google-call": google, "local-call": local})
    engine.provider_kinds.update({"google_live": "google_live", "local": "local"})
    await _register_session(
        engine,
        "google-call",
        provider_name="google_live",
        capture_enabled=False,
    )
    await _register_session(
        engine,
        "local-call",
        provider_name="local",
        capture_enabled=False,
    )
    pcm16 = b"\x34\x12" * 160

    await engine._on_transport_pcm("google-call", pcm16, 16000, source="websocket")
    await engine._on_transport_pcm("local-call", pcm16, 16000, source="websocket")

    google.send_audio.assert_awaited_once_with(
        b"\x00" * len(pcm16), sample_rate=16000, encoding="slin16"
    )
    local.send_audio.assert_not_awaited()
    assert engine._maybe_provider_barge_in_fallback.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("wire_encoding", "wire_rate"),
    [("ulaw", 8000), ("alaw", 8000), ("slin", 8000), ("slin16", 16000)],
)
async def test_websocket_common_ingress_has_canonical_pcm16_16k_provider_contract(
    wire_encoding,
    wire_rate,
):
    """The decoded WebSocket codec never leaks into the provider input contract."""
    engine = _engine()
    provider = _Provider(_capabilities())
    engine._call_providers["call-1"] = provider
    await _register_session(
        engine,
        "call-1",
        provider_name="openai_realtime",
        capture_enabled=True,
        wire_encoding=wire_encoding,
        wire_rate=wire_rate,
    )
    canonical_pcm16 = b"\x78\x56" * 160

    await engine._on_transport_pcm(
        "call-1", canonical_pcm16, 16000, source="websocket"
    )

    provider.send_audio.assert_awaited_once_with(
        canonical_pcm16, sample_rate=16000, encoding="slin16"
    )


@pytest.mark.asyncio
async def test_local_noncontinuous_provider_receives_canonical_pcm_without_wire_headers():
    engine = _engine()
    capabilities = _capabilities(native_vad=False, native_barge_in=False)
    capabilities.requires_continuous_audio = False
    provider = _Provider(capabilities)
    engine._call_providers["local-call"] = provider
    engine.provider_kinds["local"] = "local"
    session = await _register_session(
        engine, "local-call", provider_name="local", capture_enabled=True,
        wire_encoding="alaw", wire_rate=8000,
    )
    pcm16 = b"\x34\x12" * 320
    assert engine._provider_input_mode_for_transport(session) == "pcm16_16k"
    await engine._on_transport_pcm("local-call", pcm16, 16000, source="websocket")
    provider.send_audio.assert_awaited_once_with(pcm16)


@pytest.mark.asyncio
async def test_websocket_common_ingress_routes_pipeline_audio_by_call_without_profile_cross_talk():
    """Modular pipelines receive only their own canonical frames before providers run."""
    engine = _engine()
    first_provider = _Provider(_capabilities())
    second_provider = _Provider(_capabilities())
    engine._call_providers.update({"call-ulaw": first_provider, "call-slin16": second_provider})
    await _register_session(
        engine,
        "call-ulaw",
        provider_name="openai_realtime",
        capture_enabled=True,
        wire_encoding="ulaw",
        wire_rate=8000,
    )
    await _register_session(
        engine,
        "call-slin16",
        provider_name="deepgram",
        capture_enabled=True,
        wire_encoding="slin16",
        wire_rate=16000,
    )
    engine._pipeline_forced.update({"call-ulaw": "pipeline-a", "call-slin16": "pipeline-b"})
    first_queue: asyncio.Queue[bytes] = asyncio.Queue()
    second_queue: asyncio.Queue[bytes] = asyncio.Queue()
    engine._pipeline_queues.update({"call-ulaw": first_queue, "call-slin16": second_queue})
    first_pcm16 = b"\x11\x11" * 160
    second_pcm16 = b"\x22\x22" * 160

    await engine._on_transport_pcm(
        "call-ulaw", first_pcm16, 16000, source="websocket"
    )
    await engine._on_transport_pcm(
        "call-slin16", second_pcm16, 16000, source="websocket"
    )

    assert first_queue.get_nowait() == first_pcm16
    assert second_queue.get_nowait() == second_pcm16
    assert first_queue.empty() and second_queue.empty()
    first_provider.send_audio.assert_not_awaited()
    second_provider.send_audio.assert_not_awaited()
