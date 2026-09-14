import io, json, zipfile
from datetime import datetime, timedelta, timezone
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api import support as support_api
from agents_store import AgentsStore
from src.core.call_history import CallRecord

def _client(tmp_path, monkeypatch, seed):
    db = str(tmp_path / "agents.db")
    store = AgentsStore(db_path=db)
    seed(store)
    monkeypatch.setattr(support_api, "_store", lambda: AgentsStore(db_path=db))
    app = FastAPI()
    app.include_router(support_api.router, prefix="/api")
    return TestClient(app)

def test_bundle_redacts_sensitive_columns(tmp_path, monkeypatch):
    def seed(s):
        s.create(display_name="Maria - Vendas", provider="openai_realtime",
                 prompt="secret system prompt", greeting="hello there", notes="internal note",
                 role_label="Sales Lead", extension="801")
    client = _client(tmp_path, monkeypatch, seed)
    resp = client.get("/api/support-bundle")
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    agents = json.loads(z.read("agents_redacted.json"))
    a = agents[0]
    assert a["slug"] == "maria_vendas"               # operational fields kept
    assert a["display_name"].startswith("[name len=")
    assert a["prompt"].startswith("[prompt len=")
    assert "sha=" in a["prompt"]
    assert a["greeting"].startswith("[greeting len=")
    assert a["role_label"].startswith("[role len=")
    assert a["extension"].startswith("[ext len=")
    # raw sensitive text must NOT appear anywhere
    assert "secret system prompt" not in json.dumps(agents)
    assert "internal note" not in json.dumps(agents)
    assert "Sales Lead" not in json.dumps(agents)
    assert "801" not in a["extension"]
    assert "801" not in a["display_name"]
    assert "801" not in a["prompt"]
    assert "801" not in a["greeting"]
    assert "801" not in a["role_label"]
    assert "801" not in a["notes"]

def test_bundle_never_contains_env(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, lambda s: s.create(display_name="A", provider="x", prompt="p"))
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/support-bundle").content))
    assert ".env" not in z.namelist()

def test_bundle_handles_malformed_extra_json(tmp_path, monkeypatch):
    def seed(s):
        s.create(display_name="X", provider="x", prompt="p", extra_json="not-json")
    client = _client(tmp_path, monkeypatch, seed)
    resp = client.get("/api/support-bundle")
    assert resp.status_code == 200
    agents = json.loads(zipfile.ZipFile(io.BytesIO(resp.content)).read("agents_redacted.json"))
    assert agents[0]["extra_json"] == ["<unparseable>"]


def test_tools_json_structure_only(tmp_path, monkeypatch):
    def seed(s):
        s.create(display_name="T", provider="x", prompt="p",
                 tools_json='[{"name": "lookup", "url": "https://secret.example.com/api?key=abc"}]')
    client = _client(tmp_path, monkeypatch, seed)
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/support-bundle").content))
    agents = json.loads(z.read("agents_redacted.json"))
    assert "https://" not in json.dumps(agents)       # URLs scrubbed (structure only)


def test_tools_json_string_list_does_not_leak_urls(tmp_path, monkeypatch):
    """tools_json that is a JSON array of raw URL strings must never expose those URLs."""
    secret_url = "https://secret.example.com/x?key=abc"
    def seed(s):
        s.create(display_name="U", provider="x", prompt="p",
                 tools_json=json.dumps([secret_url]))
    client = _client(tmp_path, monkeypatch, seed)
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/support-bundle").content))
    bundle_text = z.read("agents_redacted.json").decode()
    assert "https://" not in bundle_text
    assert "secret.example.com" not in bundle_text


class _CallStore:
    def __init__(self, record):
        self.record = record

    async def get_by_call_id(self, call_id):
        return self.record if call_id == self.record.call_id else None


