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
