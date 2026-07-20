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
        "pbx_setup_mode": "generated_registration",
        "pbx_technology": "PJSIP",
        "pbx_trunk_name": "Support VICIdial",
        "sip_username": "ava-phone",
        "sip_auth_username": "ava-auth",
        "sip_contact_user": "ava-contact",
        "sip_transport": "tcp",
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
    async def _online_endpoint(technology, resource=None):
        return {
            "ari_connected": True,
            "probe_available": True,
            "technology": str(technology).upper(),
            "resource": resource,
            "found": bool(resource),
            "state": "online" if resource else None,
            "channel_count": 0,
            "ready": True,
            "endpoints": [] if resource else [
                {
                    "technology": str(technology).upper(),
                    "resource": "vicidial-ra",
                    "state": "online",
                    "channel_count": 0,
                }
            ],
        }

    monkeypatch.setattr(vicidial_api, "_asterisk_endpoints", _online_endpoint)
    async def _ari_connected():
        return True

    monkeypatch.setattr(vicidial_api, "_engine_health_ari_connected_compat", _ari_connected)
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
    assert body["freepbx_trunk"]["secret"] == "<VICIDIAL_PHONE_CONF_SECRET>"
    assert body["freepbx_trunk"]["name"] == "Support VICIdial"
    assert body["freepbx_trunk"]["endpoint_id"] == "vicidial-ra"
    assert body["freepbx_trunk"]["username"] == "ava-phone"
    assert body["freepbx_trunk"]["auth_username"] == "ava-auth"
    assert body["freepbx_trunk"]["contact_user"] == "ava-contact"
    assert body["freepbx_trunk"]["transport"] == "TCP"
    assert body["artifact_inputs"] == {
        "setup_mode": "generated_registration",
        "technology": "PJSIP",
        "remote_agent_extension": "8371",
        "trunk_name": "Support VICIdial",
        "endpoint_id": "vicidial-ra",
        "username": "ava-phone",
        "auth_username": "ava-auth",
        "contact_user": "ava-contact",
    }
    assert body["dialplan_install"]["path"] == "/etc/asterisk/extensions_custom.conf"
    assert "fwconsole reload" in body["dialplan_install"]["freepbx_apply"]


def test_mapping_requires_operator_selected_endpoint_and_generated_trunk_name(
    monkeypatch, tmp_path
):
    client, store = _client(monkeypatch, tmp_path)
    connection = store.save_connection(_connection_payload(), "connection-1")

    missing_endpoint = _mapping_payload(connection["id"])
    missing_endpoint["trusted_endpoint"] = ""
    response = client.post("/api/outbound/vicidial/mappings", json=missing_endpoint)
    assert response.status_code == 422
    assert "Exact Asterisk endpoint ID is required" in response.json()["detail"]

    missing_trunk = _mapping_payload(connection["id"])
    missing_trunk["pbx_trunk_name"] = ""
    response = client.post("/api/outbound/vicidial/mappings", json=missing_trunk)
    assert response.status_code == 422
    assert "PBX trunk name is required" in response.json()["detail"]

    existing_endpoint = _mapping_payload(connection["id"])
    existing_endpoint["pbx_setup_mode"] = "existing_endpoint"
    existing_endpoint["pbx_trunk_name"] = ""
    response = client.post("/api/outbound/vicidial/mappings", json=existing_endpoint)
    assert response.status_code == 200


def test_enabled_mapping_rejects_overlapping_users_and_reused_endpoint(monkeypatch, tmp_path):
    client, store = _client(monkeypatch, tmp_path)
    connection = store.save_connection(_connection_payload(), "connection-1")
    store.save_mapping(_mapping_payload(connection["id"]), "mapping-1")

    overlapping = _mapping_payload(connection["id"])
    overlapping.update(
        {
            "name": "Overlapping users",
            "user_start": "9001",
            "conf_exten": "8400",
            "trusted_endpoint": "other-endpoint",
        }
    )
    response = client.post("/api/outbound/vicidial/mappings", json=overlapping)
    assert response.status_code == 422
    assert "user range overlaps" in response.json()["detail"]

    reused_endpoint = _mapping_payload(connection["id"])
    reused_endpoint.update(
        {
            "name": "Reused endpoint",
            "user_start": "9100",
            "static_agent_user": "9100",
            "conf_exten": "8400",
        }
    )
    response = client.post("/api/outbound/vicidial/mappings", json=reused_endpoint)
    assert response.status_code == 422
    assert "endpoint is already used" in response.json()["detail"]


