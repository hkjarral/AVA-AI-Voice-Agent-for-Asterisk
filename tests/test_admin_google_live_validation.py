"""Tests for Admin UI Google Live API-key validation behavior."""

import sys
import types
from importlib import util
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "admin_ui" / "backend"

_google_live_spec = util.spec_from_file_location(
    "google_live_validation_for_tests",
    BACKEND_ROOT / "services" / "google_live_validation.py",
)
google_live_validation = util.module_from_spec(_google_live_spec)
assert _google_live_spec.loader is not None
_google_live_spec.loader.exec_module(google_live_validation)

GOOGLE_LIVE_DEFAULT_MODEL = google_live_validation.GOOGLE_LIVE_DEFAULT_MODEL
build_google_key_validation_result = google_live_validation.build_google_key_validation_result
extract_google_live_models = google_live_validation.extract_google_live_models
select_google_live_model = google_live_validation.select_google_live_model


class _FakeGoogleResponse:
    """Small response double for Google model-discovery calls."""

    def __init__(self, status_code, payload=None, text=""):
        """Store the fake HTTP status, JSON payload, and response text."""
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        """Return the configured JSON payload."""
        return self._payload


def _fake_async_client_for(response):
    """Build an AsyncClient double bound to one fake response."""

    class FakeAsyncClient:
        """Async context-manager double for httpx.AsyncClient."""

        def __init__(self, *args, **kwargs):
            """Accept the same constructor shape as httpx.AsyncClient."""
            self.response = response

        async def __aenter__(self):
            """Return this fake client from the async context manager."""
            return self

        async def __aexit__(self, exc_type, exc, tb):
            """Do not suppress exceptions from the async context manager."""
            return False

        async def get(self, *args, **kwargs):
            """Return the configured fake response."""
            return self.response

    return FakeAsyncClient


def _install_wizard_import_stubs(monkeypatch):
    """Install minimal import stubs needed to load wizard.py in isolation."""
    settings = types.ModuleType("settings")
    settings.ENV_PATH = "/tmp/.env"
    settings.CONFIG_PATH = "/tmp/ai-agent.yaml"
    settings.LOCAL_CONFIG_PATH = "/tmp/ai-agent.local.yaml"
    settings.PROJECT_ROOT = str(BACKEND_ROOT.parents[1])
    settings.ensure_env_file = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "settings", settings)

    services_pkg = types.ModuleType("services")
    services_pkg.__path__ = [str(BACKEND_ROOT / "services")]
    monkeypatch.setitem(sys.modules, "services", services_pkg)
    monkeypatch.setitem(
        sys.modules,
        "services.google_live_validation",
        google_live_validation,
    )

    fs = types.ModuleType("services.fs")
    fs.upsert_env_vars = lambda *args, **kwargs: None
    fs.atomic_write_text = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "services.fs", fs)

    api_pkg = types.ModuleType("api")
    api_pkg.__path__ = [str(BACKEND_ROOT / "api")]
    monkeypatch.setitem(sys.modules, "api", api_pkg)

    fastapi = types.ModuleType("fastapi")

    class HTTPException(Exception):
        """Tiny FastAPI HTTPException stand-in."""

        def __init__(self, status_code=None, detail=None):
            """Store status and detail like FastAPI's exception."""
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class APIRouter:
        """Tiny APIRouter stand-in whose decorators return functions unchanged."""

        def post(self, *args, **kwargs):
            """Return a no-op route decorator for POST handlers."""
            return lambda func: func

        def get(self, *args, **kwargs):
            """Return a no-op route decorator for GET handlers."""
            return lambda func: func

        def delete(self, *args, **kwargs):
            """Return a no-op route decorator for DELETE handlers."""
            return lambda func: func

    fastapi.APIRouter = APIRouter
    fastapi.HTTPException = HTTPException
    monkeypatch.setitem(sys.modules, "fastapi", fastapi)

    pydantic = types.ModuleType("pydantic")

    class BaseModel:
        """Tiny pydantic BaseModel stand-in for simple attribute storage."""

        def __init__(self, **kwargs):
            """Copy keyword arguments onto the instance."""
            for key, value in kwargs.items():
                setattr(self, key, value)

    pydantic.BaseModel = BaseModel
    pydantic.Field = lambda default=None, **kwargs: default
    monkeypatch.setitem(sys.modules, "pydantic", pydantic)

    docker = types.ModuleType("docker")
    docker.from_env = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "docker", docker)

    models_catalog = types.ModuleType("api.models_catalog")
    for name in (
        "get_full_catalog",
        "get_models_by_language",
        "get_available_languages",
    ):
        setattr(models_catalog, name, lambda *args, **kwargs: {})
    for name in (
        "LANGUAGE_NAMES",
        "REGION_NAMES",
        "VOSK_STT_MODELS",
        "SHERPA_STT_MODELS",
        "KROKO_STT_MODELS",
        "PIPER_TTS_MODELS",
        "KOKORO_TTS_MODELS",
        "SILERO_TTS_MODELS",
        "LLM_MODELS",
    ):
        setattr(models_catalog, name, {})
    monkeypatch.setitem(sys.modules, "api.models_catalog", models_catalog)

    custom_models = types.ModuleType("api.custom_models")
    custom_models.merge_into_catalog = lambda catalog: catalog
    monkeypatch.setitem(sys.modules, "api.custom_models", custom_models)

    rebuild_jobs = types.ModuleType("api.rebuild_jobs")
    for name in (
        "start_rebuild_job",
        "get_rebuild_job",
        "get_enabled_backends",
        "is_rebuild_in_progress",
    ):
        setattr(rebuild_jobs, name, lambda *args, **kwargs: None)
    rebuild_jobs.BACKEND_BUILD_ARGS = {}
    rebuild_jobs.BUILD_TIME_ESTIMATES = {}
    monkeypatch.setitem(sys.modules, "api.rebuild_jobs", rebuild_jobs)


