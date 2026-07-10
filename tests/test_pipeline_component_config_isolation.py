import pytest

from src.config import AppConfig
from src.pipelines.orchestrator import PipelineOrchestrator, PipelineOrchestratorError


def _config(providers, *, stt="local_stt", llm="local_llm", tts="local_tts"):
    return AppConfig(
        default_provider="local",
        providers=providers,
        asterisk={"host": "127.0.0.1", "username": "ari", "password": "secret"},
        llm={"initial_greeting": "hi", "prompt": "prompt"},
        pipelines={"test": {"stt": stt, "llm": llm, "tts": tts}},
        active_pipeline="test",
    )


def test_local_component_blocks_are_hydrated_independently():
    orchestrator = PipelineOrchestrator(
        _config(
            {
                "local": {"enabled": True, "ws_url": "ws://base:8765"},
                "local_stt": {"enabled": True, "ws_url": "ws://stt:8765"},
                "local_llm": {"enabled": False, "ws_url": "ws://llm:8765"},
                "local_tts": {"enabled": True, "ws_url": "ws://tts:8765"},
            }
        )
    )

    configs = orchestrator._local_component_configs
    assert configs["local_stt"].effective_ws_url == "ws://stt:8765"
    assert configs["local_tts"].effective_ws_url == "ws://tts:8765"
    assert "local_llm" not in configs

    with pytest.raises(PipelineOrchestratorError, match="local_llm"):
        orchestrator._validate_pipeline_entry("test", orchestrator.config.pipelines["test"])


def test_base_local_block_remains_a_compatibility_fallback_for_all_roles():
    orchestrator = PipelineOrchestrator(
        _config({"local": {"enabled": True, "ws_url": "ws://compat:8765"}})
    )

    assert set(orchestrator._local_component_configs) == {
        "local_stt",
        "local_llm",
        "local_tts",
    }
    assert {
        config.effective_ws_url
        for config in orchestrator._local_component_configs.values()
    } == {"ws://compat:8765"}


def test_openai_component_blocks_do_not_overwrite_each_other():
    orchestrator = PipelineOrchestrator(
        _config(
            {
                "openai": {"api_key": "test-key"},
                "openai_stt": {
                    "enabled": True,
                    "stt_base_url": "https://stt.example/v1/audio/transcriptions",
                    "stt_model": "stt-model",
                },
                "openai_llm": {
                    "enabled": False,
                    "chat_base_url": "https://llm.example/v1",
                    "chat_model": "llm-model",
                },
                "openai_tts": {
                    "enabled": True,
                    "tts_base_url": "https://tts.example/v1/audio/speech",
                    "tts_model": "tts-model",
                },
            },
            stt="openai_stt",
            llm="openai_llm",
            tts="openai_tts",
        )
    )

    configs = orchestrator._openai_component_configs
    assert configs["openai_stt"].stt_base_url == "https://stt.example/v1/audio/transcriptions"
    assert configs["openai_stt"].stt_model == "stt-model"
    assert configs["openai_tts"].tts_base_url == "https://tts.example/v1/audio/speech"
    assert configs["openai_tts"].tts_model == "tts-model"
    assert "openai_llm" not in configs

    with pytest.raises(PipelineOrchestratorError, match="openai_llm"):
        orchestrator._validate_pipeline_entry("test", orchestrator.config.pipelines["test"])
