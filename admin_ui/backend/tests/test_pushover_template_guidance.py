from unittest.mock import MagicMock
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from api import pushover, tools


def client():
    app = FastAPI()
    app.include_router(pushover.router, prefix='/api/pushover')
    return TestClient(app)


@pytest.mark.parametrize('phase', ['pre_call', 'in_call', 'post_call'])
def test_guidance_is_synthetic_and_ignores_credential_references(monkeypatch, phase):
    external = MagicMock(side_effect=AssertionError('No credentials or network in template review'))
    monkeypatch.setattr(pushover, 'read_key', external)
    monkeypatch.setattr(pushover, 'get_sender', external)
    monkeypatch.setattr(pushover.config_api, '_write_provider_secret', external)
    result = client().post('/api/pushover/templates/validate', json={'phase': phase, 'config': {
        'title': '{agent}', 'body': '{caller_number}', 'token_file': '/nonexistent/secret',
        'recipients': {'sample': {'key_file': '/nonexistent/recipient'}},
    }})
    assert result.status_code == 200
    assert result.json()['valid']
    assert result.json()['preview']['message'] == '+15550102030'
    assert '/nonexistent' not in result.text
    external.assert_not_called()


@pytest.mark.parametrize('phase', ['pre_call', 'in_call', 'post_call'])
def test_save_returns_same_safe_field_error_as_review(phase):
    cfg = {'kind': 'pushover', 'body': '{transcript_json}'}
    preview = client().post('/api/pushover/templates/validate', json={'phase': phase, 'config': cfg}).json()
    with pytest.raises(HTTPException) as error:
        tools._build_tool_doc(cfg, phase)
    assert error.value.status_code == 422
    assert error.value.detail == preview['issues'][0]
    assert error.value.detail['field'] == 'body'
    assert error.value.detail['variable'] == 'transcript_json'


def test_malformed_settings_are_reported_without_echoing_data():
    result = client().post('/api/pushover/templates/validate', json={'phase': 'bad', 'config': {'body': 'private-text'}})
    assert result.status_code == 422
    assert 'private-text' not in result.text
    assert client().post('/api/pushover/templates/validate', json={'phase': 'in_call', 'config': []}).status_code == 422
