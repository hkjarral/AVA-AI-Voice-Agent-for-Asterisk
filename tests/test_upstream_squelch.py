"""Issue #799: exercise real ingress with PCM and the providers' real capabilities."""
import asyncio
import audioop
import struct
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.audio.audiosocket_protocol import AUDIO_TYPE_TO_FORMAT, AudioSocketAudioFrame
from src.config import VADConfig
from src.providers.deepgram import DeepgramProvider
from src.providers.elevenlabs_agent import ElevenLabsAgentProvider
from src.providers.google_live import GoogleLiveProvider
from src.providers.grok import GrokProvider
from src.providers.local import LocalProvider
from src.providers.openai_realtime import OpenAIRealtimeProvider
from tests.test_google_long_audio_playback import ingress_engine


PROVIDERS = [
    ("openai_realtime", OpenAIRealtimeProvider),
    ("deepgram", DeepgramProvider),
    ("google_live", GoogleLiveProvider),
    ("vertex_live", GoogleLiveProvider),
    ("grok", GrokProvider),
    ("elevenlabs_agent", ElevenLabsAgentProvider),
    ("local", LocalProvider),
]
SPEECH = [2612, 5129, 5815, 5733, 4200, 3900, 2500, 3300, 4100, 3800] * 6


def setup(name="openai_realtime", cls=OpenAIRealtimeProvider, *, evidence=True, **vad):
    engine, session, _ = ingress_engine()
    caps = cls.__new__(cls).get_capabilities()
    provider = SimpleNamespace(
        send_audio=AsyncMock(), get_capabilities=lambda: caps,
        config=SimpleNamespace(llm_model="gemini-2.5-flash-native-audio-latest"),
    )
    # Use a named instance so applicability cannot accidentally depend on its name.
    session.provider_name = "test-instance"
    engine.provider_kinds[session.provider_name] = "google_live" if name == "vertex_live" else name
    engine._call_providers[session.call_id] = provider
    engine.config.vad = VADConfig(**vad)
    if evidence is not None:
        # Square waves test energy/hysteresis, not acoustic speech classification.
        session.upstream_speech_evidence = SimpleNamespace(
            available=True, observe=Mock(return_value=evidence), pause=Mock(),
        )
    engine._maybe_provider_barge_in_fallback = AsyncMock()
    return engine, session, provider


