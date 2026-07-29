from __future__ import annotations

import asyncio
import importlib
import io
import sys
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest


LOCAL_AI_DIR = str(Path(__file__).resolve().parents[1] / "local_ai_server")


def _server_module():
    if LOCAL_AI_DIR not in sys.path:
        sys.path.insert(0, LOCAL_AI_DIR)
    return importlib.import_module("server")


class _SlowMelo:
    def synthesize(self, _text: str) -> bytes:
        time.sleep(0.12)
        return b"\x00\x00" * 80


class _AudioProcessor:
    def pcm16_to_ulaw_8k(self, _audio: bytes, _sample_rate: int) -> bytes:
        return b"ulaw"


@pytest.mark.asyncio
async def test_melotts_synthesis_does_not_block_event_loop():
    server_mod = _server_module()
    instance = object.__new__(server_mod.LocalAIServer)
    instance.melotts_backend = _SlowMelo()
    instance.audio_processor = _AudioProcessor()
    instance._tts_lock = asyncio.Lock()
    ticks = 0

    async def ticker():
        nonlocal ticks
        deadline = asyncio.get_running_loop().time() + 0.1
        while asyncio.get_running_loop().time() < deadline:
            ticks += 1
            await asyncio.sleep(0.005)

    audio, _ = await asyncio.gather(instance._process_tts_melotts("hello"), ticker())
    assert audio == b"ulaw"
    assert ticks >= 8, "blocking synthesis starved the WebSocket event loop"


@pytest.mark.asyncio
async def test_shared_tts_backend_access_is_serialized():
    server_mod = _server_module()
    instance = object.__new__(server_mod.LocalAIServer)
    instance.melotts_backend = _SlowMelo()
    instance.audio_processor = _AudioProcessor()
    instance._tts_lock = asyncio.Lock()
    active = 0
    peak = 0

    class _TrackedMelo:
        def synthesize(self, _text: str) -> bytes:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            time.sleep(0.04)
            active -= 1
            return b"\x00\x00"

    instance.melotts_backend = _TrackedMelo()
    await asyncio.gather(
        instance._process_tts_melotts("one"),
        instance._process_tts_melotts("two"),
    )
    assert peak == 1


@pytest.mark.asyncio
async def test_piper_can_emit_native_linear16_16k_with_truthful_metadata():
    server_mod = _server_module()
    audio_mod = importlib.import_module("audio_processor")
    instance = object.__new__(server_mod.LocalAIServer)
    instance.tts_backend = "piper"
    instance.tts_model_path = "/models/piper.onnx"
    instance.silero_speaker = ""
    instance.kokoro_voice = ""
    instance.config = SimpleNamespace(
        tts_phrase_cache_enabled=False,
        tts_phrase_cache_max_text_len=120,
    )
    instance._tts_cache = {}
    instance._tts_lock = asyncio.Lock()
    instance.tts_model = object()
    instance.audio_processor = audio_mod.AudioProcessor()
    instance._synthesize_piper_pcm16 = lambda _text: b"\x01\x00" * 2205

    result = await instance.process_tts_audio(
        "wideband",
        output_encoding="linear16",
        output_sample_rate_hz=16000,
    )

    assert result.encoding == "linear16"
    assert result.sample_rate_hz == 16000
    assert 3180 <= len(result.data) <= 3220


@pytest.mark.asyncio
async def test_piper_wideband_missing_model_returns_safe_empty_contract():
    server_mod = _server_module()
    audio_mod = importlib.import_module("audio_processor")
    instance = object.__new__(server_mod.LocalAIServer)
    instance.tts_backend = "piper"
    instance.tts_model = None
    instance.tts_model_path = "/models/missing.onnx"
    instance.silero_speaker = ""
    instance.kokoro_voice = ""
    instance.config = SimpleNamespace(
        tts_phrase_cache_enabled=False,
        tts_phrase_cache_max_text_len=120,
    )
    instance._tts_cache = {}
    instance._tts_lock = asyncio.Lock()
    instance.audio_processor = audio_mod.AudioProcessor()

    result = await instance.process_tts_audio(
        "wideband",
        output_encoding="linear16",
        output_sample_rate_hz=16000,
    )

    assert result.data == b""
    assert result.encoding == "linear16"
    assert result.sample_rate_hz == 16000


