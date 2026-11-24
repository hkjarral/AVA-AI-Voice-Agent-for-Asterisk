"""REST transcription gets speech utterances rather than 160ms fragments."""
import struct
import pytest

from src.engine import _PipelineUtteranceBuffer


def pcm(ms, amplitude):
    return struct.pack("<h", amplitude) * (ms * 16)


def test_silence_is_not_uploaded_and_speech_keeps_preroll_until_turn_end():
    buffer = _PipelineUtteranceBuffer({})
    assert buffer.feed(pcm(2000, 0)) == []
    speech = pcm(1000, 2000)
    assert buffer.feed(speech) == []
    assert buffer.feed(pcm(580, 0)) == []
    assert buffer.feed(pcm(20, 0)) == [pcm(200, 0) + speech + pcm(600, 0)]
    assert buffer.feed(pcm(1000, 0)) == []
    assert buffer.flush() == b""


def test_short_noise_is_ignored_and_eof_flushes_voiced_audio():
    buffer = _PipelineUtteranceBuffer({})
    assert buffer.feed(pcm(20, 2000) + pcm(600, 0)) == []
    speech = pcm(500, 2000)
    assert buffer.feed(speech) == []
    assert buffer.flush() == speech


def test_fragmentation_does_not_change_turn_boundaries():
    audio = pcm(400, 2000) + pcm(300, 0)
    buffer = _PipelineUtteranceBuffer({"segment_silence_ms": 300})
    ready = []
    for offset in range(0, len(audio), 73):
        ready.extend(buffer.feed(audio[offset:offset + 73]))
    assert ready == [audio]


def test_long_speech_has_bounded_upload_size():
    buffer = _PipelineUtteranceBuffer({})
    audio = pcm(16000, 2000)
    assert buffer.feed(audio) == [audio[:480000]]
    assert buffer.flush() == audio[480000:]


@pytest.mark.parametrize("value", ["false", 1, None])
def test_utterance_buffering_option_requires_an_explicit_boolean(value):
    from src.config import AppConfig
    with pytest.raises(ValueError, match="utterance_buffering must be true or false"):
        AppConfig(
            default_provider="local", providers={"local": {}},
            asterisk={"host": "localhost", "username": "u", "password": "p"},
            llm={"initial_greeting": "", "prompt": "Help"},
            pipelines={"test": {"stt": "local_stt", "llm": "local_llm", "tts": "local_tts",
                                "options": {"stt": {"utterance_buffering": value}}}},
        )