def test_sip_requires_existing_endpoint_mode_and_preserves_existing_configuration(
    monkeypatch, tmp_path
):
    client, store = _client(monkeypatch, tmp_path)
    connection = store.save_connection(_connection_payload(), "connection-1")
    payload = _mapping_payload(connection["id"])
    payload["pbx_technology"] = "SIP"

    response = client.post("/api/outbound/vicidial/mappings", json=payload)
    assert response.status_code == 422
    assert "supports PJSIP only" in response.json()["detail"]

    payload["pbx_setup_mode"] = "existing_endpoint"
    response = client.post("/api/outbound/vicidial/mappings", json=payload)
    assert response.status_code == 200

    guidance = client.get(
        f"/api/outbound/vicidial/mappings/{response.json()['id']}/guidance"
    )
    assert guidance.status_code == 200
    trunk = guidance.json()["freepbx_trunk"]
    assert trunk["technology"] == "SIP"
    assert "no PBX mutation" in trunk["configuration"]
    assert "secret" not in trunk
    assert "registration" not in trunk


def test_lists_sanitized_asterisk_endpoints(monkeypatch, tmp_path):
    client, _store = _client(monkeypatch, tmp_path)
    response = client.get("/api/outbound/vicidial/asterisk/endpoints?technology=PJSIP")
    assert response.status_code == 200
    assert response.json()["endpoints"] == [
        {
            "technology": "PJSIP",
            "resource": "vicidial-ra",
            "state": "online",
            "channel_count": 0,
        }
    ]


def test_connection_verification_does_not_expose_exception_details(monkeypatch, tmp_path):
    client, store = _client(monkeypatch, tmp_path)
    store.save_connection(_connection_payload(), "connection-1")

    class _InvalidClient:
        def __init__(self, _connection):
            raise ValueError("secret path /srv/private/config and stack detail")

    monkeypatch.setattr(vicidial_api, "VicidialApiClient", _InvalidClient)
    response = client.post(
        "/api/outbound/vicidial/connections/connection-1/verify"
    )

    assert response.status_code == 200
    assert response.json() == {
        "ready": False,
        "error": "VICIdial connection configuration is invalid",
    }
    stored = store.get_connection("connection-1")["last_verification"]
    assert stored == response.json()


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
    assert body["pbx_ready"] is True
    assert body["pbx_endpoint"]["state"] == "online"
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
        CallRecord(
            id="record-3",
            call_id="asterisk-3",
            caller_number="13165550124",
            start_time=now - timedelta(minutes=3),
            end_time=now - timedelta(minutes=2, seconds=52),
            duration_seconds=8,
            context_name="demo_deepgram",
            outcome="completed",
            external_platform="vicidial",
            external_direction="outbound",
            external_disposition="AIFAIL",
            external_metadata={
                "mapping_id": "mapping-1",
                "mapping_name": "AVA Remote Agent",
                "session": {"agent_user": "9001"},
                "requested_disposition": "DNC",
                "disposition_label": "dnc",
                "finalized": True,
            },
        ),
    ]

    class _History:
        async def list_external_activity(
            self, platform, start_date, end_date=None, max_rows=5000
        ):
            assert platform == "vicidial"
            assert start_date < end_date
            assert max_rows == vicidial_api.ACTIVITY_SUMMARY_MAX_ROWS + 1
            return records

    monkeypatch.setattr(vicidial_api, "_call_history_store", lambda: _History())

    response = client.get(
        "/api/outbound/vicidial/activity?range=7d&mapping_id=mapping-1&limit=1"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {
        "handled": 3,
        "finalized": 2,
        "unconfirmed_errors": 1,
        "confirmed_failures": 1,
        "needs_attention": 2,
        "average_duration_seconds": 20.0,
        "last_call_at": now.isoformat(),
    }
    assert body["dispositions"] == [
        {"status": "AIFAIL", "count": 1},
        {"status": "AIHU", "count": 1},
    ]
    assert body["by_mapping"][0]["handled"] == 3
    assert body["by_mapping"][0]["unconfirmed_errors"] == 1
    assert body["by_mapping"][0]["confirmed_failures"] == 1
    assert len(body["recent_calls"]) == 1
    assert body["recent_calls"][0]["masked_number"] == "•••9284"
    assert body["recent_calls"][0]["remote_agent"] == "9001"
    assert body["recent_calls"][0]["disposition_confirmed"] is True
    assert "never reached" in body["scope_note"]
    assert body["truncated"] is False

    assert client.get("/api/outbound/vicidial/activity?range=90d").status_code == 422


def test_activity_reports_when_summary_rows_are_truncated(monkeypatch, tmp_path):
    client, _store = _client(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    record = CallRecord(
        id="record-1",
        call_id="asterisk-1",
        start_time=now,
        external_platform="vicidial",
        external_metadata={},
    )

    class _History:
        async def list_external_activity(self, platform, start_date, end_date=None, max_rows=5000):
            assert max_rows == vicidial_api.ACTIVITY_SUMMARY_MAX_ROWS + 1
            return [record] * max_rows

    monkeypatch.setattr(vicidial_api, "_call_history_store", lambda: _History())
    response = client.get("/api/outbound/vicidial/activity?range=30d")

    assert response.status_code == 200
    body = response.json()
    assert body["truncated"] is True
    assert body["summary"]["handled"] == vicidial_api.ACTIVITY_SUMMARY_MAX_ROWS
    assert "metrics use the most recent records" in body["scope_note"]
