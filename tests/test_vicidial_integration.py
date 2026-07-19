from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.models import CallSession
from src.core.vicidial_store import VicidialStore
from src.engine import Engine
from src.integrations.vicidial import (
    VicidialApiClient,
    VicidialApiResult,
    VicidialIntegrationError,
    VicidialSessionInfo,
    validate_call_id,
)
from src.tools.context import ToolExecutionContext
from src.tools.telephony.vicidial import (
    SetCallDispositionTool,
    commit_vicidial_disposition_workflow,
    execute_vicidial_transfer,
)


class _Response:
    def __init__(self, body: str, status: int = 200):
        self.body = body
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def text(self):
        return self.body


class _Session:
    def __init__(self, capture: dict, body: str):
        self.capture = capture
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def post(self, url, *, data, ssl):
        self.capture.update({"url": url, "data": data, "ssl": ssl})
        return _Response(self.body)


def _connection():
    return {
        "base_url": "http://vicidial.test",
        "source": "aava",
        "username_env": "VICI_USER",
        "password_env": "VICI_PASS",
        "verify_ssl": False,
        "timezone": "America/Phoenix",
    }


def _mapping(connection_id: str = "connection-1"):
    return {
        "connection_id": connection_id,
        "name": "Lab RA",
        "direction": "both",
        "campaign_id": "TESTCAMP",
        "user_start": "9001",
        "number_of_lines": 1,
        "conf_exten": "8371",
        "static_agent_user": "9001",
        "ai_agent": "demo_deepgram",
        "dispositions": {"sale": "SALE"},
        "statuses": {},
        "destinations": {
            "sales": {
                "type": "ingroup",
                "target": "SALESLINE",
                "description": "Sales",
            }
        },
    }


@pytest.mark.asyncio
async def test_non_agent_requests_include_headers_and_parse_dynamic_session(monkeypatch):
    monkeypatch.setenv("VICI_USER", "apiuser")
    monkeypatch.setenv("VICI_PASS", "secret")
    captures = []
    bodies = iter([
        "call_id|custtime|call_date|campaign_id|list_id|status|user|phone\n"
        "M4050908070000012345|12|2026-07-19 10:00:00|TESTCAMP|101|INCALL|VDAD|13165551212",
        "status|call_id|lead_id|campaign_id|calls_today|full_name|user_group|user_level|pause_code|real_time_sub_status|phone_number|vendor_lead_code|session_id\n"
        "INCALL|M4050908070000012345|456|TESTCAMP|1|AAVA|AGENTS|8|||13165551212||8371",
    ])

    def factory(**_kwargs):
        capture = {}
        captures.append(capture)
        return _Session(capture, next(bodies))

    client = VicidialApiClient(_connection(), session_factory=factory)
    info, evidence = await client.resolve_remote_agent_session(
        call_id="M4050908070000012345",
        mapping={"id": "map-1", **_mapping()},
        attempts=1,
    )

    assert info is not None
    assert info.agent_user == "9001"
    assert info.lead_id == "456"
    assert info.phone_number == "13165551212"
    assert len(evidence) == 2
    assert captures[0]["data"]["header"] == "YES"
    assert captures[0]["data"]["detail"] == "YES"
    assert captures[1]["data"]["header"] == "YES"
    assert captures[0]["data"]["pass"] == "secret"


@pytest.mark.asyncio
async def test_installed_agent_status_callerid_must_match(monkeypatch):
    monkeypatch.setenv("VICI_USER", "apiuser")
    monkeypatch.setenv("VICI_PASS", "secret")
    bodies = iter([
        "call_id|custtime|call_date|phone|call_type|campaign_id|list_id|status|user\n"
        "M4050908070000012345|12|2026-07-19 10:00:00|13165551212|OUT|TESTCAMP|101|XFER|9001",
        "status|callerid|lead_id|campaign_id|calls_today|full_name|user_group|user_level|pause_code|real_time_sub_status|phone_number|vendor_lead_code|session_id\n"
        "INCALL|M4050908070000099999|456|TESTCAMP|1|AAVA|AGENTS|8|||13165551212||8371",
    ])

    def factory(**_kwargs):
        return _Session({}, next(bodies))

    client = VicidialApiClient(_connection(), session_factory=factory)
    info, _evidence = await client.resolve_remote_agent_session(
        call_id="M4050908070000012345",
        mapping={"id": "map-1", **_mapping(), "static_agent_user": None},
        attempts=1,
    )
    assert info is None


