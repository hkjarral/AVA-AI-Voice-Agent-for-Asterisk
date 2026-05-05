from typing import Any, Dict, List, Optional


GOOGLE_LIVE_DEFAULT_MODEL = "gemini-2.5-flash-native-audio-latest"
GOOGLE_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GOOGLE_LIVE_PREFERRED_MODELS = [
    GOOGLE_LIVE_DEFAULT_MODEL,
    "gemini-2.5-flash-native-audio-preview-12-2025",
    "gemini-2.5-flash-native-audio-preview-09-2025",
    "gemini-3.1-flash-live-preview",
    "gemini-live-2.5-flash-native-audio",
    "gemini-live-2.5-flash-preview-native-audio-09-2025",
    "gemini-live-2.5-flash-preview",
]

GOOGLE_LIVE_DISCOVERY_WARNING = (
    "Google API key validated, but Google's models endpoint did not advertise "
    "Live-capable models for this key. Setup will continue using the default "
    f"Gemini Live model ({GOOGLE_LIVE_DEFAULT_MODEL}). If calls fail, verify "
    "Live API access, billing/quota, and model availability in AI Studio."
)


def extract_google_live_models(models: List[Dict[str, Any]]) -> List[str]:
    """Extract model names that support bidiGenerateContent (Gemini Live)."""
    live_models: List[str] = []
    for model in models:
        methods = model.get("supportedGenerationMethods", [])
        if "bidiGenerateContent" in methods:
            model_name = model.get("name", "").replace("models/", "")
            if model_name:
                live_models.append(model_name)
    return live_models


def select_google_live_model(live_models: List[str]) -> Optional[str]:
    """Pick the best available Google Live model using preferred order."""
    for preferred_model in GOOGLE_LIVE_PREFERRED_MODELS:
        if preferred_model in live_models:
            return preferred_model
    if live_models:
        return live_models[0]
    return None


def build_google_key_validation_result(models: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build the Admin UI key-validation response for Google Developer API keys.

    `models.list` is useful for discovery, but it is not a reliable hard gate for
    Live API access across free-tier and preview-model projects. A successful
    models response proves the key is usable; missing Live model metadata should
    warn the operator rather than blocking setup.
    """
    live_models = extract_google_live_models(models)

    if not live_models:
        return {
            "valid": True,
            "message": GOOGLE_LIVE_DISCOVERY_WARNING,
            "warning": GOOGLE_LIVE_DISCOVERY_WARNING,
            "selected_model": GOOGLE_LIVE_DEFAULT_MODEL,
            "available_models": [],
        }

    selected_model = select_google_live_model(live_models)
    if selected_model:
        return {
            "valid": True,
            "message": f"Google API key is valid. Live model '{selected_model}' is available.",
            "selected_model": selected_model,
            "available_models": live_models,
        }

    return {
        "valid": True,
        "message": f"Google API key is valid. Available Live models: {', '.join(live_models[:3])}",
        "available_models": live_models,
    }
