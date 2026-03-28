from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

LOCAL_AI_DIR = str(Path(__file__).resolve().parents[1] / "local_ai_server")
ADMIN_UI_DIR = str(Path(__file__).resolve().parents[1] / "admin_ui" / "backend")


def _ensure_path(path: str) -> None:
    if path not in sys.path:
        sys.path.insert(0, path)


def _load_module(name: str, directory: str):
    _ensure_path(directory)
    return importlib.import_module(name)


class TestToneConfigAndControlPlane:
    def test_config_reads_tone_env(self, monkeypatch):
        monkeypatch.setenv("LOCAL_STT_BACKEND", "tone")
        monkeypatch.setenv("TONE_MODEL_PATH", "/app/models/stt/t-one")
        monkeypatch.setenv("TONE_DECODER_TYPE", "greedy")
        monkeypatch.setenv("TONE_KENLM_PATH", "/app/models/stt/t-one/kenlm.bin")
        config = _load_module("config", LOCAL_AI_DIR)
        importlib.reload(config)
        cfg = config.LocalAIConfig.from_env()
        assert cfg.stt_backend == "tone"
        assert cfg.tone_model_path == "/app/models/stt/t-one"
        assert cfg.tone_decoder_type == "greedy"
        assert cfg.tone_kenlm_path == "/app/models/stt/t-one/kenlm.bin"

    def test_control_plane_applies_tone_fields(self):
        config = _load_module("config", LOCAL_AI_DIR)
        cp = _load_module("control_plane", LOCAL_AI_DIR)
        cfg = config.LocalAIConfig.from_env()
        new_cfg, changed = cp.apply_switch_model_request(
            cfg,
            {
                "stt_backend": "tone",
                "tone_model_path": "/app/models/stt/t-one",
                "tone_decoder_type": "beam_search",
                "tone_kenlm_path": "/app/models/stt/t-one/kenlm.bin",
            },
        )
        assert new_cfg.stt_backend == "tone"
        assert new_cfg.tone_model_path.endswith("/t-one")
        assert new_cfg.tone_decoder_type == "beam_search"
        assert new_cfg.tone_kenlm_path.endswith("kenlm.bin")
        assert any("stt_backend=tone" in item for item in changed)

    def test_protocol_contract_has_tone_fields(self):
        protocol = _load_module("protocol_contract", LOCAL_AI_DIR)
        props = protocol.PROTOCOL_SCHEMA["$defs"]["SwitchModelRequest"]["properties"]
        assert "tone_model_path" in props
        assert props["tone_decoder_type"]["enum"] == ["beam_search", "greedy"]
        assert "tone_kenlm_path" in props


class _FakePhrase:
    def __init__(self, text: str):
        self.text = text


class _FakeDecoder:
    def forward(self, logprobs):
        return "частичный текст"


class _FakePipeline:
    def __init__(self):
        self.decoder = _FakeDecoder()
        self.calls = 0

    def forward(self, chunk, state, is_last: bool = False):
        self.calls += 1
        if self.calls == 1:
            splitter_state = SimpleNamespace(past_logprobs=np.ones((8, 35), dtype=np.float32))
            return ([], ("model_state", splitter_state))
        return ([_FakePhrase("полный текст")], ("model_state", SimpleNamespace(past_logprobs=np.zeros((0, 35), dtype=np.float32))))

    def finalize(self, state):
        return ([_FakePhrase("финал")], ("done", SimpleNamespace(past_logprobs=np.zeros((0, 35), dtype=np.float32))))


class TestToneBackendRuntime:
    def test_process_audio_emits_partial_then_final(self):
        backends = _load_module("stt_backends", LOCAL_AI_DIR)
        backend = backends.ToneSTTBackend(model_path="/fake/model")
        backend.pipeline = _FakePipeline()
        backend._initialized = True
        state = backend.create_session_state()
        assert state is not None

        updates1 = backend.process_audio(state, np.zeros((backends.ToneSTTBackend.CHUNK_SAMPLES,), dtype=np.int32))
        assert any(item["is_partial"] for item in updates1)
        updates2 = backend.process_audio(state, np.zeros((backends.ToneSTTBackend.CHUNK_SAMPLES,), dtype=np.int32))
        assert any(item["is_final"] and item["text"] == "полный текст" for item in updates2)

    def test_finalize_emits_remaining_phrase(self):
        backends = _load_module("stt_backends", LOCAL_AI_DIR)
        backend = backends.ToneSTTBackend(model_path="/fake/model")
        backend.pipeline = _FakePipeline()
        backend._initialized = True
        updates = backend.finalize({"pipeline_state": ("state", SimpleNamespace(past_logprobs=np.ones((1, 35), dtype=np.float32)))})
        assert updates == [{"text": "финал", "is_final": True, "is_partial": False, "confidence": None}]

    def test_capabilities_detect_tone_when_package_present(self, monkeypatch):
        fake_tone = ModuleType("tone")
        fake_pipeline = ModuleType("tone.pipeline")
        fake_pipeline.StreamingCTCPipeline = object
        monkeypatch.setitem(sys.modules, "tone", fake_tone)
        monkeypatch.setitem(sys.modules, "tone.pipeline", fake_pipeline)
        capabilities = _load_module("capabilities", LOCAL_AI_DIR)
        config = _load_module("config", LOCAL_AI_DIR)
        caps = capabilities.detect_capabilities(config.LocalAIConfig.from_env())
        assert caps["tone"] is True


@pytest.mark.skipif(importlib.util.find_spec("fastapi") is None, reason="fastapi not installed")
class TestAdminToneSwitchMapping:
    def test_admin_switch_payload_and_env(self):
        _ensure_path(ADMIN_UI_DIR)
        api = _load_module("api.local_ai", ADMIN_UI_DIR)
        req = api.SwitchModelRequest(
            model_type="stt",
            backend="tone",
            model_path="/app/models/stt/t-one",
            tone_decoder_type="beam_search",
            tone_kenlm_path="/app/models/stt/t-one/kenlm.bin",
        )
        payload = api._build_local_ai_ws_switch_payload(req)
        env_updates, yaml_updates = api._build_local_ai_env_and_yaml_updates(req)
        assert payload["tone_model_path"] == "/app/models/stt/t-one"
        assert payload["tone_decoder_type"] == "beam_search"
        assert env_updates["TONE_MODEL_PATH"] == "/app/models/stt/t-one"
        assert yaml_updates["tone_decoder_type"] == "beam_search"
