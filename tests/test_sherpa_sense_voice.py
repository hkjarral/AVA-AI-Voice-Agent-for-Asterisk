"""
Tests for SenseVoice support in the Sherpa offline STT path (issue #407):
- Model-layout detection: SenseVoice (model*.onnx + tokens.txt) vs transducer
- Recognizer construction via a mocked sherpa_onnx module
- Rich-transcription tag stripping and language normalization
- Config env vars, switch_model handling, and protocol schema fields
- Models catalog entry
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_AI_DIR = str(REPO_ROOT / "local_ai_server")


def _load_module(name: str):
    if LOCAL_AI_DIR not in sys.path:
        sys.path.insert(0, LOCAL_AI_DIR)
    return importlib.import_module(name)


# ---------------------------------------------------------------------------
# Fake sherpa_onnx
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, text: str, lang: str = "", emotion: str = "", event: str = ""):
        self.text = text
        self.lang = lang
        self.emotion = emotion
        self.event = event


class _FakeStream:
    def __init__(self, result: _FakeResult):
        self.result = result

    def accept_waveform(self, sample_rate, samples):
        pass


class _FakeRecognizer:
    def __init__(self, kind: str, kwargs: dict, result: _FakeResult):
        self.kind = kind
        self.kwargs = kwargs
        self._result = result

    def create_stream(self):
        return _FakeStream(self._result)

    def decode_stream(self, stream):
        pass


class _FakeOfflineRecognizer:
    calls: list = []
    result = _FakeResult("hello world")

    @classmethod
    def from_sense_voice(cls, **kwargs):
        cls.calls.append(("sense_voice", kwargs))
        return _FakeRecognizer("sense_voice", kwargs, cls.result)

    @classmethod
    def from_transducer(cls, **kwargs):
        cls.calls.append(("transducer", kwargs))
        return _FakeRecognizer("transducer", kwargs, cls.result)


class _FakeSileroConfig:
    model = ""
    threshold = 0.5
    min_silence_duration = 0.5
    min_speech_duration = 0.25
    max_speech_duration = 20.0


class _FakeVadModelConfig:
    def __init__(self):
        self.silero_vad = _FakeSileroConfig()
        self.sample_rate = 16000


class _FakeSegment:
    def __init__(self, samples):
        self.samples = samples


class _FakeVAD:
    def __init__(self, config=None, buffer_size_in_seconds=30, segments=None):
        self._segments = list(segments or [])

    def accept_waveform(self, samples):
        pass

    def empty(self):
        return not self._segments

    @property
    def front(self):
        return self._segments[0]

    def pop(self):
        self._segments.pop(0)

    def flush(self):
        pass


@pytest.fixture
def fake_sherpa(monkeypatch):
    mod = ModuleType("sherpa_onnx")
    mod.OfflineRecognizer = _FakeOfflineRecognizer
    mod.VadModelConfig = _FakeVadModelConfig
    mod.VoiceActivityDetector = _FakeVAD
    _FakeOfflineRecognizer.calls = []
    _FakeOfflineRecognizer.result = _FakeResult("hello world")
    monkeypatch.setitem(sys.modules, "sherpa_onnx", mod)
    return _FakeOfflineRecognizer


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def _make_backend(model_dir: Path, vad: Path, **kwargs):
    sb = _load_module("stt_backends")
    return sb.SherpaOfflineSTTBackend(
        model_path=str(model_dir),
        vad_model_path=str(vad),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Backend: detection + recognizer construction
# ---------------------------------------------------------------------------


class TestSenseVoiceDetection:
    def test_sense_voice_layout_uses_from_sense_voice(self, tmp_path, fake_sherpa):
        model_dir = tmp_path / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
        model_file = _touch(model_dir / "model.int8.onnx")
        tokens = _touch(model_dir / "tokens.txt")
        vad = _touch(tmp_path / "silero_vad.onnx")

        backend = _make_backend(model_dir, vad, sense_voice_language="ZH", sense_voice_use_itn=False)
        assert backend.initialize() is True
        assert backend.model_family == "sense_voice"

        kind, kwargs = fake_sherpa.calls[-1]
        assert kind == "sense_voice"
        assert kwargs["model"] == str(model_file)
        assert kwargs["tokens"] == str(tokens)
        assert kwargs["language"] == "zh"
        assert kwargs["use_itn"] is False
        assert kwargs["sample_rate"] == backend.sample_rate

    def test_prefers_fp32_model_when_both_present(self, tmp_path, fake_sherpa):
        model_dir = tmp_path / "sense-voice"
        _touch(model_dir / "model.int8.onnx")
        fp32 = _touch(model_dir / "model.onnx")
        _touch(model_dir / "tokens.txt")
        vad = _touch(tmp_path / "silero_vad.onnx")

        backend = _make_backend(model_dir, vad)
        assert backend.initialize() is True
        assert fake_sherpa.calls[-1][1]["model"] == str(fp32)

    def test_transducer_layout_still_uses_from_transducer(self, tmp_path, fake_sherpa):
        model_dir = tmp_path / "sherpa-onnx-zipformer-en-2023-06-26"
        for name in ("tokens.txt", "encoder.onnx", "decoder.onnx", "joiner.onnx"):
            _touch(model_dir / name)
        vad = _touch(tmp_path / "silero_vad.onnx")

        backend = _make_backend(model_dir, vad)
        assert backend.initialize() is True
        assert backend.model_family == "transducer"
        assert fake_sherpa.calls[-1][0] == "transducer"

    def test_sense_voice_missing_tokens_fails(self, tmp_path, fake_sherpa):
        model_dir = tmp_path / "sense-voice"
        _touch(model_dir / "model.int8.onnx")
        vad = _touch(tmp_path / "silero_vad.onnx")

        backend = _make_backend(model_dir, vad)
        assert backend.initialize() is False
        assert fake_sherpa.calls == []

    def test_streaming_transducer_still_rejected(self, tmp_path, fake_sherpa):
        model_dir = tmp_path / "sherpa-onnx-streaming-zipformer-en-2023-06-26"
        for name in ("tokens.txt", "encoder-chunk-16.onnx", "decoder.onnx", "joiner.onnx"):
            _touch(model_dir / name)
        vad = _touch(tmp_path / "silero_vad.onnx")

        backend = _make_backend(model_dir, vad)
        assert backend.initialize() is False
        assert fake_sherpa.calls == []


class TestSenseVoiceTranscripts:
    def test_process_audio_strips_rich_tags(self, tmp_path, fake_sherpa):
        model_dir = tmp_path / "sense-voice"
        _touch(model_dir / "model.int8.onnx")
        _touch(model_dir / "tokens.txt")
        vad_path = _touch(tmp_path / "silero_vad.onnx")
        backend = _make_backend(model_dir, vad_path)
        assert backend.initialize() is True

        fake_sherpa.result = _FakeResult(
            "<|en|><|ANGRY|><|Speech|><|withitn|>I want a refund.",
            lang="<|en|>",
            emotion="<|ANGRY|>",
            event="<|Speech|>",
        )
        backend.recognizer._result = fake_sherpa.result
        segment = _FakeSegment(np.full(8000, 0.1, dtype=np.float32).tolist())
        vad = _FakeVAD(segments=[segment])

        result = backend.process_audio(vad, b"\x00\x00" * 320)
        assert result == {"type": "final", "text": "I want a refund."}

    def test_transducer_text_is_untouched(self, tmp_path, fake_sherpa):
        model_dir = tmp_path / "zipformer"
        for name in ("tokens.txt", "encoder.onnx", "decoder.onnx", "joiner.onnx"):
            _touch(model_dir / name)
        vad_path = _touch(tmp_path / "silero_vad.onnx")
        backend = _make_backend(model_dir, vad_path)
        assert backend.initialize() is True

        backend.recognizer._result = _FakeResult("HELLO <|not a tag in transducer mode|>")
        out = backend.transcribe_pcm16((np.zeros(8000, dtype=np.int16)).tobytes())
        assert out == {"type": "final", "text": "HELLO <|not a tag in transducer mode|>"}


class TestSenseVoiceHelpers:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("<|zh|><|NEUTRAL|><|Speech|><|woitn|>你好", "你好"),
            ("<|en|><|HAPPY|><|BGM|><|withitn|> Hello,  there. ", "Hello, there."),
            ("plain text", "plain text"),
            ("", ""),
        ],
    )
    def test_strip_sense_voice_tags(self, raw, expected):
        sb = _load_module("stt_backends")
        assert sb.strip_sense_voice_tags(raw) == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [("auto", "auto"), ("EN", "en"), (" yue ", "yue"), ("fr", "auto"), ("", "auto"), (None, "auto")],
    )
    def test_normalize_sense_voice_language(self, raw, expected):
        sb = _load_module("stt_backends")
        assert sb.normalize_sense_voice_language(raw) == expected


# ---------------------------------------------------------------------------
# Config / control plane / protocol
# ---------------------------------------------------------------------------


class TestSenseVoiceConfig:
    def _from_env(self, monkeypatch, **env):
        for key in ("SHERPA_SENSE_VOICE_LANGUAGE", "SHERPA_SENSE_VOICE_USE_ITN"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        mod = _load_module("config")
        importlib.reload(mod)
        return mod.LocalAIConfig.from_env()

    def test_defaults(self, monkeypatch):
        cfg = self._from_env(monkeypatch)
        assert cfg.sherpa_sense_voice_language == "auto"
        assert cfg.sherpa_sense_voice_use_itn is True

    def test_reads_env(self, monkeypatch):
        cfg = self._from_env(
            monkeypatch,
            SHERPA_SENSE_VOICE_LANGUAGE=" JA ",
            SHERPA_SENSE_VOICE_USE_ITN="false",
        )
        assert cfg.sherpa_sense_voice_language == "ja"
        assert cfg.sherpa_sense_voice_use_itn is False


class TestSenseVoiceSwitchModel:
    def _cp_and_config(self):
        cp = _load_module("control_plane")
        cfg = _load_module("config").LocalAIConfig()
        return cp, cfg

    def test_apply_language(self):
        cp, cfg = self._cp_and_config()
        new_cfg, changed = cp.apply_switch_model_request(cfg, {"sherpa_sense_voice_language": "Yue"})
        assert new_cfg.sherpa_sense_voice_language == "yue"
        assert "sherpa_sense_voice_language=yue" in changed

    def test_invalid_language_ignored(self):
        cp, cfg = self._cp_and_config()
        new_cfg, changed = cp.apply_switch_model_request(cfg, {"sherpa_sense_voice_language": "fr"})
        assert new_cfg.sherpa_sense_voice_language == cfg.sherpa_sense_voice_language
        assert not any(c.startswith("sherpa_sense_voice_language") for c in changed)

    def test_apply_use_itn(self):
        cp, cfg = self._cp_and_config()
        new_cfg, changed = cp.apply_switch_model_request(cfg, {"sherpa_sense_voice_use_itn": False})
        assert new_cfg.sherpa_sense_voice_use_itn is False
        assert "sherpa_sense_voice_use_itn=False" in changed

    def test_non_bool_use_itn_ignored(self):
        cp, cfg = self._cp_and_config()
        new_cfg, _ = cp.apply_switch_model_request(cfg, {"sherpa_sense_voice_use_itn": "no"})
        assert new_cfg.sherpa_sense_voice_use_itn is cfg.sherpa_sense_voice_use_itn

    def test_stt_config_dict(self):
        cp, cfg = self._cp_and_config()
        new_cfg, _ = cp.apply_switch_model_request(
            cfg,
            {
                "stt_backend": "sherpa",
                "stt_config": {"sherpa_sense_voice_language": "ko", "sherpa_sense_voice_use_itn": "false"},
            },
        )
        assert new_cfg.sherpa_sense_voice_language == "ko"
        assert new_cfg.sherpa_sense_voice_use_itn is False


class TestSenseVoiceProtocolSchema:
    def test_switch_model_fields(self):
        pc = _load_module("protocol_contract")
        props = pc.PROTOCOL_SCHEMA["$defs"]["SwitchModelRequest"]["properties"]
        assert props["sherpa_sense_voice_language"]["enum"] == ["auto", "zh", "en", "ja", "ko", "yue"]
        assert props["sherpa_sense_voice_use_itn"]["type"] == "boolean"
        stt_props = props["stt_config"]["properties"]
        assert "sherpa_sense_voice_language" in stt_props
        assert "sherpa_sense_voice_use_itn" in stt_props

    def test_enum_matches_backend_languages(self):
        pc = _load_module("protocol_contract")
        constants = _load_module("constants")
        props = pc.PROTOCOL_SCHEMA["$defs"]["SwitchModelRequest"]["properties"]
        assert tuple(props["sherpa_sense_voice_language"]["enum"]) == constants.SENSE_VOICE_LANGUAGES


# ---------------------------------------------------------------------------
# Models catalog
# ---------------------------------------------------------------------------


def test_models_catalog_has_sense_voice_entry():
    path = REPO_ROOT / "admin_ui" / "backend" / "api" / "models_catalog.py"
    spec = importlib.util.spec_from_file_location("_ava_models_catalog_sv", path)
    catalog = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(catalog)

    entries = [m for m in catalog.SHERPA_STT_MODELS if m["id"] == "sherpa_sense_voice_offline"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["backend"] == "sherpa"
    assert entry["model_type"] == "offline"
    assert entry["model_path"] in entry["download_url"]
    assert entry["download_url"].startswith("https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/")


def _load_catalog():
    path = REPO_ROOT / "admin_ui" / "backend" / "api" / "models_catalog.py"
    spec = importlib.util.spec_from_file_location("_ava_models_catalog_sv_filter", path)
    catalog = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(catalog)
    return catalog


@pytest.mark.parametrize("language,expected", [("zh-CN", True), ("en-US", True), ("ko-KR", True), ("es-ES", False), ("ru-RU", False)])
def test_sense_voice_offered_only_for_supported_languages(language, expected):
    catalog = _load_catalog()
    ids = {m["id"] for m in catalog.get_models_by_language(language)["stt"]}
    assert ("sherpa_sense_voice_offline" in ids) is expected


def test_multi_models_without_supported_languages_still_match_everywhere():
    catalog = _load_catalog()
    ids = {m["id"] for m in catalog.get_models_by_language("es-ES")["stt"]}
    assert "faster_whisper_base" in ids
