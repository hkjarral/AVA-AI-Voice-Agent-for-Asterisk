"""MED-A2: the reconcile / 'Import YAML changes' endpoint must run the SAME
validation create/patch use (a context with neither provider nor pipeline can't be
silently created as an unroutable agent) and import the FULL field set
(prompt, greeting, voice, audio_profile, tools, extra/extension/role_label/notes,
and the per-context email overrides), not just prompt.

Reuses the TestClient/store-override fixture pattern from test_agents_email_api_v710.py;
also overrides the YAML/contexts-dir path helpers so reconcile reads a temp ai-agent.yaml.
"""
import json

import pytest
import yaml as _yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import agents as agents_api
from agents_store import AgentsStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = str(tmp_path / "agents.db")
    yaml_path = tmp_path / "ai-agent.yaml"
    contexts_dir = tmp_path / "contexts"
    contexts_dir.mkdir()
    monkeypatch.setattr(agents_api, "_store", lambda: AgentsStore(db_path=db))
    monkeypatch.setattr(agents_api, "_yaml_path", lambda: str(yaml_path))
    monkeypatch.setattr(agents_api, "_contexts_dir", lambda: str(contexts_dir))
    app = FastAPI()
    app.include_router(agents_api.router, prefix="/api")
    client = TestClient(app)
    return client, yaml_path


def _write_yaml(yaml_path, contexts):
    yaml_path.write_text(_yaml.safe_dump({"contexts": contexts}))


def test_reconcile_imports_full_field_set(env):
    client, yaml_path = env
    _write_yaml(yaml_path, {
        "Support": {
            "provider": "openai",
            "prompt": "you are support",
            "greeting": "hello there",
            "voice": "nova",
            "profile": "telephony",
            "tools": {"send_email_summary": {"enabled": True}},
            "extension": "200",
            "role_label": "Support Agent",
            "notes": "imported note",
            "email_recipient": "ops@example.com",
            "email_from": "ava@example.com",
            "email_enabled": True,
            "background_music": "lobby.wav",  # arbitrary extra -> extra_json
        },
    })

    r = client.post("/api/agents-migration/reconcile")
    assert r.status_code == 200, r.text

    got = client.get("/api/agents/support").json()
    assert got["prompt"] == "you are support"
    assert got["greeting"] == "hello there"
    assert got["voice"] == "nova"
    assert got["provider"] == "openai"
    assert got["audio_profile"] == "telephony"
    assert json.loads(got["tools_json"]) == {"send_email_summary": {"enabled": True}}
    assert got["extension"] == "200"
    assert got["role_label"] == "Support Agent"
    assert got["notes"] == "imported note"
    assert got["email_recipient"] == "ops@example.com"
    assert got["email_from"] == "ava@example.com"
    assert got["email_enabled"] is True
    # arbitrary keys land in extra_json, not dropped
    assert json.loads(got["extra_json"])["background_music"] == "lobby.wav"


def test_reconcile_rejects_unroutable_context(env):
    # A context with neither provider nor a pipeline fails _engine_ok, exactly like
    # create/patch. Reconcile must NOT silently create it as an unroutable agent.
    client, yaml_path = env
    _write_yaml(yaml_path, {
        "Broken": {"prompt": "no provider, no pipeline"},
    })

    r = client.post("/api/agents-migration/reconcile")

    # the unroutable context must not have produced an agent row
    assert client.get("/api/agents/broken").status_code == 404
    # ...and it must be reported in the skipped list with a reason
    assert ["broken", "no provider or pipeline"] in r.json()["skipped"]


def test_reconcile_records_promptless_skip(env):
    # A context with no prompt is skipped, and (MED-A2 follow-up) must be recorded
    # in the skipped list with a reason, matching how unroutable contexts are.
    client, yaml_path = env
    _write_yaml(yaml_path, {
        "NoPrompt": {"provider": "openai"},
    })

    r = client.post("/api/agents-migration/reconcile")
    assert r.status_code == 200, r.text

    assert client.get("/api/agents/noprompt").status_code == 404
    assert ["noprompt", "missing prompt"] in r.json()["skipped"]


def test_reconcile_imports_pipeline_context(env):
    # A context with no monolithic provider but a pipeline passes _engine_ok and
    # imports (pipeline is an arbitrary key -> extra_json).
    client, yaml_path = env
    _write_yaml(yaml_path, {
        "Piped": {"prompt": "p", "pipeline": "deepgram_openai_elevenlabs"},
    })

    r = client.post("/api/agents-migration/reconcile")
    assert r.status_code == 200, r.text

    got = client.get("/api/agents/piped")
    assert got.status_code == 200, got.text
    assert json.loads(got.json()["extra_json"])["pipeline"] == "deepgram_openai_elevenlabs"


