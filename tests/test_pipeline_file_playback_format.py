"""Pipeline file playback (`sound:` .ulaw) must receive mu-law 8 kHz audio."""

import audioop
import struct

from src.engine import Engine


def _pcm16(samples):
    return struct.pack("<" + "h" * len(samples), *samples)


def test_pcm16_16k_tts_output_is_converted_to_ulaw_8k():
    tts_options = {"format": {"encoding": "slin16", "sample_rate": 16000}}
    pcm = _pcm16([0, 1000, 2000, 3000] * 40)  # 160 samples = 10 ms @ 16 kHz

    out = Engine._pipeline_audio_for_file_playback(tts_options, pcm)

    assert len(out) == 80  # 10 ms @ 8 kHz mu-law
    assert out != pcm[:80]


def test_pcm16_8k_tts_output_is_only_ulaw_encoded():
    tts_options = {"format": {"encoding": "linear16", "sample_rate": 8000}}
    pcm = _pcm16([0, 1000, -1000, 3000] * 20)

    out = Engine._pipeline_audio_for_file_playback(tts_options, pcm)

    assert out == audioop.lin2ulaw(pcm, 2)


def test_mulaw_or_unknown_tts_format_passes_through():
    ulaw = bytes(range(160))
    assert Engine._pipeline_audio_for_file_playback(
        {"format": {"encoding": "mulaw", "sample_rate": 8000}}, ulaw
    ) == ulaw
    assert Engine._pipeline_audio_for_file_playback({}, ulaw) == ulaw