@pytest.mark.asyncio
async def test_blended_inbound_uses_closer_group_not_agent_login_campaign(monkeypatch):
    monkeypatch.setenv("VICI_USER", "apiuser")
    monkeypatch.setenv("VICI_PASS", "secret")
    bodies = iter([
        "call_id|custtime|call_date|phone|call_type|campaign_id|list_id|status|user\n"
        "Y7190324550000000009|0|2026-07-19 03:24:55|8381|INBOUND|AVAIN|999|XFER|9001",
        "status|callerid|lead_id|campaign_id|phone_number|session_id\n"
        "INCALL|Y7190324550000000009|9|AVATEST|8381|8371",
    ])

    def factory(**_kwargs):
        return _Session({}, next(bodies))

    client = VicidialApiClient(_connection(), session_factory=factory)
    info, evidence = await client.resolve_remote_agent_session(
        call_id="Y7190324550000000009",
        mapping={
            "id": "map-1",
            **_mapping(),
            "campaign_id": "AVATEST",
            "closer_campaigns": ["AVAIN"],
        },
        attempts=1,
    )

    assert info is not None
    assert info.agent_user == "9001"
    assert info.campaign_id == "AVAIN"
    assert info.lead_id == "9"
    assert info.direction == "inbound"
    assert info.metadata["agent_status"]["campaign_id"] == "AVATEST"
    assert len(evidence) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("log_user", "closer_campaigns"),
    [("9002", ["AVAIN"]), ("9001", ["OTHER"]), ("9001", [])],
)
async def test_blended_inbound_rejects_wrong_user_or_closer_group(
    monkeypatch, log_user, closer_campaigns
):
    monkeypatch.setenv("VICI_USER", "apiuser")
    monkeypatch.setenv("VICI_PASS", "secret")
    bodies = iter([
        "call_id|phone|call_type|campaign_id|list_id|status|user\n"
        f"Y7190324550000000009|8381|INBOUND|AVAIN|999|XFER|{log_user}",
        "status|callerid|lead_id|campaign_id|phone_number|session_id\n"
        "INCALL|Y7190324550000000009|9|AVATEST|8381|8371",
    ])

    def factory(**_kwargs):
        return _Session({}, next(bodies))

    client = VicidialApiClient(_connection(), session_factory=factory)
    info, _evidence = await client.resolve_remote_agent_session(
        call_id="Y7190324550000000009",
        mapping={
            "id": "map-1",
            **_mapping(),
            "campaign_id": "AVATEST",
            "closer_campaigns": closer_campaigns,
            "static_agent_user": None,
        },
        attempts=1,
    )

    assert info is None


@pytest.mark.asyncio
async def test_customer_name_on_sip_leg_resolves_by_unique_mapped_agent(monkeypatch):
    monkeypatch.setenv("VICI_USER", "apiuser")
    monkeypatch.setenv("VICI_PASS", "secret")
    bodies = iter([
        "status|callerid|lead_id|campaign_id|phone_number\n"
        "QUEUE|V7190228450000000008|8|TESTCAMP|5551234567",
        "call_id|phone|call_type|campaign_id|list_id|status|user\n"
        "V7190228450000000008|5551234567|OUTBOUND_AUTO|TESTCAMP|998|INCALL|VDAD",
    ])

    def factory(**_kwargs):
        return _Session({}, next(bodies))

    client = VicidialApiClient(_connection(), session_factory=factory)
    info, evidence = await client.resolve_remote_agent_session(
        call_id="AVA Lab Customer",
        mapping={"id": "map-1", **_mapping()},
        attempts=1,
    )

    assert info is not None
    assert info.external_call_id == "V7190228450000000008"
    assert info.agent_user == "9001"
    assert info.direction == "outbound"
    assert info.resolution_source == "mapped_agent_status_scan"
    assert len(evidence) == 2


