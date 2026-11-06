import json

import pytest

from api import config as config_api

_TTS_ENV = (
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "DEEPGRAM_API_KEY",
    "OPENAI_API_KEY",
    "ELEVENLABS_API_KEY",
    "GROQ_API_KEY",
    "AZURE_SPEECH_KEY",
    "CAMB_API_KEY",
    "FISH_AUDIO_API_KEY",
    "FISH_AUDIO_REFERENCE_ID",
    "TTS_OPTIONS_TEST_GOOGLE_KEY",
)


@pytest.fixture(autouse=True)
def _isolated_tts_env(monkeypatch, tmp_path):
    for name in _TTS_ENV:
        monkeypatch.delenv(name, raising=False)
    # Keep the shared service-account file of the host out of the result.
    monkeypatch.setattr(config_api, "VERTEX_CREDENTIALS_PATH", str(tmp_path / "absent.json"))


def _use_providers(monkeypatch, providers):
    monkeypatch.setattr(config_api, "_read_merged_config_dict", lambda: {"providers": providers})


async def _options_by_key():
    response = await config_api.get_tts_provider_options()
    return response, {item["key"]: item for item in response}


@pytest.mark.asyncio
async def test_tts_options_list_engine_components_with_readiness(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-env-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-env-secret")
    _use_providers(
        monkeypatch,
        {
            "google": {"enabled": True, "api_key": "google-inline-secret"},
            "deepgram": {"enabled": True, "model": "nova-3"},
            "openai": {"api_key": "openai-inline-secret"},
            "elevenlabs": {"api_key": "eleven-inline-secret", "voice_id": "abc"},
            "azure_tts": {"enabled": False, "region": "eastus"},
            "fishaudio": {"api_key": "fish-inline-secret"},
            "local": {"enabled": True},
            "openai_llm": {"type": "openai", "api_key": "llm-inline-secret"},
            "mystery_tts": {"type": "unknown"},
        },
    )

    response, by_key = await _options_by_key()

    assert set(by_key) == {
        "google_tts",
        "deepgram_tts",
        "openai_tts",
        "elevenlabs_tts",
        "azure_tts",
        "fishaudio_tts",
    }
    assert by_key["google_tts"] == {
        "key": "google_tts",
        "label": "Google Cloud Text-to-Speech",
        "ready": True,
    }
    assert by_key["deepgram_tts"]["ready"] is True
    assert by_key["openai_tts"]["ready"] is True
    # The engine replaces an inline ElevenLabs key with ELEVENLABS_API_KEY.
    assert by_key["elevenlabs_tts"] == {
        "key": "elevenlabs_tts",
        "label": "ElevenLabs",
        "ready": False,
        "reason": "credential_missing",
    }
    assert by_key["azure_tts"]["ready"] is False
    assert by_key["azure_tts"]["reason"] == "disabled"
    assert by_key["fishaudio_tts"]["ready"] is False
    assert by_key["fishaudio_tts"]["reason"] == "voice_missing"
    assert [item["label"] for item in response] == sorted(
        (item["label"] for item in response), key=str.lower
    )
    serialized = json.dumps(response)
    for secret in (
        "google-inline-secret",
        "dg-env-secret",
        "openai-env-secret",
        "openai-inline-secret",
        "eleven-inline-secret",
        "fish-inline-secret",
        "llm-inline-secret",
    ):
        assert secret not in serialized


@pytest.mark.asyncio
async def test_inline_key_the_engine_strips_is_not_ready(monkeypatch):
    _use_providers(monkeypatch, {"openai": {"api_key": "openai-inline-secret"}})

    _, by_key = await _options_by_key()

    assert by_key["openai_tts"]["ready"] is False
    assert by_key["openai_tts"]["reason"] == "credential_missing"


@pytest.mark.asyncio
async def test_google_credentials_path_alone_is_not_ready(monkeypatch, tmp_path):
    service_account = tmp_path / "sa.json"
    service_account.write_text("{}")
    _use_providers(
        monkeypatch, {"google": {"enabled": True, "credentials_path": str(service_account)}}
    )

    _, by_key = await _options_by_key()

    assert by_key["google_tts"]["ready"] is False
    assert by_key["google_tts"]["reason"] == "credential_missing"


@pytest.mark.asyncio
async def test_google_service_account_needs_an_existing_file(monkeypatch, tmp_path):
    _use_providers(monkeypatch, {"google": {"enabled": True}})
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "missing.json"))

    _, by_key = await _options_by_key()
    assert by_key["google_tts"]["ready"] is False

    service_account = tmp_path / "sa.json"
    service_account.write_text("{}")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(service_account))

    _, by_key = await _options_by_key()
    assert by_key["google_tts"]["ready"] is True


