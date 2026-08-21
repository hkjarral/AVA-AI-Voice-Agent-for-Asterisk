import json
import os

import pytest

from api import config as config_api
from api import tools as tools_api


@pytest.mark.asyncio
async def test_llm_options_are_secret_safe_and_report_readiness(monkeypatch):
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
    assert "disabled_llm" not in by_key
    assert "super-secret" not in json.dumps(response)


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