@pytest.mark.asyncio
async def test_customer_name_scan_fails_closed_when_multiple_agents_match(monkeypatch):
    monkeypatch.setenv("VICI_USER", "apiuser")
    monkeypatch.setenv("VICI_PASS", "secret")
    bodies = iter([
        "status|callerid|campaign_id\nQUEUE|V7190228450000000008|TESTCAMP",
        "call_id|call_type|campaign_id|user\n"
        "V7190228450000000008|OUT|TESTCAMP|9001",
        "status|callerid|campaign_id\nINCALL|V7190228450000000009|TESTCAMP",
        "call_id|call_type|campaign_id|user\n"
        "V7190228450000000009|OUT|TESTCAMP|9002",
    ])

    def factory(**_kwargs):
        return _Session({}, next(bodies))

    client = VicidialApiClient(_connection(), session_factory=factory)
    info, evidence = await client.resolve_remote_agent_session(
        call_id="Customer Display Name",
        mapping={"id": "map-1", **_mapping(), "number_of_lines": 2},
        attempts=1,
    )

    assert info is None
    assert len(evidence) == 4


@pytest.mark.asyncio
async def test_customer_name_scan_rejects_wrong_campaign(monkeypatch):
    monkeypatch.setenv("VICI_USER", "apiuser")
    monkeypatch.setenv("VICI_PASS", "secret")
    bodies = iter([
        "status|callerid|campaign_id\nQUEUE|V7190228450000000008|OTHER",
        "call_id|call_type|campaign_id|user\n"
        "V7190228450000000008|OUT|OTHER|9001",
    ])

    def factory(**_kwargs):
        return _Session({}, next(bodies))

    client = VicidialApiClient(_connection(), session_factory=factory)
    info, _evidence = await client.resolve_remote_agent_session(
        call_id="Customer Display Name",
        mapping={"id": "map-1", **_mapping()},
        attempts=1,
    )

    assert info is None


@pytest.mark.asyncio
async def test_invalid_sip_identifier_without_active_api_match_is_rejected(monkeypatch):
    monkeypatch.setenv("VICI_USER", "apiuser")
    monkeypatch.setenv("VICI_PASS", "secret")

    def factory(**_kwargs):
        return _Session({}, "status|callerid|campaign_id\nREADY||TESTCAMP")

    client = VicidialApiClient(_connection(), session_factory=factory)
    info, evidence = await client.resolve_remote_agent_session(
        call_id="1784424691.638",
        mapping={"id": "map-1", **_mapping()},
        attempts=1,
    )

    assert info is None
    assert len(evidence) == 1


def test_call_id_validation_matches_vicidial_callid_info_contract():
    assert validate_call_id("M4050908070000012345") == "M4050908070000012345"
    with pytest.raises(VicidialIntegrationError):
        validate_call_id("1784424691.638")


def test_vicidial_transport_identity_is_not_spoken_as_caller_name():
    session = CallSession(
        call_id="ari-inbound",
        caller_channel_id="ari-inbound",
        caller_name="VICIdial 8381",
        caller_number="8381",
    )
    session.external_platform = "vicidial"
    session.external_call_id = "Y7190334360000000010"

    rendered = Engine._apply_prompt_template_substitution(
        SimpleNamespace(),
        "Hi {caller_name}; your number is {caller_number}.",
        session,
    )

    assert rendered == "Hi there; your number is 8381."


def test_vicidial_real_cnam_remains_available_to_templates():
    session = CallSession(
        call_id="ari-inbound",
        caller_channel_id="ari-inbound",
        caller_name="Alice Example",
        caller_number="13165551212",
    )
    session.external_platform = "vicidial"
    session.external_call_id = "Y7190334360000000010"

    rendered = Engine._apply_prompt_template_substitution(
        SimpleNamespace(), "Hi {caller_name}.", session
    )

    assert rendered == "Hi Alice Example."