def _call_bundle_client(monkeypatch, log_text):
    now = datetime.now(timezone.utc)
    call = CallRecord(
        call_id="1789247215.144",
        caller_number="+1 (316) 461-9284",
        caller_name="Sensitive Caller",
        called_number="18005551212",
        start_time=now,
        end_time=now + timedelta(seconds=20),
        duration_seconds=20,
        provider_name="google_live",
        context_name="support",
        routing_method="ai_agent",
        conversation_history=[
            {"role": "user", "content": "Call me on 316-461-9284 or me@example.com"},
            {"role": "assistant", "content": "I can help."},
        ],
        tool_calls=[{
            "name": "lookup_customer",
            "params": {"phone_number": "3164619284", "authorization": "Bearer secret-token"},
            "result": {"status": "found", "email": "me@example.com"},
        }],
        pre_call_tool_calls=[{"name": "pre_lookup", "status": "success"}],
        post_call_tool_calls=[{"name": "notify", "status": "success"}],
        diagnostics_snapshot={
            "schema_version": 1,
            "configured": {"provider": {"model": "gemini-live", "api_key": "must-not-leak"}},
            "resolved": {
                "provider_name": "google_live",
                "audio_profile": "telephony_ulaw_8k",
                "transport_profile": {"wire_encoding": "ulaw", "wire_sample_rate": 8000},
            },
        },
    )
    monkeypatch.setattr(support_api, "_call_store", lambda: _CallStore(call))

    async def fake_read_window(container, since, until, **_kwargs):
        return log_text, {
            "container": container,
            "container_id": "container123",
            "available": True,
            "truncated": False,
            "original_bytes": len(log_text.encode()),
            "exported_bytes": len(log_text.encode()),
        }

    monkeypatch.setattr(support_api, "_read_window", fake_read_window)
    app = FastAPI()
    app.include_router(support_api.router, prefix="/api")
    return TestClient(app)


def test_call_bundle_captures_all_phases_settings_and_mixed_log_levels(monkeypatch):
    log_text = "\n".join([
        '{"timestamp":"2026-09-13T12:00:00Z","level":"info","event":"RCA_CALL_START","call_id":"1789247215.144","component":"src.engine","caller_number":"3164619284","caller_name":"Sensitive Caller","api_key":"json-secret"}',
        '2026-09-13T12:00:01Z [WARNING] provider jitter [src.providers.google_live] call_id=1789247215.144 api_key=raw-secret',
        '2026-09-13T12:00:20Z [INFO] RCA_CALL_END [src.engine] call_id=1789247215.144',
    ])
    client = _call_bundle_client(monkeypatch, log_text)

    preview = client.get("/api/support/call-preview", params={"call_id": "1789247215.144"})
    assert preview.status_code == 200
    assert preview.json()["log_evidence"]["format"] == "mixed"
    assert set(preview.json()["log_evidence"]["observed_levels"]) == {"info", "warning"}
    assert preview.json()["settings"]["resolved"]["audio_profile"] == "telephony_ulaw_8k"

    response = client.post("/api/support/call-bundle", json={"call_id": "1789247215.144"})
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = set(archive.namelist())
    assert {
        "manifest.json", "call/summary.json", "call/effective_settings.json",
        "call/conversation.json", "call/tool_executions.json", "logs/ai_engine.log",
    }.issubset(names)
    assert not any("recording" in name.lower() for name in names)
    tools = json.loads(archive.read("call/tool_executions.json"))
    assert [entry["name"] for entry in tools["pre_call"]] == ["pre_lookup"]
    assert [entry["name"] for entry in tools["in_call"]] == ["lookup_customer"]
    assert [entry["name"] for entry in tools["post_call"]] == ["notify"]

    contents = b"\n".join(archive.read(name) for name in names).decode("utf-8", errors="replace")
    assert "1789247215.144" in contents
    for forbidden in (
        "3164619284", "316-461-9284", "461-9284", "18005551212",
        "Sensitive Caller", "me@example.com", "secret-token", "raw-secret", "json-secret", "must-not-leak",
    ):
        assert forbidden not in contents


@pytest.mark.asyncio
async def test_call_evidence_bounds_debug_heavy_correlation_window(monkeypatch):
    client = _call_bundle_client(
        monkeypatch,
        '2026-09-13T12:00:00Z [INFO] started [src.engine] call_id=1789247215.144',
    )
    limits = []

    async def bounded_read_window(container, since, until, *, limit):
        limits.append(limit)
        return "", {
            "container": container,
            "container_id": "container123",
            "available": True,
            "truncated": False,
            "original_bytes": 0,
            "exported_bytes": 0,
        }

    monkeypatch.setattr(support_api, "_read_window", bounded_read_window)

    response = client.get(
        "/api/support/call-preview", params={"call_id": "1789247215.144"}
    )

    assert response.status_code == 200
    assert limits == [support_api.CORRELATION_MAX_BYTES]


