import json
from urllib.parse import urlsplit
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents_store import AgentsStore
from api import agents
from services.yaml_secrets import mask_config, restore_config


@pytest.fixture
def agent_api(tmp_path, monkeypatch):
    db = tmp_path / 'agents.db'
    monkeypatch.setattr(agents, '_store', lambda: AgentsStore(str(db)))
    monkeypatch.setattr(agents, '_yaml_path', lambda: str(tmp_path/'base.yaml'))
    monkeypatch.setattr(agents, '_contexts_dir', lambda: str(tmp_path/'contexts'))
    app = FastAPI()
    app.include_router(agents.router, prefix='/api')
    return TestClient(app), AgentsStore(str(db))


def create(client, **fields):
    return client.post('/api/agents', json={'display_name':'Agent', 'prompt':'Welcome',
                                           'provider':'local', **fields})


def test_agent_read_write_surfaces_and_editor_echo(agent_api):
    client, store = agent_api
    document = {'tool_overrides': {'help.desk': {'url':'https://one.invalid/fixture-private-url',
                'headers':{'x.test':'fixture-private-header'}, 'query_params':{'q':['fixture-private-query']},
                'body_template':'token=fixture-private-template'}}, 'pipeline':'public-pipeline'}
    mcp={'servers':{'local':{'env':{'path':'fixture-private-env'},'command':'test',
                             'args':['--password','fixture-private-argument']}}}
    created=create(client, extra_json=json.dumps(document), mcp_json=json.dumps(mcp))
    assert created.status_code==201, created.text
    for response in [created,client.get('/api/agents'),client.get('/api/agents/agent'),
                     client.patch('/api/agents/agent',json={'is_active':True}),
                     client.post('/api/agents/agent/default')]:
        assert response.status_code<300 and 'fixture-private' not in response.text
    public=created.json()
    extra=json.loads(public['extra_json'])
    assert extra['pipeline'] == 'public-pipeline'
    origin = urlsplit(extra['tool_overrides']['help.desk']['url'])
    assert (origin.scheme, origin.netloc) == ('https', 'one.invalid')
    extra['language']='en'
    reply=client.patch('/api/agents/agent',json={'extra_json':json.dumps(extra),'mcp_json':public['mcp_json']})
    assert reply.status_code==200 and 'fixture-private' not in reply.text
    raw=store.get_by_slug('agent')
    assert json.loads(raw['extra_json'])=={**document,'language':'en'} and json.loads(raw['mcp_json'])==mcp


def test_foreign_stale_and_unbacked_markers_are_rejected_without_writes(agent_api):
    client,store=agent_api
    secret={'headers':{'X-Authorization':'private-header'}}
    current=create(client,extra_json=json.dumps(secret)).json()
    before=current['extra_json']
    other=create(client,slug='other',extra_json=json.dumps(secret)).json()
    for route, update in [('/api/agents/other',{'extra_json':before}),('/api/agents/agent',{'mcp_json':before})]:
        assert client.patch(route,json=update).status_code==422
    assert create(client,slug='third',extra_json=before).status_code==422
    assert client.patch('/api/agents/agent',json={'extra_json':json.dumps({'headers':{'new':{'set':True}}})}).status_code==422
    replacement=json.dumps({'headers':{'X-Authorization':'new-private-header'}})
    assert client.patch('/api/agents/agent',json={'extra_json':replacement}).status_code==200
    reply=client.patch('/api/agents/agent',json={'extra_json':before,'is_active':False})
    assert reply.status_code==422
    raw=store.get_by_slug('agent')
    assert raw['is_active'] and json.loads(raw['extra_json'])==json.loads(replacement)


@pytest.mark.parametrize('column', ['extra_json','mcp_json'])
def test_opaque_stored_strings_survive_echo_but_require_full_replacement(agent_api,column):
    client,store=agent_api
    bad='broken legacy JSON: fixture-private'
    row=create(client,**{column:bad}).json()
    assert 'fixture-private' not in row[column]
    public=json.loads(row[column])
    assert isinstance(public,dict)
    original=client.patch('/api/agents/agent',json={column:row[column]})
    assert original.status_code==200 and store.get_by_slug('agent')[column]==bad
    reply=client.patch('/api/agents/agent',json={column:json.dumps({**public,'new':'partial'})})
    assert reply.status_code==422 and store.get_by_slug('agent')[column]==bad
    assert client.patch('/api/agents/agent',json={column:'broken replacement'}).status_code==422
    assert store.get_by_slug('agent')[column]==bad
    assert client.patch('/api/agents/agent',json={column:'{"public": 1}'}).status_code==200
    assert json.loads(store.get_by_slug('agent')[column])=={'public':1}
    assert client.patch('/api/agents/agent',json={column:None}).status_code==200
    assert store.get_by_slug('agent')[column] is None


