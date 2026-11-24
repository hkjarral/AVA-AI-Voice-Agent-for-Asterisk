import pytest

from src.config import AppConfig
from src.pipelines.orchestrator import PipelineOrchestrator, PipelineOrchestratorError
from src.pipelines.openai import OpenAILLMAdapter
from src.pipelines.deepgram import DeepgramSTTAdapter, DeepgramTTSAdapter
from src.pipelines.deepgram_flux import DeepgramFluxSTTAdapter
from src.pipelines.google import GoogleSTTAdapter, GoogleTTSAdapter


def _config(providers, *, stt="local_stt", llm="local_llm", tts="local_tts"):
    return AppConfig(
        default_provider="local",
        providers=providers,
        asterisk={"host": "127.0.0.1", "username": "ari", "password": "secret"},
        llm={"initial_greeting": "hi", "prompt": "prompt"},
        pipelines={"test": {"stt": stt, "llm": llm, "tts": tts}},
        active_pipeline="test",
    )


@pytest.mark.parametrize("entry,retained", [
    ({"type": "deepgram"}, True),
    ({"type": "operator_extension", "custom": "setting"}, True),
    ({"type": "deepgram", "enabled": False}, False),
    ({"type": "deepgram", "tts_model": "flux-kit-en", "tts_version": "v1"}, False),
    ({"type": "deepgram", "api_key_file": "/missing/explicit-secret"}, False),
    ("invalid-shape", False),
    ({"type": "deepgram", "api_key": "synthetic"}, False),
])
def test_injected_speech_extension_precedence(monkeypatch, entry, retained):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    extension = lambda *_: object()
    orchestrator = PipelineOrchestrator(_config({"customer_tts": entry}), registry={"customer_tts": extension})
    assert (orchestrator._resolve_factory("customer_tts") is extension) is retained


@pytest.mark.parametrize("raw_model", [None, "", "  "])
def test_canonical_openai_blank_model_hydration_matches_validation(raw_model):
    from src.config import validate_provider_settings
    providers = {"openai_tts": {"api_key": "synthetic", "tts_model": raw_model}}
    validate_provider_settings(providers, provider_names={"openai_tts"})
    orchestrator = PipelineOrchestrator(_config(providers))
    adapter = orchestrator._build_component("openai_tts", {})
    assert adapter._provider_defaults.tts_model == "gpt-realtime-2.1-mini"


def test_openai_factory_keeps_implicit_reasoning_distinct_from_explicit_override():
    orchestrator = PipelineOrchestrator(_config({"openai_llm": {"api_key": "synthetic"}}))
    adapter = orchestrator._build_component("openai_llm", {"model": "gpt-6.2-sol"})
    assert adapter._compose_options({})["reasoning_effort"] is None


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


def test_custom_openai_compatible_llm_reads_provider_scoped_key_file(tmp_path):
    key_file = tmp_path / "deepseek-key"
    key_file.write_text("deepseek-secret")
    orchestrator = PipelineOrchestrator(
        _config(
            {
                "deepseek_llm": {
                    "type": "openai",
                    "enabled": True,
                    "api_key_file": str(key_file),
                    "chat_base_url": "https://api.deepseek.com/v1",
                    "chat_model": "deepseek-chat",
                }
            },
            llm="deepseek_llm",
        )
    )

    adapter = orchestrator._build_component("deepseek_llm", {})
    assert isinstance(adapter, OpenAILLMAdapter)
    assert adapter._provider_defaults.api_key == "deepseek-secret"
    assert adapter._provider_defaults.chat_model == "deepseek-chat"


