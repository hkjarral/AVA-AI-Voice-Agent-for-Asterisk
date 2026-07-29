import pytest
from types import SimpleNamespace

from src.config import AppConfig
from src.pipelines.orchestrator import PipelineOrchestrator, PipelineOrchestratorError


async def _healthy_connectivity(_name, _entry):
    return {"healthy": True, "failures": []}


def _build_app_config_with_one_invalid_pipeline() -> AppConfig:
    providers = {"openai": {"api_key": "test-key"}}
    pipelines = {
        "openai_stack": {
            "stt": "openai_stt",
            "llm": "openai_llm",
            "tts": "openai_tts",
        },
        # Missing GOOGLE_API_KEY by design; should be treated as invalid rather than
        # silently resolved to placeholder adapters.
        "google_stack": {
            "stt": "google_stt",
            "llm": "google_llm",
            "tts": "google_tts",
        },
    }
    return AppConfig(
        default_provider="openai",
        providers=providers,
        asterisk={"host": "127.0.0.1", "username": "ari", "password": "secret"},
        llm={"initial_greeting": "hi", "prompt": "prompt"},
        audio_transport="audiosocket",
        downstream_mode="stream",
        pipelines=pipelines,
        active_pipeline="openai_stack",
    )


@pytest.mark.asyncio
async def test_orchestrator_skips_invalid_pipelines_and_keeps_valid_ones(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    app_config = _build_app_config_with_one_invalid_pipeline()
    orchestrator = PipelineOrchestrator(app_config)
    monkeypatch.setattr(orchestrator, "_validate_pipeline_connectivity", _healthy_connectivity)
    await orchestrator.start()

    assert orchestrator.started
    assert "google_stack" in orchestrator._invalid_pipelines
    status = orchestrator.pipeline_status()
    assert status["openai_stack"]["healthy"] is True
    assert status["google_stack"]["valid"] is False
    assert status["google_stack"]["healthy"] is False

    with pytest.raises(PipelineOrchestratorError, match="google_stack.*invalid"):
        orchestrator.get_pipeline("call-1", "google_stack")


@pytest.mark.asyncio
async def test_orchestrator_rejects_explicit_missing_pipeline(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    orchestrator = PipelineOrchestrator(_build_app_config_with_one_invalid_pipeline())
    monkeypatch.setattr(orchestrator, "_validate_pipeline_connectivity", _healthy_connectivity)
    await orchestrator.start()

    with pytest.raises(PipelineOrchestratorError, match="does_not_exist.*not found"):
        orchestrator.get_pipeline("call-2", "does_not_exist")


@pytest.mark.asyncio
async def test_orchestrator_rejects_invalid_active_pipeline(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    app_config = _build_app_config_with_one_invalid_pipeline()
    app_config.active_pipeline = "google_stack"
    orchestrator = PipelineOrchestrator(app_config)
    monkeypatch.setattr(orchestrator, "_validate_pipeline_connectivity", _healthy_connectivity)
    await orchestrator.start()

    with pytest.raises(PipelineOrchestratorError, match="google_stack.*invalid"):
        orchestrator.get_pipeline("call-3")


@pytest.mark.asyncio
async def test_orchestrator_uses_first_valid_pipeline_only_without_explicit_selection(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    app_config = _build_app_config_with_one_invalid_pipeline()
    app_config.active_pipeline = None
    orchestrator = PipelineOrchestrator(app_config)
    monkeypatch.setattr(orchestrator, "_validate_pipeline_connectivity", _healthy_connectivity)
    await orchestrator.start()

    resolution = orchestrator.get_pipeline("call-4")
    assert resolution is not None
    assert resolution.pipeline_name == "openai_stack"


@pytest.mark.asyncio
async def test_local_pipeline_connectivity_can_recover_after_startup(monkeypatch):
    orchestrator = PipelineOrchestrator(_build_app_config_with_one_invalid_pipeline())
    local_entry = SimpleNamespace(stt="local_stt", llm="native_llm", tts="local_tts")
    orchestrator.config.pipelines = {"local_stack": local_entry}
    orchestrator._started = True
    orchestrator._pipeline_validation_results = {
        "local_stack": {"healthy": False, "failures": [{"component": "stt"}]}
    }

    calls = []

    async def now_healthy(name, entry):
        calls.append((name, entry))
        return {"healthy": True, "failures": []}

    monkeypatch.setattr(orchestrator, "_validate_pipeline_connectivity", now_healthy)

    remaining = await orchestrator.refresh_unhealthy_local_pipelines()

    assert remaining == 0
    assert calls == [("local_stack", local_entry)]
    assert orchestrator.pipeline_status()["local_stack"]["healthy"] is True


@pytest.mark.asyncio
async def test_cloud_only_pipeline_is_not_background_polled(monkeypatch):
    orchestrator = PipelineOrchestrator(_build_app_config_with_one_invalid_pipeline())
    cloud_entry = SimpleNamespace(stt="openai_stt", llm="openai_llm", tts="openai_tts")
    orchestrator.config.pipelines = {"cloud_stack": cloud_entry}
    orchestrator._started = True
    orchestrator._pipeline_validation_results = {
        "cloud_stack": {"healthy": False, "failures": [{"component": "stt"}]}
    }

    async def should_not_run(_name, _entry):
        raise AssertionError("cloud-only connectivity must not be background polled")

    monkeypatch.setattr(orchestrator, "_validate_pipeline_connectivity", should_not_run)

    assert await orchestrator.refresh_unhealthy_local_pipelines() == 0