@pytest.mark.parametrize('group,key', [('tool_overrides','url'),('tools','lookup_url')])
def test_nested_tool_urls_and_destination_binding_apply_to_yaml_and_agent_config(group,key):
    raw={'contexts':{'clinic':{group:{'lookup':{key:'https://one.invalid/private-path','headers':{'X-Custom':'private-header'}}}}}}
    public=mask_config(raw,raw=True)
    assert 'private-' not in json.dumps(public)
    assert restore_config(public,raw)==raw
    public['contexts']['clinic'][group]['lookup'][key]='https://two.invalid/new-path'
    with pytest.raises(ValueError): restore_config(public,raw)


def test_mcp_receiver_change_refuses_retained_args_and_env(agent_api):
    client,store=agent_api
    raw={'servers':{'source':{'transport':'stdio','command':'reader','env':{'CUSTOM':'private-value'}}}}
    public=json.loads(create(client,mcp_json=json.dumps(raw)).json()['mcp_json'])
    public['servers']['source']['command']='another'
    assert client.patch('/api/agents/agent',json={'mcp_json':json.dumps(public)}).status_code==422
    assert json.loads(store.get_by_slug('agent')['mcp_json'])==raw


@pytest.mark.parametrize('column', ['tools_json','tool_configs_json','hangup_policy_json'])
def test_historical_invalid_policy_echo_and_complete_replacement(agent_api, column):
    client, store = agent_api
    create(client)
    store.update('agent', **{column:'legacy broken fixture-private-json'})
    public = client.get('/api/agents/agent').json()[column]
    assert 'fixture-private' not in public and isinstance(json.loads(public),dict)
    reply = client.patch('/api/agents/agent',json={column:public,'greeting':'hello'})
    assert reply.status_code==200 and store.get_by_slug('agent')[column]=='legacy broken fixture-private-json'
    replacement={'tools_json':'["transfer"]','tool_configs_json':'{"transfer":{"destination_policy":"none"}}',
                 'hangup_policy_json':'{"strategy":"extend","end_call":["Later"]}'}[column]
    assert client.patch('/api/agents/agent',json={column:replacement}).status_code==200


def test_agent_schema_includes_private_type_but_never_inlines_opaque_bytes(agent_api):
    client,store=agent_api
    create(client)
    for column,value in [('extra_json','["public", "fixture-private-legacy"]'),
                         ('tools_json','{"unexpected":"fixture-private-legacy"}')]:
        store.update('agent',**{column:value})
        public=client.get('/api/agents/agent').json()[column]
        assert 'fixture-private-legacy' not in public
        assert client.patch('/api/agents/agent',json={column:public}).status_code==200
        assert store.get_by_slug('agent')[column]==value


@pytest.mark.parametrize('field', ['prompt','greeting','notes','display_name','role_label','voice'])
def test_agent_scalar_prose_hides_url_password_and_preserves_public_text_on_echo(agent_api,field):
    client,store=agent_api
    text='Ask about login tokens. Inspect redis://user:fixture-secret@cache.invalid/db.'
    reply=create(client,slug='a',**{field:text})
    assert reply.status_code==201 and 'fixture-secret' not in reply.text
    public=reply.json()[field]
    assert 'Ask about login tokens.' in public and 'cache.invalid/db' in public
    patched=client.patch('/api/agents/a',json={field:public})
    assert patched.status_code==200 and 'fixture-secret' not in patched.text
    assert store.get_by_slug('a')[field]==text


def test_unsigned_presence_cannot_retain_another_rows_saved_value(agent_api):
    client,store=agent_api
    raw=json.dumps({'headers':{'X-Custom':'fixture-private'}})
    create(client,extra_json=raw)
    reply=client.patch('/api/agents/agent',json={'extra_json':json.dumps({'headers':{'X-Custom':{'set':True}}})})
    assert reply.status_code==422 and store.get_by_slug('agent')['extra_json']==raw