@pytest.mark.asyncio
async def test_existing_dnc_is_normalized_as_idempotent_success(monkeypatch):
    monkeypatch.setenv("VICI_USER", "apiuser")
    monkeypatch.setenv("VICI_PASS", "secret")

    def factory(**_kwargs):
        return _Session({}, "ERROR: add_dnc_phone DNC NUMBER ALREADY EXISTS")

    result = await VicidialApiClient(
        _connection(), session_factory=factory
    ).add_dnc_phone(phone_number="13165551212", campaign_id="TESTCAMP")

    assert result.success is True
    assert result.data["already_exists"] is True


def test_store_round_trip_and_connection_delete_cascades(tmp_path):
    store = VicidialStore(str(tmp_path / "vicidial.db"))
    connection = store.save_connection({
        **_connection(),
        "name": "Lab",
        "username_env": "VICI_USER",
        "password_env": "VICI_PASS",
    }, "connection-1")
    mapping = store.save_mapping(_mapping(connection["id"]), "mapping-1")

    assert mapping["statuses"]["ai_hangup"] == "AIHU"
    assert mapping["destinations"]["sales"]["status"] == "AIXFR"
    assert connection["timezone"] == "America/Phoenix"
    assert store.delete_connection(connection["id"]) is True
    assert store.get_mapping(mapping["id"]) is None


def test_store_merges_directional_real_call_readiness(tmp_path):
    store = VicidialStore(str(tmp_path / "vicidial.db"))
    connection = store.save_connection({
        **_connection(),
        "name": "Lab",
        "username_env": "VICI_USER",
        "password_env": "VICI_PASS",
    }, "connection-1")
    store.save_mapping(_mapping(connection["id"]), "mapping-1")
    store.record_verification(
        kind="mapping",
        record_id="mapping-1",
        result={"configuration_ready": True},
    )

    store.record_real_call_verification(
        mapping_id="mapping-1",
        direction="outbound",
        external_call_id="M4050908070000012345",
        status="AIHU",
        operation="hangup",
    )
    verification = store.get_mapping("mapping-1")["last_verification"]
    assert verification["real_call"]["verified"] is False
    assert verification["real_call"]["required_directions"] == ["inbound", "outbound"]
    assert verification["ready"] is False

    store.record_real_call_verification(
        mapping_id="mapping-1",
        direction="inbound",
        external_call_id="M4050908070000012346",
        status="AICU",
        operation="hangup",
    )

    verification = store.get_mapping("mapping-1")["last_verification"]
    assert verification["configuration_ready"] is True
    assert verification["real_calls"]["outbound"]["status"] == "AIHU"
    assert verification["real_calls"]["inbound"]["status"] == "AICU"
    assert verification["real_call"]["verified"] is True
    assert verification["ready"] is True


def test_store_invalidates_readiness_after_material_mapping_change(tmp_path):
    store = VicidialStore(str(tmp_path / "vicidial.db"))
    connection = store.save_connection({
        **_connection(),
        "name": "Lab",
        "username_env": "VICI_USER",
        "password_env": "VICI_PASS",
    }, "connection-1")
    mapping = store.save_mapping(_mapping(connection["id"]), "mapping-1")
    store.record_verification(
        kind="mapping",
        record_id="mapping-1",
        result={"configuration_ready": True, "ready": True},
    )

    store.save_mapping({**mapping, "name": "Renamed"}, "mapping-1")
    assert store.get_mapping("mapping-1")["last_verification"] is not None

    store.save_mapping({**mapping, "name": "Renamed", "ai_agent": "other_agent"}, "mapping-1")
    assert store.get_mapping("mapping-1")["last_verification"] is None


def test_store_invalidates_mapping_readiness_after_connection_change(tmp_path):
    store = VicidialStore(str(tmp_path / "vicidial.db"))
    payload = {
        **_connection(),
        "name": "Lab",
        "username_env": "VICI_USER",
        "password_env": "VICI_PASS",
    }
    connection = store.save_connection(payload, "connection-1")
    store.save_mapping(_mapping(connection["id"]), "mapping-1")
    store.record_verification(
        kind="mapping",
        record_id="mapping-1",
        result={"configuration_ready": True, "ready": True},
    )

    store.save_connection({**payload, "name": "Renamed"}, "connection-1")
    assert store.get_mapping("mapping-1")["last_verification"] is not None

    store.save_connection({**payload, "name": "Renamed", "base_url": "http://new-vicidial.test"}, "connection-1")
    assert store.get_mapping("mapping-1")["last_verification"] is None


