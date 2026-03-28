"""
Tests for Russian STT support changes:
- Config: sherpa_model_type, sherpa_vad_model_path env vars
- Control plane: language fields in _STT_CONFIG_MAP + apply handlers
- Status builder: _stt_language helper + display strings
- Protocol contract: new schema fields
- Admin UI API: SwitchModelRequest fields + payload builder (requires fastapi)
- SherpaOfflineSTTBackend: per-session VAD, process_audio, finalize
"""
from __future__ import annotations

import importlib
import os
import struct
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

LOCAL_AI_DIR = str(Path(__file__).resolve().parents[1] / "local_ai_server")
ADMIN_UI_DIR = str(Path(__file__).resolve().parents[1] / "admin_ui" / "backend")

try:
    import fastapi  # noqa: F401
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


def _ensure_path(p: str):
    if p not in sys.path:
        sys.path.insert(0, p)


def _load_module(name: str, directory: str):
    _ensure_path(directory)
    return importlib.import_module(name)


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

class TestConfigNewFields:
    def _load_config(self, monkeypatch, **env):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        mod = _load_module("config", LOCAL_AI_DIR)
        importlib.reload(mod)
        return mod.LocalAIConfig.from_env()

    def test_sherpa_model_type_defaults_online(self, monkeypatch):
        cfg = self._load_config(monkeypatch)
        assert cfg.sherpa_model_type == "online"

    def test_sherpa_model_type_reads_env(self, monkeypatch):
        cfg = self._load_config(monkeypatch, SHERPA_MODEL_TYPE="offline")
        assert cfg.sherpa_model_type == "offline"

    def test_sherpa_model_type_strips_and_lowercases(self, monkeypatch):
        cfg = self._load_config(monkeypatch, SHERPA_MODEL_TYPE="  OFFLINE  ")
        assert cfg.sherpa_model_type == "offline"

    def test_sherpa_vad_model_path_defaults_empty(self, monkeypatch):
        cfg = self._load_config(monkeypatch)
        assert cfg.sherpa_vad_model_path == ""

    def test_sherpa_vad_model_path_reads_env(self, monkeypatch):
        cfg = self._load_config(monkeypatch, SHERPA_VAD_MODEL_PATH="/app/models/vad/silero.onnx")
        assert cfg.sherpa_vad_model_path == "/app/models/vad/silero.onnx"

    def test_sherpa_offline_tuning_defaults(self, monkeypatch):
        cfg = self._load_config(monkeypatch)
        assert cfg.sherpa_vad_threshold == pytest.approx(0.35)
        assert cfg.sherpa_vad_min_silence_ms == 700
        assert cfg.sherpa_vad_min_speech_ms == 200
        assert cfg.sherpa_offline_preroll_ms == 350

    def test_sherpa_offline_tuning_reads_env(self, monkeypatch):
        cfg = self._load_config(
            monkeypatch,
            SHERPA_VAD_THRESHOLD="0.42",
            SHERPA_VAD_MIN_SILENCE_MS="850",
            SHERPA_VAD_MIN_SPEECH_MS="300",
            SHERPA_OFFLINE_PREROLL_MS="500",
        )
        assert cfg.sherpa_vad_threshold == pytest.approx(0.42)
        assert cfg.sherpa_vad_min_silence_ms == 850
        assert cfg.sherpa_vad_min_speech_ms == 300
        assert cfg.sherpa_offline_preroll_ms == 500


# ---------------------------------------------------------------------------
# Control-plane tests
# ---------------------------------------------------------------------------