@pytest.mark.parametrize("kind,role,adapter_class", [
    ("google", "stt", GoogleSTTAdapter),
    ("google", "tts", GoogleTTSAdapter),
    ("deepgram", "stt", DeepgramSTTAdapter),
    ("deepgram", "tts", DeepgramTTSAdapter),
])
def test_custom_modular_speech_uses_own_key_and_config(tmp_path, kind, role, adapter_class):
    key_file = tmp_path / "speech-key"
    key_file.write_text("role-owned-secret")
    provider = {"type": kind, "api_key_file": str(key_file), "capabilities": [role]}
    if kind == "google":
        provider.update(tts_voice_name="es-ES-Neural2-A", stt_language_code="es-ES")
    else:
        provider.update(model="nova-2-phonecall", tts_model="aura-2-thalia-en")
    orchestrator = PipelineOrchestrator(_config({
        kind: {"api_key": "other-customer-secret"},
        f"customer_{role}": provider,
    }))
    adapter = orchestrator._build_component(f"customer_{role}", {})
    assert isinstance(adapter, adapter_class)
    assert adapter._provider_defaults.api_key == "role-owned-secret"
    if kind == "google":
        assert adapter._provider_defaults.stt_language_code == "es-ES"
    else:
        assert adapter._provider_defaults.model == "nova-2-phonecall"


