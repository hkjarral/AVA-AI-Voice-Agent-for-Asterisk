"""Issue #853: bounded, call-local gain and meaningful audio diagnostics."""

import asyncio
import audioop
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest
from pydantic import ValidationError

from src.config import (DeepgramProviderConfig, GoogleProviderConfig,
                        GrokProviderConfig, OpenAIRealtimeProviderConfig)
from src.core.input_gain import InputGainState
from src.engine import Engine
from src.providers.elevenlabs_config import ElevenLabsAgentConfig


def frame(rms, rate=8000, ms=20):
    count = rate * ms // 1000
    return struct.pack(f'<{count}h', *([rms, -rms] * (count // 2)))


@pytest.fixture
def engine():
    engine = Engine.__new__(Engine)
    engine._resample_state_provider_in = {}
    return engine


@pytest.fixture
def logs(monkeypatch):
    logger = Mock()
    monkeypatch.setattr('src.engine.logger', logger)
    return logger


def provider(*, target=2500, maximum=24, floor=200, encoding='linear16', rate=8000):
    return SimpleNamespace(config=SimpleNamespace(
        input_gain_target_rms=target, input_gain_max_db=maximum,
        input_gain_noise_floor_rms=floor,
        provider_input_encoding=encoding, provider_input_sample_rate_hz=rate,
    ))


def encode(engine, pcm, config=None, call='call-1', name='google_live', rate=8000):
    return engine._encode_for_provider(call, name, config or provider(), pcm, rate)


@pytest.mark.parametrize('target,maximum', [(0, 0), (0, 24), (2500, 0)])
def test_disabled_gain_preserves_bytes(engine, target, maximum):
    pcm = frame(100)
    assert encode(engine, pcm, provider(target=target, maximum=maximum)) == (pcm, 'slin16', 8000)
    assert not engine._resample_state_provider_in['call-1']


def test_mulaw_branch_remains_unchanged(engine):
    pcm = frame(100)
    assert encode(engine, pcm, provider(encoding='ulaw')) == (
        audioop.lin2ulaw(pcm, 2), 'ulaw', 8000
    )
    assert not engine._resample_state_provider_in['call-1']


def test_speech_envelope_is_not_flattened(engine):
    outputs = [audioop.rms(encode(engine, frame(r))[0], 2) for r in (1793, 595, 237, 368)]
    assert max(outputs) / min(outputs) > 5
    assert all(r != 2500 for r in outputs)


@pytest.mark.parametrize('rate', [8000, 16000, 24000])
def test_noise_and_digital_silence_never_raise_gain(rate):
    state = InputGainState('test', 2500, 24)
    for rms in (0, 10, 11, 100, 200):
        pcm = frame(rms, rate)
        assert state.process(pcm, rate) == pcm
    assert state.gain == 1
    assert state.active_seconds == 0


def test_noise_floor_recovers_to_unity_after_speech():
    state = InputGainState('test', 2500, 24)
    for _ in range(200):
        state.process(frame(250), 8000)
    before = state.gain
    for _ in range(10):
        output = state.process(frame(100), 8000)
        assert state.gain < before
        before = state.gain
    assert audioop.rms(output, 2) <= 101
    assert state.process(frame(0), 8000) == frame(0)


def test_gain_ramps_across_boundary_instead_of_stepping():
    state = InputGainState('test', 2500, 24)
    for _ in range(80):
        state.process(frame(300), 8000)
    previous_gain = state.gain
    output = state.process(frame(1000), 8000)
    samples = np.frombuffer(output, dtype='<i2')
    assert samples[0] / 1000 == pytest.approx(previous_gain, abs=0.08)
    # Within the frame the gain changes smoothly, rather than one fixed multiplier.
    assert abs(samples[-1]) < abs(samples[0])
    gains = np.abs(samples.astype(float)) / 1000
    assert np.max(np.abs(np.diff(gains))) < 0.08


def test_gain_timing_is_sample_rate_independent():
    gains = []
    for rate in (8000, 16000, 24000):
        state = InputGainState('test', 2500, 12)
        for _ in range(100):
            state.process(frame(300, rate), rate)
        gains.append(state.gain)
    assert gains == pytest.approx([gains[0]] * 3)
    assert 1 < gains[0] <= 10 ** (12 / 20)


def test_empty_and_loud_input_and_peak_saturation():
    state = InputGainState('test', 2500, 24)
    assert state.process(b'', 8000) == b''
    assert state.frames == 0
    assert state.process(frame(4000), 8000) == frame(4000)
    for _ in range(200):
        state.process(frame(250), 8000)
    output = state.process(struct.pack('<160h', *([30000, -30000] * 80)), 8000)
    samples = np.frombuffer(output, dtype='<i2')
    assert np.all(samples[::2] > 0)
    assert np.all(samples[1::2] < 0)
    assert np.max(np.abs(samples.astype(np.int32))) <= 32767
    assert state.gain <= 32767 / 30000


def test_excessive_gain_warns_once_and_summarizes_once(engine, logs):
    cfg = provider(floor=20)
    for _ in range(250):
        encode(engine, frame(100), cfg)
    assert logs.warning.call_count == 1
    assert logs.info.call_count == 0
    state = engine._resample_state_provider_in['call-1']['gain:google_live']
    expected = state.excessive_frames
    assert expected > 50
    engine._finish_provider_input_gain('call-1')
    engine._finish_provider_input_gain('call-1')
    assert logs.info.call_count == 1
    assert logs.info.call_args.kwargs['excessive_gain_frames'] == expected
    assert logs.info.call_args.kwargs['active_audio_rms'] == 100
    assert not engine._resample_state_provider_in['call-1']


def test_state_isolated_by_call_and_provider(engine):
    for _ in range(100):
        encode(engine, frame(300))
    fresh = encode(engine, frame(300), call='call-2')[0]
    other_provider = encode(engine, frame(300), name='other-google')[0]
    assert fresh == other_provider
    assert audioop.rms(encode(engine, frame(300))[0], 2) > audioop.rms(fresh, 2) * 2
    engine._finish_provider_input_gain('call-1', 'google_live')
    assert 'gain:other-google' in engine._resample_state_provider_in['call-1']
    assert encode(engine, frame(300))[0] == fresh


@pytest.mark.parametrize('failure', [None, RuntimeError('stop failed'), asyncio.CancelledError()])
async def test_provider_stop_releases_state_on_success_error_and_cancellation(engine, logs, failure):
    encode(engine, frame(300))
    remote = SimpleNamespace(stop_session=AsyncMock(side_effect=failure))
    if failure is None:
        await engine._stop_call_provider_instance('call-1', remote, 'google_live')
    else:
        with pytest.raises(type(failure)):
            await engine._stop_call_provider_instance('call-1', remote, 'google_live')
    assert not engine._resample_state_provider_in['call-1']
    summaries = [call for call in logs.info.call_args_list if call.args[0] == 'Provider input gain summary']
    assert len(summaries) == 1


def diagnose(engine, session, rms, *, stage='provider_in', rate=8000, ms=20, encoding='slin16'):
    pcm = frame(rms, rate, ms)
    audio = audioop.lin2ulaw(pcm, 2) if encoding == 'ulaw' else pcm
    engine._update_audio_diagnostics(session, stage, audio, encoding, rate)


def test_startup_silence_and_normal_speech_do_not_alert(engine, logs):
    session = SimpleNamespace(call_id='c', audio_diagnostics={})
    for _ in range(100):
        diagnose(engine, session, 0)
    for _ in range(100):
        diagnose(engine, session, 1000)
    assert logs.warning.call_count == 0


@pytest.mark.parametrize('rate,ms', [(8000, 10), (16000, 20), (24000, 40)])
def test_low_energy_requires_one_second_of_nonzero_audio(engine, logs, rate, ms):
    session = SimpleNamespace(call_id='c', audio_diagnostics={})
    for _ in range(1000 // ms - 1):
        diagnose(engine, session, 100, rate=rate, ms=ms)
        diagnose(engine, session, 0, rate=rate, ms=ms)
    assert logs.warning.call_count == 0
    diagnose(engine, session, 100, rate=rate, ms=ms)
    assert logs.warning.call_count == 1
    for _ in range(100):
        diagnose(engine, session, 100, rate=rate, ms=ms)
    assert logs.warning.call_count == 1


def test_energy_window_recovers_and_isolated_by_stage_and_call(engine, logs):
    first = SimpleNamespace(call_id='first', audio_diagnostics={})
    second = SimpleNamespace(call_id='second', audio_diagnostics={})
    for _ in range(25):
        diagnose(engine, first, 100)
        diagnose(engine, first, 1000)
        diagnose(engine, second, 0)
    assert logs.warning.call_count == 0
    for _ in range(50):
        diagnose(engine, first, 100)
        diagnose(engine, first, 1000, stage='transport_in')
    assert logs.warning.call_count == 1
    assert logs.warning.call_args.kwargs['call_id'] == 'first'
    assert logs.warning.call_args.kwargs['stage'] == 'provider_in'


def test_mulaw_low_energy_window_uses_decoded_pcm(engine, logs):
    session = SimpleNamespace(call_id='c', audio_diagnostics={})
    for _ in range(50):
        diagnose(engine, session, 20, encoding='ulaw')
    assert logs.warning.call_count == 1
    assert logs.warning.call_args.kwargs['threshold'] == 50


@pytest.mark.parametrize('config_class', [GoogleProviderConfig, OpenAIRealtimeProviderConfig,
                                        DeepgramProviderConfig, GrokProviderConfig])
def test_noise_floor_config_defaults_and_validation(config_class):
    assert config_class().input_gain_noise_floor_rms == 200
    assert config_class(input_gain_noise_floor_rms=0).input_gain_noise_floor_rms == 0
    assert config_class(input_gain_noise_floor_rms=300).input_gain_noise_floor_rms == 300
    for invalid in (-1, 32768):
        with pytest.raises(ValidationError):
            config_class(input_gain_noise_floor_rms=invalid)


def test_elevenlabs_noise_floor_survives_loader():
    assert ElevenLabsAgentConfig.from_dict({}).input_gain_noise_floor_rms == 200
    assert ElevenLabsAgentConfig.from_dict({'input_gain_noise_floor_rms': 350}).input_gain_noise_floor_rms == 350


@pytest.mark.parametrize('invalid', [-1, 32768])
def test_elevenlabs_rejects_invalid_noise_floor(invalid):
    with pytest.raises(ValueError):
        ElevenLabsAgentConfig.from_dict({'input_gain_noise_floor_rms': invalid})


def test_configured_floor_and_resampling_are_used_by_engine(engine):
    pcm = frame(300)
    muted_gain, encoding, rate = encode(engine, pcm, provider(floor=400, rate=16000))
    assert (encoding, rate, len(muted_gain)) == ('slin16', 16000, 640)
    state = engine._resample_state_provider_in['call-1']['gain:google_live']
    assert state.gain == 1
    assert state.below_floor_frames == 1
    assert state.noise_floor_rms == 400
    assert 'google_live:16000' in engine._resample_state_provider_in['call-1']


def test_smoothing_uses_duration_for_different_frame_sizes():
    gains = []
    for ms in (10, 20, 40):
        state = InputGainState('test', 2500, 12)
        for _ in range(4000 // ms):
            state.process(frame(300, ms=ms), 8000)
        gains.append(state.gain)
    assert max(gains) - min(gains) < 0.01


def test_floor_zero_can_admit_very_quiet_audio():
    state = InputGainState('test', 2500, 12, noise_floor_rms=0)
    for _ in range(250):
        output = state.process(frame(100), 8000)
    assert audioop.rms(output, 2) > 350
    assert state.below_floor_frames == 0