def _load_wizard_module(monkeypatch):
    """Load wizard.py with dependency stubs so route logic can be tested."""
    _install_wizard_import_stubs(monkeypatch)
    module_name = "wizard_for_google_validation_tests"
    module_path = BACKEND_ROOT / "api" / "wizard.py"
    spec = util.spec_from_file_location(module_name, module_path)
    module = util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_google_live_validation_accepts_key_when_live_models_are_not_advertised():
    """No advertised Live models should warn but still validate the key."""
    result = build_google_key_validation_result(
        [
            {
                "name": "models/gemini-2.5-flash",
                "supportedGenerationMethods": ["generateContent", "countTokens"],
            }
        ]
    )

    assert result["valid"] is True
    assert result["selected_model"] == GOOGLE_LIVE_DEFAULT_MODEL
    assert result["available_models"] == []
    assert "warning" in result
    assert "did not advertise Live-capable models" in result["warning"]


def test_google_live_validation_selects_preferred_live_model():
    """Preferred Google Live model order should win over response order."""
    result = build_google_key_validation_result(
        [
            {
                "name": "models/gemini-3.1-flash-live-preview",
                "supportedGenerationMethods": ["bidiGenerateContent"],
            },
            {
                "name": "models/gemini-2.5-flash-native-audio-preview-12-2025",
                "supportedGenerationMethods": ["generateContent", "bidiGenerateContent"],
            },
        ]
    )

    assert result["valid"] is True
    assert result["selected_model"] == "gemini-2.5-flash-native-audio-preview-12-2025"
    assert "warning" not in result


def test_google_live_model_extraction_strips_models_prefix():
    """Live model extraction should strip Google's models/ prefix."""
    live_models = extract_google_live_models(
        [
            {
                "name": "models/gemini-3.1-flash-live-preview",
                "supportedGenerationMethods": ["bidiGenerateContent"],
            },
            {
                "name": "models/gemini-2.5-flash",
                "supportedGenerationMethods": ["generateContent"],
            },
        ]
    )

    assert live_models == ["gemini-3.1-flash-live-preview"]
    assert select_google_live_model(live_models) == "gemini-3.1-flash-live-preview"


@pytest.mark.asyncio
async def test_google_validate_key_route_accepts_200_without_live_models(monkeypatch):
    """The wizard route should accept valid keys with inconclusive discovery."""
    wizard = _load_wizard_module(monkeypatch)
    response = _FakeGoogleResponse(
        200,
        {
            "models": [
                {
                    "name": "models/gemini-2.5-flash",
                    "supportedGenerationMethods": ["generateContent"],
                }
            ]
        },
    )
    monkeypatch.setattr(wizard.httpx, "AsyncClient", _fake_async_client_for(response))

    result = await wizard.validate_api_key(
        wizard.ApiKeyValidation(provider="google", api_key="AIza-test-key")
    )

    assert result["valid"] is True
    assert result["selected_model"] == GOOGLE_LIVE_DEFAULT_MODEL
    assert result["available_models"] == []
    assert "warning" in result


@pytest.mark.asyncio
async def test_google_validate_key_route_treats_429_as_advisory(monkeypatch):
    """Rate-limited model discovery should not block setup."""
    wizard = _load_wizard_module(monkeypatch)
    response = _FakeGoogleResponse(429, {"error": {"message": "quota"}})
    monkeypatch.setattr(wizard.httpx, "AsyncClient", _fake_async_client_for(response))

    result = await wizard.validate_api_key(
        wizard.ApiKeyValidation(provider="google", api_key="AIza-test-key")
    )

    assert result["valid"] is True
    assert result["selected_model"] == GOOGLE_LIVE_DEFAULT_MODEL
    assert result["available_models"] == []
    assert "warning" in result
    assert "rate-limited" in result["warning"]


@pytest.mark.asyncio
async def test_google_validate_key_route_rejects_401_invalid_key(monkeypatch):
    """HTTP 401 should be treated as an invalid Google API key."""
    wizard = _load_wizard_module(monkeypatch)
    response = _FakeGoogleResponse(
        401,
        {"error": {"message": "Invalid API key"}},
    )
    monkeypatch.setattr(wizard.httpx, "AsyncClient", _fake_async_client_for(response))

    result = await wizard.validate_api_key(
        wizard.ApiKeyValidation(provider="google", api_key="AIza-test-key")
    )

    assert result["valid"] is False
    assert result["error"] == "Invalid API key"
    assert "selected_model" not in result
    assert "available_models" not in result


@pytest.mark.asyncio
async def test_google_validate_key_route_separates_403_access_denied(monkeypatch):
    """HTTP 403 should expose access guidance instead of invalid-key text."""
    wizard = _load_wizard_module(monkeypatch)
    response = _FakeGoogleResponse(
        403,
        {"error": {"message": "API key not authorized for this project"}},
    )
    monkeypatch.setattr(wizard.httpx, "AsyncClient", _fake_async_client_for(response))

    result = await wizard.validate_api_key(
        wizard.ApiKeyValidation(provider="google", api_key="AIza-test-key")
    )

    assert result["valid"] is False
    assert result["error"] == "API key not authorized for this project"
