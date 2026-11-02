import audioop
import random
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.core import upstream_speech_evidence as module
from src.core.upstream_speech_evidence import UpstreamSpeechEvidence
from tests.test_upstream_squelch import setup, nonzero, frame, send
from src.audio.audiosocket_protocol import AUDIO_TYPE_TO_FORMAT, AudioSocketAudioFrame


@pytest.mark.parametrize("rate", [8000, 12000, 16000, 24000, 32000, 44100, 48000, 96000, 192000])
@pytest.mark.parametrize("ptime", [5, 10, 20, 30, 60])
def test_real_detector_frames_all_audiosocket_rates_without_growing_buffers(rate, ptime):
    detector = UpstreamSpeechEvidence()
    assert detector.available, "Install core requirements, including WebRTC/pkg_resources"
    decisions = []
    for _ in range(1000 // ptime):
        # Packet sizes alternate naturally at rates such as 44.1 kHz / 5 ms.
        samples = rate * ptime // 1000
        decisions.append(detector.observe(b"\0\0" * samples, rate))
        assert len(detector._pending) < 960  # Less than 10 ms at the largest VAD rate.
    assert detector.available
    assert False in decisions[-max(5, 100 // ptime):]


def test_short_fragments_are_unknown_not_non_speech_and_pause_discards_them(monkeypatch):
    vad = Mock()
    vad.is_speech.return_value = False
    factory = Mock(return_value=vad)
    monkeypatch.setattr(module, "webrtcvad", SimpleNamespace(Vad=factory))
    detector = UpstreamSpeechEvidence()
    assert detector.observe(b"\0\0" * 80, 16000) is None  # 5 ms
    vad.is_speech.assert_not_called()
    assert detector.observe(b"\0\0" * 80, 16000) is False
    assert vad.is_speech.call_args.args == (b"\0\0" * 160, 16000)
    detector.observe(b"\1\0" * 80, 16000)
    detector.pause()
    assert detector.observe(b"\0\0" * 80, 16000) is None
    assert factory.call_count == 2


def test_rate_change_creates_fresh_detector_and_failed_evidence_is_unknown(monkeypatch):
    factory = Mock(side_effect=lambda mode: Mock(is_speech=Mock(return_value=True)))
    monkeypatch.setattr(module, "webrtcvad", SimpleNamespace(Vad=factory))
    detector = UpstreamSpeechEvidence()
    assert detector.observe(b"\0\0" * 160, 16000) is True
    first = detector._vad
    assert detector.observe(b"\0\0" * 80, 8000) is True
    assert detector._vad is not first
    detector._vad.is_speech.side_effect = RuntimeError("classifier failed")
    assert detector.observe(b"\0\0" * 80, 8000) is None
    assert not detector.available
    assert detector._pending == b""


@pytest.mark.parametrize("pcm,rate", [(b"x", 16000), (b"\0" * 640, 0), (b"\0" * 4000, 16000)])
def test_invalid_or_oversized_evidence_is_bounded_and_unknown(pcm, rate):
    detector = UpstreamSpeechEvidence()
    assert detector.observe(pcm, rate) is None
    assert not detector._pending


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "failed"])
async def test_unavailable_detector_preserves_provider_audio_and_warns_once(monkeypatch, failure):
    logger = Mock()
    monkeypatch.setattr("src.engine.logger", logger)
    if failure == "missing":
        monkeypatch.setattr(module, "webrtcvad", None)
    else:
        monkeypatch.setattr(module, "webrtcvad", SimpleNamespace(Vad=Mock(side_effect=RuntimeError("failed"))))
    engine, session, provider = setup(evidence=None)
    await engine.session_store.upsert_call(session)
    await send(engine, [100] * 10)
    assert nonzero(provider) == 10
    assert session.vad_state["upstream_squelch"]["suppressed_frames"] == 0
    logger.warning.assert_called_once_with(
        "Upstream squelch bypassed: speech evidence unavailable", call_id=session.call_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("rate,message_type,encoding", [(8000, 0x10, "slin"), (16000, 0x12, "slin16")])
@pytest.mark.parametrize("duration_ms", [5, 200])
@pytest.mark.parametrize("prior_floor", [0.0, 1000.0])
async def test_unknown_packet_evidence_preserves_whole_utterance(
    rate, message_type, encoding, duration_ms, prior_floor,
):
    engine, session, provider = setup(evidence=None)
    session.vad_state["upstream_squelch"] = {"noise_ema": prior_floor}
    await engine.session_store.upsert_call(session)
    payload = struct.pack("<hh", 1500, -1500) * (rate * duration_ms // 2000)
    await engine._audiosocket_handle_audio(
        "ingress", AudioSocketAudioFrame(payload, message_type, encoding, rate),
    )
    assert session.upstream_speech_evidence.available
    state = session.vad_state["upstream_squelch"]
    assert state["speech_evidence"] is None
    assert provider.send_audio.await_args.args[0] == payload
    assert state["frames"] == 1
    assert state["suppressed_frames"] == 0
    if prior_floor:
        # An unknown packet below the learned threshold must not raise it and
        # mute the next confirmed speech just above that prior threshold.
        session.upstream_speech_evidence.observe = Mock(return_value=True)
        provider.send_audio.reset_mock()
        await send(engine, [2550] * 3)
        assert nonzero(provider) == 2
        assert state["noise_ema"] == prior_floor


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["alternating", "white_noise", "tone"])
async def test_real_noise_does_not_hold_squelch_open_and_real_speech_recovers(kind):
    import math

    engine, session, provider = setup(evidence=None)
    await engine.session_store.upsert_call(session)
    rng = random.Random(799)
    for n in range(100):
        if kind == "alternating":
            payload = frame(600).payload
        else:
            samples = [rng.randint(-1039, 1039) for _ in range(320)] if kind == "white_noise" else [
                int(850 * math.sin(2 * math.pi * 440 * (n * 320 + i) / 16000)) for i in range(320)
            ]
            payload = struct.pack("<320h", *samples)
        await engine._audiosocket_handle_audio("ingress", AudioSocketAudioFrame(payload, 0x12, "slin16", 16000))
    assert session.upstream_speech_evidence.available
    assert nonzero(provider) < 40
    assert all(not any(c.args[0]) for c in provider.send_audio.await_args_list[-50:])
    assert not session.vad_state["upstream_squelch"]["speaking"]
    # Start mid-utterance: there are no quiet onset frames to calibrate against.
    speech = (Path(__file__).parent / "fixtures/webrtcvad/test-audio.raw").read_bytes()[4800:]
    speech, _ = audioop.ratecv(speech, 2, 1, 8000, 16000, None)
    provider.send_audio.reset_mock()
    for pos in range(0, len(speech) - 639, 640):
        await engine._audiosocket_handle_audio(
            "ingress", AudioSocketAudioFrame(speech[pos:pos + 640], 0x12, "slin16", 16000),
        )
    assert nonzero(provider) >= 8


@pytest.mark.asyncio
@pytest.mark.parametrize("quiet", [False, True])
@pytest.mark.parametrize("message_type", list(AUDIO_TYPE_TO_FORMAT))
async def test_real_speech_first_frame_without_quiet_onset(quiet, message_type):
    engine, session, provider = setup(evidence=None)
    await engine.session_store.upsert_call(session)
    speech = (Path(__file__).parent / "fixtures/webrtcvad/test-audio.raw").read_bytes()[4800:]
    if quiet:
        speech = audioop.mul(speech, 2, 0.3)
    encoding, rate = AUDIO_TYPE_TO_FORMAT[message_type]
    if rate != 8000:
        speech, _ = audioop.ratecv(speech, 2, 1, 8000, rate, None)
    frame_bytes = rate * 2 // 50
    for pos in range(0, len(speech) - frame_bytes + 1, frame_bytes):
        await engine._audiosocket_handle_audio(
            "ingress", AudioSocketAudioFrame(speech[pos:pos + frame_bytes], message_type, encoding, rate),
        )
    assert session.upstream_speech_evidence.available
    assert nonzero(provider) >= (7 if quiet else 12)


def test_real_detectors_are_independent_between_calls():
    first, second = UpstreamSpeechEvidence(), UpstreamSpeechEvidence()
    first.observe(b"\0\0" * 160, 16000)
    second.observe(b"\0\0" * 160, 16000)
    assert first._vad is not second._vad
    first.observe(b"\1\0" * 80, 16000)
    assert first._pending and not second._pending
    first.pause()
    assert second._vad is not None
