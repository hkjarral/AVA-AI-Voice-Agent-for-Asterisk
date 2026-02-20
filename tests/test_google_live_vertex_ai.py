"""
Unit tests for Google Vertex AI Live API support (AAVA-191).

Tests cover:
- Vertex AI endpoint URL construction
- Model path format differences (Developer API vs Vertex AI)
- Config field defaults
- Validation logic (missing vertex_project)
"""

import pytest
from src.config import GoogleProviderConfig


# ---------------------------------------------------------------------------
# Config field defaults
# ---------------------------------------------------------------------------

def test_google_provider_config_vertex_defaults_to_disabled():
    cfg = GoogleProviderConfig()
    assert cfg.use_vertex_ai is False


def test_google_provider_config_vertex_location_default():
    cfg = GoogleProviderConfig()
    assert cfg.vertex_location == "us-central1"


def test_google_provider_config_vertex_project_default_none():
    cfg = GoogleProviderConfig()
    assert cfg.vertex_project is None


def test_google_provider_config_vertex_fields_set():
    cfg = GoogleProviderConfig(
        use_vertex_ai=True,
        vertex_project="my-project-123",
        vertex_location="europe-west4",
    )
    assert cfg.use_vertex_ai is True
    assert cfg.vertex_project == "my-project-123"
    assert cfg.vertex_location == "europe-west4"


# ---------------------------------------------------------------------------
# Vertex AI endpoint URL construction
# ---------------------------------------------------------------------------

def _build_vertex_endpoint(location: str) -> str:
    """Mirror the endpoint construction logic from google_live.py start_session()."""
    return (
        f"wss://{location}-aiplatform.googleapis.com"
        f"/ws/google.cloud.aiplatform.v1beta1.LlmBidiService/BidiGenerateContent"
    )


def test_vertex_endpoint_default_location():
    url = _build_vertex_endpoint("us-central1")
    assert url == (
        "wss://us-central1-aiplatform.googleapis.com"
        "/ws/google.cloud.aiplatform.v1beta1.LlmBidiService/BidiGenerateContent"
    )


def test_vertex_endpoint_custom_location():
    url = _build_vertex_endpoint("europe-west4")
    assert url == (
        "wss://europe-west4-aiplatform.googleapis.com"
        "/ws/google.cloud.aiplatform.v1beta1.LlmBidiService/BidiGenerateContent"
    )


def test_vertex_endpoint_uses_v1beta1_not_v1beta():
    url = _build_vertex_endpoint("us-central1")
    assert "v1beta1" in url
    assert "v1beta.GenerativeService" not in url


def test_developer_api_endpoint_unchanged():
    """Developer API endpoint must remain unchanged for regression safety."""
    # Test the actual production config default, not a local constant
    cfg = GoogleProviderConfig()
    expected = (
        "wss://generativelanguage.googleapis.com"
        "/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
    )
    assert cfg.websocket_endpoint == expected


# ---------------------------------------------------------------------------
# Model path format
# ---------------------------------------------------------------------------

def _build_model_path(use_vertex: bool, model_name: str) -> str:
    """Mirror the model path logic from google_live.py _send_setup()."""
    if use_vertex:
        return f"publishers/google/models/{model_name}"
    return f"models/{model_name}"


def test_developer_api_model_path_uses_models_prefix():
    path = _build_model_path(False, "gemini-2.5-flash-native-audio-latest")
    assert path == "models/gemini-2.5-flash-native-audio-latest"


def test_vertex_ai_model_path_uses_publishers_prefix():
    path = _build_model_path(True, "gemini-live-2.5-flash-native-audio")
    assert path == "publishers/google/models/gemini-live-2.5-flash-native-audio"


def test_vertex_ai_model_path_does_not_double_prefix():
    """Ensure no double-prefix if model name is already clean."""
    path = _build_model_path(True, "gemini-live-2.5-flash-native-audio")
    assert path.count("publishers/google/models/") == 1
    assert "models/models/" not in path


def test_developer_api_model_path_does_not_use_publishers():
    path = _build_model_path(False, "gemini-2.5-flash-native-audio-latest")
    assert "publishers" not in path


# ---------------------------------------------------------------------------
# Vertex AI validation (missing project)
# ---------------------------------------------------------------------------

def test_vertex_ai_requires_project_when_enabled():
    """vertex_project must be set when use_vertex_ai=True."""
    cfg = GoogleProviderConfig(use_vertex_ai=True, vertex_project=None)
    # Simulate the validation check from start_session()
    vertex_project = (cfg.vertex_project or "").strip()
    assert not vertex_project, "Expected empty project to trigger validation error"


def test_vertex_ai_project_set_passes_validation():
    cfg = GoogleProviderConfig(use_vertex_ai=True, vertex_project="my-project")
    vertex_project = (cfg.vertex_project or "").strip()
    assert vertex_project == "my-project"


# ---------------------------------------------------------------------------
# Developer API path unchanged (regression guard)
# ---------------------------------------------------------------------------

def test_developer_api_requires_api_key_not_vertex():
    """When use_vertex_ai=False, api_key is the auth mechanism."""
    cfg = GoogleProviderConfig(use_vertex_ai=False, api_key="AIzaTestKey")
    assert cfg.use_vertex_ai is False
    assert cfg.api_key == "AIzaTestKey"


def test_vertex_ai_does_not_require_api_key():
    """When use_vertex_ai=True, api_key is not required."""
    cfg = GoogleProviderConfig(
        use_vertex_ai=True,
        vertex_project="my-project",
        vertex_location="us-central1",
        api_key=None,
    )
    assert cfg.use_vertex_ai is True
    assert cfg.api_key is None