@pytest.mark.parametrize("kind", ["google", "deepgram"])
@pytest.mark.parametrize("role", ["stt", "tts"])
@pytest.mark.parametrize("entry", [{"enabled": False}, {"api_key_file": "/missing/explicit-key"}])
def test_explicit_unavailable_speech_role_never_inherits_full_provider(monkeypatch, kind, role, entry):
    monkeypatch.delenv(f"{kind.upper()}_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    key = f"{kind}_{role}"
    orchestrator = PipelineOrchestrator(_config({
        "local": {"enabled": True},
        kind: {"api_key": "full-provider-secret"},
        key: {"type": kind, **entry},
    }, **{role: key}))
    with pytest.raises(PipelineOrchestratorError, match=key):
        orchestrator._validate_pipeline_entry("test", orchestrator.config.pipelines["test"])


def test_modular_deepgram_flux_model_selects_streaming_adapter():
    orchestrator = PipelineOrchestrator(_config({
        "customer_stt": {
            "type": "deepgram", "api_key": "synthetic-key",
            "model": "flux-general-multi", "language_hints": ["en", "es"],
        },
    }))
    adapter = orchestrator._build_component("customer_stt", {})
    assert isinstance(adapter, DeepgramFluxSTTAdapter)
    assert adapter._provider_defaults.model == "flux-general-multi"
    assert adapter._provider_defaults.language_hints == ["en", "es"]


@pytest.mark.parametrize("provider_key", ["google", "google_tts", "customer_tts"])
@pytest.mark.parametrize("explicit_key", [None, "explicit-cloud-key"])
def test_google_explicit_service_account_survives_factory_hydration(monkeypatch, provider_key, explicit_key):
    monkeypatch.setenv("GOOGLE_API_KEY", "ambient-gemini-key")
    entry = {"type": "google", "credentials_path": "/operator-cloud-service-account.json"}
    if explicit_key:
        entry["api_key"] = explicit_key
    orchestrator = PipelineOrchestrator(_config({provider_key: entry}))
    component_key = "google_tts" if provider_key == "google" else provider_key
    adapter = orchestrator._build_component(component_key, {})
    assert isinstance(adapter, GoogleTTSAdapter)
    assert (adapter._provider_defaults.api_key or None) == explicit_key
    assert adapter._credential_manager._api_key == explicit_key
    assert adapter._credential_manager._service_account_path == "/operator-cloud-service-account.json"


@pytest.mark.parametrize("role", ["stt", "tts"])
@pytest.mark.parametrize("entry", [
    {"type": "invalid-provider", "enabled": False},
    {"type": "invalid-provider", "enabled": True},
    {"type": "elevenlabs", "enabled": False},
    "invalid-shape",
])
def test_invalid_explicit_speech_entry_does_not_keep_canonical_factory(role, entry):
    key = f"deepgram_{role}"
    orchestrator = PipelineOrchestrator(_config({
        "local": {"enabled": True},
        "deepgram": {"api_key": "full-provider-secret"},
        key: entry,
    }, **{role: key}))
    with pytest.raises(PipelineOrchestratorError, match=key):
        orchestrator._validate_pipeline_entry("test", orchestrator.config.pipelines["test"])


@pytest.mark.parametrize("marker", ["tts", "modular", "full"])
def test_legacy_type_markers_preserve_canonical_openai_speech_config(marker):
    from src.pipelines.openai import OpenAITTSAdapter

    orchestrator = PipelineOrchestrator(_config({
        "openai": {"api_key": "legacy-shared-secret", "tts_model": "tts-1"},
        "openai_tts": {"type": marker, "voice": "alloy"},
    }))
    adapter = orchestrator._build_component("openai_tts", {})
    assert isinstance(adapter, OpenAITTSAdapter)
    assert adapter._provider_defaults.api_key == "legacy-shared-secret"
    assert adapter._provider_defaults.tts_model == "tts-1"


@pytest.mark.parametrize("name", ["elevenlabs_tts", "customer_tts"])
@pytest.mark.parametrize("empty", ["", None])
def test_blank_elevenlabs_settings_use_defaults_without_inheriting_credentials(name, empty):
    from src.config import ElevenLabsProviderConfig, validate_provider_settings
    from src.pipelines.elevenlabs import ElevenLabsTTSAdapter

    providers = {
        "elevenlabs": {"api_key": "other-customer-secret"},
        name: {"type": "elevenlabs", "base_url": empty, "model_id": empty,
               "voice_id": empty, "api_key": "role-owned-secret"},
    }
    validate_provider_settings(providers)
    adapter = PipelineOrchestrator(_config(providers))._build_component(name, {})
    assert isinstance(adapter, ElevenLabsTTSAdapter)
    assert adapter._provider_config.base_url == ElevenLabsProviderConfig().base_url
    assert adapter._provider_config.model_id == ElevenLabsProviderConfig().model_id
    assert adapter._provider_config.api_key == "role-owned-secret"


def test_blank_elevenlabs_endpoint_never_inherits_missing_role_credentials(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    key = "elevenlabs_tts"
    orchestrator = PipelineOrchestrator(_config({
        "local": {"enabled": True},
        "elevenlabs": {"api_key": "other-customer-secret"},
        key: {"type": "elevenlabs", "base_url": "", "api_key_env": "ABSENT_ROLE_KEY"},
    }, tts=key))
    with pytest.raises(PipelineOrchestratorError, match=key):
        orchestrator._validate_pipeline_entry("test", orchestrator.config.pipelines["test"])


@pytest.mark.parametrize("name", ["openai", "openai_stt", "openai_llm", "openai_tts", "customer_tts"])
def test_empty_typed_openai_wrapper_resolves_environment_credential(monkeypatch, name):
    from src.config import OpenAIProviderConfig

    monkeypatch.setenv("OPENAI_API_KEY", "environment-owned-secret")
    orchestrator = PipelineOrchestrator(_config({name: OpenAIProviderConfig()}))
    component = "openai_llm" if name == "openai" else name
    adapter = orchestrator._build_component(component, {})
    assert adapter._provider_defaults.api_key == "environment-owned-secret"
    assert "reasoning_effort" not in adapter._provider_defaults.model_fields_set
    if component == "openai_llm":
        assert adapter._compose_options({"model": "gpt-6.2-sol"})["reasoning_effort"] is None


def test_empty_mapping_remains_omitted_but_typed_openai_is_an_explicit_wrapper(monkeypatch):
    from src.config import OpenAIProviderConfig

    monkeypatch.setenv("OPENAI_API_KEY", "environment-owned-secret")
    orchestrator = PipelineOrchestrator(_config({}))
    assert orchestrator._hydrate_openai_config({}, component_key="openai_llm") is None
    typed = orchestrator._hydrate_openai_config(OpenAIProviderConfig(), component_key="openai_llm")
    assert typed is not None and typed.api_key == "environment-owned-secret"


@pytest.mark.parametrize("name", ["openai_llm", "customer_tts"])
def test_typed_openai_keeps_explicit_reasoning_after_environment_key_resolution(monkeypatch, name):
    from src.config import OpenAIProviderConfig

    monkeypatch.setenv("OPENAI_API_KEY", "environment-owned-secret")
    orchestrator = PipelineOrchestrator(_config({name: OpenAIProviderConfig(reasoning_effort="low")}))
    adapter = orchestrator._build_component(name, {})
    assert adapter._provider_defaults.api_key == "environment-owned-secret"
    assert adapter._provider_defaults.reasoning_effort == "low"
    assert "reasoning_effort" in adapter._provider_defaults.model_fields_set