def test_reconcile_imports_context_defined_only_in_local_override(env):
    client, yaml_path = env
    _write_yaml(yaml_path, {
        "Base": {"provider": "local", "prompt": "base"},
    })
    local_path = yaml_path.with_name("ai-agent.local.yaml")
    _write_yaml(local_path, {
        "Base": {"greeting": "overridden greeting"},
        "Local Full": {
            "provider": "local",
            "prompt": "fully local project agent",
            "tools": ["hangup_call"],
        },
    })

    response = client.post("/api/agents-migration/reconcile")

    assert response.status_code == 200, response.text
    assert ["added", "local_full"] in response.json()["changed"]
    assert client.get("/api/agents/base").json()["greeting"] == "overridden greeting"
    local_agent = client.get("/api/agents/local_full").json()
    assert local_agent["prompt"] == "fully local project agent"
    assert json.loads(local_agent["tools_json"]) == ["hangup_call"]


def test_reconcile_skips_invalid_email(env):
    # CodeRabbit Minor: reconcile writes email_recipient/email_from straight to the
    # store, bypassing the AgentIn/AgentPatch email validation (H3/MED-E1). An invalid
    # address must be skipped (recorded in `skipped`), not persisted.
    client, yaml_path = env
    _write_yaml(yaml_path, {
        "Support": {
            "provider": "openai",
            "prompt": "you are support",
            "email_recipient": "not-an-email",
        },
    })

    r = client.post("/api/agents-migration/reconcile")
    assert r.status_code == 200, r.text

    # not persisted...
    assert client.get("/api/agents/support").status_code == 404
    # ...and reported as skipped with an invalid-email reason
    assert ["support", "invalid email"] in r.json()["skipped"]


def test_reconcile_imports_valid_email(env):
    # Sanity: a valid email still imports (the validator does not over-reject).
    client, yaml_path = env
    _write_yaml(yaml_path, {
        "Support": {
            "provider": "openai",
            "prompt": "you are support",
            "email_recipient": "ops@example.com",
            "email_from": "ava@example.com",
        },
    })

    r = client.post("/api/agents-migration/reconcile")
    assert r.status_code == 200, r.text
    got = client.get("/api/agents/support").json()
    assert got["email_recipient"] == "ops@example.com"
    assert got["email_from"] == "ava@example.com"


def test_reconcile_updates_existing_full_fields(env):
    # Idempotency / merge: an existing agent whose YAML changed gets its full field
    # set updated, not just prompt, and is not destructively recreated (slug stable).
    client, yaml_path = env
    _write_yaml(yaml_path, {
        "Support": {"provider": "openai", "prompt": "v1", "voice": "nova"},
    })
    client.post("/api/agents-migration/reconcile")
    first = client.get("/api/agents/support").json()

    _write_yaml(yaml_path, {
        "Support": {"provider": "openai", "prompt": "v2", "voice": "shimmer",
                    "greeting": "new greeting"},
    })
    r = client.post("/api/agents-migration/reconcile")
    assert r.status_code == 200, r.text

    got = client.get("/api/agents/support").json()
    assert got["id"] == first["id"]          # same row, not recreated
    assert got["prompt"] == "v2"
    assert got["voice"] == "shimmer"
    assert got["greeting"] == "new greeting"


def test_reconcile_colliding_contexts_update_both_agents(env):
    # Finding 2: two context names that slugify to the same value ("Sales-East" and
    # "sales_east" -> "sales_east") must map to TWO distinct agents, exactly like the
    # one-time migration's CRIT-3 disambiguation. Reconcile must NOT overwrite one with
    # the other, and must not orphan the disambiguated "sales_east_2" row.
    client, yaml_path = env
    _write_yaml(yaml_path, {
        "Sales-East": {"provider": "openai", "prompt": "east one", "voice": "nova"},
        "sales_east": {"provider": "openai", "prompt": "east two", "voice": "shimmer"},
    })

    r = client.post("/api/agents-migration/reconcile")
    assert r.status_code == 200, r.text

    agents = {a["display_name"]: a for a in client.get("/api/agents").json()}
    # both contexts produced their own agent (distinct slugs), neither overwritten
    assert "Sales-East" in agents and "sales_east" in agents
    assert agents["Sales-East"]["slug"] != agents["sales_east"]["slug"]
    assert {agents["Sales-East"]["slug"], agents["sales_east"]["slug"]} == \
        {"sales_east", "sales_east_2"}
    # each agent kept its OWN context's fields (no cross-contamination)
    assert agents["Sales-East"]["prompt"] == "east one"
    assert agents["Sales-East"]["voice"] == "nova"
    assert agents["sales_east"]["prompt"] == "east two"
    assert agents["sales_east"]["voice"] == "shimmer"

    # idempotent re-run: same two rows updated in place, no third row, no overwrite
    first_ids = {agents["Sales-East"]["id"], agents["sales_east"]["id"]}
    r2 = client.post("/api/agents-migration/reconcile")
    assert r2.status_code == 200, r2.text
    after = {a["display_name"]: a for a in client.get("/api/agents").json()}
    assert len(after) == 2
    assert {after["Sales-East"]["id"], after["sales_east"]["id"]} == first_ids
    assert after["Sales-East"]["prompt"] == "east one"
    assert after["sales_east"]["prompt"] == "east two"
