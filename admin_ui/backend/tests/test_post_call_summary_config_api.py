import json
import os

import httpx
import pytest
from fastapi import HTTPException

from api import config as config_api
from api import tools as tools_api


@pytest.mark.asyncio
async def test_llm_options_are_secret_safe_and_report_readiness(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "legacy-openai-secret")
    monkeypatch.setattr(
        config_api,
        "_read_merged_config_dict",
        lambda: {
            "providers": {
                "deepseek_llm": {
                    "name": "DeepSeek",
                    "type": "openai",
                    "chat_model": "deepseek-chat",
                    "chat_base_url": "https://api.deepseek.com/v1",
                    "api_key": "super-secret",
                },
                "claude_llm": {
                    "name": "Claude via OpenRouter",
                    "type": "openai",
                    "chat_model": "anthropic/claude-sonnet-4",
                    "chat_base_url": "https://openrouter.ai/api/v1",
                    "api_key": "${UNSET_OPENROUTER_KEY}",
                },
                "ollama_llm": {
                    "name": "Local Ollama",
                    "type": "ollama",
                    "model": "qwen3",
                },
                "telnyx": {
                    "name": "Telnyx AI",
                    "api_key": "telnyx-secret",
                },
                "minimax": {
                    "name": "MiniMax",
                    "api_key": "minimax-secret",
                },
                "disabled_llm": {"type": "openai", "enabled": False, "api_key": "hidden"},
            }
        },
    )

    response = await config_api.get_llm_provider_options()
    by_key = {item["key"]: item for item in response["providers"]}

    assert by_key["deepseek_llm"]["ready"] is True
    assert by_key["deepseek_llm"]["model"] == "deepseek-chat"
    assert by_key["claude_llm"]["ready"] is False
    assert by_key["ollama_llm"]["ready"] is True
    assert by_key["telnyx_llm"]["ready"] is True
    assert by_key["minimax_llm"]["ready"] is True
    assert by_key["disabled_llm"]["enabled"] is False
    assert by_key["disabled_llm"]["credential_configured"] is True
    assert by_key["disabled_llm"]["ready"] is False
    assert by_key["disabled_llm"]["readiness"] == "disabled"
    assert response["legacy_provider"] == {
        "key": "",
        "label": "OpenAI (legacy default)",
        "type": "openai",
        "model": "gpt-4o-mini",
        "enabled": True,
        "credential_required": True,
        "credential_configured": True,
        "ready": True,
        "readiness": "ready",
        "legacy": True,
    }
    assert "super-secret" not in json.dumps(response)
    assert "legacy-openai-secret" not in json.dumps(response)


@pytest.mark.asyncio
async def test_deepseek_readiness_does_not_inherit_openai_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-only-secret")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        config_api,
        "_read_merged_config_dict",
        lambda: {
            "providers": {
                "deepseek_llm": {
                    "type": "openai",
                    "enabled": True,
                    "chat_model": "deepseek-v4-flash",
                    "chat_base_url": "https://api.deepseek.com",
                }
            }
        },
    )

    response = await config_api.get_llm_provider_options()

    assert response["providers"][0]["key"] == "deepseek_llm"
    assert response["providers"][0]["credential_configured"] is False
    assert response["providers"][0]["ready"] is False
    assert response["legacy_provider"]["ready"] is True


@pytest.mark.asyncio
async def test_key_required_provider_sentinel_is_not_ready(monkeypatch):
    monkeypatch.setattr(
        config_api,
        "_read_merged_config_dict",
        lambda: {
            "providers": {
                "native_llm": {"type": "openai", "api_key": "not-needed"},
                "telnyx_llm": {"type": "telnyx", "api_key": "not-needed"},
                "minimax_llm": {"type": "minimax", "api_key": "not-needed"},
            }
        },
    )

    response = await config_api.get_llm_provider_options()
    by_key = {item["key"]: item for item in response["providers"]}

    assert by_key["native_llm"]["ready"] is True
    assert by_key["telnyx_llm"]["credential_configured"] is False
    assert by_key["telnyx_llm"]["ready"] is False
    assert by_key["minimax_llm"]["credential_configured"] is False
    assert by_key["minimax_llm"]["ready"] is False


@pytest.mark.asyncio
async def test_modular_llm_api_key_upload_uses_owner_only_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config_api, "PROVIDER_SECRETS_ROOT", str(tmp_path))
    monkeypatch.setattr(
        config_api,
        "_read_merged_config_dict",
        lambda: {"providers": {"deepseek_llm": {"type": "openai"}}},
    )
    updates = []
    monkeypatch.setattr(
        config_api,
        "_update_provider_credentials_field",
        lambda provider, field, value: updates.append((provider, field, value)),
    )

    response = await config_api.upload_provider_api_key(
        "deepseek_llm", {"api_key": "provider-secret"}
    )

    credential = tmp_path / "deepseek_llm" / "api-key"
    assert credential.read_text() == "provider-secret"
    assert os.stat(credential).st_mode & 0o777 == 0o600
    assert updates == [("deepseek_llm", "api_key_file", str(credential))]
    assert response["restart_pending"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("summary_max_words", 9),
        ("summary_timeout_ms", 999),
        ("summary_provider", "not-an-llm"),
        ("summary_prompt", "Use {transcript} directly"),
        ("summary_prompt", "Use {} words"),
    ],
)
def test_managed_tool_rejects_invalid_summary_settings(field, value):
    payload = {
        "name": "post_call",
        "phase": "post_call",
        "url": "https://example.com/hook",
        field: value,
    }
    with pytest.raises(ValueError):
        tools_api.ManagedToolWrite(**payload)


