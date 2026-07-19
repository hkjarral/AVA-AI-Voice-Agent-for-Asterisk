from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import vicidial as vicidial_api
from src.core.vicidial_store import VicidialStore
from src.integrations.vicidial import VicidialApiResult
from src.core.call_history import CallRecord


def _connection_payload():
    return {
        "name": "Lab VICIdial",
        "base_url": "http://192.168.10.100",
        "vicidial_host": "192.168.10.100",
        "username_env": "VICIDIAL_API_USER",
        "password_env": "VICIDIAL_API_PASS",
        "verify_ssl": False,
        "topology": "lan_vpn",
        "timezone": "America/Phoenix",
    }


def _mapping_payload(connection_id):
    return {
        "connection_id": connection_id,
        "name": "AVA Remote Agent",
        "direction": "both",
        "campaign_id": "AVATEST",
        "user_start": "9001",
        "number_of_lines": 1,
        "conf_exten": "8371",
        "static_agent_user": "9001",
        "ai_agent": "demo_deepgram",
        "trusted_context": "from-vicidial-ra",
        "trusted_endpoint": "vicidial-ra",
        "dispositions": {"sale": "SALE", "callback": "CALLBK"},
        "statuses": {},
        "destinations": {
            "sales": {
                "type": "ingroup",
                "target": "SALESLINE",
                "description": "Sales",
            }
        },
    }


def _client(monkeypatch, tmp_path):
    store = VicidialStore(str(tmp_path / "vicidial.db"))
    monkeypatch.setattr(vicidial_api, "_store", lambda: store)
    monkeypatch.setattr(
        vicidial_api,
        "_active_agent",
        lambda slug: {"slug": slug, "is_active": True} if slug == "demo_deepgram" else None,
    )
    app = FastAPI()
    app.include_router(vicidial_api.router, prefix="/api")
    return TestClient(app), store


def test_vicidial_crud_and_guidance_are_typed(monkeypatch, tmp_path):
    client, _store = _client(monkeypatch, tmp_path)
    connection = client.post(
        "/api/outbound/vicidial/connections", json=_connection_payload()
    )
    assert connection.status_code == 200
    connection_id = connection.json()["id"]

    mapping = client.post(
        "/api/outbound/vicidial/mappings",
        json=_mapping_payload(connection_id),
    )
    assert mapping.status_code == 200
    mapping_id = mapping.json()["id"]

    guidance = client.get(
        f"/api/outbound/vicidial/mappings/{mapping_id}/guidance"
    )
    assert guidance.status_code == 200
    body = guidance.json()
    assert any("On-Hook Agent=N" in step for step in body["vicidial_steps"])
    assert any("Allow Inbound and Blended=Y" in step for step in body["vicidial_steps"])
    assert any("Drop Call Seconds" in step for step in body["vicidial_steps"])
    assert any("agent_status" in step for step in body["vicidial_steps"])
    assert any("share Phone/conf_exten 8371" in step for step in body["vicidial_steps"])
    assert "exten => 8371,1" in body["dialplan"]
    assert "Set(__AAVA_CALL_OWNER=vicidial)" in body["dialplan"]
    assert "Set(__AI_AGENT=demo_deepgram)" in body["dialplan"]
    assert body["freepbx_trunk"]["secret"].startswith("Use the VICIdial Phone")


