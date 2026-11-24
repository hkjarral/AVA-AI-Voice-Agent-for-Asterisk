"""Smoke reports expose protocol identifiers without provider/private messages."""
import logging
import runpy
from pathlib import Path

import pytest
import structlog


@pytest.fixture(scope="module")
def safe_error_details():
    disabled = logging.root.manager.disable
    configuration = structlog.get_config()
    try:
        module = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/provider_catalog_smoke.py"))
        yield module["safe_error_details"]
    finally:
        logging.disable(disabled)
        structlog.configure(**configuration)


def test_embedded_http_error_keeps_only_safe_identifiers(safe_error_details):
    error = RuntimeError('OpenAI STT request failed (status 400): {"error":{"message":"private prompt and secret","code":"unsupported_parameter","param":"temperature"}}')
    assert safe_error_details(error) == {
        "error_type": "RuntimeError", "http_status": 400,
        "provider_error_code": "unsupported_parameter", "provider_error_param": "temperature",
    }


@pytest.mark.parametrize("value", ["secret value", "https://private.example/key", "sk-xxx\\nsecret", "x" * 81])
def test_non_identifier_values_are_not_emitted(safe_error_details, value):
    error = RuntimeError('{"code":"' + value + '","param":"' + value + '"}')
    assert safe_error_details(error) == {"error_type": "RuntimeError"}


def test_typed_http_status_is_supported_without_printing_message(safe_error_details):
    error = RuntimeError("private operator information")
    error.status = 403
    assert safe_error_details(error) == {"error_type": "RuntimeError", "http_status": 403}
