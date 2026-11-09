"""First-install contract: preflight -> save -> restart/resume -> routable Agent."""
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

import settings
from api import wizard, agents
from agents_store import AgentsStore
from services import onboarding
from services.dialplan import agent_dialplan


@pytest.fixture
def install(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    base = config_dir / "ai-agent.yaml"
    base.write_text(yaml.safe_dump({
        "contexts": {}, "asterisk": {"app_name": "base-app"},
        "audio_transport": "audiosocket", "audiosocket": {"host": "127.0.0.1", "port": 8090},
        "default_provider": "local_hybrid", "providers": {}, "pipelines": {},
    }))
    env = tmp_path / ".env"
    env.write_text("ASTERISK_HOST=127.0.0.1\nASTERISK_ARI_USERNAME=asterisk\nASTERISK_ARI_PASSWORD=asterisk\n")
    monkeypatch.setattr(settings, "PROJECT_ROOT", str(tmp_path))
    for module in (settings, wizard):
        monkeypatch.setattr(module, "CONFIG_PATH", str(base))
        monkeypatch.setattr(module, "LOCAL_CONFIG_PATH", str(config_dir / "ai-agent.local.yaml"))
        monkeypatch.setattr(module, "ENV_PATH", str(env))
    monkeypatch.setenv("AGENTS_DB_PATH", str(tmp_path / "data/operator/agents.db"))
    app = FastAPI()
    app.include_router(wizard.router, prefix="/wizard")
    app.include_router(agents.router)
    return TestClient(app), tmp_path


def setup(**changes):
    return {
        "provider": "openai_realtime", "openai_key": "synthetic-key",
        "asterisk_host": "127.0.0.1", "asterisk_username": "test",
        "asterisk_password": "synthetic-password", "asterisk_app": "custom-stasis",
        "greeting": "Hello test caller", "ai_name": "Test", "ai_role": "Assistant",
        **changes,
    }


def test_preflight_template_does_not_skip_wizard_or_create_database(install):
    client, root = install
    assert client.get("/wizard/status").json() == {"configured": False, "stage": "not_started", "resume_required": False}
    assert not (root / "data/operator/agents.db").exists()


@pytest.mark.parametrize("provider,llm,target", [
    ("openai_realtime", None, None), ("deepgram", None, None), ("local", None, None),
    ("local_hybrid", "groq", "local_hybrid_groq"),
    ("local_hybrid", "ollama", "local_hybrid_ollama"),
    ("local_hybrid", "openai", "local_hybrid"),
])
def test_save_resume_and_generated_dialplan_resolve_created_agent(install, provider, llm, target):
    client, root = install
    response = client.post("/wizard/save", json=setup(provider=provider, hybrid_llm_provider=llm, groq_key="synthetic", deepgram_key="synthetic"))
    assert response.status_code == 200, response.text
    handoff = response.json()["handoff"]
    assert handoff["agent_slug"] == "receptionist"
    assert "Set(AI_AGENT=receptionist)" in handoff["dialplan"]
    assert "Stasis(custom-stasis)" in handoff["dialplan"]
    assert "AI_PROVIDER" not in handoff["dialplan"]
    with AgentsStore() as store:
        row = store.get_by_slug(handoff["agent_slug"])
        assert row["is_active"] and row["is_default"]
        assert json.loads(row["extra_json"] or "{}").get("pipeline") == target
    # The actual engine reader must resolve the emitted selector.
    from src.core.agent_store import EngineAgentStore
    assert EngineAgentStore().resolve(handoff["agent_slug"]) is not None
    resumed = client.get("/wizard/load-config").json()
    assert resumed["onboarding"]["resume_required"]
    assert resumed["asterisk_app"] == "custom-stasis"
    assert resumed["greeting"] == "Hello test caller"
    assert resumed["provider"] == provider
    if llm:
        assert resumed["hybrid_llm_provider"] == llm
    assert "synthetic" not in onboarding.state_path().read_text()
    assert yaml.safe_load((root / "config/ai-agent.yaml").read_text())["asterisk"]["app_name"] == "base-app"
    # Retry is idempotent; does not create extra Agents or change their identity.
    assert client.post("/wizard/save", json=setup(provider=provider, hybrid_llm_provider=llm, groq_key="synthetic", deepgram_key="synthetic")).status_code == 200
    with AgentsStore() as store:
        assert len(store.list_all()) == 3
    text = client.get("/agents/receptionist/dialplan").json()["dialplan"]
    assert "Stasis(custom-stasis)" in text and "AI_PROVIDER" not in text and "XXXX" not in text


def test_existing_and_explicit_manual_installations_are_not_forced_through_setup(install):
    client, root = install
    with AgentsStore() as store:
        store.create(slug="existing", display_name="Existing", provider="local", prompt="Keep")
    assert client.get("/wizard/status").json()["stage"] == "existing"
    before = (root / ".env").read_bytes()
    assert client.post("/wizard/skip").status_code == 200
    assert client.get("/wizard/status").json()["stage"] == "manual"
    assert (root / ".env").read_bytes() == before


@pytest.mark.parametrize("transport", ["audiosocket", "externalmedia"])
def test_remote_topology_saves_listener_and_return_audio_address(install, transport):
    client, root = install
    config = setup(deployment_topology="remote", audio_transport=transport, asterisk_host="192.0.2.10", media_advertise_host="192.0.2.20")
    assert client.post("/wizard/save", json=config).status_code == 200
    result = yaml.safe_load((root / "config/ai-agent.local.yaml").read_text())
    media = result["audiosocket" if transport == "audiosocket" else "external_media"]
    assert media["advertise_host"] == "192.0.2.20"
    assert media["host" if transport == "audiosocket" else "rtp_host"] == "0.0.0.0"
    if transport == "externalmedia":
        assert media["allowed_remote_hosts"] == ["192.0.2.10"]
    loaded = client.get("/wizard/load-config").json()
    assert loaded["deployment_topology"] == "remote" and loaded["media_advertise_host"] == "192.0.2.20"


@pytest.mark.parametrize("address", ["", "127.0.0.1", "0.0.0.0", "invalid", "192.0.2.1\nINJECT=yes"])
def test_invalid_remote_address_is_rejected_before_any_write(install, address):
    client, root = install
    before = (root / ".env").read_bytes()
    response = client.post("/wizard/save", json=setup(deployment_topology="remote", audio_transport="audiosocket", asterisk_host="192.0.2.10", media_advertise_host=address))
    assert response.status_code == 400
    assert (root / ".env").read_bytes() == before
    assert not onboarding.state_path().exists()


def test_failed_save_is_not_marked_completed(install, monkeypatch):
    client, _ = install
    from starter_agents import seed_starter_agents
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic DB failure")
    monkeypatch.setattr("starter_agents.seed_starter_agents", fail)
    assert client.post("/wizard/save", json=setup()).status_code == 500
    assert client.get("/wizard/status").json()["configured"] is False
    monkeypatch.setattr("starter_agents.seed_starter_agents", seed_starter_agents)
    assert client.post("/wizard/save", json=setup()).status_code == 200


def test_malformed_override_cannot_be_loaded_or_overwritten(install):
    client, root = install
    local = root / "config/ai-agent.local.yaml"
    local.write_text("asterisk: [unterminated")
    before = (root / ".env").read_bytes()
    assert client.get("/wizard/load-config").status_code == 503
    assert client.post("/wizard/save", json=setup()).status_code == 500
    assert local.read_text() == "asterisk: [unterminated"
    assert (root / ".env").read_bytes() == before


def test_existing_agent_is_preserved_and_selected_for_handoff(install):
    client, _ = install
    with AgentsStore() as store:
        store.create(slug="operator_agent", display_name="Operator", provider="deepgram", prompt="Keep me", greeting="Keep greeting")
        store.set_default("operator_agent")
    result = client.post("/wizard/save", json=setup()).json()
    assert result["handoff"]["agent_slug"] == "operator_agent"
    assert "AI_PROVIDER" not in result["handoff"]["dialplan"]
    with AgentsStore() as store:
        assert store.get_by_slug("operator_agent")["provider"] == "deepgram"
        assert len(store.list_all()) == 1
    assert client.get("/wizard/load-config").json()["greeting"] == "Keep greeting"


def test_remote_to_same_host_clears_stale_advertised_address(install):
    client, root = install
    config = setup(deployment_topology="remote", audio_transport="externalmedia", asterisk_host="192.0.2.10", media_advertise_host="192.0.2.20")
    assert client.post("/wizard/save", json=config).status_code == 200
    config.update(deployment_topology="same_host", asterisk_host="127.0.0.1")
    assert client.post("/wizard/save", json=config).status_code == 200
    assert client.get("/wizard/load-config").json()["media_advertise_host"] == "127.0.0.1"
    local = yaml.safe_load((root / "config/ai-agent.local.yaml").read_text())
    assert local["external_media"]["rtp_host"] == "127.0.0.1"
    assert local["external_media"]["allowed_remote_hosts"] == ["127.0.0.1"]


def test_complete_requires_operator_confirmation_and_live_readiness(install, monkeypatch):
    client, _ = install
    client.post("/wizard/save", json=setup())
    monkeypatch.setattr(wizard, "_probe_engine_readiness", AsyncMock(return_value={"ready": False}))
    confirmation = dict(greeting_heard=True, two_way_audio=True, clean_hangup=True, history_checked=True)
    assert client.post("/wizard/complete", json=confirmation).status_code == 409
    monkeypatch.setattr(wizard, "_probe_engine_readiness", AsyncMock(return_value={"ready": True}))
    assert client.post("/wizard/complete", json={**confirmation, "two_way_audio": False}).status_code == 400
    assert client.post("/wizard/complete", json=confirmation).status_code == 200
    assert client.get("/wizard/status").json()["stage"] == "verified"
    with AgentsStore() as store:
        for row in store.list_all():
            store.delete(row["slug"])
    assert client.get("/wizard/readiness").json()["ready_for_test"] is False


@pytest.mark.parametrize("slug,app", [("x\nHangup()", "app"), ("sales", "app,other"), ("sales", "")])
def test_dialplan_rejects_injected_identifiers(slug, app):
    with pytest.raises(ValueError):
        agent_dialplan(slug, app)


@pytest.mark.parametrize("source", ["malformed_yaml", "list_yaml", "unreadable_db"])
def test_existing_install_repair_pages_remain_accessible(install, source):
    client, root = install
    if source == "unreadable_db":
        path = root / "data/operator/agents.db"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"not a sqlite database")
    else:
        (root / "config/ai-agent.yaml").write_text("asterisk: [" if source == "malformed_yaml" else "- item\n")
    response = client.get("/wizard/status")
    assert response.status_code == 200
    assert response.json() == {"configured": True, "stage": "existing", "resume_required": False}