class TestControlPlaneLanguageFields:
    def _load_cp(self):
        return _load_module("control_plane", LOCAL_AI_DIR)

    def _base_config(self):
        config_mod = _load_module("config", LOCAL_AI_DIR)
        return config_mod.LocalAIConfig.from_env()

    def test_stt_config_map_has_language_keys(self):
        cp = self._load_cp()
        m = cp._STT_CONFIG_MAP
        assert "faster_whisper_language" in m
        assert "whisper_cpp_language" in m
        assert "sherpa_model_type" in m
        assert "sherpa_vad_model_path" in m

    def test_apply_faster_whisper_language(self):
        cp = self._load_cp()
        cfg = self._base_config()
        new_cfg, changed = cp.apply_switch_model_request(
            cfg, {"faster_whisper_language": "ru"}
        )
        assert new_cfg.faster_whisper_language == "ru"
        assert any("faster_whisper_language=ru" in c for c in changed)

    def test_apply_whisper_cpp_language(self):
        cp = self._load_cp()
        cfg = self._base_config()
        new_cfg, changed = cp.apply_switch_model_request(
            cfg, {"whisper_cpp_language": "ru"}
        )
        assert new_cfg.whisper_cpp_language == "ru"
        assert any("whisper_cpp_language=ru" in c for c in changed)

    def test_apply_sherpa_model_type_valid(self):
        cp = self._load_cp()
        cfg = self._base_config()
        new_cfg, changed = cp.apply_switch_model_request(
            cfg, {"sherpa_model_type": "offline"}
        )
        assert new_cfg.sherpa_model_type == "offline"
        assert any("sherpa_model_type=offline" in c for c in changed)

    def test_apply_sherpa_model_type_invalid_ignored(self):
        cp = self._load_cp()
        cfg = self._base_config()
        new_cfg, changed = cp.apply_switch_model_request(
            cfg, {"sherpa_model_type": "bogus"}
        )
        assert new_cfg.sherpa_model_type == cfg.sherpa_model_type
        assert not any("sherpa_model_type" in c for c in changed)

    def test_apply_sherpa_vad_model_path(self):
        cp = self._load_cp()
        cfg = self._base_config()
        new_cfg, changed = cp.apply_switch_model_request(
            cfg, {"sherpa_vad_model_path": "/app/models/vad/silero_vad.onnx"}
        )
        assert new_cfg.sherpa_vad_model_path == "/app/models/vad/silero_vad.onnx"
        assert any("sherpa_vad_model_path" in c for c in changed)


# ---------------------------------------------------------------------------
# Status builder tests
# ---------------------------------------------------------------------------

