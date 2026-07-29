from types import SimpleNamespace
from typing import ClassVar

import pytest

from src.config import AppConfig, AzureSTTProviderConfig
from src.pipelines import azure as azure_module
from src.pipelines.azure import AzureSTTRealtimeAdapter


class _Signal:
    def connect(self, callback):
        self.callback = callback


class _CompletedOperation:
    def get(self):
        return None


class _SpeechConfig:
    def __init__(self, subscription, region):
        self.subscription = subscription
        self.region = region
        self.properties = {}
        self.speech_recognition_language = None
        self.output_format = None

    def set_property(self, key, value):
        self.properties[key] = value


class _AudioStreamFormat:
    created: ClassVar[list] = []

    def __init__(self, *, samples_per_second, bits_per_sample, channels):
        self.samples_per_second = samples_per_second
        self.bits_per_sample = bits_per_sample
        self.channels = channels
        self.__class__.created.append(self)


class _PushAudioInputStream:
    def __init__(self, *, stream_format):
        self.stream_format = stream_format
        self.writes = []
        self.closed = False

    def write(self, data):
        self.writes.append(bytes(data))

    def close(self):
        self.closed = True


class _AudioConfig:
    def __init__(self, *, stream):
        self.stream = stream


class _SpeechRecognizer:
    def __init__(self, *, speech_config, audio_config):
        self.speech_config = speech_config
        self.audio_config = audio_config
        self.recognized = _Signal()
        self.canceled = _Signal()
        self.session_stopped = _Signal()

    def start_continuous_recognition_async(self):
        return _CompletedOperation()

    def stop_continuous_recognition(self):
        return None


def _fake_speechsdk():
    return SimpleNamespace(
        SpeechConfig=_SpeechConfig,
        SpeechRecognizer=_SpeechRecognizer,
        OutputFormat=SimpleNamespace(Detailed="detailed"),
        PropertyId=SimpleNamespace(
            SpeechServiceConnection_EndSilenceTimeoutMs="end",
            Speech_SegmentationSilenceTimeoutMs="segmentation",
            SpeechServiceConnection_InitialSilenceTimeoutMs="initial",
        ),
        ResultReason=SimpleNamespace(RecognizedSpeech="recognized"),
        CancellationReason=SimpleNamespace(Error="error"),
        audio=SimpleNamespace(
            AudioStreamFormat=_AudioStreamFormat,
            PushAudioInputStream=_PushAudioInputStream,
            AudioConfig=_AudioConfig,
        ),
    )


def _app_config():
    return AppConfig(
        default_provider="local",
        providers={"local": {"enabled": True}},
        asterisk={"host": "127.0.0.1", "username": "u", "password": "p"},
        llm={"initial_greeting": "", "prompt": "prompt", "model": "gpt-4o"},
    )


@pytest.mark.asyncio
async def test_azure_realtime_declares_the_engine_stream_format(monkeypatch):
    _AudioStreamFormat.created.clear()
    monkeypatch.setattr(azure_module, "speechsdk", _fake_speechsdk())
    adapter = AzureSTTRealtimeAdapter(
        "azure_stt_realtime",
        _app_config(),
        AzureSTTProviderConfig(api_key="test-key", region="eastus", language="zh-TW"),
        {"stream_format": "pcm16_8k", "sample_rate": 8000},
    )

    await adapter.open_call("call-1", {"stream_format": "pcm16_16k", "sample_rate": 16000})
    await adapter.start_stream(
        "call-1",
        {"stream_format": "pcm16_16k", "sample_rate": 16000},
        sample_rate_hz=16000,
        fmt="pcm16_16k",
    )

    created = _AudioStreamFormat.created[-1]
    assert created.samples_per_second == 16000
    assert created.bits_per_sample == 16
    assert created.channels == 1
    assert adapter._active_sessions["call-1"]["sample_rate"] == 16000

    await adapter.send_audio("call-1", b"\x00\x00" * 160)
    assert adapter._active_sessions["call-1"]["push_stream"].writes
    await adapter.close_call("call-1")


@pytest.mark.asyncio
async def test_azure_realtime_rejects_unknown_stream_format(monkeypatch):
    monkeypatch.setattr(azure_module, "speechsdk", _fake_speechsdk())
    adapter = AzureSTTRealtimeAdapter(
        "azure_stt_realtime",
        _app_config(),
        AzureSTTProviderConfig(api_key="test-key"),
    )
    with pytest.raises(ValueError, match="Unsupported Azure streaming STT format"):
        await adapter.start_stream(
            "call-2",
            {},
            sample_rate_hz=16000,
            fmt="mulaw8k",
        )