class _SessionStore:
    def __init__(self, session):
        self.session = session

    async def get_by_call_id(self, _call_id):
        return self.session

    async def upsert_call(self, session):
        self.session = session


@pytest.mark.asyncio
async def test_vicidial_transfer_uses_api_and_marks_session_without_ari(monkeypatch):
    session = CallSession(call_id="ari-1", caller_channel_id="ari-1")
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
    ).to_dict()
    session.external_mapping = _mapping()
    session.external_connection = _connection()
    store = _SessionStore(session)
    context = ToolExecutionContext(
        call_id="ari-1",
        caller_channel_id="ari-1",
        session_store=store,
        ari_client=SimpleNamespace(),
    )

    class Client:
        def __init__(self, _connection):
            self.lookup_count = 0

        async def call_control(self, info, **kwargs):
            assert info.agent_user == "9001"
            assert kwargs == {
                "stage": "INGROUPTRANSFER",
                "status": "AIXFR",
                "ingroup_choices": "SALESLINE",
            }
            return VicidialApiResult(True, "ra_call_control", "SUCCESS: transferred")

    monkeypatch.setattr("src.tools.telephony.vicidial.VicidialApiClient", Client)
    result = await execute_vicidial_transfer(
        context=context,
        destination={
            "type": "vicidial_ingroup",
            "target": "SALESLINE",
            "description": "Sales",
            "status": "AIXFR",
        },
    )

    assert result["status"] == "success"
    assert store.session.external_finalized is True
    assert store.session.transfer_active is True
    assert store.session.transfer_destination == "SALESLINE"


@pytest.mark.asyncio
async def test_disposition_is_allowlisted_and_deferred_until_hangup():
    session = CallSession(call_id="ari-2", caller_channel_id="ari-2")
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
    ).to_dict()
    session.external_mapping = _mapping()
    session.external_connection = _connection()
    store = _SessionStore(session)
    context = ToolExecutionContext(call_id="ari-2", session_store=store)

    result = await SetCallDispositionTool().execute({"disposition": "sale"}, context)

    assert result["status"] == "success"
    assert store.session.external_requested_disposition == "SALE"
    assert store.session.external_disposition is None
    assert store.session.external_finalized is False


@pytest.mark.asyncio
async def test_callback_is_converted_to_vicidial_timezone_and_verified(monkeypatch):
    session = CallSession(call_id="ari-callback", caller_channel_id="ari-callback")
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
        campaign_id="TESTCAMP",
        lead_id="456",
    ).to_dict()
    session.external_mapping = {
        **_mapping(),
        "dispositions": {**_mapping()["dispositions"], "callback": "CALLBK"},
    }
    session.external_connection = _connection()
    store = _SessionStore(session)
    context = ToolExecutionContext(call_id="ari-callback", session_store=store)

    class Client:
        def __init__(self, _connection):
            self.lookup_count = 0

        async def update_lead_callback(self, **kwargs):
            assert kwargs["callback_datetime"] == "2026-07-19 18:30:00"
            return VicidialApiResult(True, "update_lead", "SUCCESS")

        async def lead_callback_info(self, **_kwargs):
            self.lookup_count += 1
            row = {
                "lead_id": "456",
                "callback_type": "CURRENT",
                "recipient": "ANYONE",
                "callback_status": "ACTIVE",
                "lead_status": "CALLBK",
                "campaign_id": "TESTCAMP",
                "callback_date": "2026-07-19 18:30:00",
            }
            return VicidialApiResult(
                True,
                "lead_callback_info",
                "verified",
                data=row if self.lookup_count > 1 else {},
                rows=[row] if self.lookup_count > 1 else [],
            )

    monkeypatch.setattr("src.tools.telephony.vicidial.VicidialApiClient", Client)
    result = await SetCallDispositionTool().execute(
        {
            "disposition": "callback",
            "callback_datetime": "2026-07-20T01:30:00Z",
            "comments": "Customer requested evening",
        },
        context,
    )

    assert result["status"] == "success"
    assert store.session.external_requested_disposition == "CALLBK"
    assert store.session.external_disposition is None
    assert [event["operation"] for event in store.session.external_events] == [
        "disposition_selected",
    ]

    assert await commit_vicidial_disposition_workflow(store.session) is True
    assert [event["operation"] for event in store.session.external_events] == [
        "disposition_selected",
        "callback",
        "callback_verify",
    ]
    assert store.session.external_disposition_payload["workflow_committed"] is True