def _mock_server(**overrides) -> SimpleNamespace:
    defaults = dict(
        stt_backend="vosk",
        mock_models=False,
        stt_model=None,
        stt_model_path="/app/models/stt/vosk-model",
        kroko_backend=None,
        kroko_embedded=False,
        kroko_url="wss://example.com",
        kroko_model_path="",
        kroko_port=6006,
        kroko_language="en-US",
        sherpa_backend=None,
        sherpa_model_path="/app/models/stt/sherpa",
        sherpa_model_type="online",
        faster_whisper_backend=None,
        faster_whisper_model="base",
        faster_whisper_language="en",
        whisper_cpp_backend=None,
        whisper_cpp_model_path="/app/models/stt/ggml-base.en.bin",
        whisper_cpp_language="en",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestStatusBuilderLanguage:
    def _load_sb(self):
        return _load_module("status_builder", LOCAL_AI_DIR)

    def test_stt_language_kroko(self):
        sb = self._load_sb()
        server = _mock_server(stt_backend="kroko", kroko_language="en-US")
        assert sb._stt_language(server) == "en-US"

    def test_stt_language_faster_whisper(self):
        sb = self._load_sb()
        server = _mock_server(stt_backend="faster_whisper", faster_whisper_language="ru")
        assert sb._stt_language(server) == "ru"

    def test_stt_language_whisper_cpp(self):
        sb = self._load_sb()
        server = _mock_server(stt_backend="whisper_cpp", whisper_cpp_language="ru")
        assert sb._stt_language(server) == "ru"

    def test_stt_language_vosk_returns_none(self):
        sb = self._load_sb()
        server = _mock_server(stt_backend="vosk")
        assert sb._stt_language(server) is None

    def test_stt_language_sherpa_returns_none(self):
        sb = self._load_sb()
        server = _mock_server(stt_backend="sherpa")
        assert sb._stt_language(server) is None


class TestStatusBuilderDisplay:
    def _load_sb(self):
        return _load_module("status_builder", LOCAL_AI_DIR)

    def test_faster_whisper_display_includes_language(self):
        sb = self._load_sb()
        server = _mock_server(
            stt_backend="faster_whisper",
            faster_whisper_model="base",
            faster_whisper_language="ru",
            faster_whisper_backend=True,
        )
        loaded, path, display = sb._stt_status(server)
        assert "ru" in display
        assert "base" in display

    def test_whisper_cpp_display_includes_language(self):
        sb = self._load_sb()
        server = _mock_server(
            stt_backend="whisper_cpp",
            whisper_cpp_language="ru",
            whisper_cpp_backend=True,
        )
        loaded, path, display = sb._stt_status(server)
        assert "ru" in display

    def test_sherpa_display_includes_model_type(self):
        sb = self._load_sb()
        server = _mock_server(
            stt_backend="sherpa",
            sherpa_model_type="offline",
            sherpa_backend=True,
        )
        loaded, path, display = sb._stt_status(server)
        assert "offline" in display


# ---------------------------------------------------------------------------
# Protocol contract schema tests
# ---------------------------------------------------------------------------

class TestProtocolContractSchema:
    def _load_pc(self):
        return _load_module("protocol_contract", LOCAL_AI_DIR)

    def test_switch_model_schema_has_language_fields(self):
        pc = self._load_pc()
        defs = pc.PROTOCOL_SCHEMA["$defs"]
        switch_props = defs["SwitchModelRequest"]["properties"]
        assert "faster_whisper_language" in switch_props
        assert "whisper_cpp_language" in switch_props
        assert "sherpa_model_type" in switch_props
        assert "sherpa_vad_model_path" in switch_props

    def test_sherpa_model_type_enum(self):
        pc = self._load_pc()
        defs = pc.PROTOCOL_SCHEMA["$defs"]
        smt = defs["SwitchModelRequest"]["properties"]["sherpa_model_type"]
        assert smt.get("enum") == ["online", "offline"]

    def test_status_response_stt_has_language(self):
        pc = self._load_pc()
        defs = pc.PROTOCOL_SCHEMA["$defs"]
        stt_props = defs["StatusResponse"]["properties"]["models"]["properties"]["stt"]["properties"]
        assert "language" in stt_props


# ---------------------------------------------------------------------------
# Admin UI SwitchModelRequest + payload builder tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")
class TestAdminUISwitchModelRequest:
    def _load_api(self):
        _ensure_path(ADMIN_UI_DIR)
        return _load_module("api.local_ai", ADMIN_UI_DIR)

    def test_switch_model_request_has_new_fields(self):
        api = self._load_api()
        req = api.SwitchModelRequest(model_type="stt")
        assert hasattr(req, "faster_whisper_language")
        assert hasattr(req, "whisper_cpp_language")
        assert hasattr(req, "sherpa_model_type")
        assert hasattr(req, "sherpa_vad_model_path")

    def test_payload_builder_faster_whisper_language(self):
        api = self._load_api()
        req = api.SwitchModelRequest(
            model_type="stt",
            backend="faster_whisper",
            faster_whisper_language="ru",
        )
        payload = api._build_local_ai_ws_switch_payload(req)
        assert payload is not None
        assert payload.get("faster_whisper_language") == "ru"

    def test_payload_builder_whisper_cpp_language(self):
        api = self._load_api()
        req = api.SwitchModelRequest(
            model_type="stt",
            backend="whisper_cpp",
            whisper_cpp_language="ru",
        )
        payload = api._build_local_ai_ws_switch_payload(req)
        assert payload is not None
        assert payload.get("whisper_cpp_language") == "ru"

    def test_payload_builder_sherpa_offline(self):
        api = self._load_api()
        req = api.SwitchModelRequest(
            model_type="stt",
            backend="sherpa",
            sherpa_model_path="/app/models/stt/gigaam",
            sherpa_model_type="offline",
            sherpa_vad_model_path="/app/models/vad/silero_vad.onnx",
        )
        payload = api._build_local_ai_ws_switch_payload(req)
        assert payload is not None
        assert payload.get("sherpa_model_type") == "offline"
        assert payload.get("sherpa_vad_model_path") == "/app/models/vad/silero_vad.onnx"
        assert payload.get("sherpa_model_path") == "/app/models/stt/gigaam"

    # --- env/yaml persist tests ---

    def test_persist_faster_whisper_language(self):
        api = self._load_api()
        req = api.SwitchModelRequest(
            model_type="stt",
            backend="faster_whisper",
            model_path="base",
            faster_whisper_language="ru",
        )
        env, yaml_u = api._build_local_ai_env_and_yaml_updates(req)
        assert env.get("FASTER_WHISPER_LANGUAGE") == "ru"
        assert yaml_u.get("faster_whisper_language") == "ru"

    def test_persist_whisper_cpp_language(self):
        api = self._load_api()
        req = api.SwitchModelRequest(
            model_type="stt",
            backend="whisper_cpp",
            whisper_cpp_model_path="/app/models/stt/whisper.cpp",
            whisper_cpp_language="es",
        )
        env, yaml_u = api._build_local_ai_env_and_yaml_updates(req)
        assert env.get("WHISPER_CPP_LANGUAGE") == "es"
        assert yaml_u.get("whisper_cpp_language") == "es"

    def test_persist_sherpa_offline_fields(self):
        api = self._load_api()
        req = api.SwitchModelRequest(
            model_type="stt",
            backend="sherpa",
            sherpa_model_path="/app/models/stt/gigaam",
            sherpa_model_type="offline",
            sherpa_vad_model_path="/app/models/vad/silero_vad.onnx",
        )
        env, yaml_u = api._build_local_ai_env_and_yaml_updates(req)
        assert env.get("SHERPA_MODEL_TYPE") == "offline"
        assert yaml_u.get("sherpa_model_type") == "offline"
        assert env.get("SHERPA_VAD_MODEL_PATH") == "/app/models/vad/silero_vad.onnx"
        assert yaml_u.get("sherpa_vad_model_path") == "/app/models/vad/silero_vad.onnx"

    def test_persist_omits_unset_language(self):
        api = self._load_api()
        req = api.SwitchModelRequest(
            model_type="stt",
            backend="faster_whisper",
            model_path="base",
        )
        env, yaml_u = api._build_local_ai_env_and_yaml_updates(req)
        assert "FASTER_WHISPER_LANGUAGE" not in env
        assert "faster_whisper_language" not in yaml_u


# ---------------------------------------------------------------------------
# SherpaOfflineSTTBackend unit tests (mocked sherpa_onnx)
# ---------------------------------------------------------------------------

def _load_stt_backends():
    return _load_module("stt_backends", LOCAL_AI_DIR)


class _FakeSpeechSegment:
    """Simulates a sherpa_onnx speech segment returned by VoiceActivityDetector."""
    def __init__(self, samples):
        self.samples = samples


class _ArrayPoisonedSamples:
    """Behaves correctly when iterated, but returns garbage via __array__()."""

    def __init__(self, values):
        self._values = list(values)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __array__(self, dtype=None):
        arr = np.full(len(self._values), np.nan, dtype=np.float32)
        if dtype is not None:
            return arr.astype(dtype)
        return arr


class _FakeStreamResult:
    """Simulates the result attribute on a sherpa_onnx offline stream."""
    def __init__(self, text: str):
        self.text = text


class _FakeStream:
    """Simulates a sherpa_onnx offline stream."""
    def __init__(self):
        self.result = _FakeStreamResult("")

    def accept_waveform(self, sample_rate, samples):
        pass


class _FakeOfflineRecognizer:
    """Simulates sherpa_onnx.OfflineRecognizer."""
    def __init__(self, text="hello world"):
        self._text = text

    def create_stream(self):
        s = _FakeStream()
        s.result = _FakeStreamResult(self._text)
        return s

    def decode_stream(self, stream):
        pass

    @classmethod
    def from_transducer(cls, **kwargs):
        return cls()


class _FakeVAD:
    """Simulates sherpa_onnx.VoiceActivityDetector with controllable segments."""
    def __init__(self, segments=None):
        self._segments = list(segments or [])
        self._flushed = False

    def accept_waveform(self, samples):
        pass

    def empty(self):
        return len(self._segments) == 0

    @property
    def front(self):
        return self._segments[0] if self._segments else None

    def pop(self):
        if self._segments:
            self._segments.pop(0)

    def flush(self):
        self._flushed = True


class _MutatingPopVAD(_FakeVAD):
    def pop(self):
        if self._segments:
            segment = self._segments[0]
            if hasattr(segment.samples, "_values"):
                segment.samples._values[:] = [float("nan")] * len(segment.samples._values)
        super().pop()


class TestSherpaOfflineBackendSessionVAD:
    """Verify that SherpaOfflineSTTBackend uses per-session VAD, not a shared one."""

    def _make_backend(self):
        sb = _load_stt_backends()
        backend = sb.SherpaOfflineSTTBackend(
            model_path="/fake/model",
            vad_model_path="/fake/vad.onnx",
            preroll_ms=350,
            vad_threshold=0.35,
            vad_min_silence_ms=700,
            vad_min_speech_ms=200,
        )
        backend.recognizer = _FakeOfflineRecognizer()
        backend._vad_config = "fake_config"
        backend._initialized = True
        return backend

    def test_create_session_vad_returns_independent_instances(self):
        backend = self._make_backend()
        # Patch sherpa_onnx.VoiceActivityDetector to return our fake
        with patch.dict(sys.modules, {"sherpa_onnx": MagicMock()}):
            sys.modules["sherpa_onnx"].VoiceActivityDetector.side_effect = [
                _FakeVAD(), _FakeVAD()
            ]
            vad1 = backend.create_session_vad()
            vad2 = backend.create_session_vad()
            assert vad1 is not vad2

    def test_backend_stores_offline_tuning(self):
        backend = self._make_backend()
        assert backend.preroll_ms == 350
        assert backend.vad_threshold == pytest.approx(0.35)
        assert backend.vad_min_silence_ms == 700
        assert backend.vad_min_speech_ms == 200

    def test_process_audio_uses_provided_vad(self):
        backend = self._make_backend()
        # Create two VADs: one with speech, one without
        one_second_samples = [0.0] * 16000  # 1 second at 16kHz > min_audio_length
        vad_with_speech = _FakeVAD(segments=[_FakeSpeechSegment(one_second_samples)])
        vad_empty = _FakeVAD(segments=[])

        pcm_silence = b"\x00\x00" * 160  # 20ms chunk

        result_speech = backend.process_audio(vad_with_speech, pcm_silence)
        result_empty = backend.process_audio(vad_empty, pcm_silence)

        assert result_speech is not None
        assert result_speech["type"] == "final"
        assert result_speech["text"] == "hello world"
        assert result_empty is None

    def test_process_audio_rejects_none_vad(self):
        backend = self._make_backend()
        pcm_silence = b"\x00\x00" * 160
        assert backend.process_audio(None, pcm_silence) is None

    def test_finalize_flushes_vad_and_transcribes(self):
        backend = self._make_backend()
        one_second_samples = [0.0] * 16000
        vad = _FakeVAD()
        # Simulate: flush populates the queue
        original_flush = vad.flush
        def flush_with_segment():
            original_flush()
            vad._segments.append(_FakeSpeechSegment(one_second_samples))
        vad.flush = flush_with_segment

        result = backend.finalize(vad)
        assert vad._flushed
        assert result is not None
        assert result["type"] == "final"
        assert result["text"] == "hello world"

    def test_finalize_returns_none_when_no_speech(self):
        backend = self._make_backend()
        vad = _FakeVAD()
        result = backend.finalize(vad)
        assert vad._flushed
        assert result is None

    def test_finalize_rejects_none_vad(self):
        backend = self._make_backend()
        assert backend.finalize(None) is None

    def test_backend_has_no_shared_vad(self):
        """After init, backend should not store a shared VAD — only _vad_config."""
        backend = self._make_backend()
        assert not hasattr(backend, "vad") or backend.__dict__.get("vad") is None

    def test_process_audio_copies_samples_before_pop(self):
        backend = self._make_backend()
        good_samples = [0.05] * 16000
        vad = _MutatingPopVAD(segments=[_FakeSpeechSegment(_ArrayPoisonedSamples(good_samples))])
        captured = {}

        def _capture(samples):
            captured["samples"] = samples
            return "hello world"

        backend._transcribe_segment = _capture
        result = backend.process_audio(vad, b"\x00\x00" * 160)

        assert result == {"type": "final", "text": "hello world"}
        assert "samples" in captured
        assert np.isfinite(captured["samples"]).all()
        assert captured["samples"][0] == pytest.approx(0.05)

    def test_process_audio_rejects_invalid_segment_samples(self):
        backend = self._make_backend()
        bad_samples = [0.0] * 15999 + [float("nan")]
        vad = _FakeVAD(segments=[_FakeSpeechSegment(bad_samples)])
        backend._transcribe_segment = MagicMock(return_value="should-not-run")

        result = backend.process_audio(vad, b"\x00\x00" * 160)

        assert result is None
        backend._transcribe_segment.assert_not_called()

    def test_process_audio_prepends_preroll_without_duplicate_overlap(self):
        backend = self._make_backend()
        seg = [0.10, 0.20, 0.30, 0.40]
        preroll = [0.01, 0.02, 0.10, 0.20]
        vad = _FakeVAD(segments=[_FakeSpeechSegment(seg * 4000)])
        captured = {}

        def _capture(samples):
            captured["samples"] = samples
            return "hello world"

        backend._transcribe_segment = _capture
        preroll_pcm16 = (np.array(preroll * 1000, dtype=np.float32) * 32768.0).astype(np.int16).tobytes()

        result = backend.process_audio(vad, b"\x00\x00" * 160, preroll_pcm16=preroll_pcm16)

        assert result == {"type": "final", "text": "hello world"}
        assert "samples" in captured
        assert captured["samples"][0] == pytest.approx(0.01, abs=1e-3)
        assert captured["samples"][1] == pytest.approx(0.02, abs=1e-3)
        assert captured["samples"][2] == pytest.approx(0.10, abs=1e-3)

    def test_process_audio_without_preroll_does_not_prepend_audio(self):
        backend = self._make_backend()
        speech = [0.10] * 16000
        vad = _FakeVAD(segments=[_FakeSpeechSegment(speech)])
        captured = {}

        def _capture(samples):
            captured["samples"] = samples
            return "hello world"

        backend._transcribe_segment = _capture
        result = backend.process_audio(vad, b"\x00\x00" * 160, preroll_pcm16=b"")

        assert result == {"type": "final", "text": "hello world"}
        assert len(captured["samples"]) == len(speech)

    def test_initialize_rejects_streaming_model_in_offline_mode(self):
        sb = _load_stt_backends()
        backend = sb.SherpaOfflineSTTBackend(
            model_path="/fake/streaming-model",
            vad_model_path="/fake/vad.onnx",
        )

        fake_sherpa = MagicMock()
        with patch("os.path.exists", return_value=True), \
             patch.object(backend, "_find_file", return_value="/fake/tokens.txt"), \
             patch.object(
                 backend,
                 "_find_onnx",
                 side_effect=[
                     "/fake/encoder-epoch-99-avg-1-chunk-16-left-128.onnx",
                     "/fake/decoder.onnx",
                     "/fake/joiner.onnx",
                 ],
             ), \
             patch.dict(sys.modules, {"sherpa_onnx": fake_sherpa}):
            assert backend.initialize() is False
            fake_sherpa.OfflineRecognizer.from_transducer.assert_not_called()


class TestSherpaOfflineBackendShutdown:
    def test_shutdown_clears_state(self):
        sb = _load_stt_backends()
        backend = sb.SherpaOfflineSTTBackend(
            model_path="/fake/model",
            vad_model_path="/fake/vad.onnx",
        )
        backend.recognizer = _FakeOfflineRecognizer()
        backend._vad_config = "fake"
        backend._initialized = True
        backend.shutdown()
        assert backend.recognizer is None
        assert backend._vad_config is None
        assert backend._initialized is False