@pytest.mark.asyncio
async def test_google_shared_service_account_file_counts(monkeypatch, tmp_path):
    shared = tmp_path / "gcp-service-account.json"
    shared.write_text("{}")
    monkeypatch.setattr(config_api, "VERTEX_CREDENTIALS_PATH", str(shared))
    _use_providers(monkeypatch, {"google": {"enabled": True}})

    _, by_key = await _options_by_key()

    assert by_key["google_tts"]["ready"] is True


@pytest.mark.asyncio
async def test_google_env_reference_and_legacy_key(monkeypatch):
    _use_providers(monkeypatch, {"google": {"api_key": "${TTS_OPTIONS_TEST_GOOGLE_KEY}"}})

    _, by_key = await _options_by_key()
    assert by_key["google_tts"]["ready"] is False

    monkeypatch.setenv("TTS_OPTIONS_TEST_GOOGLE_KEY", "resolved-google-secret")
    response, by_key = await _options_by_key()
    assert by_key["google_tts"]["ready"] is True
    assert "resolved-google-secret" not in json.dumps(response)

    _use_providers(monkeypatch, {"google": {"enabled": True}})
    monkeypatch.setenv("GOOGLE_API_KEY", "legacy-google-secret")
    response, by_key = await _options_by_key()
    assert by_key["google_tts"]["ready"] is True
    assert "legacy-google-secret" not in json.dumps(response)


@pytest.mark.asyncio
async def test_disabled_provider_with_key_is_not_ready(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "legacy-google-secret")
    _use_providers(monkeypatch, {"google": {"enabled": False}})

    _, by_key = await _options_by_key()

    assert by_key["google_tts"] == {
        "key": "google_tts",
        "label": "Google Cloud Text-to-Speech",
        "ready": False,
        "reason": "disabled",
    }


@pytest.mark.asyncio
async def test_custom_tts_instances_use_engine_component_keys(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "groq-env-secret")
    _use_providers(
        monkeypatch,
        {
            "narrator_tts": {
                "type": "fishaudio",
                "name": "Narrator",
                "api_key": "fish-inline-secret",
                "reference_id": "voice-1",
            },
            "fast_tts": {"type": "groq", "voice": "hannah"},
        },
    )

    response, by_key = await _options_by_key()

    assert by_key["narrator_tts"] == {"key": "narrator_tts", "label": "Narrator", "ready": True}
    assert by_key["groq_tts"]["ready"] is True
    assert "fast_tts" not in by_key
    assert "groq-env-secret" not in json.dumps(response)
    assert "fish-inline-secret" not in json.dumps(response)


@pytest.mark.asyncio
async def test_no_tts_providers_returns_empty_list(monkeypatch):
    _use_providers(monkeypatch, {"local": {"enabled": True}, "google_live": {"api_key": "x"}})

    response, _ = await _options_by_key()

    assert response == []


@pytest.mark.asyncio
async def test_separate_component_block_disables_provider(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-env-secret")
    monkeypatch.setenv("GOOGLE_API_KEY", "legacy-google-secret")
    _use_providers(
        monkeypatch,
        {
            "deepgram": {"enabled": True},
            "google": {"enabled": True},
            "deepgram_tts": {"enabled": False},
            "google_tts": {"enabled": False},
        },
    )

    _, by_key = await _options_by_key()

    assert by_key["deepgram_tts"]["ready"] is False
    assert by_key["deepgram_tts"]["reason"] == "disabled"
    assert by_key["google_tts"]["ready"] is False
    assert by_key["google_tts"]["reason"] == "disabled"


@pytest.mark.asyncio
async def test_disabled_provider_prefix_disables_custom_instance(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "groq-env-secret")
    _use_providers(
        monkeypatch,
        {
            "groq": {"enabled": False},
            "fast_tts": {"type": "groq", "voice": "hannah"},
        },
    )

    _, by_key = await _options_by_key()

    assert by_key["groq_tts"]["ready"] is False
    assert by_key["groq_tts"]["reason"] == "disabled"