@pytest.mark.asyncio
async def test_callback_retry_reuses_verified_existing_record(monkeypatch):
    session = CallSession(call_id="ari-callback-retry", caller_channel_id="ari-callback-retry")
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
        campaign_id="TESTCAMP",
        lead_id="456",
    ).to_dict()
    session.external_mapping = _mapping()
    session.external_connection = _connection()
    session.external_requested_disposition = "CALLBK"
    session.external_disposition_label = "callback"
    session.external_disposition_payload = {
        "lead_id": "456",
        "campaign_id": "TESTCAMP",
        "callback_datetime": "2026-07-19 18:30:00",
        "callback_type": "ANYONE",
    }

    class Client:
        def __init__(self, _connection):
            pass

        async def update_lead_callback(self, **_kwargs):
            raise AssertionError("an already verified callback must not be recreated")

        async def lead_callback_info(self, **_kwargs):
            row = {
                "lead_id": "456",
                "callback_type": "CURRENT",
                "recipient": "ANYONE",
                "callback_status": "ACTIVE",
                "lead_status": "CALLBK",
                "campaign_id": "TESTCAMP",
                "callback_date": "2026-07-19 18:30:00",
            }
            return VicidialApiResult(True, "lead_callback_info", "verified", rows=[row])

    monkeypatch.setattr("src.tools.telephony.vicidial.VicidialApiClient", Client)
    assert await commit_vicidial_disposition_workflow(session) is True
    assert session.external_disposition_payload["workflow_committed"] is True


@pytest.mark.asyncio
async def test_engine_finalizer_does_not_claim_failed_vicidial_hangup(monkeypatch):
    session = CallSession(call_id="ari-3", caller_channel_id="ari-3")
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
    ).to_dict()
    session.external_mapping = _mapping()
    session.external_connection = _connection()

    class Client:
        def __init__(self, _connection):
            pass

        async def call_control(self, *_args, **_kwargs):
            return VicidialApiResult(False, "ra_call_control", "ERROR: no active call")

        async def callid_info(self, *_args, **_kwargs):
            return VicidialApiResult(
                True,
                "callid_info",
                "active",
                data={
                    "call_id": "M4050908070000012345",
                    "campaign_id": "TESTCAMP",
                    "status": "QUEUE",
                },
            )

        async def agent_status(self, *_args, **_kwargs):
            return VicidialApiResult(
                True,
                "agent_status",
                "active",
                data={"callerid": "M4050908070000012345", "status": "INCALL"},
            )

    async def save(saved_session):
        assert saved_session is session

    monkeypatch.setattr("src.integrations.vicidial.VicidialApiClient", Client)
    engine = SimpleNamespace(_save_session=save)

    result = await Engine._finalize_vicidial_call(
        engine,
        session,
        semantic="ai_hangup",
        operation_reason="test",
    )

    assert result is False
    assert session.external_finalized is False
    hangup_event = next(
        event for event in session.external_events if event["operation"] == "hangup"
    )
    assert hangup_event["success"] is False


