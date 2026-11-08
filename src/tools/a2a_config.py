"""Shared YAML validation for optional A2A tools."""

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

A2A_SECRETS_ROOT = "/app/project/secrets/providers"

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _validate_base_url(url: str) -> str:
    value = str(url or "").strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("A2A agent_url is invalid") from exc
    if (
        len(value) > 2048
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "A2A agent_url must be an HTTP(S) base URL without credentials, query or fragment"
        )
    return value


@dataclass(frozen=True)
class A2AAgentConfig:
    name: str
    agent_url: str
    description: str = "Delegate a request to a remote A2A agent."
    bearer_token_env: str | None = None
    bearer_token_file: str | None = None
    enabled: bool = True
    timeout_ms: int = 15000
    poll_interval_ms: int = 250
    max_input_chars: int = 4000
    max_response_chars: int = 1000
    error_message: str = "The remote agent could not complete that request."

    def __post_init__(self) -> None:
        """Validate execution bounds and credential references before registration."""
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", str(self.name)):
            raise ValueError("A2A tool name is invalid")
        object.__setattr__(self, "agent_url", _validate_base_url(self.agent_url))
        if self.bearer_token_env is not None and (
            not isinstance(self.bearer_token_env, str)
            or not _ENV_NAME.fullmatch(self.bearer_token_env)
        ):
            raise ValueError(
                "A2A bearer_token_env must be an environment variable name"
            )
        if not isinstance(self.description, str) or not isinstance(
            self.error_message, str
        ):
            raise TypeError("A2A description and error_message must be text")
        if type(self.enabled) is not bool:
            raise ValueError("A2A enabled must be a boolean")
        if self.bearer_token_file is not None and not isinstance(self.bearer_token_file, str):
            raise ValueError("A2A bearer_token_file must be a managed credential path")
        if self.bearer_token_file:
            from pathlib import Path

            from src.config.provider_instances import safe_secret_path

            path = Path(self.bearer_token_file)
            if not re.fullmatch(r"a2a-tool-[a-f0-9]{32}", path.parent.name):
                raise ValueError("A2A token must reference a managed A2A credential")
            expected = safe_secret_path(path.parent.name, "api-key", root=A2A_SECRETS_ROOT)
            if str(path) != expected:
                raise ValueError("A2A token must reference a managed A2A credential")
            if self.bearer_token_env:
                raise ValueError("Choose one A2A credential source")
        if self.bearer_token_env or self.bearer_token_file:
            parsed = urlsplit(self.agent_url)
            if parsed.scheme != "https" and parsed.hostname not in {
                "localhost",
                "127.0.0.1",
                "::1",
            }:
                raise ValueError(
                    "A2A Bearer authentication requires HTTPS except on loopback"
                )
        if type(self.timeout_ms) is not int or not 1000 <= self.timeout_ms <= 120000:
            raise ValueError("A2A timeout_ms must be between 1000 and 120000")
        if (
            type(self.poll_interval_ms) is not int
            or not 50 <= self.poll_interval_ms <= 5000
        ):
            raise ValueError("A2A poll_interval_ms must be between 50 and 5000")
        if (
            type(self.max_input_chars) is not int
            or not 1 <= self.max_input_chars <= 16000
        ):
            raise ValueError("A2A max_input_chars must be between 1 and 16000")
        if (
            type(self.max_response_chars) is not int
            or not 1 <= self.max_response_chars <= 8000
        ):
            raise ValueError("A2A max_response_chars must be between 1 and 8000")


def parse_a2a_agent_config(name: str, config: dict[str, Any]) -> A2AAgentConfig:
    """Validate A2A settings without importing the runtime SDK."""
    allowed = {
        "agent_url",
        "description",
        "bearer_token_env",
        "bearer_token_file",
        "enabled",
        "timeout_ms",
        "poll_interval_ms",
        "max_input_chars",
        "max_response_chars",
        "error_message",
    }
    values = {key: value for key, value in config.items() if key in allowed}
    if "agent_url" not in values:
        raise ValueError("A2A agent_url is required")
    return A2AAgentConfig(name=name, **values)
