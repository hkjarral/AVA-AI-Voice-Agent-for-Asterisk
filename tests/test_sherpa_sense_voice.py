"""
Tests for SenseVoice support in the Sherpa offline STT path (issue #407):
- Model-layout detection: SenseVoice (model*.onnx + tokens.txt) vs transducer
- Recognizer construction via a mocked sherpa_onnx module
- Rich-transcription tag stripping and language normalization
- Config env vars, switch_model handling, and protocol schema fields
- Model metadata validation, ambiguous/incomplete directories, decode diagnostics
- Models catalog entry and language filter (incl. Kroko Cloud region codes)
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


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _len_field(field: int, payload: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(payload)) + payload


def _onnx_bytes(metadata: dict, graph_size: int = 4096) -> bytes:
    """Minimal ONNX ModelProto: ir_version, a large opaque graph, then metadata_props."""
    data = _varint((1 << 3) | 0) + _varint(8)  # ir_version = 8
    data += _len_field(7, b"\x00" * graph_size)  # graph (skipped by the reader)
    for key, value in metadata.items():
        entry = _len_field(1, key.encode()) + _len_field(2, str(value).encode())
        data += _len_field(14, entry)
    return data


def _sense_voice_metadata() -> dict:
    sb = _load_module("stt_backends")
    return {key: "1" for key in sb.SENSE_VOICE_REQUIRED_METADATA}


def _sense_voice_model(path: Path, metadata: dict | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_onnx_bytes(_sense_voice_metadata() if metadata is None else metadata))
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
        model_file = _sense_voice_model(model_dir / "model.int8.onnx")
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
        _sense_voice_model(model_dir / "model.int8.onnx")
        fp32 = _sense_voice_model(model_dir / "model.onnx")
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
        _sense_voice_model(model_dir / "model.int8.onnx")
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


    def test_non_sense_voice_single_file_model_is_rejected_before_native_load(self, tmp_path, fake_sherpa, caplog):
        # e.g. a Paraformer/Whisper export: model.onnx + tokens.txt but no SenseVoice metadata.
        model_dir = tmp_path / "sherpa-onnx-paraformer-zh"
        _sense_voice_model(model_dir / "model.int8.onnx", metadata={"model_type": "paraformer", "vocab_size": "8404"})
        _touch(model_dir / "tokens.txt")
        vad = _touch(tmp_path / "silero_vad.onnx")

        backend = _make_backend(model_dir, vad)
        with caplog.at_level("ERROR"):
            assert backend.initialize() is False
        assert fake_sherpa.calls == []
        assert "not a SenseVoice export" in caplog.text
        assert "lfr_window_size" in caplog.text

    def test_unreadable_model_file_is_rejected(self, tmp_path, fake_sherpa, caplog):
        model_dir = tmp_path / "sense-voice"
        (model_dir).mkdir()
        (model_dir / "model.onnx").write_bytes(b"\xff" * 64)  # truncated varint / garbage
        _touch(model_dir / "tokens.txt")
        vad = _touch(tmp_path / "silero_vad.onnx")

        backend = _make_backend(model_dir, vad)
        with caplog.at_level("ERROR"):
            assert backend.initialize() is False
        assert fake_sherpa.calls == []
        assert "not a readable ONNX model" in caplog.text

    def test_ambiguous_directory_prefers_transducer_and_warns(self, tmp_path, fake_sherpa, caplog):
        model_dir = tmp_path / "mixed"
        for name in ("tokens.txt", "encoder.onnx", "decoder.onnx", "joiner.onnx"):
            _touch(model_dir / name)
        _sense_voice_model(model_dir / "model.onnx")
        vad = _touch(tmp_path / "silero_vad.onnx")

        backend = _make_backend(model_dir, vad)
        with caplog.at_level("WARNING"):
            assert backend.initialize() is True
        assert backend.model_family == "transducer"
        assert fake_sherpa.calls[-1][0] == "transducer"
        assert "ignoring model.onnx" in caplog.text

    def test_empty_directory_reports_missing_files(self, tmp_path, fake_sherpa, caplog):
        model_dir = tmp_path / "empty"
        model_dir.mkdir()
        vad = _touch(tmp_path / "silero_vad.onnx")

        backend = _make_backend(model_dir, vad)
        with caplog.at_level("ERROR"):
            assert backend.initialize() is False
        assert fake_sherpa.calls == []
        assert "Missing model files" in caplog.text


class TestOnnxMetadataReader:
    def test_reads_metadata_after_large_graph(self, tmp_path):
        sb = _load_module("stt_backends")
        path = tmp_path / "model.onnx"
        path.write_bytes(_onnx_bytes({"lang_zh": "3", "comment": "SenseVoice"}, graph_size=200_000))
        assert sb.read_onnx_metadata(str(path)) == {"lang_zh": "3", "comment": "SenseVoice"}

    def test_missing_keys_listed(self, tmp_path):
        sb = _load_module("stt_backends")
        meta = _sense_voice_metadata()
        meta.pop("neg_mean")
        path = _sense_voice_model(tmp_path / "model.onnx", metadata=meta)
        assert sb.missing_sense_voice_metadata(str(path)) == ["neg_mean"]

    def test_truncated_file_returns_none(self, tmp_path):
        sb = _load_module("stt_backends")
        path = tmp_path / "model.onnx"
        path.write_bytes(_onnx_bytes({"a": "b"})[:20])
        assert sb.missing_sense_voice_metadata(str(path)) is None


class TestSenseVoiceTranscripts:
    def test_process_audio_strips_rich_tags(self, tmp_path, fake_sherpa):
        model_dir = tmp_path / "sense-voice"
        _sense_voice_model(model_dir / "model.int8.onnx")
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

    def test_decode_log_has_call_id_and_timing(self, tmp_path, fake_sherpa, caplog):
        model_dir = tmp_path / "sense-voice"
        _sense_voice_model(model_dir / "model.int8.onnx")
        _touch(model_dir / "tokens.txt")
        vad_path = _touch(tmp_path / "silero_vad.onnx")
        backend = _make_backend(model_dir, vad_path)
        assert backend.initialize() is True

        segment = _FakeSegment(np.full(8000, 0.1, dtype=np.float32).tolist())
        with caplog.at_level("INFO"):
            backend.finalize(_FakeVAD(segments=[segment]), call_id="call-42")
        line = next(r.getMessage() for r in caplog.records if "decode - " in r.getMessage())
        assert "call_id=call-42" in line
        assert "family=sense_voice" in line
        assert "audio_ms=500" in line
        assert "decode_ms=" in line and "rtf=" in line

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

    def test_repeated_top_level_values_are_no_change(self):
        cp, cfg = self._cp_and_config()
        request = {"sherpa_sense_voice_language": "en", "sherpa_sense_voice_use_itn": False}
        cfg, changed = cp.apply_switch_model_request(cfg, request)
        assert len(changed) == 2
        _, changed_again = cp.apply_switch_model_request(cfg, request)
        assert changed_again == []

    def test_repeated_request_does_not_reload(self):
        import asyncio

        mm = _load_module("model_manager")
        cfg = _load_module("config").LocalAIConfig()

        class _Server:
            config = cfg
            reloads = 0

            def _apply_config(self, config):
                pass

            async def reload_models(self):
                _Server.reloads += 1

        server = _Server()
        manager = mm.ModelManager(server)
        request = {"sherpa_sense_voice_language": "ja", "sherpa_sense_voice_use_itn": False}
        first = asyncio.run(manager.switch_model(dict(request)))
        second = asyncio.run(manager.switch_model(dict(request)))
        assert first["status"] == "success"
        assert second["status"] == "no_change"
        assert _Server.reloads == 1

    def test_stt_config_invalid_language_ignored(self):
        cp, cfg = self._cp_and_config()
        new_cfg, changed = cp.apply_switch_model_request(
            cfg, {"stt_backend": "sherpa", "stt_config": {"sherpa_sense_voice_language": "fr"}}
        )
        assert new_cfg.sherpa_sense_voice_language == "auto"
        assert not any(c.startswith("sherpa_sense_voice_language") for c in changed)

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


class TestSenseVoiceStatus:
    def _server(self, backend):
        class _S:
            stt_backend = "sherpa"
            sherpa_backend = backend

        return _S()

    def test_reports_effective_sense_voice_settings(self):
        sb_status = _load_module("status_builder")

        class _B:
            model_family = "sense_voice"
            sense_voice_language = "yue"
            sense_voice_use_itn = False

        assert sb_status._sherpa_sense_voice_status(self._server(_B())) == {
            "sherpa_model_family": "sense_voice",
            "sherpa_sense_voice_language": "yue",
            "sherpa_sense_voice_use_itn": False,
        }

    def test_transducer_and_unloaded_hide_sense_voice_settings(self):
        sb_status = _load_module("status_builder")

        class _T:
            model_family = "transducer"
            sense_voice_language = "auto"
            sense_voice_use_itn = True

        assert sb_status._sherpa_sense_voice_status(self._server(_T()))["sherpa_sense_voice_language"] is None
        assert sb_status._sherpa_sense_voice_status(self._server(None))["sherpa_model_family"] is None


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


@pytest.mark.parametrize(
    "language,expected",
    [("en-US", True), ("en", True), ("en-GB", True), ("es-ES", True), ("he-IL", True), ("zh-CN", False), ("ru-RU", False)],
)
def test_kroko_cloud_region_codes_still_match(language, expected):
    # Regression: base-code comparison dropped Kroko Cloud (en-US style entries) everywhere.
    catalog = _load_catalog()
    ids = {m["id"] for m in catalog.get_models_by_language(language)["stt"]}
    assert ("kroko_cloud" in ids) is expected


@pytest.mark.parametrize("key,value", [
    ("vocab_size", ""), ("vocab_size", "0"), ("vocab_size", "-1"),
    ("vocab_size", "2147483648"), ("lang_en", "english"),
    ("lfr_window_shift", "0"), ("neg_mean", ""),
    ("neg_mean", "1,bad"), ("inv_stddev", "inf"), ("inv_stddev", "nan"),
    ("blank_id", "-1"), ("neg_mean", "1_000"),
    ("inv_stddev", "1e39"), ("neg_mean", "1, ,2"),
])
def test_invalid_metadata_is_rejected_before_native_construction(tmp_path, fake_sherpa, key, value):
    metadata = _sense_voice_metadata()
    metadata[key] = value
    _sense_voice_model(tmp_path / "model" / "model.onnx", metadata)
    _touch(tmp_path / "model" / "tokens.txt")
    backend = _make_backend(tmp_path / "model", _touch(tmp_path / "vad.onnx"))
    assert backend.initialize() is False
    assert fake_sherpa.calls == []


def test_valid_signed_float_vectors_and_optional_blank_id_are_accepted(tmp_path, fake_sherpa):
    metadata = _sense_voice_metadata()
    metadata.update(neg_mean="0.1,-2e-2,3", inv_stddev="1,2,3", blank_id="0")
    _sense_voice_model(tmp_path / "model" / "model.onnx", metadata)
    _touch(tmp_path / "model" / "tokens.txt")
    backend = _make_backend(tmp_path / "model", _touch(tmp_path / "vad.onnx"))
    assert backend.initialize() is True


@pytest.mark.parametrize("fail_once", [False, True])
def test_dry_run_keeps_a_pending_model_reload_until_success(fail_once):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    cfg = _load_module("config").LocalAIConfig()
    reload = AsyncMock()
    if fail_once:
        reload.side_effect = [RuntimeError("load failed"), None]
    server = SimpleNamespace(config=cfg, reload_models=reload, _apply_config=lambda config: None)
    manager = _load_module("model_manager").ModelManager(server)
    request = {"sherpa_sense_voice_language": "zh"}
    staged = asyncio.run(manager.switch_model({**request, "dry_run": True}))
    assert staged["status"] == "success"
    assert reload.await_count == 0
    if fail_once:
        with pytest.raises(RuntimeError, match="load failed"):
            asyncio.run(manager.switch_model(request))
    applied = asyncio.run(manager.switch_model(request))
    assert applied["status"] == "success"
    assert reload.await_count == (2 if fail_once else 1)
    assert asyncio.run(manager.switch_model(request))["status"] == "no_change"


@pytest.mark.parametrize("component", ["stt", "llm", "tts"])
def test_degraded_reload_reports_error_and_retries_unchanged_config(component):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    cfg = _load_module("config").LocalAIConfig()
    server = SimpleNamespace(config=cfg, startup_errors={}, _apply_config=lambda config: None)
    attempts = 0

    async def reload():
        nonlocal attempts
        attempts += 1
        server.startup_errors = {component: "load failed"} if attempts == 1 else {}

    server.reload_models = AsyncMock(side_effect=reload)
    manager = _load_module("model_manager").ModelManager(server)
    request = {"sherpa_sense_voice_language": "zh"}
    failed = asyncio.run(manager.switch_model(request))
    assert failed["status"] == "error"
    assert f"{component}: load failed" in failed["message"]
    assert manager._pending_model_reload is True
    assert asyncio.run(manager.switch_model(request))["status"] == "success"
    assert server.reload_models.await_count == 2
    assert manager._pending_model_reload is False
    assert asyncio.run(manager.switch_model(request))["status"] == "no_change"


def test_concurrent_dry_run_stays_pending_after_earlier_reload():
    import asyncio
    from types import SimpleNamespace

    async def scenario():
        started = asyncio.Event()
        finish = asyncio.Event()
        loaded = []
        cfg = _load_module("config").LocalAIConfig()
        server = SimpleNamespace(config=cfg, startup_errors={}, _apply_config=lambda config: None)

        async def reload():
            loaded.append(server.config.sherpa_sense_voice_language)
            started.set()
            await finish.wait()

        server.reload_models = reload
        manager = _load_module("model_manager").ModelManager(server)
        first = asyncio.create_task(manager.switch_model({"sherpa_sense_voice_language": "zh"}))
        await asyncio.wait_for(started.wait(), timeout=1)
        staged = asyncio.create_task(manager.switch_model({"sherpa_sense_voice_language": "ja", "dry_run": True}))
        await asyncio.sleep(0)
        assert not staged.done()
        assert server.config.sherpa_sense_voice_language == "zh"
        finish.set()
        assert (await first)["status"] == "success"
        assert (await staged)["status"] == "success"
        assert loaded == ["zh"]
        assert manager._pending_model_reload is True
        assert (await manager.switch_model({"sherpa_sense_voice_language": "ja"}))["status"] == "success"
        assert loaded == ["zh", "ja"]
        assert manager._pending_model_reload is False

    asyncio.run(scenario())
