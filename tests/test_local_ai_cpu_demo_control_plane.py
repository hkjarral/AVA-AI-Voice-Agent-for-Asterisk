from __future__ import annotations

import importlib
import sys
from dataclasses import replace
from pathlib import Path


LOCAL_AI_DIR = str(Path(__file__).resolve().parents[1] / "local_ai_server")


def _ensure_path(path: str) -> None:
    if path not in sys.path:
        sys.path.insert(0, path)


def _load_module(name: str):
    _ensure_path(LOCAL_AI_DIR)
    return importlib.import_module(name)


def test_control_plane_applies_faster_whisper_cpu_demo_fields():
    config = _load_module("config")
    cp = _load_module("control_plane")
    cfg = config.LocalAIConfig.from_env()

    new_cfg, changed = cp.apply_switch_model_request(
        cfg,
        {
            "stt_backend": "faster_whisper",
            "stt_config": {
                "model": "tiny.en",
                "device": "cpu",
                "compute_type": "int8",
                "faster_whisper_language": "en",
            },
        },
    )

    assert new_cfg.stt_backend == "faster_whisper"
    assert new_cfg.faster_whisper_model == "tiny.en"
    assert new_cfg.faster_whisper_device == "cpu"
    assert new_cfg.faster_whisper_compute == "int8"
    assert new_cfg.faster_whisper_language == "en"
    assert "stt_backend=faster_whisper" in changed
    assert "faster_whisper_model=tiny.en" in changed


def test_control_plane_applies_runtime_cpu_demo_flags():
    config = _load_module("config")
    cp = _load_module("control_plane")
    cfg = replace(
        config.LocalAIConfig.from_env(),
        enable_filler_audio=True,
        llm_streaming_tts_overlap=True,
    )

    new_cfg, changed = cp.apply_switch_model_request(
        cfg,
        {
            "runtime_config": {
                "enable_filler_audio": False,
                "llm_streaming_tts_overlap": False,
            },
        },
    )

    assert new_cfg.enable_filler_audio is False
    assert new_cfg.llm_streaming_tts_overlap is False
    assert "enable_filler_audio=0" in changed
    assert "llm_streaming_tts_overlap=0" in changed


def test_control_plane_ignores_runtime_no_ops():
    config = _load_module("config")
    cp = _load_module("control_plane")
    cfg = replace(
        config.LocalAIConfig.from_env(),
        enable_filler_audio=False,
        llm_streaming_tts_overlap=False,
    )

    new_cfg, changed = cp.apply_switch_model_request(
        cfg,
        {
            "runtime_config": {
                "enable_filler_audio": False,
                "llm_streaming_tts_overlap": False,
            },
        },
    )

    assert new_cfg == cfg
    assert changed == []


def test_model_manager_reload_gate_allows_runtime_only_without_model_reload():
    manager = _load_module("model_manager")

    assert manager._requires_model_reload(["enable_filler_audio=1"]) is False
    assert manager._requires_model_reload(["llm_streaming_tts_overlap=0"]) is False
    assert manager._requires_model_reload(["llm_context=2048"]) is True
