"""A2A setup, credential isolation and cross-store deletion policy."""
import json
import sqlite3
from pathlib import Path

import pytest
from api import config, tools
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from services.a2a_tools import agent_usage, protect_assigned_tools, validate_document

from src.tools import a2a_config


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'PROVIDER_SECRETS_ROOT', str(tmp_path))
    monkeypatch.setattr(a2a_config, 'A2A_SECRETS_ROOT', str(tmp_path))
    monkeypatch.setattr(tools, '_load_cfg', lambda: {'in_call_tools': {}})
    app = FastAPI()
    app.include_router(tools.router, prefix='/tools')
    return TestClient(app)


def test_credential_staging_is_private_immutable_and_not_echoed(client):
    token = 'fixture-secret-opaque'
    first = client.post('/tools/a2a/credentials', json={'token': token})
    assert first.status_code == 200, first.text
    assert token not in first.text
    path = Path(first.json()['bearer_token_file'])
    assert path.read_text() == token
    assert path.stat().st_mode & 0o777 == 0o600
    second = client.post('/tools/a2a/credentials', json={'token': 'replacement'})
    assert second.json() != first.json()
    assert path.read_text() == token  # existing call snapshots keep their credential
    doc = {'kind': 'a2a_agent', 'agent_url': 'https://agent.example', **first.json()}
    assert client.post('/tools/a2a/validate', json={'name': 'delegate', 'config': doc}).status_code == 200


@pytest.mark.parametrize('payload', [{'token': 'private\nsecret'}, {'token': 'private', 'extra': True}, {'token': []}])
def test_invalid_credentials_do_not_echo_input(client, payload):
    response = client.post('/tools/a2a/credentials', json=payload)
    assert response.status_code == 422
    assert 'private' not in response.text


def test_credentials_bound_body_size(client):
    assert client.post('/tools/a2a/credentials', content='x' * 17000).status_code == 413


def test_validation_is_shared_and_preserves_legacy_yaml():
    doc = {'kind': 'a2a_agent', 'agent_url': 'https://agent.example', 'bearer_token_env': 'OLD_TOKEN', 'custom': {'keep': 1}}
    assert validate_document('delegate', doc, doc) == doc
    with pytest.raises(HTTPException, match='') as exc:
        validate_document('delegate', doc)
    assert exc.value.status_code == 422
    for changes in ({'is_global': True}, {'phase': 'post_call'}, {'token': 'secret'}, {'timeout_ms': 0}, {'agent_url': 'file:///etc/passwd'}, {'bearer_token_file': '/etc/passwd'}):
        with pytest.raises(HTTPException):
            validate_document('delegate', {**doc, **changes}, doc)


@pytest.mark.parametrize('legacy', [False, True])
def test_usage_and_deletion_cover_both_assignment_lists(monkeypatch, tmp_path, legacy):
    db = tmp_path / 'agents.db'
    monkeypatch.setenv('AGENTS_DB_PATH', str(db))
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE agents(slug, display_name, tools_json, extra_json)')
        conn.execute('INSERT INTO agents VALUES(?,?,?,?)', ('sales', 'Sales', json.dumps([] if legacy else ['delegate']), json.dumps({'in_call_http_tools': ['delegate']} if legacy else {})))
    before = {'in_call_tools': {'delegate': {'kind': 'a2a_agent'}}}
    assert agent_usage({'delegate'})['delegate'] == [{'slug': 'sales', 'name': 'Sales'}]
    with pytest.raises(HTTPException) as exc:
        protect_assigned_tools(before, {})
    assert exc.value.status_code == 409 and 'Sales' in exc.value.detail
    protect_assigned_tools(before, before)
    with sqlite3.connect(db) as conn:
        conn.execute('DELETE FROM agents')
    protect_assigned_tools(before, {})


def test_usage_fails_closed_on_corrupt_database(monkeypatch, tmp_path):
    db = tmp_path / 'agents.db'
    db.write_text('broken')
    monkeypatch.setenv('AGENTS_DB_PATH', str(db))
    with pytest.raises(HTTPException) as exc:
        agent_usage({'delegate'})
    assert exc.value.status_code == 503


def test_full_config_persistence_blocks_assigned_tool_removal(monkeypatch, tmp_path):
    import yaml
    desired = yaml.safe_load(Path(config.settings.CONFIG_PATH).read_text())
    original = {**desired, 'in_call_tools': {'delegate': {'kind': 'a2a_agent', 'agent_url': 'https://agent.example'}}}
    monkeypatch.setattr(config, '_read_merged_config_dict', lambda: original)
    monkeypatch.setattr(config, '_assert_in_use_audio_profiles_unchanged', lambda *a, **k: None)
    monkeypatch.setattr('services.a2a_tools.agent_usage', lambda names: {'delegate': [{'slug': 'sales', 'name': 'Sales'}]})
    writes = []
    monkeypatch.setattr(config, '_write_local_config', lambda *a, **k: writes.append(a))
    with pytest.raises(HTTPException) as exc:
        config.persist_config_content(yaml.safe_dump(desired))
    assert exc.value.status_code == 409
    assert not writes