@pytest.mark.asyncio
async def test_engine_reconciles_vicidial_terminal_status_after_caller_hangup(monkeypatch):
    session = CallSession(call_id="ari-reconcile", caller_channel_id="ari-reconcile")
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
        campaign_id="TESTCAMP",
        direction="outbound",
    ).to_dict()
    session.external_mapping = _mapping()
    session.external_connection = _connection()

    class Client:
        def __init__(self, _connection):
            pass

        async def call_control(self, *_args, **_kwargs):
            return VicidialApiResult(False, "ra_call_control", "ERROR: no active call")

        async def callid_info(self, *_args, **_kwargs):
            return VicidialApiResult(
                True,
                "callid_info",
                "terminal",
                data={
                    "call_id": "M4050908070000012345",
                    "campaign_id": "TESTCAMP",
                    "status": "XFER",
                },
            )

        async def agent_status(self, *_args, **_kwargs):
            return VicidialApiResult(
                True,
                "agent_status",
                "cleanup pending",
                data={
                    "callerid": "M4050908070000012345",
                    "status": "INCALL",
                    "real_time_sub_status": "DEAD",
                },
            )

    recorded = {}

    class Store:
        def record_real_call_verification(self, **kwargs):
            recorded.update(kwargs)

    async def save(saved_session):
        assert saved_session is session

    monkeypatch.setattr("src.integrations.vicidial.VicidialApiClient", Client)
    monkeypatch.setattr("src.core.vicidial_store.get_vicidial_store", lambda: Store())
    engine = SimpleNamespace(_save_session=save)

    result = await Engine._finalize_vicidial_call(
        engine,
        session,
        semantic="caller_hangup",
        operation_reason="call-cleanup",
    )

    assert result is True
    assert session.external_finalized is True
    assert session.external_disposition == "XFER"
    assert session.external_disposition_label == "vicidial_terminal"
    assert recorded == {
        "mapping_id": "map-1",
        "direction": "outbound",
        "external_call_id": "M4050908070000012345",
        "status": "XFER",
        "operation": "terminal_reconcile",
    }


def test_vicidial_tool_policy_is_scoped_to_external_calls():
    ordinary = CallSession(call_id="ordinary", caller_channel_id="ordinary")
    assert Engine._apply_vicidial_tool_policy(ordinary, ["attended_transfer"]) == ["attended_transfer"]

    ordinary.external_platform = "vicidial"
    tools = Engine._apply_vicidial_tool_policy(
        ordinary,
        ["attended_transfer", "hangup_call"],
    )
    assert tools == ["hangup_call"]

    ordinary.external_mapping = _mapping()
    tools = Engine._apply_vicidial_tool_policy(ordinary, ["attended_transfer"])
    assert tools == ["hangup_call", "blind_transfer", "set_call_disposition"]


def test_agent_runtime_resolution_preserves_vicidial_destinations():
    global_config = {
        "tools": {
            "transfer": {
                "destinations": {
                    "Live Agent": {
                        "type": "extension",
                        "target": "6000",
                    }
                }
            }
        }
    }
    effective = SimpleNamespace(
        config=global_config,
        policy="selected",
        requested_destination_keys=("Live Agent",),
        effective_destination_keys=("Live Agent",),
        stale_destination_keys=(),
        policies={"transfer": "selected"},
        effective_resource_keys={"transfer": ("Live Agent",)},
        stale_resource_keys={},
    )
    generation = SimpleNamespace(
        generation_id=1,
        config_hash="hash",
        registry=SimpleNamespace(),
        for_agent=lambda _policy: effective,
    )
    engine = Engine.__new__(Engine)
    engine._tool_generation = generation
    session = CallSession(call_id="ari-runtime", caller_channel_id="ari-runtime")
    session.external_platform = "vicidial"
    session.external_mapping = _mapping()

    Engine._resolve_session_tool_runtime(engine, session)

    transfer = session.tool_runtime_config["tools"]["transfer"]
    assert transfer["destinations"] == {
        "sales": {
            "type": "vicidial_ingroup",
            "target": "SALESLINE",
            "description": "Sales",
        }
    }
    assert session.tool_policy["effective_destination_keys"] == ["sales"]
    assert session.tool_policy["effective_resource_keys"]["transfer"] == ["sales"]
    assert session.tool_runtime_config["tools"]["vicidial"]["dispositions"] == {
        "sale": "SALE"
    }
