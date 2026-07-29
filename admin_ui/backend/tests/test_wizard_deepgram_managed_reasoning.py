import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from api.wizard import SetupConfig, _validate_setup_provider_credentials  # noqa: E402


def _setup_config(**overrides):
    values = {
        "provider": "deepgram",
        "asterisk_host": "127.0.0.1",
        "asterisk_username": "asterisk",
        "asterisk_password": "secret",
        "deepgram_key": "deepgram-secret",
        "openai_key": None,
        "greeting": "Hello",
        "ai_name": "Ava",
        "ai_role": "assistant",
    }
    values.update(overrides)
    return SetupConfig(**values)


def test_deepgram_setup_accepts_managed_reasoning_without_openai_key():
    _validate_setup_provider_credentials(_setup_config())


def test_deepgram_setup_still_requires_deepgram_key():
    with pytest.raises(HTTPException) as exc_info:
        _validate_setup_provider_credentials(_setup_config(deepgram_key=None))
    assert exc_info.value.status_code == 400
    assert "Deepgram API Key is required" in exc_info.value.detail
