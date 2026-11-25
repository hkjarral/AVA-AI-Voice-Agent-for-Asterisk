from secrets import token_hex

import pytest

from services.secret_keys import SECRET_SET, is_secret_key, redact_mapping


@pytest.mark.parametrize("name", [
    "OPENAI_API_KEY", "DEEPGRAM_API_KEY", "JWT_SECRET", "ASTERISK_ARI_PASSWORD",
    "ASTERISK_MEDIA_WS_PASSWORD", "TELNYX_API_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS_JSON",
    "SMTP_PASSWORD", "PUSHOVER_USER_KEY", "SLACK_WEBHOOK_URL", "some_custom_secret",
    "unix_passwd", "custom_credential_file", "vicidial_api_pass",
])
def test_secret_names(name):
    assert is_secret_key(name)


@pytest.mark.parametrize("name", [
    "ASTERISK_HOST", "ASTERISK_ARI_PORT", "ASTERISK_ARI_USERNAME", "LOG_LEVEL",
    "LOG_FORMAT", "TZ", "DOCKER_GID", "KEYBOARD_LAYOUT", "LOCAL_LLM_MAX_TOKENS",
])
def test_plain_names(name):
    assert not is_secret_key(name)


def test_redact_mapping():
    canary = token_hex()
    values = {"OPENAI_API_KEY": canary, "LOG_LEVEL": "info", "SMTP_PASSWORD": ""}
    assert SECRET_SET == {"set": True}
    result = redact_mapping(values)
    assert result == {"OPENAI_API_KEY": {"set": True}, "LOG_LEVEL": "info", "SMTP_PASSWORD": {"set": False}}
    assert canary not in repr(result)
    assert values["OPENAI_API_KEY"] == canary
