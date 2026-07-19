from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import vicidial as vicidial_api
from src.core.vicidial_store import VicidialStore


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

    monkeypatch.setattr(vicidial_api, "VicidialApiClient", _Client)
    response = client.post(
        "/api/outbound/vicidial/mappings/mapping-1/verify"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["configuration_ready"] is True
    assert body["ready"] is False
    assert body["real_call"]["required_directions"] == ["inbound", "outbound"]
    assert body["real_calls"]["outbound"]["verified"] is True
    assert "inbound" not in body["real_calls"]
