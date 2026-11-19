from src.config import GoogleProviderConfig

RETIRED_MODELS = ("gemini-1.5", "gemini-2.0")


def test_google_llm_default_model_is_not_a_retired_gemini_model():
    model = GoogleProviderConfig().llm_model

    assert model == "models/gemini-3.8-flash"
    assert not any(retired in model for retired in RETIRED_MODELS)
