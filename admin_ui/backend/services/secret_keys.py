"""Presence-only projection for configuration environment variables."""
from src.core.credential_fields import connection_password_spans, is_secret_key

SECRET_SET = {"set": True}


def is_secret_env_value(key, value, secret_names=()):
    return key in secret_names or is_secret_key(key) or bool(connection_password_spans(value))


def redact_mapping(values: dict[str, str], secret_names: set[str] | frozenset[str] = frozenset()) -> dict[str, str | dict[str, bool]]:
    return {key: {"set": bool(value)} if is_secret_env_value(key, value, secret_names) else value for key, value in values.items()}
