import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.config import (
    WebSocketMediaConfig,
    media_websocket_capability_reason,
    supports_media_websocket,
    validate_production_config,
)
from src.config_apply import classify_config_change


def test_websocket_media_defaults_are_json_and_profile_driven():
    config = WebSocketMediaConfig()

    assert config.connection_mode == "asterisk_outbound"
    assert config.format_policy == "profile"
    assert config.fallback_format == "ulaw"
    assert config.control_format == "json"
    assert config.max_input_queue_frames == 32
    assert config.max_message_bytes == 65500
    assert config.auth.password_env == "ASTERISK_MEDIA_WS_PASSWORD"


def test_websocket_media_rejects_unknown_and_raw_secret_fields():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WebSocketMediaConfig.model_validate({"unexpected": True})
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WebSocketMediaConfig.model_validate({"auth": {"password": "do-not-store-me"}})
    with pytest.raises(ValidationError, match="connection_name"):
        WebSocketMediaConfig.model_validate({"connection_name": "aava_media\npassword = leak"})


def test_websocket_media_requires_auth_on_non_loopback_listener():
    with pytest.raises(ValidationError, match="auth.required"):
        WebSocketMediaConfig.model_validate(
            {"bind_host": "0.0.0.0", "auth": {"required": False}}
        )


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "192.0.2.10", "media.example.test"])
def test_production_websocket_validation_completes_and_checks_later_fields(monkeypatch, host):
    monkeypatch.setenv("ASTERISK_MEDIA_WS_PASSWORD", "test-only-password")
    config = SimpleNamespace(
        audio_transport="websocket",
        websocket_media=WebSocketMediaConfig(bind_host=host, advertise_host=host),
        # Later validation must still run; a caught ipaddress scope error used
        # to skip this check on every selected WebSocket deployment.
        audiosocket=SimpleNamespace(port=70000),
    )
    errors, warnings = validate_production_config(config)
    assert "AudioSocket port 70000 out of valid range (1024-65535)" in errors
    assert not any("Validation check failed" in warning for warning in warnings)
    assert any("without TLS" in warning for warning in warnings) == (
        host not in {"127.0.0.1", "::1", "localhost"}
    )


def test_production_websocket_validation_requires_engine_secret(monkeypatch):
    monkeypatch.delenv("ASTERISK_MEDIA_WS_PASSWORD", raising=False)
    errors, warnings = validate_production_config(SimpleNamespace(
        audio_transport="websocket", websocket_media=WebSocketMediaConfig(),
    ))
    assert any("ASTERISK_MEDIA_WS_PASSWORD" in error for error in errors)
    assert not any("Validation check failed" in warning for warning in warnings)


def test_production_externalmedia_validation_preserves_hostname_allowlist_check():
    errors, warnings = validate_production_config(SimpleNamespace(
        audio_transport="externalmedia",
        asterisk=SimpleNamespace(host="pbx.example.test"),
        external_media=SimpleNamespace(allowed_remote_hosts=[]),
    ))
    assert any("allowed_remote_hosts is required" in error for error in errors)
    assert not any("Validation check failed" in warning for warning in warnings)


@pytest.mark.parametrize(
    ("version", "supported"),
    [
        ("Asterisk 20.17.0", False),
        ("Asterisk 20.18.0", True),
        ("Asterisk 21.12.0", False),
        ("Asterisk 22.8.0", True),
        ("Asterisk 23.2.0", True),
        ("Asterisk 24.0.0", False),
        ("Asterisk 24.0.0 build 20.18.0", False),
        (None, False),
    ],
)
def test_websocket_json_version_gate_fails_closed(version, supported):
    assert supports_media_websocket(version) is supported
    if not supported:
        assert "requires" in media_websocket_capability_reason(version).lower() or "unavailable" in media_websocket_capability_reason(version).lower()


def test_websocket_change_is_restart_only():
    decision = classify_config_change(
        {"audio_transport": "externalmedia"},
        {"audio_transport": "websocket"},
    )

    assert decision.restart_required is True
    assert decision.recommended_apply_method == "restart"


def test_config_capability_check_does_not_import_websocket_listener_stack():
    """Admin UI uses websockets 12, which lacks ``websockets.asyncio``."""
    root = Path(__file__).resolve().parents[1]
    script = """
import importlib.abc
import sys

class BlockWebSocketAsyncio(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'websockets.asyncio' or fullname.startswith('websockets.asyncio.'):
            raise ImportError('websockets.asyncio intentionally unavailable')
        return None

sys.meta_path.insert(0, BlockWebSocketAsyncio())
from src.config import supports_media_websocket
assert supports_media_websocket('Asterisk 20.18.0') is True
assert supports_media_websocket('Asterisk 20.17.0') is False
assert not any(name.startswith('src.audio.transports') for name in sys.modules)
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