def test_managed_tool_accepts_provider_prompt_and_timeout():
    model = tools_api.ManagedToolWrite(
        name="post_call",
        phase="post_call",
        url="https://example.com/hook",
        generate_summary=True,
        summary_provider="deepseek_llm",
        summary_max_words=120,
        summary_timeout_ms=20000,
        summary_prompt="Summarize in {max_words} words; return JSON like {{\"summary\": \"...\"}}.",
    )
    assert model.summary_provider == "deepseek_llm"


def test_inline_secret_migration_ignores_no_auth_sentinel(monkeypatch):
    writes = []
    monkeypatch.setattr(
        config_api,
        "_write_provider_secret",
        lambda *args: writes.append(args),
    )
    config = {
        "providers": {
            "native_llm": {
                "type": "openai",
                "api_key": "not-needed",
            }
        }
    }

    assert config_api._migrate_inline_provider_secrets(config) is False
    assert config["providers"]["native_llm"]["api_key"] == "not-needed"
    assert writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_key,provider_cfg,expected_url",
    [
        (
            "openai_llm",
            {"type": "openai", "chat_base_url": "https://api.openai.com/v1"},
            "https://api.openai.com/v1/models",
        ),
        (
            "google_llm",
            {"type": "google"},
            "https://generativelanguage.googleapis.com/v1beta/models",
        ),
        (
            "telnyx_llm",
            {"type": "telnyx", "chat_base_url": "https://api.telnyx.com/v2/ai"},
            "https://api.telnyx.com/v2/ai/models",
        ),
        (
            "telenyx_llm",
            {"type": "telenyx", "chat_base_url": "https://api.telnyx.com/v2/ai"},
            "https://api.telnyx.com/v2/ai/models",
        ),
        (
            "minimax_llm",
            {"type": "minimax", "chat_base_url": "https://api.minimax.io/v1"},
            "https://api.minimax.io/v1/models",
        ),
    ],
)
async def test_modular_provider_credentials_verify_success(
    monkeypatch, provider_key, provider_cfg, expected_url
):
    calls = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return type("Response", (), {"status_code": 200})()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        config_api,
        "_read_merged_config_dict",
        lambda: {"providers": {provider_key: {**provider_cfg, "api_key": "test-secret"}}},
    )

    response = await config_api.verify_provider_credentials(provider_key)

    assert response["status"] == "success"
    assert calls[0][0] == expected_url


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", ["openai", "google", "telnyx", "telenyx", "minimax"])
async def test_modular_provider_credentials_verify_failure(monkeypatch, provider_type):
    provider_key = f"{provider_type}_llm"

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url, **_kwargs):
            return type("Response", (), {"status_code": 401})()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        config_api,
        "_read_merged_config_dict",
        lambda: {
            "providers": {
                provider_key: {
                    "type": provider_type,
                    "api_key": "bad-secret",
                }
            }
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        await config_api.verify_provider_credentials(provider_key)

    assert exc_info.value.status_code == 400
    assert "verification failed" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_fish_audio_credentials_verify_uses_official_model_endpoint(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return type("Response", (), {"status_code": 200})()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        config_api,
        "_read_merged_config_dict",
        lambda: {
            "providers": {
                "fishaudio_tts": {
                    "type": "fishaudio",
                    "api_key": "test-secret",
                    "base_url": "https://api.fish.audio/v1",
                }
            }
        },
    )

    response = await config_api.verify_provider_credentials("fishaudio_tts")

    assert response["status"] == "success"
    assert calls[0][0] == "https://api.fish.audio/model"
    assert calls[0][1]["params"] == {"page_size": 1}


@pytest.mark.asyncio
async def test_fish_audio_credentials_verify_rejects_nonofficial_remote_host(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return type("Response", (), {"status_code": 200})()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        config_api,
        "_read_merged_config_dict",
        lambda: {
            "providers": {
                "fishaudio_tts": {
                    "type": "fishaudio",
                    "api_key": "test-secret",
                    "base_url": "https://untrusted.example/v1",
                }
            }
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        await config_api.verify_provider_credentials("fishaudio_tts")

    assert exc_info.value.status_code == 400
    assert "api.fish.audio" in str(exc_info.value.detail)
    assert calls == []


@pytest.mark.asyncio
async def test_fish_audio_credentials_verify_uses_fixed_bundled_mock_endpoint(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return type("Response", (), {"status_code": 200})()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        config_api,
        "_read_merged_config_dict",
        lambda: {
            "providers": {
                "fishaudio_tts": {
                    "type": "fishaudio",
                    "api_key": "mock-key",
                    "base_url": "http://localhost:8788/v1",
                }
            }
        },
    )

    response = await config_api.verify_provider_credentials("fishaudio_tts")

    assert response["status"] == "success"
    assert calls[0][0] == "http://127.0.0.1:8788/model"


@pytest.mark.asyncio
async def test_fish_audio_credentials_verify_rejects_other_loopback_port(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return type("Response", (), {"status_code": 200})()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        config_api,
        "_read_merged_config_dict",
        lambda: {
            "providers": {
                "fishaudio_tts": {
                    "type": "fishaudio",
                    "api_key": "mock-key",
                    "base_url": "http://127.0.0.1:9999/v1",
                }
            }
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        await config_api.verify_provider_credentials("fishaudio_tts")

    assert exc_info.value.status_code == 400
    assert "port 8788" in str(exc_info.value.detail)
    assert calls == []


@pytest.mark.asyncio
async def test_fish_audio_provider_connection_test_verifies_real_synthesis(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return type("Response", (), {"status_code": 200, "content": b"pcm-audio"})()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    response = await config_api.test_provider_connection(
        config_api.ProviderTestRequest(
            name="fishaudio_tts",
            config={
                "type": "fishaudio",
                "capabilities": ["tts"],
                "api_key": "test-secret",
                "base_url": "https://api.fish.audio/v1",
                "model": "s2.1-pro-free",
                "reference_id": "voice-123",
            },
        )
    )

    assert response == {"success": True, "message": "Fish Audio synthesis verified"}
    assert calls[0][0] == "https://api.fish.audio/v1/tts"
    assert calls[0][1]["headers"]["model"] == "s2.1-pro-free"
    assert calls[0][1]["json"]["reference_id"] == "voice-123"


@pytest.mark.asyncio
async def test_fish_audio_provider_connection_test_reports_payment_required(monkeypatch):
    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, **_kwargs):
            return type("Response", (), {"status_code": 402, "content": b""})()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    response = await config_api.test_provider_connection(
        config_api.ProviderTestRequest(
            name="fishaudio_tts",
            config={
                "type": "fishaudio",
                "api_key": "test-secret",
                "base_url": "https://api.fish.audio/v1",
                "model": "s2.1-pro",
                "reference_id": "voice-123",
            },
        )
    )

    assert response["success"] is False
    assert "402" in response["message"]
    assert "account credit" in response["message"]


@pytest.mark.asyncio
async def test_fish_audio_provider_connection_test_uses_fixed_mock_endpoint(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return type("Response", (), {"status_code": 200, "content": b"pcm"})()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    response = await config_api.test_provider_connection(
        config_api.ProviderTestRequest(
            name="fishaudio_tts",
            config={
                "type": "fishaudio",
                "api_key": "mock-key",
                "base_url": "http://localhost:8788/v1",
                "reference_id": "voice-123",
            },
        )
    )

    assert response["success"] is True
    assert calls[0][0] == "http://127.0.0.1:8788/v1/tts"


@pytest.mark.asyncio
async def test_fish_audio_provider_connection_test_rejects_untrusted_host(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return type("Response", (), {"status_code": 200, "content": b"pcm"})()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    response = await config_api.test_provider_connection(
        config_api.ProviderTestRequest(
            name="fishaudio_tts",
            config={
                "type": "fishaudio",
                "api_key": "test-secret",
                "base_url": "https://untrusted.example/v1",
                "reference_id": "voice-123",
            },
        )
    )

    assert response["success"] is False
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", ["telnyx", "telenyx", "minimax"])
async def test_key_required_provider_rejects_no_auth_sentinel(monkeypatch, provider_type):
    provider_key = f"{provider_type}_llm"
    monkeypatch.setattr(
        config_api,
        "_read_merged_config_dict",
        lambda: {
            "providers": {
                provider_key: {
                    "type": provider_type,
                    "api_key": "not-needed",
                }
            }
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        await config_api.verify_provider_credentials(provider_key)

    assert exc_info.value.status_code == 400
    assert "not configured" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", ["openai", "telnyx", "minimax"])
async def test_modular_verification_rejects_unallowlisted_configured_host(
    monkeypatch, provider_type
):
    provider_key = f"{provider_type}_llm"
    calls = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return type("Response", (), {"status_code": 200})()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        config_api,
        "_read_merged_config_dict",
        lambda: {
            "providers": {
                provider_key: {
                    "type": provider_type,
                    "api_key": "provider-secret",
                    "chat_base_url": "https://untrusted.example/v1",
                }
            }
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        await config_api.verify_provider_credentials(provider_key)

    assert exc_info.value.status_code == 400
    assert "not allowlisted" in str(exc_info.value.detail).lower()
    assert calls == []