def frame(energy, message_type=0x12, *, ulaw=False):
    encoding, rate = AUDIO_TYPE_TO_FORMAT[message_type]
    pcm = struct.pack("<hh", energy, -energy) * (rate // 100)
    return AudioSocketAudioFrame(
        audioop.lin2ulaw(pcm, 2) if ulaw else pcm,
        message_type, "ulaw" if ulaw else encoding, rate,
    )


async def send(engine, energies, **kwargs):
    for energy in energies:
        await engine._audiosocket_handle_audio("ingress", frame(energy, **kwargs))


def nonzero(provider):
    return sum(bool(any(call.args[0])) for call in provider.send_audio.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("quiet_onset", [[], [20, 173]])
@pytest.mark.parametrize("speech", [SPEECH, [201] * 60, [450] * 60, [550] * 60])
async def test_first_utterance_survives_without_quiet_onset(quiet_onset, speech):
    engine, session, provider = setup()
    await engine.session_store.upsert_call(session)
    await send(engine, [0] * 50 + quiet_onset + speech + [0] * 50)
    assert nonzero(provider) == 59  # The configured two-frame onset still applies.
    state = session.vad_state["upstream_squelch"]
    assert not state["speaking"]
    assert state["frames"] == 160 + len(quiet_onset)
    assert state["suppressed_frames"] == state["frames"] - 73


@pytest.mark.asyncio
@pytest.mark.parametrize("message_type", list(AUDIO_TYPE_TO_FORMAT))
async def test_squelch_uses_pcm_energy_independent_of_audiosocket_rate(message_type):
    engine, session, provider = setup()
    await engine.session_store.upsert_call(session)
    await send(engine, [1000] * 4, message_type=message_type)
    assert nonzero(provider) == 3
    for call in provider.send_audio.await_args_list:
        assert len(call.args[0]) == len(frame(1000, message_type).payload)


@pytest.mark.asyncio
async def test_legacy_ulaw_is_decoded_before_squelch():
    engine, session, provider = setup()
    await engine.session_store.upsert_call(session)
    await send(engine, [1000] * 4, message_type=0x10, ulaw=True)
    assert nonzero(provider) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("alpha", [0.0, 0.06, 0.5, 1.0])
@pytest.mark.parametrize("minimum", [1, 2, 6])
@pytest.mark.parametrize("factor", [0.0, 1.0, 2.5, 10.0])
async def test_candidate_speech_cannot_train_noise_floor(alpha, minimum, factor):
    engine, session, provider = setup(
        upstream_squelch_noise_ema_alpha=alpha,
        upstream_squelch_min_speech_frames=minimum,
        upstream_squelch_noise_factor=factor,
    )
    await engine.session_store.upsert_call(session)
    await send(engine, [201] * (minimum + 2))
    assert nonzero(provider) == 3
    assert session.vad_state["upstream_squelch"]["last_threshold"] == 200


@pytest.mark.asyncio
async def test_noise_spike_tail_and_later_turn_retain_hysteresis():
    engine, session, provider = setup()
    await engine.session_store.upsert_call(session)
    await send(engine, [100] * 50 + [6000] + [100] * 20)
    assert nonzero(provider) == 0
    await send(engine, [6000] * 2)
    assert nonzero(provider) == 1
    floor = session.vad_state["upstream_squelch"]["noise_ema"]
    await send(engine, [100] * 14)
    assert session.vad_state["upstream_squelch"]["speaking"]
    assert session.vad_state["upstream_squelch"]["noise_ema"] == floor
    await send(engine, [100])
    assert not session.vad_state["upstream_squelch"]["speaking"]
    provider.send_audio.reset_mock()
    await send(engine, SPEECH)
    assert nonzero(provider) == 59


@pytest.mark.asyncio
async def test_independent_non_speech_recovers_loud_startup_noise():
    engine, session, provider = setup(evidence=None)
    await engine.session_store.upsert_call(session)
    await send(engine, [600] * 100)
    assert session.upstream_speech_evidence.available
    assert nonzero(provider) < 30  # Initial admission + configured end hysteresis.
    assert all(not any(call.args[0]) for call in provider.send_audio.await_args_list[-50:])
    assert not session.vad_state["upstream_squelch"]["speaking"]
    assert session.vad_state["upstream_squelch"]["noise_ema"] > 500


@pytest.mark.asyncio
async def test_confirmed_speech_preserves_learned_noise_floor():
    engine, session, provider = setup(evidence=False)
    await engine.session_store.upsert_call(session)
    await send(engine, [600] * 100)
    floor = session.vad_state["upstream_squelch"]["noise_ema"]
    assert not session.vad_state["upstream_squelch"]["speaking"]
    session.upstream_speech_evidence.observe.return_value = True
    provider.send_audio.reset_mock()
    await send(engine, [2000] * 60)
    assert nonzero(provider) == 59
    assert session.vad_state["upstream_squelch"]["noise_ema"] == floor


@pytest.mark.asyncio
@pytest.mark.parametrize("name,cls", PROVIDERS)
@pytest.mark.parametrize("mode", ["enabled", "disabled", "gated", "pipeline"])
async def test_provider_capability_and_gating_boundaries(name, cls, mode):
    engine, session, provider = setup(name, cls, upstream_squelch_enabled=mode != "disabled")
    if mode == "gated":
        session.audio_capture_enabled = False
    if mode == "pipeline":
        engine._pipeline_forced[session.call_id] = True
        engine._pipeline_queues[session.call_id] = asyncio.Queue()
    await engine.session_store.upsert_call(session)
    await send(engine, [6000] * 2)
    applicable = mode == "enabled" and name != "local"
    assert ("upstream_squelch" in session.vad_state) is applicable
    if mode == "pipeline":
        provider.send_audio.assert_not_awaited()
        assert engine._pipeline_queues[session.call_id].qsize() == 2
    elif mode == "gated" and name in {"google_live", "vertex_live"}:
        assert nonzero(provider) == 0
    else:
        assert nonzero(provider) == (1 if applicable else 2)


@pytest.mark.asyncio
async def test_greeting_release_does_not_require_quiet_preroll_or_reset_floor():
    engine, session, provider = setup("google_live", GoogleLiveProvider)
    await engine.session_store.upsert_call(session)
    await engine.session_store.set_gating_token(session.call_id, "greeting")
    await send(engine, SPEECH[:4])
    assert "upstream_squelch" not in session.vad_state
    await engine.session_store.clear_gating_token(session.call_id, "greeting")
    provider.send_audio.reset_mock()
    await send(engine, SPEECH)
    assert nonzero(provider) == 59
    state = session.vad_state["upstream_squelch"]
    await engine.session_store.set_gating_token(session.call_id, "response")
    await send(engine, [100] * 4)
    await engine.session_store.clear_gating_token(session.call_id, "response")
    assert session.vad_state["upstream_squelch"] is state
    assert state["frames"] == 60


@pytest.mark.asyncio
@pytest.mark.parametrize("name,cls", PROVIDERS)
@pytest.mark.parametrize("source", ["externalmedia", "websocket"])
@pytest.mark.parametrize("gated", [False, True])
async def test_common_transport_bus_remains_unsquelched(name, cls, source, gated):
    engine, session, provider = setup(name, cls)
    engine.config.audio_transport = source
    session.audio_capture_enabled = not gated
    await engine.session_store.upsert_call(session)
    pcm = frame(6000).payload
    await engine._on_transport_pcm(session.call_id, pcm, 16000, source=source)
    assert "upstream_squelch" not in session.vad_state
    if gated and name == "local":
        provider.send_audio.assert_not_awaited()
    elif gated and name in {"google_live", "vertex_live"}:
        assert nonzero(provider) == 0
        provider.send_audio.assert_awaited_once()
    else:
        provider.send_audio.assert_awaited_once()
        assert provider.send_audio.await_args.args[0] == pcm


@pytest.mark.asyncio
async def test_squelch_diagnostics_distinguish_substitution_from_tts_gating(monkeypatch):
    logger = Mock()
    monkeypatch.setattr("src.engine.logger", logger)
    engine, session, provider = setup()
    await engine.session_store.upsert_call(session)
    await send(engine, [6000] * 2)
    forwarded = [call.kwargs for call in logger.debug.call_args_list
                 if "Forwarding frame to provider" in str(call.args[0])]
    assert len(forwarded) == 2
    assert all(not call["gating_active"] and call["squelch_applicable"] for call in forwarded)
    assert [call["squelch_suppressed"] for call in forwarded] == [True, False]


@pytest.mark.asyncio
async def test_concurrent_call_noise_floors_and_counters_are_isolated():
    engine, session, provider = setup()
    other = deepcopy(session)
    other.call_id = other.caller_channel_id = "other-call"
    other_provider = SimpleNamespace(send_audio=AsyncMock(), get_capabilities=provider.get_capabilities)
    engine.conn_to_channel["other-ingress"] = other.call_id
    engine._call_providers[other.call_id] = other_provider
    await engine.session_store.upsert_call(session)
    await engine.session_store.upsert_call(other)
    for _ in range(20):
        await asyncio.gather(
            engine._audiosocket_handle_audio("ingress", frame(6000)),
            engine._audiosocket_handle_audio("other-ingress", frame(100)),
        )
    assert nonzero(provider) == 19
    assert nonzero(other_provider) == 0
    assert session.vad_state["upstream_squelch"]["suppressed_frames"] == 1
    assert other.vad_state["upstream_squelch"]["suppressed_frames"] == 20
    assert session.vad_state["upstream_squelch"] is not other.vad_state["upstream_squelch"]
