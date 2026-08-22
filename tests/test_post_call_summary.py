import asyncio
from unittest.mock import patch

import pytest

from src.config import AppConfig, OpenAIProviderConfig
from src.engine import Engine
from src.pipelines.base import LLMComponent, LLMResponse
from src.pipelines.orchestrator import PipelineOrchestrator, PipelineOrchestratorError
from src.post_call_summary import PostCallSummaryService


def _app_config(providers=None):
    return AppConfig(
        default_provider="local",
        providers=providers or {},
        asterisk={"host": "127.0.0.1", "username": "ari", "password": "secret"},
        llm={"initial_greeting": "hi", "prompt": "prompt"},
    )


class _RecordingLLM(LLMComponent):
    def __init__(self, component_key, events):
        self.component_key = component_key
        self.events = events

    async def start(self):
        self.events.append("start")

    async def open_call(self, call_id, options):
        self.events.append(("open", call_id, options))

    async def generate(self, call_id, transcript, context, options):
        self.events.append(("generate", transcript, context, options))
        return LLMResponse(text="Concise result")

    async def close_call(self, call_id):
        self.events.append(("close", call_id))

    async def stop(self):
        self.events.append("stop")


class _SlowRecordingLLM(_RecordingLLM):
    async def generate(self, call_id, transcript, context, options):
        self.events.append(("generate", transcript, context, options))
        await asyncio.sleep(0.05)
        return LLMResponse(text="late")


@pytest.mark.asyncio
async def test_generate_once_uses_explicit_component_and_closes_it():
    events = []
    orchestrator = PipelineOrchestrator(
        _app_config({"chosen_llm": {"type": "unsupported", "chat_model": "chosen-model"}}),
        registry={"chosen_llm": lambda key, options: _RecordingLLM(key, events)},
    )

    text, model = await orchestrator.generate_once(
        component_key="chosen_llm",
        call_id="summary:1",
        transcript="user: hello",
        system_prompt="Summarize safely.",
        max_words=80,
        timeout_sec=5,
    )

    assert text == "Concise result"
    assert model == "chosen-model"
    opened = next(event for event in events if isinstance(event, tuple) and event[0] == "open")
    generate = next(event for event in events if isinstance(event, tuple) and event[0] == "generate")
    assert opened[2]["system_prompt"] == "Summarize safely."
    assert opened[2]["instructions"] == "Summarize safely."
    assert generate[2]["system_prompt"] == "Summarize safely."
    assert generate[3]["system_prompt"] == "Summarize safely."
    assert generate[3]["instructions"] == "Summarize safely."
    assert generate[3]["tools"] == []
    assert events[-2:] == [("close", "summary:1"), "stop"]


@pytest.mark.asyncio
async def test_unknown_explicit_provider_fails_without_fallback():
    orchestrator = PipelineOrchestrator(_app_config())
    with pytest.raises(PipelineOrchestratorError, match="unavailable or not configured"):
        await orchestrator.generate_once(
            component_key="missing_llm",
            call_id="summary:2",
            transcript="user: hello",
            system_prompt="Summarize.",
            max_words=50,
            timeout_sec=1,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "component_key,providers",
    [
        ("ollama_llm", {"ollama_llm": {"type": "ollama", "enabled": False}}),
        (
            "google_llm",
            {"google": {"enabled": False, "api_key": "test-key"}},
        ),
        (
            "telnyx_llm",
            {"telnyx": {"enabled": False, "api_key": "test-key"}},
        ),
        (
            "minimax_llm",
            {"minimax": {"enabled": False, "api_key": "test-key"}},
        ),
    ],
)
async def test_disabled_summary_provider_is_rejected(component_key, providers):
    orchestrator = PipelineOrchestrator(_app_config(providers))

    with pytest.raises(PipelineOrchestratorError, match="disabled"):
        await orchestrator.generate_once(
            component_key=component_key,
            call_id="summary:disabled",
            transcript="user: private transcript",
            system_prompt="Summarize.",
            max_words=50,
            timeout_sec=1,
        )


def test_summary_service_construction_failure_is_fail_open():
    engine = Engine.__new__(Engine)
    engine.pipeline_orchestrator = object()

    with patch(
        "src.post_call_summary.PostCallSummaryService",
        side_effect=RuntimeError("construction failed"),
    ):
        assert engine._post_call_summary_generator() is None


def test_configured_model_supports_typed_configs_and_adapter_defaults():
    typed = PipelineOrchestrator(_app_config({
        "openai_llm": OpenAIProviderConfig(api_key="test-key", chat_model="gpt-5-mini"),
    }))
    assert typed._configured_llm_model("openai_llm") == "gpt-5-mini"

    defaulted = PipelineOrchestrator(_app_config({
        "deepseek_llm": {"type": "openai", "api_key": "test-key"},
    }))
    assert defaulted._configured_llm_model("deepseek_llm") == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_summary_service_timeout_returns_safe_diagnostics():
    class _SlowOrchestrator:
        async def generate_once(self, **kwargs):
            await asyncio.sleep(0.05)
            return "late", "model"

    result = await PostCallSummaryService(_SlowOrchestrator()).generate(
        provider="slow_llm",
        call_id="call-3",
        conversation_history=[{"role": "user", "content": "hello"}],
        system_prompt="Summarize.",
        max_words=50,
        timeout_ms=1,
    )
    assert result.text == ""
    assert result.status == "timeout"
    assert result.error_code == "timeout"


@pytest.mark.asyncio
async def test_summary_timeout_still_closes_one_shot_component():
    events = []
    orchestrator = PipelineOrchestrator(
        _app_config({"slow_llm": {"type": "unsupported"}}),
        registry={"slow_llm": lambda key, options: _SlowRecordingLLM(key, events)},
    )

    result = await PostCallSummaryService(orchestrator).generate(
        provider="slow_llm",
        call_id="call-4",
        conversation_history=[{"role": "user", "content": "hello"}],
        system_prompt="Summarize.",
        max_words=50,
        timeout_ms=1,
    )

    assert result.status == "timeout"
    assert events[-2:] == [("close", "post-call-summary:call-4"), "stop"]
