"""
Modular pipeline orchestrator and placeholder component adapters.

This module introduces the PipelineOrchestrator that resolves STT/LLM/TTS
component adapters per configured pipeline. Components that are not yet
implemented are represented by placeholder adapters that transparently raise
NotImplementedError when invoked. Phase 4 will replace these placeholders.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

from ..config import (
    AppConfig,
    PipelineEntry,
    DeepgramProviderConfig,
    ElevenLabsProviderConfig,
    GoogleProviderConfig,
    GroqSTTProviderConfig,
    GroqTTSProviderConfig,
    LocalProviderConfig,
    OpenAIProviderConfig,
    TelnyxLLMProviderConfig,
)
from ..logging_config import get_logger
from .base import Component, STTComponent, LLMComponent, TTSComponent
from .deepgram import DeepgramSTTAdapter, DeepgramTTSAdapter
from .deepgram_flux import DeepgramFluxSTTAdapter
from .elevenlabs import ElevenLabsTTSAdapter
from .google import GoogleLLMAdapter, GoogleSTTAdapter, GoogleTTSAdapter
from .local import LocalLLMAdapter, LocalSTTAdapter, LocalTTSAdapter
from .ollama import OllamaLLMAdapter
from .openai import OpenAISTTAdapter, OpenAILLMAdapter, OpenAITTSAdapter
from .groq import GroqSTTAdapter, GroqTTSAdapter
from .telnyx import TelnyxLLMAdapter

logger = get_logger(__name__)

ComponentFactory = Callable[[str, Dict[str, Any]], Component]
_PLACEHOLDER_FACTORY_ATTR = "_ava_placeholder_role"


class PipelineOrchestratorError(Exception):
    """Raised when the pipeline orchestrator cannot resolve components."""


@dataclass
class PipelineResolution:
    """Snapshot of the STT/LLM/TTS adapters assigned to a call."""
    call_id: str
    pipeline_name: str
    stt_key: str
    stt_adapter: STTComponent
    stt_options: Dict[str, Any]
    llm_key: str
    llm_adapter: LLMComponent
    llm_options: Dict[str, Any]
    tts_key: str
    tts_adapter: TTSComponent
    tts_options: Dict[str, Any]
    primary_provider: Optional[str] = None
    prepared: bool = False

    def component_summary(self) -> Dict[str, str]:
        return {
            "stt": self.stt_key,
            "llm": self.llm_key,
            "tts": self.tts_key,
        }

    def options_summary(self) -> Dict[str, Dict[str, Any]]:
        return {
            "stt": self.stt_options,
            "llm": self.llm_options,
            "tts": self.tts_options,
        }


class _PlaceholderBase:
    """Shared helper for placeholder adapters."""

    def __init__(self, component_key: str, options: Optional[Dict[str, Any]] = None):
        self.component_key = component_key
        self.options = options or {}

    def __repr__(self) -> str:
        return f"<PlaceholderComponent key={self.component_key}>"


class PlaceholderSTTAdapter(STTComponent, _PlaceholderBase):
    """Placeholder STT adapter awaiting concrete implementation."""

    def __init__(self, component_key: str, options: Optional[Dict[str, Any]] = None):
        _PlaceholderBase.__init__(self, component_key, options)

    async def transcribe(
        self,
        call_id: str,
        audio_pcm16: bytes,
        sample_rate_hz: int,
        options: Dict[str, Any],
    ) -> str:
        raise NotImplementedError(
            f"Placeholder STT adapter '{self.component_key}' is not implemented yet."
        )


class PlaceholderLLMAdapter(LLMComponent, _PlaceholderBase):
    """Placeholder LLM adapter awaiting concrete implementation."""

    def __init__(self, component_key: str, options: Optional[Dict[str, Any]] = None):
        _PlaceholderBase.__init__(self, component_key, options)

    async def generate(
        self,
        call_id: str,
        transcript: str,
        context: Dict[str, Any],
        options: Dict[str, Any],
    ) -> str:
        raise NotImplementedError(
            f"Placeholder LLM adapter '{self.component_key}' is not implemented yet."
        )


class PlaceholderTTSAdapter(TTSComponent, _PlaceholderBase):
    """Placeholder TTS adapter awaiting concrete implementation."""

    def __init__(self, component_key: str, options: Optional[Dict[str, Any]] = None):
        _PlaceholderBase.__init__(self, component_key, options)

    async def synthesize(
        self,
        call_id: str,
        text: str,
        options: Dict[str, Any],
    ):
        raise NotImplementedError(
            f"Placeholder TTS adapter '{self.component_key}' is not implemented yet."
        )


_PLACEHOLDER_CLASS_BY_ROLE: Dict[str, Callable[[str, Dict[str, Any]], Component]] = {
    "stt": PlaceholderSTTAdapter,
    "llm": PlaceholderLLMAdapter,
    "tts": PlaceholderTTSAdapter,
}


def _extract_role(component_key: str) -> str:
    """Extract the role (stt, llm, tts) from a component key like 'local_stt' or 'openai_llm'."""
    parts = component_key.rsplit("_", 1)
    if len(parts) != 2 or parts[1] not in ("stt", "llm", "tts"):
        raise PipelineOrchestratorError(
            f"Invalid component key '{component_key}'. "
            f"Expected format: '<provider>_<role>' where role is 'stt', 'llm', or 'tts'. "
            f"Example: 'local_stt', 'openai_llm', 'deepgram_tts'"
        )
    return parts[1]


def _extract_provider(component_key: str) -> Optional[str]:
    parts = component_key.rsplit("_", 1)
    if len(parts) != 2:
        return None
    return parts[0]


def _make_placeholder_factory(role: str) -> ComponentFactory:
    adapter_cls = _PLACEHOLDER_CLASS_BY_ROLE.get(role)
    if adapter_cls is None:
        raise PipelineOrchestratorError(f"No placeholder adapter registered for role '{role}'")

    def factory(component_key: str, options: Dict[str, Any]) -> Component:
        return adapter_cls(component_key, options)

    setattr(factory, _PLACEHOLDER_FACTORY_ATTR, role)
    return factory


def _build_default_registry() -> Dict[str, ComponentFactory]:
    registry: Dict[str, ComponentFactory] = {}
    default_providers = (
        "local",
        "deepgram",
        "openai",
        "openai_realtime",
        "google",
        "elevenlabs",
    )

    for provider in default_providers:
        for role in ("stt", "llm", "tts"):
            key = f"{provider}_{role}"
            registry[key] = _make_placeholder_factory(role)

    for role in ("stt", "llm", "tts"):
        registry[f"*_{role}"] = _make_placeholder_factory(role)

    return registry


DEFAULT_COMPONENT_REGISTRY = _build_default_registry()


class PipelineOrchestrator:
    """Resolve STT/LLM/TTS adapters for calls based on pipeline config."""

    def __init__(
        self,
        config: AppConfig,
        *,
        registry: Optional[Dict[str, ComponentFactory]] = None,
    ):
        self.config = config
        self._registry: Dict[str, ComponentFactory] = dict(DEFAULT_COMPONENT_REGISTRY)
        if registry:
            self._registry.update(registry)

        self._local_provider_config: Optional[LocalProviderConfig] = self._hydrate_local_config()
        self._deepgram_provider_config: Optional[DeepgramProviderConfig] = self._hydrate_deepgram_config()
        self._openai_provider_config: Optional[OpenAIProviderConfig] = self._hydrate_openai_config()
        self._telnyx_llm_provider_config: Optional[TelnyxLLMProviderConfig] = self._hydrate_telnyx_llm_config()
        self._google_provider_config: Optional[GoogleProviderConfig] = self._hydrate_google_config()
        self._elevenlabs_provider_config: Optional[ElevenLabsProviderConfig] = self._hydrate_elevenlabs_config()
        self._groq_stt_provider_config: Optional[GroqSTTProviderConfig] = self._hydrate_groq_stt_config()
        self._groq_tts_provider_config: Optional[GroqTTSProviderConfig] = self._hydrate_groq_tts_config()
        self._register_builtin_factories()

        self._assignments: Dict[str, PipelineResolution] = {}
        self._started: bool = False
        self._enabled: bool = bool(getattr(config, "pipelines", {}) or {})
        self._active_pipeline_name: Optional[str] = getattr(config, "active_pipeline", None)
        self._invalid_pipelines: Dict[str, str] = {}

    @property
    def started(self) -> bool:
        return self._started

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def start(self) -> None:
        if not self.enabled:
            logger.info("Pipeline orchestrator disabled - no pipelines configured.")
            return

        pipelines = getattr(self.config, "pipelines", {}) or {}

        # Phase 1: Validate pipeline component resolution (and refuse placeholders).
        self._invalid_pipelines = {}
        for name, entry in pipelines.items():
            try:
                self._validate_pipeline_entry(name, entry)
            except PipelineOrchestratorError as exc:
                self._invalid_pipelines[name] = str(exc)
                logger.error(
                    "Pipeline is invalid and will be unavailable",
                    pipeline=name,
                    error=str(exc),
                )

        valid_pipelines = {k: v for k, v in pipelines.items() if k not in self._invalid_pipelines}
        if not valid_pipelines:
            details = "; ".join([f"{name}: {err}" for name, err in self._invalid_pipelines.items()])
            raise PipelineOrchestratorError(f"No valid pipelines available. Fix pipeline configuration. Details: {details}")

        # If the active pipeline is invalid, fall back to the first valid pipeline.
        if self._active_pipeline_name and self._active_pipeline_name in self._invalid_pipelines:
            try:
                fallback = next(iter(valid_pipelines.keys()))
            except StopIteration:
                fallback = None
            logger.warning(
                "Active pipeline is invalid; falling back to first valid pipeline",
                requested_pipeline=self._active_pipeline_name,
                fallback_pipeline=fallback,
                error=self._invalid_pipelines.get(self._active_pipeline_name),
            )
            self._active_pipeline_name = fallback

        # Phase 2: Validate connectivity for valid pipelines
        validation_results = {}
        for name, entry in valid_pipelines.items():
            validation_results[name] = await self._validate_pipeline_connectivity(name, entry)
        
        # Check if active pipeline is healthy
        # NOTE: Validation failures should NOT disable the pipeline - it may still work!
        # Local providers can fail validation if ws://127.0.0.1 isn't reachable during startup
        # but work fine at runtime via Docker networking (ws://local_ai_server:8765)
        active_healthy = True
        if self._active_pipeline_name:
            active_result = validation_results.get(self._active_pipeline_name, {})
            active_healthy = active_result.get("healthy", False)
            if not active_healthy:
                logger.warning(
                    "Active pipeline validation FAILED - pipeline will still be available (may work at runtime)",
                    pipeline=self._active_pipeline_name,
                    failures=active_result.get("failures", []),
                )
                # DON'T disable: self._active_pipeline_name = None
                # Local pipelines may fail validation but work at runtime
        
        # Log summary
        healthy_count = sum(1 for r in validation_results.values() if r.get("healthy"))
        unhealthy_count = len(validation_results) - healthy_count
        
        self._started = True
        logger.info(
            "Pipeline orchestrator initialized",
            active_pipeline=self._active_pipeline_name,
            pipeline_count=len(valid_pipelines),
            invalid_pipelines=len(self._invalid_pipelines),
            healthy_pipelines=healthy_count,
            unhealthy_pipelines=unhealthy_count,
        )

    async def stop(self) -> None:
        if not self._started:
            return

        for call_id in list(self._assignments.keys()):
            await self.release_pipeline(call_id)

        self._started = False
        logger.info("Pipeline orchestrator stopped", remaining_assignments=len(self._assignments))

    def get_pipeline(
        self,
        call_id: str,
        pipeline_name: Optional[str] = None,
    ) -> Optional[PipelineResolution]:
        if not self.enabled:
            return None
        if not self._started:
            logger.debug("Pipeline orchestrator requested before start; skipping resolution", call_id=call_id)
            return None

        if call_id in self._assignments:
            return self._assignments[call_id]

        pipelines = getattr(self.config, "pipelines", {}) or {}
        selected_name = pipeline_name or self._active_pipeline_name

        if not selected_name:
            try:
                selected_name = next(iter(pipelines.keys()))
            except StopIteration:
                logger.error("No pipelines available to assign", call_id=call_id)
                return None
        if selected_name in self._invalid_pipelines:
            logger.warning(
                "Requested pipeline is invalid; falling back to first valid pipeline",
                call_id=call_id,
                requested_pipeline=selected_name,
                error=self._invalid_pipelines.get(selected_name),
            )
            selected_name = None

        entry = pipelines.get(selected_name) if selected_name else None
        if entry is None or selected_name in self._invalid_pipelines:
            logger.warning(
                "Requested pipeline not found; falling back to first available pipeline",
                call_id=call_id,
                requested_pipeline=selected_name,
            )
            try:
                for candidate_name, candidate_entry in pipelines.items():
                    if candidate_name in self._invalid_pipelines:
                        continue
                    selected_name, entry = candidate_name, candidate_entry
                    break
                else:
                    return None
            except StopIteration:
                return None

        resolution = self._build_resolution(call_id, selected_name, entry)
        self._assignments[call_id] = resolution
        return resolution

    async def release_pipeline(self, call_id: str) -> None:
        resolution = self._assignments.pop(call_id, None)
        if not resolution:
            return

        for adapter in (resolution.stt_adapter, resolution.llm_adapter, resolution.tts_adapter):
            await self._shutdown_component(adapter, call_id)

    def register_factory(self, component_key: str, factory: ComponentFactory) -> None:
        self._registry[component_key] = factory

    def _hydrate_local_config(self) -> Optional[LocalProviderConfig]:
        providers = getattr(self.config, "providers", {}) or {}
        raw_config = providers.get("local")
        if not raw_config:
            # Fallback: accept modular local providers (local_stt/local_llm/local_tts)
            for name, cfg in providers.items():
                try:
                    lower = str(name).lower()
                except Exception:
                    lower = ""
                if lower.startswith("local_") or (isinstance(cfg, dict) and str(cfg.get("type", "")).lower() == "local"):
                    raw_config = cfg
                    break
        if not raw_config:
            return None
        if isinstance(raw_config, LocalProviderConfig):
            cfg = raw_config
        elif isinstance(raw_config, dict):
            enabled = raw_config.get("enabled", True)
            if not enabled:
                logger.debug("Local provider disabled via configuration")
                return None
            try:
                cfg = LocalProviderConfig(**raw_config)
            except Exception as exc:
                logger.warning(
                    "Failed to hydrate Local provider config for pipelines",
                    error=str(exc),
                )
                return None
        else:
            logger.warning(
                "Unsupported Local provider config type for pipelines",
                config_type=type(raw_config).__name__,
            )
            return None

        if not cfg.enabled:
            logger.debug("Local provider disabled after hydration")
            return None

        return cfg

    def _hydrate_deepgram_config(self) -> Optional[DeepgramProviderConfig]:
        providers = getattr(self.config, "providers", {}) or {}
        raw_config = providers.get("deepgram")
        if not raw_config:
            return None
        if isinstance(raw_config, DeepgramProviderConfig):
            return raw_config
        if isinstance(raw_config, dict):
            try:
                return DeepgramProviderConfig(**raw_config)
            except Exception as exc:
                logger.warning(
                    "Failed to hydrate Deepgram provider config for pipelines",
                    error=str(exc),
                )
                return None
        logger.warning(
            "Unsupported Deepgram provider config type for pipelines",
            config_type=type(raw_config).__name__,
        )
        return None

    def _register_builtin_factories(self) -> None:
        if self._local_provider_config:
            stt_factory = self._make_local_stt_factory(self._local_provider_config)
            llm_factory = self._make_local_llm_factory(self._local_provider_config)
            tts_factory = self._make_local_tts_factory(self._local_provider_config)

            self.register_factory("local_stt", stt_factory)
            self.register_factory("local_llm", llm_factory)
            self.register_factory("local_tts", tts_factory)

            # Log configured backends from LocalProviderConfig
            stt_backend = getattr(self._local_provider_config, 'stt_backend', 'vosk')
            tts_backend = getattr(self._local_provider_config, 'tts_backend', 'piper')
            
            logger.info(
                "Local pipeline adapters registered",
                stt_factory="local_stt",
                llm_factory="local_llm",
                tts_factory="local_tts",
                stt_backend=stt_backend,
                tts_backend=tts_backend,
            )
        else:
            logger.debug("Local pipeline adapters not registered - provider config unavailable or disabled")

        if self._deepgram_provider_config:
            stt_factory = self._make_deepgram_stt_factory(self._deepgram_provider_config)
            flux_stt_factory = self._make_deepgram_flux_stt_factory(self._deepgram_provider_config)
            tts_factory = self._make_deepgram_tts_factory(self._deepgram_provider_config)

            self.register_factory("deepgram_stt", stt_factory)
            self.register_factory("deepgram_flux_stt", flux_stt_factory)
            self.register_factory("deepgram_tts", tts_factory)

            logger.info(
                "Deepgram pipeline adapters registered",
                stt_factory="deepgram_stt",
                flux_stt_factory="deepgram_flux_stt",
                tts_factory="deepgram_tts",
            )
        else:
            logger.debug("Deepgram pipeline adapters not registered - provider config unavailable")

        if self._openai_provider_config:
            stt_factory = self._make_openai_stt_factory(self._openai_provider_config)
            llm_factory = self._make_openai_llm_factory(self._openai_provider_config)
            tts_factory = self._make_openai_tts_factory(self._openai_provider_config)

            self.register_factory("openai_stt", stt_factory)
            self.register_factory("openai_llm", llm_factory)
            self.register_factory("openai_tts", tts_factory)

            logger.info(
                "OpenAI pipeline adapters registered",
                stt_factory="openai_stt",
                llm_factory="openai_llm",
                tts_factory="openai_tts",
            )
        else:
            logger.debug("OpenAI pipeline adapters not registered - provider config unavailable or invalid")

        if self._telnyx_llm_provider_config:
            llm_factory = self._make_telnyx_llm_factory(self._telnyx_llm_provider_config)
            self.register_factory("telnyx_llm", llm_factory)
            # Alias for typo tolerance.
            self.register_factory("telenyx_llm", llm_factory)
            try:
                host = (urlparse(str(self._telnyx_llm_provider_config.chat_base_url)).hostname or "").lower()
            except Exception:
                host = None
            logger.info(
                "Telnyx LLM pipeline adapter registered",
                llm_factory="telnyx_llm",
                host=host,
            )
        else:
            logger.debug("Telnyx LLM pipeline adapter not registered - API key unavailable or config missing")

        if self._google_provider_config:
            stt_factory = self._make_google_stt_factory(self._google_provider_config)
            llm_factory = self._make_google_llm_factory(self._google_provider_config)
            tts_factory = self._make_google_tts_factory(self._google_provider_config)

            self.register_factory("google_stt", stt_factory)
            self.register_factory("google_llm", llm_factory)
            self.register_factory("google_tts", tts_factory)

            logger.info(
                "Google pipeline adapters registered",
                stt_factory="google_stt",
                llm_factory="google_llm",
                tts_factory="google_tts",
            )
        else:
            logger.debug("Google pipeline adapters not registered - credentials unavailable or invalid")

        if self._groq_stt_provider_config:
            stt_factory = self._make_groq_stt_factory(self._groq_stt_provider_config)
            self.register_factory("groq_stt", stt_factory)
            logger.info(
                "Groq STT pipeline adapter registered",
                stt_factory="groq_stt",
                model=self._groq_stt_provider_config.stt_model,
            )
        else:
            logger.debug("Groq STT pipeline adapter not registered - API key unavailable or config missing")

        if self._groq_tts_provider_config:
            tts_factory = self._make_groq_tts_factory(self._groq_tts_provider_config)
            self.register_factory("groq_tts", tts_factory)
            logger.info(
                "Groq TTS pipeline adapter registered",
                tts_factory="groq_tts",
                model=self._groq_tts_provider_config.tts_model,
                voice=self._groq_tts_provider_config.voice,
            )
        else:
            logger.debug("Groq TTS pipeline adapter not registered - API key unavailable or config missing")

        # ElevenLabs TTS adapter
        if self._elevenlabs_provider_config:
            tts_factory = self._make_elevenlabs_tts_factory(self._elevenlabs_provider_config)
            self.register_factory("elevenlabs_tts", tts_factory)
            
            logger.info(
                "ElevenLabs pipeline adapters registered",
                tts_factory="elevenlabs_tts",
                voice_id=self._elevenlabs_provider_config.voice_id,
            )
        else:
            logger.debug("ElevenLabs pipeline adapters not registered - API key unavailable")

        # Ollama LLM adapter - for self-hosted local LLMs
        # Read config from providers.ollama_llm in YAML if available
        ollama_provider_config = {}
        providers = getattr(self.config, "providers", {}) or {}
        if isinstance(providers, dict) and "ollama_llm" in providers:
            ollama_cfg = providers.get("ollama_llm", {})
            if isinstance(ollama_cfg, dict) and ollama_cfg.get("enabled", True) is not False:
                ollama_provider_config = dict(ollama_cfg)
        
        ollama_llm_factory = self._make_ollama_llm_factory(ollama_provider_config)
        self.register_factory("ollama_llm", ollama_llm_factory)
        configured_url = ollama_provider_config.get("base_url", "http://localhost:11434")
        logger.info(
            "Ollama LLM adapter registered",
            llm_factory="ollama_llm",
            configured_endpoint=configured_url,
            note="Self-hosted LLM with optional tool calling",
        )

        self._register_openai_compatible_llm_factories()


    def _register_openai_compatible_llm_factories(self) -> None:
        providers = getattr(self.config, "providers", {}) or {}
        if not isinstance(providers, dict):
            return

        for name, cfg in providers.items():
            if not isinstance(cfg, dict):
                continue
            try:
                role = _extract_role(name)
            except Exception:
                continue
            if role != "llm":
                continue
            if str(cfg.get("type", "")).lower() != "openai":
                continue
            if cfg.get("enabled") is False:
                continue

            provider_prefix = _extract_provider(str(name))
            if not provider_prefix:
                continue

            payload = dict(cfg)
            # Prefer config-expanded api_key; fallback to environment variable named after provider key
            payload["api_key"] = cfg.get("api_key") or os.getenv(f"{provider_prefix.upper()}_API_KEY")
            try:
                provider_cfg = OpenAIProviderConfig(**payload)
            except Exception:
                continue

            llm_factory = self._make_openai_llm_factory(provider_cfg)
            self.register_factory(str(name), llm_factory)

    def _make_ollama_llm_factory(self, provider_config: Dict[str, Any]) -> ComponentFactory:
        """Create factory for Ollama LLM adapter (self-hosted local models)."""
        # Merge provider config with pipeline options at runtime
        base_config = dict(provider_config)
        
        def factory(component_key: str, options: Dict[str, Any]) -> Component:
            # Provider config from YAML takes precedence, runtime options can override
            merged = dict(base_config)
            merged.update(options or {})
            return OllamaLLMAdapter(
                self.config,
                merged,
            )
        return factory

    def _make_local_stt_factory(
        self,
        provider_config: LocalProviderConfig,
    ) -> ComponentFactory:
        config_payload = provider_config.model_dump()

        def factory(component_key: str, options: Dict[str, Any]) -> Component:
            return LocalSTTAdapter(
                component_key,
                self.config,
                LocalProviderConfig(**config_payload),
                options,
            )

        return factory

    def _make_local_llm_factory(
        self,
        provider_config: LocalProviderConfig,
    ) -> ComponentFactory:
        config_payload = provider_config.model_dump()

        def factory(component_key: str, options: Dict[str, Any]) -> Component:
            return LocalLLMAdapter(
                component_key,
                self.config,
                LocalProviderConfig(**config_payload),
                options,
            )

        return factory

    def _make_local_tts_factory(
        self,
        provider_config: LocalProviderConfig,
    ) -> ComponentFactory:
        config_payload = provider_config.model_dump()

        def factory(component_key: str, options: Dict[str, Any]) -> Component:
            return LocalTTSAdapter(
                component_key,
                self.config,
                LocalProviderConfig(**config_payload),
                options,
            )

        return factory

    

    def _make_deepgram_stt_factory(
        self,
        provider_config: DeepgramProviderConfig,
    ) -> ComponentFactory:
        config_payload = provider_config.model_dump()

        def factory(component_key: str, options: Dict[str, Any]) -> Component:
            return DeepgramSTTAdapter(
                component_key,
                self.config,
                DeepgramProviderConfig(**config_payload),
                options,
            )

        return factory
    
    def _make_deepgram_flux_stt_factory(
        self,
        provider_config: DeepgramProviderConfig,
    ) -> ComponentFactory:
        config_payload = provider_config.model_dump()

        def factory(component_key: str, options: Dict[str, Any]) -> Component:
            return DeepgramFluxSTTAdapter(
                component_key,
                self.config,
                DeepgramProviderConfig(**config_payload),
                options,
            )

        return factory

    def _make_openai_stt_factory(
        self,
        provider_config: OpenAIProviderConfig,
    ) -> ComponentFactory:
        config_payload = provider_config.model_dump()

        def factory(component_key: str, options: Dict[str, Any]) -> Component:
            return OpenAISTTAdapter(
                component_key,
                self.config,
                OpenAIProviderConfig(**config_payload),
                options,
            )

        return factory

    def _make_openai_llm_factory(
        self,
        provider_config: OpenAIProviderConfig,
    ) -> ComponentFactory:
        config_payload = provider_config.model_dump()

        def factory(component_key: str, options: Dict[str, Any]) -> Component:
            return OpenAILLMAdapter(
                component_key,
                self.config,
                OpenAIProviderConfig(**config_payload),
                options,
            )

        return factory

    def _make_telnyx_llm_factory(
        self,
        provider_config: TelnyxLLMProviderConfig,
    ) -> ComponentFactory:
        config_payload = provider_config.model_dump()

        def factory(component_key: str, options: Dict[str, Any]) -> Component:
            return TelnyxLLMAdapter(
                component_key,
                self.config,
                TelnyxLLMProviderConfig(**config_payload),
                options,
            )

        return factory

    def _make_openai_tts_factory(
        self,
        provider_config: OpenAIProviderConfig,
    ) -> ComponentFactory:
        config_payload = provider_config.model_dump()

        def factory(component_key: str, options: Dict[str, Any]) -> Component:
            return OpenAITTSAdapter(
                component_key,
                self.config,
                OpenAIProviderConfig(**config_payload),
                options,
            )

        return factory

    def _make_deepgram_tts_factory(
        self,
        provider_config: DeepgramProviderConfig,
    ) -> ComponentFactory:
        config_payload = provider_config.model_dump()

        def factory(component_key: str, options: Dict[str, Any]) -> Component:
            return DeepgramTTSAdapter(
                component_key,
                self.config,
                DeepgramProviderConfig(**config_payload),
                options,
            )

        return factory

    def _make_google_stt_factory(
        self,
        provider_config: GoogleProviderConfig,
    ) -> ComponentFactory:
        config_payload = provider_config.model_dump()

        def factory(component_key: str, options: Dict[str, Any]) -> Component:
            return GoogleSTTAdapter(
                component_key,
                self.config,
                GoogleProviderConfig(**config_payload),
                options,
            )

        return factory

    def _make_google_llm_factory(
        self,
        provider_config: GoogleProviderConfig,
    ) -> ComponentFactory:
        config_payload = provider_config.model_dump()

        def factory(component_key: str, options: Dict[str, Any]) -> Component:
            return GoogleLLMAdapter(
                component_key,
                self.config,
                GoogleProviderConfig(**config_payload),
                options,
            )

        return factory

    def _make_google_tts_factory(
        self,
        provider_config: GoogleProviderConfig,
    ) -> ComponentFactory:
        config_payload = provider_config.model_dump()

        def factory(component_key: str, options: Dict[str, Any]) -> Component:
            return GoogleTTSAdapter(
                component_key,
                self.config,
                GoogleProviderConfig(**config_payload),
                options,
            )

        return factory

    def _make_elevenlabs_tts_factory(
        self,
        provider_config: ElevenLabsProviderConfig,
    ) -> ComponentFactory:
        """Create factory for ElevenLabs TTS adapter."""
        config_payload = provider_config.model_dump()

        def factory(component_key: str, options: Dict[str, Any]) -> Component:
            return ElevenLabsTTSAdapter(
                component_key,
                self.config,
                ElevenLabsProviderConfig(**config_payload),
                options,
            )

        return factory

    def _hydrate_google_config(self) -> Optional[GoogleProviderConfig]:
        providers = getattr(self.config, "providers", {}) or {}
        raw_config = providers.get("google")
        if not raw_config:
            return None
        if isinstance(raw_config, GoogleProviderConfig):
            config = raw_config
        elif isinstance(raw_config, dict):
            try:
                config = GoogleProviderConfig(**raw_config)
            except Exception as exc:
                logger.warning(
                    "Failed to hydrate Google provider config for pipelines",
                    error=str(exc),
                )
                return None
        else:
            logger.warning(
                "Unsupported Google provider config type for pipelines",
                config_type=type(raw_config).__name__,
            )
            return None

        if not (
            config.api_key
            or os.getenv("GOOGLE_API_KEY")
            or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        ):
            logger.warning(
                "Google pipeline adapters require GOOGLE_API_KEY or GOOGLE_APPLICATION_CREDENTIALS; falling back to placeholder adapters",
            )
            return None

        return config

    def _hydrate_elevenlabs_config(self) -> Optional[ElevenLabsProviderConfig]:
        """Hydrate ElevenLabs provider config from YAML or env."""
        providers = getattr(self.config, "providers", {}) or {}
        raw_config = providers.get("elevenlabs")
        if not raw_config:
            # Fallback: accept modular elevenlabs_tts provider.
            # AAVA-181: Skip full-agent type providers (elevenlabs_agent) — they
            # have their own config path and different voice/model semantics.
            for name, cfg in providers.items():
                try:
                    lower = str(name).lower()
                except Exception:
                    lower = ""
                if not isinstance(cfg, dict):
                    continue
                cfg_type = str(cfg.get("type", "")).lower()
                if cfg_type in ("elevenlabs_agent", "full"):
                    continue
                # Also skip providers with all three capabilities (full agents)
                cfg_caps = cfg.get("capabilities", [])
                if isinstance(cfg_caps, list) and "stt" in cfg_caps and "llm" in cfg_caps and "tts" in cfg_caps:
                    continue
                if lower.startswith("elevenlabs_") or cfg_type == "elevenlabs":
                    raw_config = cfg
                    break
        if not raw_config:
            # Check if API key exists in env (allow usage without explicit provider config)
            api_key = os.getenv("ELEVENLABS_API_KEY")
            if api_key:
                return ElevenLabsProviderConfig(api_key=api_key)
            return None
        if isinstance(raw_config, ElevenLabsProviderConfig):
            config = raw_config
        elif isinstance(raw_config, dict):
            try:
                # AAVA-181: Filter out Admin UI metadata fields (name, type,
                # capabilities, continuous_input) and empty strings that would
                # override Pydantic defaults (e.g., base_url: '').
                el_fields = set(ElevenLabsProviderConfig.model_fields.keys())
                filtered = {}
                for k, v in raw_config.items():
                    if k not in el_fields:
                        continue
                    if isinstance(v, str) and v == "":
                        continue
                    filtered[k] = v
                config = ElevenLabsProviderConfig(**filtered)
            except Exception as exc:
                logger.warning(
                    "Failed to hydrate ElevenLabs provider config for pipelines",
                    error=str(exc),
                )
                return None
        else:
            logger.warning(
                "Unsupported ElevenLabs provider config type for pipelines",
                config_type=type(raw_config).__name__,
            )
            return None

        # Check for API key
        if not config.api_key and not os.getenv("ELEVENLABS_API_KEY"):
            logger.warning(
                "ElevenLabs pipeline adapters require ELEVENLABS_API_KEY; falling back to placeholder adapters",
            )
            return None

        # Fill API key from env if not in config
        if not config.api_key:
            config = ElevenLabsProviderConfig(
                **{**config.model_dump(), "api_key": os.getenv("ELEVENLABS_API_KEY")}
            )

        return config

    def _hydrate_openai_config(self) -> Optional[OpenAIProviderConfig]:
        providers = getattr(self.config, "providers", {}) or {}
        merged: Dict[str, Any] = {}

        # Base OpenAI config (optional)
        raw_base = providers.get("openai")
        if isinstance(raw_base, dict):
            merged.update(raw_base)
        elif isinstance(raw_base, OpenAIProviderConfig):
            merged.update(raw_base.model_dump())

        # Prefer explicit modular OpenAI provider blocks when present.
        for key in ("openai_llm", "openai_stt", "openai_tts"):
            raw = providers.get(key)
            if isinstance(raw, dict):
                merged.update(raw)
            elif isinstance(raw, OpenAIProviderConfig):
                merged.update(raw.model_dump())

        # Backward-compatible fallback: accept any openai_* provider entries but skip realtime agent.
        if not merged:
            for name, cfg in providers.items():
                try:
                    lower = str(name).lower()
                except Exception:
                    lower = ""
                if "realtime" in lower:
                    continue
                if not lower.startswith("openai_"):
                    continue
                if isinstance(cfg, dict):
                    merged.update(cfg)
                elif isinstance(cfg, OpenAIProviderConfig):
                    merged.update(cfg.model_dump())

        if not merged:
            return None

        try:
            config = OpenAIProviderConfig(**merged)
        except Exception as exc:
            logger.warning(
                "Failed to hydrate OpenAI provider config for pipelines",
                error=str(exc),
            )
            return None

        if not config.api_key:
            logger.warning("OpenAI pipeline adapters require an API key; falling back to placeholder adapters")
            return None

        return config

    def _hydrate_telnyx_llm_config(self) -> Optional[TelnyxLLMProviderConfig]:
        providers = getattr(self.config, "providers", {}) or {}
        raw = providers.get("telnyx_llm") or providers.get("telenyx_llm") or providers.get("telnyx")
        merged: Dict[str, Any] = {}

        if isinstance(raw, dict):
            merged.update(raw)
        elif isinstance(raw, OpenAIProviderConfig):
            merged.update(raw.model_dump())

        if not merged:
            for _, cfg in providers.items():
                if not isinstance(cfg, dict):
                    continue
                base = str(cfg.get("chat_base_url") or cfg.get("base_url") or "").strip()
                try:
                    host = (urlparse(base).hostname or "").lower()
                except Exception:
                    host = ""
                if host == "api.telnyx.com":
                    merged.update(cfg)
                    break

        if not merged:
            return None

        merged.setdefault("chat_base_url", "https://api.telnyx.com/v2/ai")

        try:
            config = TelnyxLLMProviderConfig(**merged)
        except Exception as exc:
            logger.warning(
                "Failed to hydrate Telnyx LLM provider config for pipelines",
                error=str(exc),
            )
            return None

        if not config.api_key:
            logger.warning("Telnyx pipeline adapter requires TELNYX_API_KEY; falling back to placeholder adapters")
            return None

        return config

    def _hydrate_groq_stt_config(self) -> Optional[GroqSTTProviderConfig]:
        providers = getattr(self.config, "providers", {}) or {}
        raw_config = providers.get("groq_stt")
        if not raw_config:
            for name, cfg in providers.items():
                if not isinstance(cfg, dict):
                    continue
                try:
                    role = _extract_role(str(name))
                except Exception:
                    continue
                if role != "stt":
                    continue
                if str(cfg.get("type", "")).lower() != "groq":
                    continue
                raw_config = cfg
                break
        if not raw_config:
            return None
        if isinstance(raw_config, GroqSTTProviderConfig):
            config = raw_config
        elif isinstance(raw_config, dict):
            try:
                config = GroqSTTProviderConfig(**raw_config)
            except Exception as exc:
                logger.warning(
                    "Failed to hydrate Groq STT provider config for pipelines",
                    error=str(exc),
                )
                return None
        else:
            logger.warning(
                "Unsupported Groq STT provider config type for pipelines",
                config_type=type(raw_config).__name__,
            )
            return None

        if not config.enabled:
            return None

        if not config.api_key:
            config.api_key = os.getenv("GROQ_API_KEY")

        if not config.api_key:
            logger.warning("Groq STT pipeline adapter requires GROQ_API_KEY; falling back to placeholder adapters")
            return None

        return config

    def _hydrate_groq_tts_config(self) -> Optional[GroqTTSProviderConfig]:
        providers = getattr(self.config, "providers", {}) or {}
        raw_config = providers.get("groq_tts")
        if not raw_config:
            for name, cfg in providers.items():
                if not isinstance(cfg, dict):
                    continue
                try:
                    role = _extract_role(str(name))
                except Exception:
                    continue
                if role != "tts":
                    continue
                if str(cfg.get("type", "")).lower() != "groq":
                    continue
                raw_config = cfg
                break
        if not raw_config:
            return None
        if isinstance(raw_config, GroqTTSProviderConfig):
            config = raw_config
        elif isinstance(raw_config, dict):
            try:
                config = GroqTTSProviderConfig(**raw_config)
            except Exception as exc:
                logger.warning(
                    "Failed to hydrate Groq TTS provider config for pipelines",
                    error=str(exc),
                )
                return None
        else:
            logger.warning(
                "Unsupported Groq TTS provider config type for pipelines",
                config_type=type(raw_config).__name__,
            )
            return None

        if not config.enabled:
            return None

        if not config.api_key:
            config.api_key = os.getenv("GROQ_API_KEY")

        if not config.api_key:
            logger.warning("Groq TTS pipeline adapter requires GROQ_API_KEY; falling back to placeholder adapters")
            return None

        return config

    def _make_groq_stt_factory(self, provider_config: GroqSTTProviderConfig) -> ComponentFactory:
        config_payload = provider_config.model_dump()

        def factory(component_key: str, options: Dict[str, Any]) -> Component:
            return GroqSTTAdapter(
                component_key,
                self.config,
                GroqSTTProviderConfig(**config_payload),
                options,
            )

        return factory

    def _make_groq_tts_factory(self, provider_config: GroqTTSProviderConfig) -> ComponentFactory:
        config_payload = provider_config.model_dump()

        def factory(component_key: str, options: Dict[str, Any]) -> Component:
            return GroqTTSAdapter(
                component_key,
                self.config,
                GroqTTSProviderConfig(**config_payload),
                options,
            )

        return factory

    def _resolve_factory(self, component_key: str) -> ComponentFactory:
        factory = self._registry.get(component_key)
        if factory:
            return factory

        role = _extract_role(component_key)
        wildcard_key = f"*_{role}"
        factory = self._registry.get(wildcard_key)
        if factory:
            # Cache the wildcard resolution for quicker lookups next time.
            self._registry[component_key] = factory
            return factory

        raise PipelineOrchestratorError(f"No component factory registered for '{component_key}'")

    def _build_component(self, component_key: str, options: Dict[str, Any]) -> Component:
        factory = self._resolve_factory(component_key)
        return factory(component_key, options)

    def _derive_primary_provider(self, entry: PipelineEntry) -> Optional[str]:
        for key in (entry.llm, entry.tts, entry.stt):
            provider = _extract_provider(key)
            if provider:
                return provider
        return None

    def _validate_pipeline_entry(self, pipeline_name: str, entry: PipelineEntry) -> None:
        """Validate pipeline component resolution (static check)."""
        for key in (entry.stt, entry.llm, entry.tts):
            factory = self._resolve_factory(key)
            if getattr(factory, _PLACEHOLDER_FACTORY_ATTR, None):
                raise PipelineOrchestratorError(self._format_placeholder_error(pipeline_name, key))

    def _format_placeholder_error(self, pipeline_name: str, component_key: str) -> str:
        role = "unknown"
        provider = None
        try:
            role = _extract_role(component_key)
        except Exception:
            role = "unknown"
        try:
            provider = _extract_provider(component_key)
        except Exception:
            provider = None

        hints = []
        if provider in ("openai", "openai_realtime"):
            hints.append("Set OPENAI_API_KEY (and configure providers.openai_llm/providers.openai_stt/providers.openai_tts).")
        elif provider == "google":
            hints.append("Set GOOGLE_API_KEY or GOOGLE_APPLICATION_CREDENTIALS.")
        elif provider == "elevenlabs":
            hints.append("Set ELEVENLABS_API_KEY.")
        elif provider == "deepgram":
            hints.append("Set DEEPGRAM_API_KEY and configure providers.deepgram.")
        elif provider == "local":
            hints.append("Ensure providers.local is enabled and local-ai-server is reachable.")
        elif provider == "groq":
            hints.append("Set GROQ_API_KEY and configure providers.groq_stt/providers.groq_tts.")
        elif provider in ("telnyx", "telenyx"):
            hints.append("Set TELNYX_API_KEY and configure providers.telnyx_llm.")

        hint = f" Hint: {' '.join(hints)}" if hints else ""
        return f"Pipeline '{pipeline_name}' cannot resolve {role} component '{component_key}' (placeholder adapter).{hint}"
    
    async def _validate_pipeline_connectivity(self, pipeline_name: str, entry: PipelineEntry) -> Dict[str, Any]:
        """Validate pipeline components can connect to required services.
        
        Returns:
            Dict with:
                - healthy: bool
                - failures: List[Dict] - Component failure details
        """
        failures = []
        options_map = entry.options or {}
        
        # Validate STT
        try:
            stt_options = dict(options_map.get("stt", {}))
            stt_adapter = self._build_component(entry.stt, stt_options)
            result = await stt_adapter.validate_connectivity(stt_options)
            if not result.get("healthy"):
                failures.append({
                    "component": "stt",
                    "key": entry.stt,
                    "error": result.get("error"),
                    "details": result.get("details", {}),
                })
                logger.error(
                    "Pipeline STT validation FAILED",
                    pipeline=pipeline_name,
                    component_key=entry.stt,
                    error=result.get("error"),
                    details=result.get("details", {}),
                )
        except Exception as exc:
            failures.append({
                "component": "stt",
                "key": entry.stt,
                "error": f"Validation exception: {exc}",
                "details": {},
            })
            logger.error(
                "Pipeline STT validation exception",
                pipeline=pipeline_name,
                component_key=entry.stt,
                exc_info=True,
            )
        
        # Validate LLM
        try:
            llm_options = dict(options_map.get("llm", {}))
            llm_adapter = self._build_component(entry.llm, llm_options)
            result = await llm_adapter.validate_connectivity(llm_options)
            if not result.get("healthy"):
                failures.append({
                    "component": "llm",
                    "key": entry.llm,
                    "error": result.get("error"),
                    "details": result.get("details", {}),
                })
                logger.error(
                    "Pipeline LLM validation FAILED",
                    pipeline=pipeline_name,
                    component_key=entry.llm,
                    error=result.get("error"),
                    details=result.get("details", {}),
                )
        except Exception as exc:
            failures.append({
                "component": "llm",
                "key": entry.llm,
                "error": f"Validation exception: {exc}",
                "details": {},
            })
            logger.error(
                "Pipeline LLM validation exception",
                pipeline=pipeline_name,
                component_key=entry.llm,
                exc_info=True,
            )
        
        # Validate TTS
        try:
            tts_options = dict(options_map.get("tts", {}))
            tts_adapter = self._build_component(entry.tts, tts_options)
            result = await tts_adapter.validate_connectivity(tts_options)
            if not result.get("healthy"):
                failures.append({
                    "component": "tts",
                    "key": entry.tts,
                    "error": result.get("error"),
                    "details": result.get("details", {}),
                })
                logger.error(
                    "Pipeline TTS validation FAILED",
                    pipeline=pipeline_name,
                    component_key=entry.tts,
                    error=result.get("error"),
                    details=result.get("details", {}),
                )
        except Exception as exc:
            failures.append({
                "component": "tts",
                "key": entry.tts,
                "error": f"Validation exception: {exc}",
                "details": {},
            })
            logger.error(
                "Pipeline TTS validation exception",
                pipeline=pipeline_name,
                component_key=entry.tts,
                exc_info=True,
            )
        
        healthy = len(failures) == 0
        if healthy:
            logger.info(
                "Pipeline validation SUCCESS",
                pipeline=pipeline_name,
                components={"stt": entry.stt, "llm": entry.llm, "tts": entry.tts},
            )
        
        return {"healthy": healthy, "failures": failures}

    def _build_resolution(
        self,
        call_id: str,
        pipeline_name: str,
        entry: PipelineEntry,
    ) -> PipelineResolution:
        options_map = entry.options or {}
        stt_options = dict(options_map.get("stt", {}))
        llm_options = dict(options_map.get("llm", {}))
        tts_options = dict(options_map.get("tts", {}))

        # Tools are allowlisted per-context (engine injects llm_options["tools"]).
        # Do not allow pipeline-level tool configuration.

        stt_adapter = self._build_component(entry.stt, stt_options)
        llm_adapter = self._build_component(entry.llm, llm_options)
        tts_adapter = self._build_component(entry.tts, tts_options)

        primary_provider = self._derive_primary_provider(entry)

        return PipelineResolution(
            call_id=call_id,
            pipeline_name=pipeline_name,
            stt_key=entry.stt,
            stt_adapter=stt_adapter,
            stt_options=stt_options,
            llm_key=entry.llm,
            llm_adapter=llm_adapter,
            llm_options=llm_options,
            tts_key=entry.tts,
            tts_adapter=tts_adapter,
            tts_options=tts_options,
            primary_provider=primary_provider,
        )

    async def _shutdown_component(self, component: Component, call_id: str) -> None:
        try:
            await component.close_call(call_id)
        except NotImplementedError:
            logger.debug(
                "Placeholder component close_call not implemented",
                call_id=call_id,
                component_key=getattr(component, "component_key", repr(component)),
            )
        except Exception as exc:
            logger.warning(
                "Pipeline component close_call failed",
                call_id=call_id,
                component_key=getattr(component, "component_key", repr(component)),
                error=str(exc),
                exc_info=True,
            )

        try:
            await component.stop()
        except NotImplementedError:
            logger.debug(
                "Placeholder component stop not implemented",
                call_id=call_id,
                component_key=getattr(component, "component_key", repr(component)),
            )
        except Exception as exc:
            logger.warning(
                "Pipeline component stop failed",
                call_id=call_id,
                component_key=getattr(component, "component_key", repr(component)),
                error=str(exc),
                exc_info=True,
            )


__all__ = [
    "PipelineOrchestrator",
    "PipelineResolution",
    "PipelineOrchestratorError",
]