def test_call_bundle_options_can_omit_optional_sources_and_content(monkeypatch):
    client = _call_bundle_client(
        monkeypatch,
        '2026-09-13T12:00:00Z [INFO] started [src.engine] call_id=1789247215.144',
    )
    response = client.post("/api/support/call-bundle", json={
        "call_id": "1789247215.144",
        "include_local_ai_server": False,
        "include_admin_ui": False,
        "include_transcript": False,
        "include_tools": False,
        "include_settings": False,
    })
    archive = zipfile.ZipFile(io.BytesIO(response.content))

    assert "logs/local_ai_server.log" not in archive.namelist()
    assert "logs/admin_ui.log" not in archive.namelist()
    assert "call/conversation.json" not in archive.namelist()
    assert "call/tool_executions.json" not in archive.namelist()
    assert "call/effective_settings.json" not in archive.namelist()


def test_sanitize_text_redacts_phone_but_preserves_asterisk_call_id():
    value = "call_id=1789247215.144 caller phone +1 (316) 461-9284"
    sanitized = support_api.sanitize_text(value)

    assert "1789247215.144" in sanitized
    assert "316" not in sanitized
    assert "[PHONE_REDACTED]" in sanitized


def test_sanitize_text_preserves_diagnostic_timestamps_calendar_slots_and_ipv4():
    value = (
        "2026-09-13 18:24:10 event at 2026-09-14T09:00:00-07:00 "
        "slots 2026-09-14 09:00, 2026-09-14 09:30 "
        "archive 20260914-012606 host 192.168.10.149 "
        "caller +1 (316) 461-9284"
    )

    sanitized = support_api.sanitize_text(value)

    assert "2026-09-13 18:24:10" in sanitized
    assert "2026-09-14T09:00:00-07:00" in sanitized
    assert "2026-09-14 09:00" in sanitized
    assert "2026-09-14 09:30" in sanitized
    assert "20260914-012606" in sanitized
    assert "192.168.10.149" in sanitized
    assert "316" not in sanitized
    assert sanitized.count("[PHONE_REDACTED]") == 1


def test_sanitize_text_redacts_private_context_repr_but_keeps_diagnostics():
    value = (
        r"ContextConfig(prompt='Private caller flow with don\'t wording', "
        r'greeting="Hello caller", provider="google_live", profile="telephony_ulaw_8k")'
    )

    sanitized = support_api.sanitize_text(value)

    assert "Private caller flow" not in sanitized
    assert "Hello caller" not in sanitized
    assert "prompt=[REDACTED]" in sanitized
    assert "greeting=[REDACTED]" in sanitized
    assert 'provider="google_live"' in sanitized
    assert 'profile="telephony_ulaw_8k"' in sanitized


def test_system_bundle_is_bounded_and_sanitizes_config_and_logs(monkeypatch):
    from api import config as config_api
    from api import system as system_api

    raw_log = (
        "caller_number=3164619284 api_key=raw-secret "
        "email=me@example.com call_id=1789247215.144\n"
    ).encode()
    monkeypatch.setattr(
        support_api,
        "_read_container_logs_sync",
        lambda container, **_kwargs: (raw_log, f"{container}-id", container),
    )
    monkeypatch.setattr(config_api, "_read_merged_config_dict", lambda: {
        "providers": {"main": {"api_key_file": "/secret/key", "model": "safe-model"}},
        "contexts": {"support": {"prompt": "private business instructions"}},
    })
    monkeypatch.setattr(system_api, "get_basic_system_info", lambda: {"version": "test"})
    app = FastAPI()
    app.include_router(support_api.router, prefix="/api")
    client = TestClient(app)

    response = client.post("/api/support/system-bundle", json={
        "hours": 1,
        "include_ai_engine": True,
        "include_local_ai_server": False,
        "include_admin_ui": False,
        "include_config": True,
    })

    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["window_hours"] == 1
    assert manifest["sources"]["local_ai_server"] == {"selected": False}
    config = json.loads(archive.read("config/current_config.sanitized.json"))
    assert config["providers"]["main"]["api_key_file"] == "[REDACTED]"
    assert config["contexts"]["support"]["prompt"] == "[REDACTED]"
    all_text = b"\n".join(archive.read(name) for name in archive.namelist()).decode(errors="replace")
    assert "1789247215.144" in all_text
    assert "3164619284" not in all_text
    assert "raw-secret" not in all_text
    assert "me@example.com" not in all_text
    assert "private business instructions" not in all_text