def test_signed_replacement_does_not_confuse_json_types(agent_api):
    client,store=agent_api
    raw = '{"headers":{"X-Custom":"fixture-private"},"number":true}'
    public = json.loads(create(client,extra_json=raw).json()['extra_json'])
    public['number'] = 1
    assert client.patch('/api/agents/agent',json={'extra_json':json.dumps(public)}).status_code==200
    assert type(json.loads(store.get_by_slug('agent')['extra_json'])['number']) is int


def test_marker_restore_holds_sqlite_writer_lock_until_row_update(agent_api, monkeypatch):
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    client,store=agent_api
    raw=json.dumps({'headers':{'X-Custom':'fixture-secret'}})
    public=create(client,extra_json=raw).json()['extra_json']
    started=Event()
    release=Event()
    restore=agents.restore_agent_fields
    def pause(fields, current=None):
        if current is not None:
            started.set()
            assert release.wait(5)
        return restore(fields, current)
    monkeypatch.setattr(agents,'restore_agent_fields',pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        patch=pool.submit(client.patch,'/api/agents/agent',json={'extra_json':public})
        try:
            assert started.wait(5)
            store.conn.execute('PRAGMA busy_timeout=0')
            with pytest.raises(sqlite3.OperationalError,match='locked'):
                store.conn.execute('BEGIN IMMEDIATE')
        finally:
            release.set()
        assert patch.result(timeout=5).status_code==200
    assert store.get_by_slug('agent')['extra_json']==raw


@pytest.mark.parametrize('legacy', ['{"public": NaN, "label": "fixture-private-old"}',
                                    '{"conflict": 1, "conflict":"fixture-private-old"}'])
def test_legacy_json_the_browser_cannot_represent_is_opaque(agent_api, legacy):
    client,store=agent_api
    public=create(client,extra_json=legacy).json()['extra_json']
    assert 'fixture-private-old' not in public and '__ava_stored_json__' in public
    assert client.patch('/api/agents/agent',json={'extra_json':public}).status_code==200
    assert store.get_by_slug('agent')['extra_json']==legacy


@pytest.mark.parametrize("column", ["extra_json", "mcp_json"])
@pytest.mark.parametrize("representation", ["escaped", "literal", "unmarked"])
def test_invalid_json_patch_preserves_parseable_agent_and_other_fields(agent_api, column, representation):
    client, store = agent_api
    raw = '{ "headers" : { "Authorization" : "fixture-original-credential" } }'
    row = create(client, **{column: raw}).json()
    displayed = row[column]
    edited = {"escaped": displayed[:-1],
              "literal": json.dumps(json.loads(displayed), ensure_ascii=False)[:-1],
              "unmarked": '{"invalid":'}[representation]
    before = store.get_by_slug("agent")
    response = client.patch("/api/agents/agent", json={column: edited, "is_active": False, "prompt": "Changed"})
    assert response.status_code == 422
    assert "fixture-original-credential" not in response.text
    assert store.get_by_slug("agent") == before
    assert before[column].encode() == raw.encode()


@pytest.mark.parametrize("prefix", ["«secret:", r"\u00absecret:", r"\u00AB\u0073ecret:"])
def test_invalid_new_document_with_marker_is_never_persisted(agent_api, prefix):
    client, store = agent_api
    response = create(client, extra_json='{"unfinished":"' + prefix)
    assert response.status_code == 422
    assert store.get_by_slug("agent") is None


def test_renamed_mcp_server_refusal_identifies_position_without_echoing_identifier(agent_api):
    client, store = agent_api
    original = {"servers": {"public": {"command": "first"},
                            "reader": {"command": "second", "env": {"TOKEN": "fixture-credential"}}}}
    row = create(client, mcp_json=json.dumps(original)).json()
    edited = json.loads(row["mcp_json"])
    edited["servers"]["fixture-untrusted-name"] = edited["servers"].pop("reader")
    before = store.get_by_slug("agent")
    response = client.patch("/api/agents/agent", json={"mcp_json": json.dumps(edited)})
    assert response.status_code == 422
    assert "MCP server 2" in response.json()["detail"]
    assert "fixture-" not in response.text
    assert store.get_by_slug("agent") == before
