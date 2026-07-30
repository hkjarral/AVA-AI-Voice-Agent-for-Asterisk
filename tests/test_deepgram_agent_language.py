import json
from unittest.mock import AsyncMock, patch

import pytest

from src.config import DeepgramProviderConfig, LLMConfig
from src.providers.deepgram import (
    DEEPGRAM_AGENT_LANGUAGES,
    DeepgramProvider,
    deepgram_aura_language,
    normalize_agent_language,
    validate_agent_language_configuration,
)


class _CapturingWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, payload):
        self.messages.append(payload)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("en-US", "en"), ("es-419", "es"), ("JA", "ja"), ("nl_NL", "nl")],
)
def test_agent_language_normalizes_existing_locale_variants(raw, expected):
    assert normalize_agent_language(raw) == expected


@pytest.mark.parametrize(
    ("voice", "expected"),
    [
        ("aura-2-thalia-en", "en"),
        ("aura-2-celeste-es", "es"),
        ("aura-2-julius-de", "de"),
        ("aura-2-agathe-fr", "fr"),
        ("aura-2-livia-it", "it"),
        ("aura-2-rhea-nl", "nl"),
        ("aura-2-fujin-ja", "ja"),
    ],
)
def test_aura_catalog_covers_supported_agent_languages(voice, expected):
    assert deepgram_aura_language(voice) == expected
    assert expected in DEEPGRAM_AGENT_LANGUAGES


def test_unknown_aura_voice_has_no_inferred_language():
    # Suffix-shaped custom strings are not enough: the closed catalog owns the
    # support contract.
    assert deepgram_aura_language("aura-future-en") is None


@pytest.mark.parametrize(
    ("model", "language", "voice"),
    [
        ("nova-3", "en-US", "aura-2-thalia-en"),
        ("flux-general-en", "en", "aura-2-thalia-en"),
        ("flux-general-multi", "es", "aura-2-celeste-es"),
        ("nova-3", "de", "aura-2-julius-de"),
        ("nova-3", "fr", "aura-2-agathe-fr"),
        ("nova-3", "it", "aura-2-livia-it"),
        ("nova-3", "nl", "aura-2-rhea-nl"),
        ("nova-3", "ja", "aura-2-fujin-ja"),
        ("nova-2-medical", "en", "aura-2-thalia-en"),
    ],
)
def test_supported_and_legacy_model_combinations_validate(model, language, voice):
    assert validate_agent_language_configuration(
        listen_model=model,
        agent_language=language,
        speak_model=voice,
    ) == normalize_agent_language(language)


def test_unsupported_language_fails_closed_with_actionable_message():
    with pytest.raises(ValueError, match="AAVA supports"):
        validate_agent_language_configuration(
            listen_model="nova-3",
            agent_language="pt-BR",
            speak_model="aura-2-thalia-en",
        )


def test_english_only_flux_model_rejects_spanish():
    with pytest.raises(ValueError, match="English-only"):
        validate_agent_language_configuration(
            listen_model="flux-general-en",
            agent_language="es",
            speak_model="aura-2-celeste-es",
        )


def test_voice_language_mismatch_fails_without_fallback():
    with pytest.raises(ValueError, match="will not silently replace"):
        validate_agent_language_configuration(
            listen_model="flux-general-multi",
            agent_language="es",
            speak_model="aura-2-thalia-en",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "language", "voice", "expected_listen", "expected_top_level_language"),
    [
        (
            "nova-3",
            "en-US",
            "aura-2-thalia-en",
            {"type": "deepgram", "model": "nova-3", "language": "en"},
            "en-US",
        ),
        (
            "flux-general-en",
            "en",
            "aura-2-luna-en",
            {
                "type": "deepgram",
                "model": "flux-general-en",
                "version": "v2",
                "eot_threshold": 0.7,
            },
            None,
        ),
        (
            "flux-general-multi",
            "es",
            "aura-2-celeste-es",
            {
                "type": "deepgram",
                "model": "flux-general-multi",
                "version": "v2",
                "eot_threshold": 0.7,
                "language_hints": ["es"],
            },
            None,
        ),
    ],
)
async def test_primary_and_retry_payloads_share_language_contract(
    model,
    language,
    voice,
    expected_listen,
    expected_top_level_language,
):
    provider = DeepgramProvider(
        {
            "model": model,
            "agent_language": language,
            "tts_model": voice,
        },
        LLMConfig(),
        None,
    )
    provider.call_id = "deepgram-language"
    provider.websocket = _CapturingWebSocket()
    provider._allowed_tools = []

    await provider._configure_agent()

    primary = json.loads(provider.websocket.messages[0])
    retry = provider._last_settings_minimal
    assert primary["agent"]["listen"]["provider"] == expected_listen
    assert retry["agent"]["listen"]["provider"] == expected_listen
    assert primary["agent"]["speak"] == retry["agent"]["speak"]
    assert "language" not in primary["agent"]["speak"]["provider"]
    if expected_top_level_language is None:
        # Deepgram rejects deprecated agent.language with the V2/Flux API.
        assert "language" not in primary["agent"]
        assert "language" not in retry["agent"]
        if model == "flux-general-multi":
            assert primary["agent"]["listen"]["provider"]["language_hints"] == ["es"]
        else:
            assert "language_hints" not in primary["agent"]["listen"]["provider"]
        assert primary["agent"]["speak"]["provider"]["model"] == voice
    else:
        # Nova/V1 retains the deprecated top-level value for compatibility.
        assert primary["agent"]["language"] == expected_top_level_language
        assert retry["agent"]["language"] == expected_top_level_language


@pytest.mark.asyncio
async def test_invalid_configuration_sends_no_settings_payload():
    provider = DeepgramProvider(
        {
            "model": "nova-3",
            "agent_language": "es",
            "tts_model": "aura-2-thalia-en",
        },
        LLMConfig(),
        None,
    )
    provider.call_id = "deepgram-invalid-language"
    provider.websocket = _CapturingWebSocket()

    with pytest.raises(ValueError, match="will not silently replace"):
        await provider._configure_agent()

    assert provider.websocket.messages == []


@pytest.mark.asyncio
async def test_invalid_configuration_opens_no_remote_websocket():
    provider = DeepgramProvider(
        DeepgramProviderConfig(
            api_key="test-key",
            model="nova-3",
            agent_language="es",
            tts_model="aura-2-thalia-en",
        ),
        LLMConfig(),
        None,
    )

    with patch("src.providers.deepgram.websockets.connect", new=AsyncMock()) as connect:
        with pytest.raises(ValueError, match="will not silently replace"):
            await provider.start_session("deepgram-invalid-preflight")

    connect.assert_not_awaited()
    assert provider.websocket is None
    assert provider._receive_task is None