@pytest.mark.asyncio
async def test_piper_wideband_synthesis_failure_returns_safe_empty_contract():
    server_mod = _server_module()
    audio_mod = importlib.import_module("audio_processor")
    instance = object.__new__(server_mod.LocalAIServer)
    instance.tts_backend = "piper"
    instance.tts_model = object()
    instance.tts_model_path = "/models/piper.onnx"
    instance.silero_speaker = ""
    instance.kokoro_voice = ""
    instance.config = SimpleNamespace(
        tts_phrase_cache_enabled=False,
        tts_phrase_cache_max_text_len=120,
    )
    instance._tts_cache = {}
    instance._tts_lock = asyncio.Lock()
    instance.audio_processor = audio_mod.AudioProcessor()

    def fail_synthesis(_text: str) -> bytes:
        raise RuntimeError("synthesis failed")

    instance._synthesize_piper_pcm16 = fail_synthesis

    result = await instance.process_tts_audio(
        "wideband",
        output_encoding="linear16",
        output_sample_rate_hz=16000,
    )

    assert result.data == b""
    assert result.encoding == "linear16"
    assert result.sample_rate_hz == 16000


def _kokoro_server(server_mod, *, mode: str = "local"):
    audio_mod = importlib.import_module("audio_processor")
    instance = object.__new__(server_mod.LocalAIServer)
    instance.tts_backend = "kokoro"
    instance.tts_model_path = "/models/kokoro"
    instance.silero_speaker = ""
    instance.kokoro_voice = "af_heart"
    instance.kokoro_mode = mode
    instance.kokoro_api_base_url = "http://kokoro.test/v1" if mode == "api" else ""
    instance.config = SimpleNamespace(
        tts_phrase_cache_enabled=False,
        tts_phrase_cache_max_text_len=120,
    )
    instance._tts_cache = {}
    instance._tts_lock = asyncio.Lock()
    instance.audio_processor = audio_mod.AudioProcessor()
    return instance


@pytest.mark.asyncio
async def test_kokoro_local_can_emit_native_linear16_16k_with_truthful_metadata():
    server_mod = _server_module()
    instance = _kokoro_server(server_mod)

    class _Kokoro:
        sample_rate = 24000

        @staticmethod
        def synthesize(_text: str) -> bytes:
            return b"\x01\x00" * 2400

    instance.kokoro_backend = _Kokoro()

    result = await instance.process_tts_audio(
        "wideband",
        output_encoding="linear16",
        output_sample_rate_hz=16000,
    )

    assert result.encoding == "linear16"
    assert result.sample_rate_hz == 16000
    assert 3180 <= len(result.data) <= 3220


@pytest.mark.asyncio
async def test_kokoro_api_decodes_actual_wav_rate_before_wideband_resample():
    server_mod = _server_module()
    instance = _kokoro_server(server_mod, mode="api")
    instance.kokoro_backend = None

    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        wav_file.writeframes(b"\x01\x00" * 2205)
    instance._kokoro_api_speech_request = lambda _text: wav_buffer.getvalue()

    result = await instance.process_tts_audio(
        "wideband API",
        output_encoding="linear16",
        output_sample_rate_hz=16000,
    )

    assert result.encoding == "linear16"
    assert result.sample_rate_hz == 16000
    assert 3180 <= len(result.data) <= 3220


@pytest.mark.asyncio
async def test_kokoro_legacy_client_still_receives_mulaw_8k():
    server_mod = _server_module()
    instance = _kokoro_server(server_mod)

    class _Kokoro:
        sample_rate = 24000

        @staticmethod
        def synthesize(_text: str) -> bytes:
            return b"\x01\x00" * 2400

    instance.kokoro_backend = _Kokoro()

    result = await instance.process_tts_audio("legacy")

    assert result.encoding == "mulaw"
    assert result.sample_rate_hz == 8000
    assert 790 <= len(result.data) <= 810


@pytest.mark.asyncio
async def test_unscoped_local_backends_truthfully_retain_legacy_output():
    server_mod = _server_module()
    instance = object.__new__(server_mod.LocalAIServer)
    instance.tts_backend = "melotts"
    instance.tts_model_path = "/models/melo"
    instance.silero_speaker = ""
    instance.kokoro_voice = ""
    instance.config = SimpleNamespace(
        tts_phrase_cache_enabled=False,
        tts_phrase_cache_max_text_len=120,
    )
    instance._tts_cache = {}
    instance._process_tts_melotts = lambda _text: asyncio.sleep(0, result=b"legacy-ulaw")

    result = await instance.process_tts_audio(
        "unsupported wideband",
        output_encoding="linear16",
        output_sample_rate_hz=16000,
    )

    assert result.data == b"legacy-ulaw"
    assert result.encoding == "mulaw"
    assert result.sample_rate_hz == 8000
