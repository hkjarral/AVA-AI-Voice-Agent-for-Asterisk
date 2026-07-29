from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType


LOCAL_AI_DIR = str(Path(__file__).resolve().parents[1] / "local_ai_server")


def _load(name: str):
    if LOCAL_AI_DIR not in sys.path:
        sys.path.insert(0, LOCAL_AI_DIR)
    module = importlib.import_module(name)
    return importlib.reload(module)


def test_silero_requires_prepared_model_cache(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "torch", ModuleType("torch"))
    config = _load("config")
    capabilities = _load("capabilities")

    empty_cache = tmp_path / "silero"
    empty_cache.mkdir()
    cfg = config.LocalAIConfig(silero_model_path=str(empty_cache))
    assert capabilities.detect_capabilities(cfg)["silero"] is False

    (empty_cache / "hubconf.py").write_text("# prepared cache\n", encoding="utf-8")
    assert capabilities.detect_capabilities(cfg)["silero"] is True


def test_matcha_requires_sherpa_and_both_model_files(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "sherpa_onnx", ModuleType("sherpa_onnx"))
    config = _load("config")
    capabilities = _load("capabilities")

    model = tmp_path / "model.onnx"
    vocoder = tmp_path / "vocoder.onnx"
    cfg = config.LocalAIConfig(
        matcha_model_path=str(model),
        matcha_vocoder_path=str(vocoder),
    )
    assert capabilities.detect_capabilities(cfg)["matcha"] is False

    model.touch()
    assert capabilities.detect_capabilities(cfg)["matcha"] is False

    vocoder.touch()
    assert capabilities.detect_capabilities(cfg)["matcha"] is True