def test_mapping_verification_preserves_directional_live_call_evidence(
    monkeypatch, tmp_path
):
    client, store = _client(monkeypatch, tmp_path)
    connection = store.save_connection(_connection_payload(), "connection-1")
    store.save_mapping(_mapping_payload(connection["id"]), "mapping-1")
    store.record_real_call_verification(
        mapping_id="mapping-1",
        direction="outbound",
        external_call_id="M4050908070000012345",
        status="AIHU",
        operation="hangup",
    )

    class _Client:
        def __init__(self, _connection):
            pass

        async def verify_connection(self):
            return {
                "ready": True,
                "authentication": {
                    "success": True,
                    "rows": [{"campaign_id": "AVATEST"}],
                },
                "agent_visibility": {
                    "success": True,
                    "rows": [{"user": "9001", "status": "READY"}],
                },
            }

        async def agent_status(self, agent_user):
            return VicidialApiResult(
                True,
                "agent_status",
                "ok",
                data={"user": agent_user, "status": "READY"},
            )

    monkeypatch.setattr(vicidial_api, "VicidialApiClient", _Client)
    response = client.post(
        "/api/outbound/vicidial/mappings/mapping-1/verify"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["configuration_ready"] is True
    assert body["remote_agent"]["api_users"] == ["9001"]
    assert body["remote_agent"]["unverified_users"] == []
    assert body["ready"] is False
    assert body["real_call"]["required_directions"] == ["inbound", "outbound"]
    assert body["real_calls"]["outbound"]["verified"] is True
    assert "inbound" not in body["real_calls"]


def test_mapping_verification_rejects_live_row_without_vicidial_user(
    monkeypatch, tmp_path
):
    client, store = _client(monkeypatch, tmp_path)
    connection = store.save_connection(_connection_payload(), "connection-1")
    payload = _mapping_payload(connection["id"])
    payload.update({"number_of_lines": 2, "static_agent_user": None})
    store.save_mapping(payload, "mapping-1")

    class _Client:
        def __init__(self, _connection):
            pass

        async def verify_connection(self):
            return {
                "ready": True,
                "authentication": {
                    "success": True,
                    "rows": [{"campaign_id": "AVATEST"}],
                },
                "agent_visibility": {
                    "success": True,
                    "rows": [
                        {"user": "9001", "status": "READY"},
                        {"user": "9002", "status": "READY"},
                    ],
                },
            }

        async def agent_status(self, agent_user):
            if agent_user == "9002":
                return VicidialApiResult(
                    False,
                    "agent_status",
                    "ERROR: AGENT NOT FOUND",
                    error_code="api_error",
                )
            return VicidialApiResult(
                True,
                "agent_status",
                "ok",
                data={"user": agent_user, "status": "READY"},
            )

    monkeypatch.setattr(vicidial_api, "VicidialApiClient", _Client)
    response = client.post("/api/outbound/vicidial/mappings/mapping-1/verify")

    assert response.status_code == 200
    body = response.json()
    assert body["configuration_ready"] is False
    assert body["remote_agent"]["api_users"] == ["9001"]
    assert body["remote_agent"]["unverified_users"] == ["9002"]
    assert body["remote_agent"]["status_checks"]["9002"] == {
        "success": False,
        "status": None,
        "error_code": "api_error",
    }


def test_activity_summarizes_only_aava_handled_vicidial_calls(monkeypatch, tmp_path):
    client, store = _client(monkeypatch, tmp_path)
    connection = store.save_connection(_connection_payload(), "connection-1")
    store.save_mapping(_mapping_payload(connection["id"]), "mapping-1")
    now = datetime.now(timezone.utc)
    records = [
        CallRecord(
            id="record-1",
            call_id="asterisk-1",
            caller_number="13164619284",
            start_time=now,
            end_time=now + timedelta(seconds=42),
            duration_seconds=42,
            context_name="demo_deepgram",
            outcome="completed",
            external_platform="vicidial",
            external_direction="outbound",
            external_disposition="AIHU",
            external_metadata={
                "mapping_id": "mapping-1",
                "mapping_name": "AVA Remote Agent",
                "session": {"agent_user": "9001"},
                "disposition_label": "ai_hangup",
                "finalized": True,
            },
        ),
        CallRecord(
            id="record-2",
            call_id="asterisk-2",
            caller_number="13165550123",
            start_time=now - timedelta(minutes=2),
            end_time=now - timedelta(minutes=1, seconds=50),
            duration_seconds=10,
            context_name="demo_deepgram",
            outcome="error",
            external_platform="vicidial",
            external_direction="outbound",
            external_metadata={
                "mapping_id": "mapping-1",
                "mapping_name": "AVA Remote Agent",
                "session": {"agent_user": "9001"},
                "requested_disposition": "AIFAIL",
                "disposition_label": "ai_failure",
                "finalized": False,
            },
        ),
    ]

    class _History:
        async def list_external_activity(self, platform, start_date, end_date=None):
            assert platform == "vicidial"
            assert start_date < end_date
            return records

    monkeypatch.setattr(vicidial_api, "_call_history_store", lambda: _History())

    response = client.get(
        "/api/outbound/vicidial/activity?range=7d&mapping_id=mapping-1&limit=1"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {
        "handled": 2,
        "finalized": 1,
        "needs_attention": 1,
        "average_duration_seconds": 26.0,
        "last_call_at": now.isoformat(),
    }
    assert body["dispositions"] == [{"status": "AIHU", "count": 1}]
    assert body["by_mapping"][0]["handled"] == 2
    assert len(body["recent_calls"]) == 1
    assert body["recent_calls"][0]["masked_number"] == "•••9284"
    assert body["recent_calls"][0]["remote_agent"] == "9001"
    assert body["recent_calls"][0]["disposition_confirmed"] is True
    assert "never reached" in body["scope_note"]

    assert client.get("/api/outbound/vicidial/activity?range=90d").status_code == 422