def test_invalid_app_readiness_and_completion_are_actionable(install):
    client, root = install
    assert client.post("/wizard/save", json=setup()).status_code == 200
    (root / "config/ai-agent.local.yaml").write_text("asterisk:\n  app_name: invalid,app\n")
    before = onboarding.state_path().read_bytes()
    for response in (
        client.get("/wizard/readiness"),
        client.post("/wizard/complete", json=dict(greeting_heard=True, two_way_audio=True, clean_hangup=True, history_checked=True)),
    ):
        assert response.status_code == 400
        assert "Stasis application" in response.json()["detail"]
    assert onboarding.state_path().read_bytes() == before


@pytest.mark.parametrize("stage", ["verified", "configuration_saved"])
def test_rejected_local_model_does_not_reset_saved_progress(install, stage):
    client, root = install
    onboarding.write_state(stage)
    before = onboarding.state_path().read_bytes()
    env_before = (root / ".env").read_bytes()
    response = client.post("/wizard/save", json=setup(provider="local", local_llm_model="custom_gguf_url", local_llm_custom_filename="../bad.gguf"))
    assert response.status_code == 400, response.text
    assert onboarding.state_path().read_bytes() == before
    assert (root / ".env").read_bytes() == env_before
    assert not list(root.glob(".env.bak.*"))


def test_invalid_existing_agent_is_rejected_before_save(install):
    import os
    import sqlite3
    client, root = install
    assert client.post("/wizard/save", json=setup()).status_code == 200
    with sqlite3.connect(os.environ["AGENTS_DB_PATH"]) as db:
        db.execute("UPDATE agents SET slug='bad,slug' WHERE is_default=1")
    before = onboarding.state_path().read_bytes()
    env_before = (root / ".env").read_bytes()
    response = client.post("/wizard/save", json=setup(ai_name="Changed"))
    assert response.status_code == 400
    assert "Agent" in response.json()["detail"]
    assert onboarding.state_path().read_bytes() == before
    assert (root / ".env").read_bytes() == env_before


def test_engine_start_failure_does_not_expose_exception(install, monkeypatch):
    client, _ = install
    monkeypatch.setattr(wizard, "setup_media_paths", lambda: {})
    def fail(*args):
        raise RuntimeError("synthetic-secret-in-exception")
    monkeypatch.setattr("shutil.which", fail)
    response = client.post("/wizard/start-engine")
    assert response.status_code == 200
    assert response.json()["success"] is False
    assert "container logs" in response.json()["message"]
    assert "synthetic-secret" not in response.text
