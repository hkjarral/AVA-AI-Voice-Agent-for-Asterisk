"""Completion policies use the same validation on structured, YAML and Agent APIs."""
import copy
import json
from pathlib import Path
import pytest
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from api import config as config_api, tools as tools_api, agents as agents_api

@pytest.fixture
def config_client(monkeypatch):
    state={"config":yaml.safe_load(Path(config_api.settings.CONFIG_PATH).read_text()),"writes":0}
    monkeypatch.setattr(tools_api,"_load_cfg",lambda:copy.deepcopy(state["config"]))
    async def persist(payload,previous=None):
        config_api._validate_ai_agent_config(yaml.safe_dump(payload))
        state["config"]=copy.deepcopy(payload)
        state["writes"]+=1
        return {"status":"success","apply_required":True,"recommended_apply_method":"hot_reload"}
    monkeypatch.setattr(tools_api,"_persist_cfg",persist)
    monkeypatch.setattr(config_api,"_write_local_config",lambda text:pytest.fail("Invalid YAML must not be written"))
    app=FastAPI()
    app.include_router(config_api.router,prefix="/api/config")
    app.include_router(tools_api.router,prefix="/api/tools")
    return TestClient(app),state

@pytest.mark.parametrize("policy",[{"destination_keys":"survey"},{"default_next_step":"invented"},{"unknown":True}])
@pytest.mark.parametrize("surface",["tools","yaml"])
def test_bad_completion_rejected_without_writing(config_client,policy,surface):
    client,state=config_client
    if surface=="tools":
        response=client.patch("/api/tools/builtin/hangup_call",json={"completion":policy})
    else:
        payload=copy.deepcopy(state["config"])
        payload.setdefault("tools",{}).setdefault("hangup_call",{})["completion"]=policy
        response=client.post("/api/config/yaml",json={"content":yaml.safe_dump(payload)})
    assert response.status_code==400,response.text
    assert state["writes"]==0


def test_completion_structured_round_trip(config_client):
    client,state=config_client
    route={"type":"dialplan","target":"s","dialplan_context":"ava-survey","priority":2,"requires_opt_in":True}
    assert client.patch("/api/tools/builtin/transfer",json={"destinations":{"survey":route}}).status_code==200
    policy={"destination_keys":["survey"],"default_next_step":"hangup"}
    assert client.patch("/api/tools/builtin/hangup_call",json={"completion":policy}).status_code==200
    assert client.get("/api/tools/builtin/hangup_call").json()["config"]["completion"]==policy
    assert client.patch("/api/tools/builtin/hangup_call",json={"completion":{**policy,"default_next_step":"survey"}}).status_code==400

@pytest.mark.parametrize("policy",[{"default_next_step":"survey"},{"destination_keys":["private"]}])
def test_agent_cannot_select_opt_in_default_or_broaden_routes(monkeypatch,policy):
    from services import onboarding
    config={"tools":{"transfer":{"destinations":{"survey":{"type":"dialplan","target":"s","dialplan_context":"survey","requires_opt_in":True}}},"hangup_call":{"completion":{"destination_keys":["survey"]}}}}
    monkeypatch.setattr(onboarding,"effective_config",lambda:config)
    with pytest.raises(HTTPException) as exc:
        agents_api._validate_agent_completion({"tool_configs_json":json.dumps({"hangup_call":policy})})
    assert exc.value.status_code==422


def test_unchanged_stale_agent_completion_round_trips_without_reapproving_it():
    policy=json.dumps({"hangup_call":{"destination_keys":["deleted"],"default_next_step":"deleted"}})
    agents_api._validate_agent_completion({"tool_configs_json":policy},{"tool_configs_json":policy})


def test_live_agent_override_rejects_dialplan_route(config_client):
    client, state = config_client
    response = client.patch('/api/tools/builtin/transfer', json={
        'destinations': {'menu': {'type': 'dialplan', 'target': 's', 'dialplan_context': 'ivr'}},
        'live_agent_destination_key': 'menu'})
    assert response.status_code == 400
    assert state['writes'] == 0


@pytest.mark.parametrize('journal', [False, True])
def test_history_masks_invalid_requested_destination_even_in_old_journal(monkeypatch, journal):
    from types import SimpleNamespace
    from api.calls import _record_to_response
    from src.core.call_history import CallRecord
    from src.core.handoff_journal import HandoffJournal
    action = {'id': 'test', 'conversation_id': 'conversation', 'requested_next_step': 'private@example.com', 'next_step': 'hangup', 'result': 'unavailable'}
    record = CallRecord(call_id='channel:conversation', diagnostics_snapshot={'conversation_id': 'conversation', 'conversation_completion': action})
    monkeypatch.setattr(HandoffJournal, 'get', lambda *args: action if journal else None)
    response = _record_to_response(record)
    assert response.diagnostics_snapshot['conversation_completion']['requested_next_step'] == '<invalid>'


@pytest.mark.parametrize('route,expected', [(None, '<invalid>'), ({'type': 'dialplan', 'target': 's', 'dialplan_context': 'survey'}, 'survey')])
def test_ineligible_request_preserves_only_validated_destination_snapshot(monkeypatch, route, expected):
    from api.calls import _record_to_response
    from src.core.call_history import CallRecord
    action = {'requested_next_step': 'survey', 'next_step': 'hangup', 'result': 'ineligible'}
    if route is not None:
        action['route'] = route
    response = _record_to_response(CallRecord(call_id='channel:conversation', diagnostics_snapshot={'conversation_completion': action}))
    assert response.diagnostics_snapshot['conversation_completion']['requested_next_step'] == expected
