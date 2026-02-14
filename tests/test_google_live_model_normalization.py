from src.providers.google_live import GoogleLiveProvider


def test_google_live_model_normalization_defaults_to_provider_default():
    assert GoogleLiveProvider._normalize_model_name(None) == GoogleLiveProvider.DEFAULT_LIVE_MODEL
    assert GoogleLiveProvider._normalize_model_name("") == GoogleLiveProvider.DEFAULT_LIVE_MODEL


def test_google_live_model_normalization_maps_legacy_aliases():
    assert (
        GoogleLiveProvider._normalize_model_name("gemini-live-2.5-flash-preview")
        == GoogleLiveProvider.DEFAULT_LIVE_MODEL
    )


def test_google_live_model_normalization_keeps_live_native_audio_models():
    assert (
        GoogleLiveProvider._normalize_model_name("gemini-2.5-flash-native-audio-preview-09-2025")
        == "gemini-2.5-flash-native-audio-preview-09-2025"
    )
    assert (
        GoogleLiveProvider._normalize_model_name("gemini-2.5-flash-native-audio-latest")
        == "gemini-2.5-flash-native-audio-latest"
    )


def test_google_live_model_normalization_rejects_non_live_model_values():
    assert (
        GoogleLiveProvider._normalize_model_name("models/gemini-1.5-pro-latest")
        == GoogleLiveProvider.DEFAULT_LIVE_MODEL
    )


def test_google_live_model_normalization_keeps_non_native_audio_live_models():
    assert (
        GoogleLiveProvider._normalize_model_name("gemini-2.0-flash-live-001")
        == "gemini-2.0-flash-live-001"
    )
