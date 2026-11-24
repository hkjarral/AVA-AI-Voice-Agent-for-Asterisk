import asyncio
import json
from pathlib import Path

import pytest

from api import config
from services.secret_keys import redact_mapping
from services.yaml_secrets import mask_config, restore_config


def test_env_credential_aliases_and_connection_userinfo():
    source = {name: "fixture-private" for name in ("SMTP_PASS", "DB_PWD", "SERVICE_APIKEY")}
    source.update(REDIS_URL="redis://:fixture-private@cache.invalid/0",
                  MODEL_CATALOG_URL="https://models.invalid/catalog?model=local")
    public = redact_mapping(source)
    assert "fixture-private" not in json.dumps(public)
    assert public["MODEL_CATALOG_URL"] == source["MODEL_CATALOG_URL"]
    assert all(public[k] == {"set":True} for k in source if k != "MODEL_CATALOG_URL")


@pytest.mark.asyncio
async def test_editor_preserves_stored_connection_and_removes_only_on_null(tmp_path, monkeypatch):
    target = tmp_path / ".env"
    original = "REDIS_URL=redis://user:fixture-private@cache.invalid/0\n"
    target.write_text(original)
    monkeypatch.setattr(config.settings,"ENV_PATH",str(target))
    monkeypatch.setattr(config,"_credential_env_references",lambda:set())
    monkeypatch.setattr(config,"_running_container_names",lambda:set())
    masked = await config.get_env_config()
    assert masked == {"REDIS_URL":{"set":True}}
    await config.update_env(masked)
    assert target.read_text() == original
    await config.update_env({"REDIS_URL":None})
    assert "fixture-private" not in target.read_text()


@pytest.mark.parametrize("raw", [True, False])
def test_provider_query_is_one_private_unit_and_retains_the_saved_origin(raw):
    public = "https://provider.invalid/versions"
    desired = {"providers":{"sample":{"base_url": public+"?tenant=fixture-tenant&access=fixture-private"}}}
    masked = mask_config(desired,raw=raw)
    text = json.dumps(masked)
    assert public in text and "fixture-tenant" not in text and "fixture-private" not in text
    assert restore_config(masked,desired) == desired
    masked["providers"]["sample"]["base_url"] = masked["providers"]["sample"]["base_url"].replace("provider.invalid","other.invalid")
    with pytest.raises(ValueError):
        restore_config(masked,desired)


def test_calendar_parser_error_cannot_quote_the_credential_file(tmp_path, monkeypatch):
    from google.oauth2 import service_account

    path = tmp_path / "service-account.json"
    path.write_text('{"private_key":"CANARY-parser"}')
    monkeypatch.setattr(config, "_assert_creds_path_in_allowed_dir", lambda *_: None)

    def parser(filename, **kwargs):
        raise ValueError(Path(filename).read_text())

    monkeypatch.setattr(service_account.Credentials, "from_service_account_file", parser)
    result = config._discover_accessible_calendars(str(path))
    assert result["error_code"] == "invalid_credentials"
    assert "CANARY-parser" not in json.dumps(result)


def test_provider_query_remains_private_with_env_selected_host():
    document = {"providers": {"local": {"base_url": "https://${MODEL_HOST}/v1?tenant=fixture-private"}}}
    masked = mask_config(document, raw=True)
    assert "fixture-private" not in json.dumps(masked)
    assert restore_config(masked, document) == document


@pytest.mark.asyncio
async def test_structural_env_aliases_and_invalid_override_recovery(tmp_path, monkeypatch):
    base = tmp_path / "base.yaml"
    local = tmp_path / "local.yaml"
    env = tmp_path / ".env"
    base.write_text("tools:\n  lookup:\n    headers:\n      Authorization: Bearer ${LOOKUP_VALUE}\n")
    env.write_text("LOOKUP_VALUE=example\nMODEL_LABEL=local\n")
    for name, path in (("CONFIG_PATH", base), ("LOCAL_CONFIG_PATH", local), ("ENV_PATH", env)):
        monkeypatch.setattr(config.settings, name, str(path))
    monkeypatch.setattr(config, "_running_container_names", lambda: set())
    assert await config.get_env_config() == {"LOOKUP_VALUE": {"set": True}, "MODEL_LABEL": "local"}
    local.write_text("broken: [")
    projected = await config.get_env_config()
    assert projected == {"LOOKUP_VALUE": {"set": True}, "MODEL_LABEL": {"set": True}}
    await config.update_env(projected)
    assert env.read_text() == "LOOKUP_VALUE=example\nMODEL_LABEL=local\n"
    await config.update_env({"LOOKUP_VALUE": None, "MODEL_LABEL": "updated"})
    assert "LOOKUP_VALUE" not in env.read_text() and "updated" in env.read_text()


def test_template_basic_header_masks_short_credential_and_preserves_reference():
    text = "Authorization: bAsIc YTpi"
    source = {"tools": {"example": {"args": [text, "Authorization: Basic ${HEADER_VALUE}", "basic information"]}}}
    masked = mask_config(source, raw=True)
    args = masked["tools"]["example"]["args"]
    assert "YTpi" not in args[0]
    assert args[1:] == source["tools"]["example"]["args"][1:]
    assert restore_config(masked, source) == source


def test_editor_can_preserve_rotation_only_at_same_live_destination():
    def provider(host, key):
        return {"providers": {"example": {"base_url": host, "api_key": key}}}
    old = provider("https://first.invalid", "fixture-first")
    edited = mask_config(old, raw=True)
    rotated = provider("https://first.invalid", "fixture-rotated")
    assert restore_config(edited, rotated) == rotated
    with pytest.raises(ValueError):
        restore_config(edited, provider("https://second.invalid", "fixture-second"))


def test_connection_password_uses_final_authority_separator():
    from src.core.credential_fields import mask_business_data

    url = "redis://user:fixture@piece@cache.invalid/path@view"
    assert redact_mapping({"DATA_SOURCE": url}) == {"DATA_SOURCE": {"set": True}}
    assert mask_business_data(url) == "redis://user:<redacted>@cache.invalid/path@view"
