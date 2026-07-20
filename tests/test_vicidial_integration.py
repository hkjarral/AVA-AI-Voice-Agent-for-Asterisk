from __future__ import annotations

import asyncio
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.core.models import CallSession
from src.core.vicidial_lifecycle import vicidial_lifecycle_lock
from src.core.vicidial_store import (
    VicidialStore,
    vicidial_configuration_revision,
)
from src.engine import Engine
from src.integrations.vicidial import (
    VicidialApiClient,
    VicidialApiResult,
    VicidialIntegrationError,
    VicidialSessionInfo,
    remote_agent_user_range,
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
        "closer_campaigns": ["TESTINGROUP"],
        "user_start": "9001",
        "number_of_lines": 1,
        "conf_exten": "8371",
        "static_agent_user": "9001",
        "ai_agent": "demo_deepgram",
        "trusted_endpoint": "vicidial-ra",
        "pbx_trunk_name": "VICIdial RA",
        "dispositions": {"sale": "SALE", "dnc": "DNC"},
        "statuses": {},
        "destinations": {
            "sales": {
                "type": "ingroup",
                "target": "SALESLINE",
                "description": "Sales",
            }
        },
    }


def _stored_mapping_revision(store: VicidialStore, mapping_id: str) -> str:
    mapping = store.get_mapping(mapping_id)
    assert mapping is not None
    connection = store.get_connection(str(mapping.get("connection_id") or ""))
    assert connection is not None
    return vicidial_configuration_revision(mapping, connection)


def test_remote_agent_user_range_preserves_leading_zero_width():
    assert list(remote_agent_user_range({
        "user_start": "09001",
        "number_of_lines": 3,
    })) == ["09001", "09002", "09003"]


def test_enabled_mapping_requires_dnc_but_disabled_draft_can_omit_it():
    without_dnc = {
        **_mapping(),
        "dispositions": {"sale": "SALE"},
    }

    with pytest.raises(ValueError, match="require a dnc disposition"):
        VicidialStore.validate_mapping(without_dnc)

    disabled = VicidialStore.validate_mapping(
        {**without_dnc, "enabled": False}
    )
    assert disabled["enabled"] is False
    assert disabled["dispositions"] == {"sale": "SALE"}


@pytest.mark.asyncio
async def test_hydration_applies_authoritative_mapping_agent(monkeypatch):
    mapping = {"id": "map-1", "enabled": True, **_mapping()}
    mapping["ai_agent"] = "demo_google_live"
    connection = {"id": "connection-1", "enabled": True, **_connection()}
    store = SimpleNamespace(
        get_mapping=lambda _mapping_id: mapping,
        get_connection=lambda _connection_id: connection,
    )
    monkeypatch.setattr(
        "src.core.vicidial_store.get_vicidial_store", lambda: store
    )

    resolved = VicidialSessionInfo(
        external_call_id="V4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
        campaign_id="TESTCAMP",
        lead_id="456",
        list_id="101",
        phone_number="13165551212",
        call_type="OUT",
        vicidial_status="XFER",
        direction="outbound",
        resolution_source="callid_info",
        metadata={},
    )
    resolve = AsyncMock(return_value=(resolved, []))
    monkeypatch.setattr(VicidialApiClient, "resolve_remote_agent_session", resolve)

    engine = Engine.__new__(Engine)
    engine.ari_client = SimpleNamespace(set_channel_var=AsyncMock(return_value=True))
    variables = {
        "AAVA_CALL_OWNER": "vicidial",
        "VICIDIAL_MAPPING_ID": "map-1",
        "VICIDIAL_MAPPING_REVISION": vicidial_configuration_revision(
            mapping, connection
        ),
        "VICIDIAL_RA_CALL_ID": resolved.external_call_id,
        "AI_AGENT": "demo_deepgram",
    }
    engine._read_channel_variable = AsyncMock(
        side_effect=lambda _channel_id, name: variables.get(name, "")
    )
    engine._read_channel_variable_result = AsyncMock(
        side_effect=lambda _channel_id, name: (variables.get(name, ""), True)
    )
    engine._overlay_vicidial_tool_runtime = lambda _session: None
    engine._save_session = AsyncMock()
    session = CallSession(call_id="ari-call", caller_channel_id="ari-call")

    await Engine._hydrate_vicidial_session(engine, session, "ari-call")

    engine.ari_client.set_channel_var.assert_awaited_once_with(
        "ari-call", "AI_AGENT", "demo_google_live"
    )
    assert session.external_mapping["ai_agent"] == "demo_google_live"
    assert session.external_session["agent_user"] == "9001"
    assert session.caller_number == "13165551212"


@pytest.mark.asyncio
async def test_hydration_allows_empty_transport_call_id_for_status_correlation(
    monkeypatch,
):
    mapping = {"id": "map-1", "enabled": True, **_mapping()}
    connection = {"id": "connection-1", "enabled": True, **_connection()}
    store = SimpleNamespace(
        get_mapping=lambda _mapping_id: mapping,
        get_connection=lambda _connection_id: connection,
    )
    monkeypatch.setattr(
        "src.core.vicidial_store.get_vicidial_store", lambda: store
    )
    resolved = VicidialSessionInfo(
        external_call_id="V4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
        campaign_id="TESTCAMP",
        direction="outbound",
        resolution_source="mapped_agent_status_scan",
    )
    resolve = AsyncMock(return_value=(resolved, []))
    monkeypatch.setattr(VicidialApiClient, "resolve_remote_agent_session", resolve)

    engine = Engine.__new__(Engine)
    engine.ari_client = SimpleNamespace(set_channel_var=AsyncMock(return_value=True))
    variables = {
        "AAVA_CALL_OWNER": "vicidial",
        "VICIDIAL_MAPPING_ID": "map-1",
        "VICIDIAL_MAPPING_REVISION": vicidial_configuration_revision(
            mapping, connection
        ),
        "VICIDIAL_RA_CALL_ID": "",
        "AI_AGENT": "demo_deepgram",
    }
    engine._read_channel_variable = AsyncMock(
        side_effect=lambda _channel_id, name: variables.get(name, "")
    )
    engine._read_channel_variable_result = AsyncMock(
        side_effect=lambda _channel_id, name: (variables.get(name, ""), True)
    )
    engine._overlay_vicidial_tool_runtime = lambda _session: None
    engine._save_session = AsyncMock()
    session = CallSession(call_id="ari-call", caller_channel_id="ari-call")

    await Engine._hydrate_vicidial_session(engine, session, "ari-call")

    resolve.assert_awaited_once_with(call_id="", mapping=mapping)
    assert session.external_call_id == resolved.external_call_id
    assert session.external_session["resolution_source"] == "mapped_agent_status_scan"


@pytest.mark.asyncio
async def test_hydration_rejects_call_when_mapping_agent_cannot_be_applied(monkeypatch):
    mapping = {"id": "map-1", "enabled": True, **_mapping()}
    connection = {"id": "connection-1", "enabled": True, **_connection()}
    store = SimpleNamespace(
        get_mapping=lambda _mapping_id: mapping,
        get_connection=lambda _connection_id: connection,
    )
    monkeypatch.setattr(
        "src.core.vicidial_store.get_vicidial_store", lambda: store
    )
    resolved = VicidialSessionInfo(
        external_call_id="V4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
        campaign_id="TESTCAMP",
        lead_id="456",
        list_id="101",
        phone_number="13165551212",
        call_type="OUT",
        vicidial_status="XFER",
        direction="outbound",
        resolution_source="callid_info",
        metadata={},
    )
    monkeypatch.setattr(
        VicidialApiClient,
        "resolve_remote_agent_session",
        AsyncMock(return_value=(resolved, [])),
    )

    engine = Engine.__new__(Engine)
    engine.ari_client = SimpleNamespace(set_channel_var=AsyncMock(return_value=False))
    variables = {
        "AAVA_CALL_OWNER": "vicidial",
        "VICIDIAL_MAPPING_ID": "map-1",
        "VICIDIAL_MAPPING_REVISION": vicidial_configuration_revision(
            mapping, connection
        ),
        "VICIDIAL_RA_CALL_ID": resolved.external_call_id,
        "AI_AGENT": "stale-agent",
    }
    engine._read_channel_variable = AsyncMock(
        side_effect=lambda _channel_id, name: variables.get(name, "")
    )
    engine._read_channel_variable_result = AsyncMock(
        side_effect=lambda _channel_id, name: (variables.get(name, ""), True)
    )
    engine._overlay_vicidial_tool_runtime = lambda _session: None
    engine._save_session = AsyncMock()
    session = CallSession(call_id="ari-call", caller_channel_id="ari-call")

    with pytest.raises(RuntimeError, match="Unable to apply VICIdial mapping Agent"):
        await Engine._hydrate_vicidial_session(engine, session, "ari-call")


@pytest.mark.asyncio
async def test_rejected_admission_forces_cleanup_when_ari_hangup_fails():
    engine = Engine.__new__(Engine)
    engine.ari_client = SimpleNamespace(
        hangup_channel=AsyncMock(return_value=False)
    )
    engine._save_session = AsyncMock()
    engine._cleanup_call = AsyncMock()
    session = CallSession(
        call_id="ari-rejected",
        caller_channel_id="ari-rejected",
    )
    session.external_platform = "vicidial"

    await Engine._reject_vicidial_admission(
        engine,
        session,
        "ari-rejected",
    )

    assert session.call_outcome == "failed"
    engine._cleanup_call.assert_awaited_once_with(
        "ari-rejected",
        force_caller_hangup=True,
    )


@pytest.mark.asyncio
async def test_forced_vicidial_hangup_retry_owns_channel_until_accepted(
    monkeypatch,
):
    engine = Engine.__new__(Engine)
    engine.ari_client = SimpleNamespace(
        hangup_channel=AsyncMock(side_effect=[False, True])
    )
    engine._vicidial_forced_hangup_tasks = {}

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr("src.engine.asyncio.sleep", no_delay)

    Engine._schedule_vicidial_forced_hangup_retry(
        engine,
        call_id="ari-rejected",
        channel_id="ari-rejected",
    )
    task = engine._vicidial_forced_hangup_tasks["ari-rejected"]
    await task

    assert engine.ari_client.hangup_channel.await_count == 2
    assert engine._vicidial_forced_hangup_tasks == {}


@pytest.mark.asyncio
async def test_hydration_rejects_stale_generated_dialplan(monkeypatch):
    mapping = {"id": "map-1", "enabled": True, **_mapping()}
    connection = {"id": "connection-1", "enabled": True, **_connection()}
    store = SimpleNamespace(
        get_mapping=lambda _mapping_id: mapping,
        get_connection=lambda _connection_id: connection,
    )
    monkeypatch.setattr(
        "src.core.vicidial_store.get_vicidial_store", lambda: store
    )
    monkeypatch.setattr(
        VicidialApiClient,
        "resolve_remote_agent_session",
        AsyncMock(side_effect=AssertionError("stale dialplan must not reach VICIdial")),
    )
    engine = Engine.__new__(Engine)
    variables = {
        "AAVA_CALL_OWNER": "vicidial",
        "VICIDIAL_MAPPING_ID": "map-1",
        "VICIDIAL_MAPPING_REVISION": "stale-revision",
        "VICIDIAL_RA_CALL_ID": "V4050908070000012345",
    }
    engine._read_channel_variable = AsyncMock(
        side_effect=lambda _channel_id, name: variables.get(name, "")
    )
    engine._read_channel_variable_result = AsyncMock(
        side_effect=lambda _channel_id, name: (variables.get(name, ""), True)
    )
    engine._save_session = AsyncMock()
    session = CallSession(call_id="ari-stale", caller_channel_id="ari-stale")

    with pytest.raises(RuntimeError, match="mapping revision is missing or stale"):
        await Engine._hydrate_vicidial_session(engine, session, "ari-stale")

    assert session.external_platform == "vicidial"
    assert session.external_mapping_revision == vicidial_configuration_revision(
        mapping, connection
    )
    assert session.external_events[-1]["success"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mapping", "connection", "expected_error"),
    [
        (None, None, "mapping 'map-1' is missing or disabled"),
        ({"id": "map-1", "enabled": True, **_mapping()}, None, "connection is missing or disabled"),
    ],
)
async def test_hydration_marks_vicidial_owner_before_store_validation(
    monkeypatch, mapping, connection, expected_error
):
    store = SimpleNamespace(
        get_mapping=lambda _mapping_id: mapping,
        get_connection=lambda _connection_id: connection,
    )
    monkeypatch.setattr(
        "src.core.vicidial_store.get_vicidial_store", lambda: store
    )
    engine = Engine.__new__(Engine)
    variables = {
        "AAVA_CALL_OWNER": "vicidial",
        "VICIDIAL_MAPPING_ID": "map-1",
        "VICIDIAL_RA_CALL_ID": "V4050908070000012345",
    }
    engine._read_channel_variable = AsyncMock(
        side_effect=lambda _channel_id, name: variables.get(name, "")
    )
    engine._read_channel_variable_result = AsyncMock(
        side_effect=lambda _channel_id, name: (variables.get(name, ""), True)
    )
    session = CallSession(call_id="ari-call", caller_channel_id="ari-call")

    with pytest.raises(RuntimeError, match=expected_error):
        await Engine._hydrate_vicidial_session(engine, session, "ari-call")

    assert session.external_platform == "vicidial"


@pytest.mark.asyncio
async def test_hydration_fails_closed_when_owner_read_fails_on_trusted_route(
    monkeypatch,
):
    mapping = {
        "id": "map-1",
        "enabled": True,
        "trusted_context": "from-vicidial-ra",
        "conf_exten": "8371",
    }
    store = SimpleNamespace(list_mappings=lambda: [mapping])
    monkeypatch.setattr(
        "src.core.vicidial_store.get_vicidial_store", lambda: store
    )
    engine = Engine.__new__(Engine)
    engine._read_channel_variable_result = AsyncMock(return_value=("", False))
    engine._save_session = AsyncMock()
    session = CallSession(call_id="ari-call", caller_channel_id="ari-call")

    with pytest.raises(RuntimeError, match="Unable to read VICIdial ownership marker"):
        await Engine._hydrate_vicidial_session(
            engine,
            session,
            "ari-call",
            dialplan_context="from-vicidial-ra",
            dialplan_extension="8371",
        )

    assert session.external_platform == "vicidial"
    assert session.external_events[-1]["success"] is False
    engine._save_session.assert_awaited_once_with(session)


@pytest.mark.asyncio
async def test_hydration_fails_closed_on_a_deleted_generated_route(
    monkeypatch, tmp_path
):
    store = VicidialStore(str(tmp_path / "vicidial.db"))
    connection = store.save_connection(
        {
            **_connection(),
            "name": "Lab",
            "username_env": "VICI_USER",
            "password_env": "VICI_PASS",
        },
        "connection-1",
    )
    store.save_mapping(_mapping(connection["id"]), "mapping-1")
    assert store.delete_mapping("mapping-1") is True
    assert store.list_mappings() == []
    assert store.list_route_tombstones()[0]["trusted_context"] == (
        "from-vicidial-ra"
    )
    monkeypatch.setattr(
        "src.core.vicidial_store.get_vicidial_store", lambda: store
    )
    engine = Engine.__new__(Engine)
    engine._read_channel_variable_result = AsyncMock(return_value=("", False))
    engine._save_session = AsyncMock()
    session = CallSession(call_id="ari-deleted-route", caller_channel_id="ari-deleted-route")

    with pytest.raises(RuntimeError, match="Unable to read VICIdial ownership marker"):
        await Engine._hydrate_vicidial_session(
            engine,
            session,
            "ari-deleted-route",
            dialplan_context="from-vicidial-ra",
            dialplan_extension="8371",
        )

    assert session.external_platform == "vicidial"


@pytest.mark.asyncio
async def test_hydration_fails_closed_on_a_superseded_generated_route(
    monkeypatch, tmp_path
):
    store = VicidialStore(str(tmp_path / "vicidial.db"))
    connection = store.save_connection(
        {
            **_connection(),
            "name": "Lab",
            "username_env": "VICI_USER",
            "password_env": "VICI_PASS",
        },
        "connection-1",
    )
    mapping = store.save_mapping(_mapping(connection["id"]), "mapping-1")
    store.save_mapping(
        {
            **mapping,
            "trusted_context": "from-vicidial-ra-new",
            "conf_exten": "8372",
        },
        "mapping-1",
    )
    monkeypatch.setattr(
        "src.core.vicidial_store.get_vicidial_store", lambda: store
    )
    engine = Engine.__new__(Engine)
    engine._read_channel_variable_result = AsyncMock(return_value=("", False))
    engine._save_session = AsyncMock()
    session = CallSession(
        call_id="ari-superseded-route",
        caller_channel_id="ari-superseded-route",
    )

    with pytest.raises(RuntimeError, match="Unable to read VICIdial ownership marker"):
        await Engine._hydrate_vicidial_session(
            engine,
            session,
            "ari-superseded-route",
            dialplan_context="from-vicidial-ra",
            dialplan_extension="8371",
        )

    assert session.external_platform == "vicidial"


@pytest.mark.asyncio
async def test_hydration_does_not_claim_ordinary_route_when_owner_read_fails(
    monkeypatch,
):
    mapping = {
        "id": "map-1",
        "enabled": True,
        "trusted_context": "from-vicidial-ra",
        "conf_exten": "8371",
    }
    store = SimpleNamespace(list_mappings=lambda: [mapping])
    monkeypatch.setattr(
        "src.core.vicidial_store.get_vicidial_store", lambda: store
    )
    engine = Engine.__new__(Engine)
    engine._read_channel_variable_result = AsyncMock(return_value=("", False))
    session = CallSession(call_id="ari-call", caller_channel_id="ari-call")

    await Engine._hydrate_vicidial_session(
        engine,
        session,
        "ari-call",
        dialplan_context="from-internal",
        dialplan_extension="100",
    )

    assert session.external_platform is None


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
async def test_correlation_queries_zero_padded_remote_agent_users(monkeypatch):
    monkeypatch.setenv("VICI_USER", "apiuser")
    monkeypatch.setenv("VICI_PASS", "secret")
    client = VicidialApiClient(_connection())
    queried_users = []

    async def callid_info(call_id):
        return VicidialApiResult(
            success=True,
            function="callid_info",
            message="ok",
            data={
                "call_id": call_id,
                "call_type": "OUT",
                "campaign_id": "TESTCAMP",
            },
        )

    async def agent_status(user):
        queried_users.append(user)
        return VicidialApiResult(
            success=True,
            function="agent_status",
            message="ok",
            data={
                "status": "INCALL" if user == "09002" else "READY",
                "callerid": (
                    "M4050908070000012345" if user == "09002" else ""
                ),
                "campaign_id": "TESTCAMP",
            },
        )

    monkeypatch.setattr(client, "callid_info", callid_info)
    monkeypatch.setattr(client, "agent_status", agent_status)

    info, _evidence = await client.resolve_remote_agent_session(
        call_id="M4050908070000012345",
        mapping={
            "id": "map-1",
            **_mapping(),
            "user_start": "09001",
            "number_of_lines": 2,
            "static_agent_user": None,
        },
        attempts=1,
    )

    assert queried_users == ["09001", "09002"]
    assert info is not None
    assert info.agent_user == "09002"


@pytest.mark.asyncio
async def test_agent_status_correlation_uses_bounded_concurrency(monkeypatch):
    monkeypatch.setenv("VICI_USER", "apiuser")
    monkeypatch.setenv("VICI_PASS", "secret")
    client = VicidialApiClient(_connection())
    active = 0
    peak = 0

    async def callid_info(_call_id):
        return VicidialApiResult(
            success=True,
            function="callid_info",
            message="ok",
            data={
                "call_type": "OUT",
                "campaign_id": "TESTCAMP",
                "phone": "13165551212",
            },
        )

    async def agent_status(user):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return VicidialApiResult(
            success=True,
            function="agent_status",
            message="ok",
            data={
                "status": "INCALL" if user == "9001" else "READY",
                "callerid": "M4050908070000012345" if user == "9001" else "",
                "campaign_id": "TESTCAMP",
                "phone_number": "13165551212",
            },
        )

    monkeypatch.setattr(client, "callid_info", callid_info)
    monkeypatch.setattr(client, "agent_status", agent_status)

    info, _evidence = await client.resolve_remote_agent_session(
        call_id="M4050908070000012345",
        mapping={
            "id": "map-1",
            **_mapping(),
            "number_of_lines": 25,
            "static_agent_user": None,
        },
        attempts=1,
    )

    assert info is not None
    assert info.agent_user == "9001"
    assert 1 < peak <= 10


@pytest.mark.asyncio
async def test_agent_status_limit_is_shared_across_concurrent_calls(monkeypatch):
    monkeypatch.setenv("VICI_USER", "apiuser")
    monkeypatch.setenv("VICI_PASS", "secret")
    clients = [VicidialApiClient(_connection()) for _ in range(2)]
    active = 0
    peak = 0
    call_info_started = 0
    call_info_gate = asyncio.Event()

    async def callid_info(call_id):
        nonlocal call_info_started
        call_info_started += 1
        if call_info_started == len(clients):
            call_info_gate.set()
        await call_info_gate.wait()
        return VicidialApiResult(
            success=True,
            function="callid_info",
            message="ok",
            data={
                "call_id": call_id,
                "call_type": "OUT",
                "campaign_id": "TESTCAMP",
                "phone": "13165551212",
            },
        )

    async def agent_status(_user):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return VicidialApiResult(
            success=True,
            function="agent_status",
            message="ok",
            data={
                "status": "READY",
                "callerid": "",
                "campaign_id": "TESTCAMP",
                "phone_number": "13165551212",
            },
        )

    for client in clients:
        monkeypatch.setattr(client, "callid_info", callid_info)
        monkeypatch.setattr(client, "agent_status", agent_status)

    mapping = {
        "id": "map-1",
        **_mapping(),
        "number_of_lines": 15,
        "static_agent_user": None,
    }
    results = await asyncio.gather(*(
        client.resolve_remote_agent_session(
            call_id=f"M405090807000001234{index + 5}",
            mapping=mapping,
            attempts=1,
        )
        for index, client in enumerate(clients)
    ))

    assert all(info is None for info, _evidence in results)
    assert 1 < peak <= 10


@pytest.mark.asyncio
async def test_correlation_retry_budget_runs_within_hard_deadline(monkeypatch):
    monkeypatch.setenv("VICI_USER", "apiuser")
    monkeypatch.setenv("VICI_PASS", "secret")
    connection = {**_connection(), "timeout_ms": 250}
    client = VicidialApiClient(connection)
    request_count = 0

    async def callid_info(_call_id):
        nonlocal request_count
        request_count += 1
        return VicidialApiResult(
            success=False,
            function="callid_info",
            message="not ready",
        )

    monkeypatch.setattr(client, "callid_info", callid_info)
    info, evidence = await client.resolve_remote_agent_session(
        call_id="M4050908070000012345",
        mapping={"id": "map-1", **_mapping(), "static_agent_user": None},
        attempts=4,
        delay_seconds=0.2,
    )

    assert info is None
    assert request_count == 4
    assert [item["function"] for item in evidence] == ["callid_info"] * 4


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
    client = VicidialApiClient(_connection())

    async def agent_status(user):
        suffix = "8" if user == "9001" else "9"
        return VicidialApiResult(
            success=True,
            function="agent_status",
            message="ok",
            data={
                "status": "QUEUE" if user == "9001" else "INCALL",
                "callerid": f"V719022845000000000{suffix}",
                "campaign_id": "TESTCAMP",
            },
        )

    async def callid_info(call_id):
        return VicidialApiResult(
            success=True,
            function="callid_info",
            message="ok",
            data={
                "call_id": call_id,
                "call_type": "OUT",
                "campaign_id": "TESTCAMP",
            },
        )

    monkeypatch.setattr(client, "agent_status", agent_status)
    monkeypatch.setattr(client, "callid_info", callid_info)
    info, evidence = await client.resolve_remote_agent_session(
        call_id="Customer Display Name",
        mapping={"id": "map-1", **_mapping(), "number_of_lines": 2},
        attempts=1,
    )

    assert info is None
    assert len(evidence) == 4


@pytest.mark.asyncio
async def test_customer_name_scan_rejects_same_call_reported_by_multiple_users(
    monkeypatch,
):
    monkeypatch.setenv("VICI_USER", "apiuser")
    monkeypatch.setenv("VICI_PASS", "secret")
    client = VicidialApiClient(_connection())
    call_id = "V7190228450000000008"

    async def agent_status(_user):
        return VicidialApiResult(
            success=True,
            function="agent_status",
            message="ok",
            data={
                "status": "INCALL",
                "callerid": call_id,
                "campaign_id": "TESTCAMP",
            },
        )

    async def callid_info(_call_id):
        return VicidialApiResult(
            success=True,
            function="callid_info",
            message="ok",
            data={
                "call_id": call_id,
                "call_type": "OUT",
                "campaign_id": "TESTCAMP",
            },
        )

    monkeypatch.setattr(client, "agent_status", agent_status)
    monkeypatch.setattr(client, "callid_info", callid_info)
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
    retired_route = store.list_route_tombstones()[0]
    assert store.list_route_tombstones() == [
        {
            "id": retired_route["id"],
            "mapping_id": "mapping-1",
            "trusted_context": "from-vicidial-ra",
            "conf_exten": "8371",
            "trusted_endpoint": "vicidial-ra",
            "deleted_at": retired_route["deleted_at"],
        }
    ]

    assert store.delete_route_tombstone(retired_route["id"]) is True
    assert store.list_route_tombstones() == []
    assert store.delete_route_tombstone(retired_route["id"]) is False


def test_recreated_exact_route_retires_deleted_mapping_tombstone(tmp_path):
    store = VicidialStore(str(tmp_path / "vicidial.db"))
    connection = store.save_connection(
        {
            **_connection(),
            "name": "Lab",
            "username_env": "VICI_USER",
            "password_env": "VICI_PASS",
        },
        "connection-1",
    )
    store.save_mapping(_mapping(connection["id"]), "mapping-1")
    assert store.delete_mapping("mapping-1") is True
    assert len(store.list_route_tombstones()) == 1

    store.save_mapping(_mapping(connection["id"]), "mapping-2")

    assert store.list_route_tombstones() == []


def test_mapping_edits_preserve_every_superseded_generated_route(tmp_path):
    store = VicidialStore(str(tmp_path / "vicidial.db"))
    connection = store.save_connection(
        {
            **_connection(),
            "name": "Lab",
            "username_env": "VICI_USER",
            "password_env": "VICI_PASS",
        },
        "connection-1",
    )
    original = store.save_mapping(_mapping(connection["id"]), "mapping-1")

    second = store.save_mapping(
        {
            **original,
            "trusted_context": "from-vicidial-ra-two",
            "conf_exten": "8372",
        },
        "mapping-1",
    )
    store.save_mapping(
        {
            **second,
            "trusted_context": "from-vicidial-ra-three",
            "conf_exten": "8373",
        },
        "mapping-1",
    )

    assert [
        (route["mapping_id"], route["trusted_context"], route["conf_exten"])
        for route in store.list_route_tombstones()
    ] == [
        ("mapping-1", "from-vicidial-ra", "8371"),
        ("mapping-1", "from-vicidial-ra-two", "8372"),
    ]


def test_store_migrates_single_route_tombstones_without_losing_protection(tmp_path):
    db_path = str(tmp_path / "vicidial.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE vicidial_route_tombstones (
                mapping_id TEXT PRIMARY KEY,
                trusted_context TEXT NOT NULL,
                conf_exten TEXT NOT NULL,
                trusted_endpoint TEXT,
                deleted_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO vicidial_route_tombstones VALUES (?,?,?,?,?)
            """,
            (
                "mapping-legacy",
                "from-vicidial-ra-old",
                "8370",
                "vicidial-ra-old",
                "2026-07-20T00:00:00+00:00",
            ),
        )

    store = VicidialStore(db_path)

    retired_route = store.list_route_tombstones()[0]
    assert store.list_route_tombstones() == [
        {
            "id": retired_route["id"],
            "mapping_id": "mapping-legacy",
            "trusted_context": "from-vicidial-ra-old",
            "conf_exten": "8370",
            "trusted_endpoint": "vicidial-ra-old",
            "deleted_at": "2026-07-20T00:00:00+00:00",
        }
    ]


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
        result={"configuration_ready": True, "pbx_ready": True},
    )
    mapping_revision = _stored_mapping_revision(store, "mapping-1")

    store.record_real_call_verification(
        mapping_id="mapping-1",
        mapping_revision=mapping_revision,
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
        mapping_revision=mapping_revision,
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


def test_real_call_readiness_does_not_bypass_pbx_gate(tmp_path):
    store = VicidialStore(str(tmp_path / "vicidial.db"))
    connection = store.save_connection({
        **_connection(),
        "name": "Lab",
        "username_env": "VICI_USER",
        "password_env": "VICI_PASS",
    }, "connection-1")
    mapping = {**_mapping(connection["id"]), "direction": "outbound"}
    store.save_mapping(mapping, "mapping-1")
    store.record_verification(
        kind="mapping",
        record_id="mapping-1",
        result={"configuration_ready": True, "pbx_ready": False},
    )

    store.record_real_call_verification(
        mapping_id="mapping-1",
        mapping_revision=_stored_mapping_revision(store, "mapping-1"),
        direction="outbound",
        external_call_id="M4050908070000012345",
        status="AIHU",
        operation="hangup",
    )

    verification = store.get_mapping("mapping-1")["last_verification"]
    assert verification["configuration_ready"] is True
    assert verification["pbx_ready"] is False
    assert verification["real_call"]["verified"] is True
    assert verification["ready"] is False


def test_reconciled_hangup_is_delivery_evidence_not_control_readiness(tmp_path):
    store = VicidialStore(str(tmp_path / "vicidial.db"))
    connection = store.save_connection({
        **_connection(),
        "name": "Lab",
        "username_env": "VICI_USER",
        "password_env": "VICI_PASS",
    }, "connection-1")
    store.save_mapping(
        {**_mapping(connection["id"]), "direction": "outbound"},
        "mapping-1",
    )
    store.record_verification(
        kind="mapping",
        record_id="mapping-1",
        result={"configuration_ready": True, "pbx_ready": True},
    )
    mapping_revision = _stored_mapping_revision(store, "mapping-1")

    store.record_real_call_verification(
        mapping_id="mapping-1",
        mapping_revision=mapping_revision,
        direction="outbound",
        external_call_id="M4050908070000012345",
        status="XFER",
        operation="terminal_reconcile",
    )

    verification = store.get_mapping("mapping-1")["last_verification"]
    assert verification["real_calls"]["outbound"]["delivery_verified"] is True
    assert verification["real_calls"]["outbound"]["verified"] is False
    assert verification["real_call"]["verified"] is False
    assert verification["ready"] is False

    store.record_real_call_verification(
        mapping_id="mapping-1",
        mapping_revision=mapping_revision,
        direction="outbound",
        external_call_id="M4050908070000012346",
        status="AIHU",
        operation="hangup",
    )
    verification = store.get_mapping("mapping-1")["last_verification"]
    assert verification["real_calls"]["outbound"]["verified"] is True
    assert verification["ready"] is True


def test_real_call_evidence_rejects_older_mapping_revision(tmp_path):
    store = VicidialStore(str(tmp_path / "vicidial.db"))
    connection = store.save_connection({
        **_connection(),
        "name": "Lab",
        "username_env": "VICI_USER",
        "password_env": "VICI_PASS",
    }, "connection-1")
    original = {**_mapping(connection["id"]), "direction": "outbound"}
    store.save_mapping(original, "mapping-1")
    admitted_revision = _stored_mapping_revision(store, "mapping-1")

    store.save_mapping(
        {**original, "campaign_id": "CHANGED"},
        "mapping-1",
    )

    assert store.record_real_call_verification(
        mapping_id="mapping-1",
        mapping_revision=admitted_revision,
        direction="outbound",
        external_call_id="M4050908070000012345",
        status="AIHU",
        operation="hangup",
    ) is False
    assert store.get_mapping("mapping-1")["last_verification"] is None

    assert store.record_real_call_verification(
        mapping_id="mapping-1",
        mapping_revision=_stored_mapping_revision(store, "mapping-1"),
        direction="outbound",
        external_call_id="M4050908070000012346",
        status="AIHU",
        operation="hangup",
    ) is True
    assert store.get_mapping("mapping-1")["last_verification"]["real_calls"][
        "outbound"
    ]["external_call_id"] == "M4050908070000012346"


def test_readiness_merges_serialize_across_store_instances(tmp_path):
    db_path = str(tmp_path / "vicidial.db")
    verification_store = VicidialStore(db_path)
    call_store = VicidialStore(db_path)
    connection = verification_store.save_connection({
        **_connection(),
        "name": "Lab",
        "username_env": "VICI_USER",
        "password_env": "VICI_PASS",
    }, "connection-1")
    mapping = {**_mapping(connection["id"]), "direction": "outbound"}
    verification_store.save_mapping(mapping, "mapping-1")
    mapping_revision = _stored_mapping_revision(verification_store, "mapping-1")

    update_reached = threading.Event()
    release_update = threading.Event()
    errors = []
    original_connection = verification_store._connection

    def traced_connection():
        conn = original_connection()

        def trace(statement):
            if statement.strip().upper().startswith("UPDATE VICIDIAL_MAPPINGS"):
                update_reached.set()
                if not release_update.wait(timeout=2):
                    raise RuntimeError("timed out waiting to release verification update")

        conn.set_trace_callback(trace)
        return conn

    verification_store._connection = traced_connection

    def record_verification():
        try:
            verification_store.record_mapping_verification(
                mapping_id="mapping-1",
                mapping_revision=mapping_revision,
                result={"configuration_ready": True, "pbx_ready": True},
            )
        except Exception as exc:  # pragma: no cover - asserted through errors
            errors.append(exc)

    def record_call():
        try:
            call_store.record_real_call_verification(
                mapping_id="mapping-1",
                mapping_revision=mapping_revision,
                direction="outbound",
                external_call_id="M4050908070000012345",
                status="AIHU",
                operation="hangup",
            )
        except Exception as exc:  # pragma: no cover - asserted through errors
            errors.append(exc)

    verification_thread = threading.Thread(target=record_verification)
    call_thread = threading.Thread(target=record_call)
    verification_thread.start()
    assert update_reached.wait(timeout=2)
    call_thread.start()
    call_thread.join(timeout=0.05)
    assert call_thread.is_alive()
    release_update.set()
    verification_thread.join(timeout=2)
    call_thread.join(timeout=2)

    assert not verification_thread.is_alive()
    assert not call_thread.is_alive()
    assert errors == []
    verification = verification_store.get_mapping("mapping-1")["last_verification"]
    assert verification["configuration_ready"] is True
    assert verification["pbx_ready"] is True
    assert verification["real_calls"]["outbound"]["external_call_id"] == (
        "M4050908070000012345"
    )
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


def test_store_persists_pending_dnc_actions_across_instances(tmp_path):
    db_path = str(tmp_path / "vicidial.db")
    first_store = VicidialStore(db_path)
    queued = first_store.enqueue_pending_action(
        operation="dnc",
        connection={**_connection(), "id": "connection-1"},
        payload={
            "phone_number": "13165551212",
            "campaign_id": "TESTCAMP",
            "ignored": "not persisted",
        },
        call_id="ari-dnc-durable",
        external_call_id="M4050908070000012345",
    )

    second_store = VicidialStore(db_path)
    duplicate = second_store.enqueue_pending_action(
        operation="dnc",
        connection={**_connection(), "id": "connection-1"},
        payload={
            "phone_number": "13165551212",
            "campaign_id": "TESTCAMP",
        },
        call_id="ari-dnc-durable",
    )
    due = second_store.list_pending_actions()
    assert len(due) == 1
    assert due[0]["id"] == queued["id"]
    assert duplicate["id"] == queued["id"]
    assert due[0]["operation"] == "dnc"
    assert due[0]["connection"]["id"] == "connection-1"
    assert due[0]["connection"]["username_env"] == "VICI_USER"
    assert due[0]["payload"] == {
        "phone_number": "13165551212",
        "campaign_id": "TESTCAMP",
    }

    assert first_store.complete_pending_action(queued["id"]) is True
    assert first_store.complete_pending_action(queued["id"]) is True
    assert VicidialStore(db_path).list_pending_actions() == []


def test_store_reactivates_a_canceled_duplicate_action(tmp_path):
    store = VicidialStore(str(tmp_path / "vicidial.db"))
    connection = {**_connection(), "id": "connection-1"}
    payload = {
        "lead_id": "456",
        "campaign_id": "TESTCAMP",
        "callback_datetime": "2026-07-21 10:00:00",
        "callback_type": "ANYONE",
    }
    queued = store.enqueue_pending_action(
        operation="callback",
        connection=connection,
        payload=payload,
        call_id="ari-reactivate",
    )
    assert store.cancel_pending_action(queued["id"]) is True

    reactivated = store.enqueue_pending_action(
        operation="callback",
        connection=connection,
        payload=payload,
        call_id="ari-reactivate",
    )

    assert reactivated["id"] == queued["id"]
    assert reactivated["status"] == "pending"
    assert reactivated["workflow_completed"] is False
    assert reactivated["attempt_count"] == 0
    assert [item["id"] for item in store.list_pending_actions()] == [queued["id"]]


def test_store_does_not_reactivate_a_completed_duplicate_action(tmp_path):
    store = VicidialStore(str(tmp_path / "vicidial.db"))
    kwargs = {
        "operation": "dnc",
        "connection": {**_connection(), "id": "connection-1"},
        "payload": {
            "phone_number": "13165551212",
            "campaign_id": "TESTCAMP",
        },
        "call_id": "ari-completed",
    }
    queued = store.enqueue_pending_action(**kwargs)
    assert store.complete_pending_action(queued["id"]) is True

    duplicate = store.enqueue_pending_action(**kwargs)

    assert duplicate["id"] == queued["id"]
    assert duplicate["status"] == "completed"
    assert store.list_pending_actions() == []


def test_store_persists_sanitized_terminal_retry_identity(tmp_path):
    store = VicidialStore(str(tmp_path / "vicidial.db"))
    queued = store.enqueue_pending_action(
        operation="dnc",
        connection={**_connection(), "id": "connection-1"},
        payload={
            "phone_number": "13165551212",
            "campaign_id": "TESTCAMP",
            "requested_status": "DNCLST",
            "retry_terminal": {
                "semantic": "ai_hangup",
                "operation_reason": "hangup-tool",
                "mapping_revision": "revision-1",
                "session": {
                    "external_call_id": "M4050908070000012345",
                    "mapping_id": "map-1",
                    "agent_user": "9001",
                    "campaign_id": "TESTCAMP",
                    "phone_number": "13165551212",
                    "metadata": {"secret": "must-not-persist"},
                },
            },
        },
        call_id="ari-terminal-durable",
        external_call_id="M4050908070000012345",
    )

    terminal = queued["payload"]["retry_terminal"]
    assert terminal["mapping_revision"] == "revision-1"
    assert terminal["session"]["agent_user"] == "9001"
    assert terminal["session"]["external_call_id"] == "M4050908070000012345"
    assert "metadata" not in terminal["session"]


@pytest.mark.asyncio
async def test_ai_failure_terminal_retry_is_durable_and_replayable(
    tmp_path, monkeypatch
):
    store = VicidialStore(str(tmp_path / "vicidial.db"))
    monkeypatch.setattr(
        "src.core.vicidial_store.get_vicidial_store", lambda: store
    )
    session = CallSession(
        call_id="ari-provider-retry",
        caller_channel_id="ari-provider-retry",
    )
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
        campaign_id="TESTCAMP",
    ).to_dict()
    session.external_mapping = _mapping()
    session.external_mapping_revision = "revision-1"
    session.external_connection = _connection()

    assert Engine._queue_vicidial_terminal_retry(
        SimpleNamespace(),
        session,
        semantic="ai_failure",
        operation_reason="provider-start-failed",
    ) is True
    queued = store.list_pending_actions()
    assert len(queued) == 1
    action = queued[0]
    assert action["operation"] == "terminal"
    assert action["payload"]["requested_status"] == "AIFAIL"
    assert action["payload"]["retry_terminal"]["semantic"] == "ai_failure"

    finalizer = AsyncMock(return_value=True)
    engine = SimpleNamespace(
        session_store=_SessionStore(session),
        _save_session=AsyncMock(),
        _finalize_vicidial_call_locked=finalizer,
        _execute_pending_vicidial_workflow=AsyncMock(),
    )

    await Engine._retry_pending_vicidial_action(engine, action, store)

    engine._execute_pending_vicidial_workflow.assert_not_awaited()
    finalizer.assert_awaited_once_with(
        session,
        semantic="ai_failure",
        operation_reason="provider-start-failed",
    )
    assert store.get_pending_action(action["id"])["status"] == "completed"


def test_store_migrates_pending_actions_created_before_workflow_state(tmp_path):
    db_path = str(tmp_path / "vicidial.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE vicidial_pending_actions (
                id TEXT PRIMARY KEY,
                dedupe_key TEXT NOT NULL UNIQUE,
                operation TEXT NOT NULL,
                connection_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                call_id TEXT,
                external_call_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            )
            """
        )

    VicidialStore(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(vicidial_pending_actions)")
        }
    assert "workflow_completed" in columns


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
async def test_vicidial_transfer_cannot_race_terminal_finalization(monkeypatch):
    session = CallSession(call_id="ari-transfer-race", caller_channel_id="ari-transfer-race")
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
    ).to_dict()
    session.external_mapping = _mapping()
    session.external_connection = _connection()
    store = _SessionStore(session)
    context = ToolExecutionContext(call_id=session.call_id, session_store=store)
    lock = vicidial_lifecycle_lock(session.call_id)

    class Client:
        def __init__(self, _connection):
            pass

        async def call_control(self, *_args, **_kwargs):
            raise AssertionError("transfer control must not run after finalization starts")

    monkeypatch.setattr("src.tools.telephony.vicidial.VicidialApiClient", Client)
    await lock.acquire()
    try:
        pending = asyncio.create_task(
            execute_vicidial_transfer(
                context=context,
                destination={
                    "type": "vicidial_ingroup",
                    "target": "SALESLINE",
                },
            )
        )
        await asyncio.sleep(0)
        assert pending.done() is False
        session.external_finalizing = True
    finally:
        lock.release()

    assert await pending == {
        "status": "failed",
        "message": "The VICIdial terminal workflow has already started",
    }
    assert session.external_finalized is False
    assert bool(getattr(session, "transfer_active", False)) is False


@pytest.mark.asyncio
async def test_vicidial_transfer_commits_pending_dnc_before_call_control(monkeypatch):
    session = CallSession(call_id="ari-dnc-transfer", caller_channel_id="ari-dnc-transfer")
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
        phone_number="13165551212",
    ).to_dict()
    session.external_mapping = {
        **_mapping(),
        "dispositions": {**_mapping()["dispositions"], "dnc": "DNC"},
    }
    session.external_connection = _connection()
    store = _SessionStore(session)
    context = ToolExecutionContext(
        call_id=session.call_id,
        caller_channel_id=session.caller_channel_id,
        session_store=store,
        ari_client=SimpleNamespace(),
    )
    operations = []

    class Client:
        def __init__(self, _connection):
            pass

        async def add_dnc_phone(self, **_kwargs):
            operations.append("dnc")
            return VicidialApiResult(True, "add_dnc_phone", "SUCCESS")

        async def call_control(self, _info, **_kwargs):
            operations.append("transfer")
            return VicidialApiResult(True, "ra_call_control", "SUCCESS: transferred")

    monkeypatch.setattr("src.tools.telephony.vicidial.VicidialApiClient", Client)
    selected = await SetCallDispositionTool().execute(
        {"disposition": "dnc"}, context
    )
    transferred = await execute_vicidial_transfer(
        context=context,
        destination={
            "type": "vicidial_ingroup",
            "target": "SALESLINE",
            "status": "AIXFR",
        },
    )

    assert selected["status"] == "success"
    assert transferred["status"] == "success"
    assert operations == ["dnc", "transfer"]
    assert session.external_disposition_payload["workflow_committed"] is True


@pytest.mark.asyncio
async def test_vicidial_transfer_commits_pending_callback_before_call_control(
    monkeypatch,
):
    session = CallSession(
        call_id="ari-callback-transfer", caller_channel_id="ari-callback-transfer"
    )
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
    ).to_dict()
    session.external_mapping = _mapping()
    session.external_connection = _connection()
    session.external_requested_disposition = "CALLBK"
    session.external_disposition_label = "callback"
    session.external_disposition_payload = {
        "lead_id": "456",
        "campaign_id": "TESTCAMP",
        "callback_datetime": "2026-07-21 10:00:00",
        "callback_type": "ANYONE",
    }
    store = _SessionStore(session)
    context = ToolExecutionContext(
        call_id=session.call_id,
        caller_channel_id=session.caller_channel_id,
        session_store=store,
        ari_client=SimpleNamespace(),
    )
    operations = []

    async def commit(pending_session):
        assert pending_session is session
        operations.append("callback")
        pending_session.external_disposition_payload["workflow_committed"] = True
        return True

    class Client:
        def __init__(self, _connection):
            pass

        async def call_control(self, _info, **_kwargs):
            operations.append("transfer")
            return VicidialApiResult(True, "ra_call_control", "SUCCESS: transferred")

    monkeypatch.setattr(
        "src.tools.telephony.vicidial.commit_vicidial_disposition_workflow",
        commit,
    )
    monkeypatch.setattr("src.tools.telephony.vicidial.VicidialApiClient", Client)
    result = await execute_vicidial_transfer(
        context=context,
        destination={
            "type": "vicidial_ingroup",
            "target": "SALESLINE",
            "status": "AIXFR",
        },
    )

    assert result["status"] == "success"
    assert operations == ["callback", "transfer"]
    assert session.external_disposition_payload["workflow_committed"] is True


@pytest.mark.asyncio
async def test_pending_vicidial_dnc_cannot_be_overwritten():
    session = CallSession(call_id="ari-dnc-lock", caller_channel_id="ari-dnc-lock")
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
        phone_number="13165551212",
    ).to_dict()
    session.external_mapping = {
        **_mapping(),
        "dispositions": {**_mapping()["dispositions"], "dnc": "DNC"},
    }
    session.external_connection = _connection()
    store = _SessionStore(session)
    context = ToolExecutionContext(call_id=session.call_id, session_store=store)

    selected = await SetCallDispositionTool().execute(
        {"disposition": "dnc"}, context
    )
    replacement = await SetCallDispositionTool().execute(
        {"disposition": "sale"}, context
    )

    assert selected["status"] == "success"
    assert replacement == {
        "status": "failed",
        "message": "A do-not-call request is already selected and cannot be replaced",
    }
    assert session.external_requested_disposition == "DNC"
    assert session.external_disposition_label == "dnc"
    assert session.external_disposition_payload["phone_number"] == "13165551212"


@pytest.mark.asyncio
async def test_pending_vicidial_callback_cannot_be_overwritten_by_classification():
    session = CallSession(
        call_id="ari-callback-lock", caller_channel_id="ari-callback-lock"
    )
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
        lead_id="456",
        campaign_id="TESTCAMP",
    ).to_dict()
    session.external_mapping = {
        **_mapping(),
        "dispositions": {**_mapping()["dispositions"], "callback": "CALLBK"},
    }
    session.external_connection = _connection()
    store = _SessionStore(session)
    context = ToolExecutionContext(call_id=session.call_id, session_store=store)

    selected = await SetCallDispositionTool().execute(
        {
            "disposition": "callback",
            "callback_datetime": "2026-07-21T10:00:00-07:00",
        },
        context,
    )
    replacement = await SetCallDispositionTool().execute(
        {"disposition": "sale"}, context
    )

    assert selected["status"] == "success"
    assert replacement == {
        "status": "failed",
        "message": (
            "A callback is already selected and cannot be replaced by another disposition"
        ),
    }
    assert session.external_requested_disposition == "CALLBK"
    assert session.external_disposition_label == "callback"
    assert session.external_disposition_payload["lead_id"] == "456"


@pytest.mark.asyncio
async def test_committed_callback_cannot_be_changed_or_replaced_with_dnc():
    session = CallSession(
        call_id="ari-committed-callback", caller_channel_id="ari-committed-callback"
    )
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
        lead_id="456",
        campaign_id="TESTCAMP",
        phone_number="13165551212",
    ).to_dict()
    session.external_mapping = {
        **_mapping(),
        "dispositions": {
            **_mapping()["dispositions"],
            "callback": "CALLBK",
            "dnc": "DNC",
        },
    }
    session.external_connection = _connection()
    session.external_requested_disposition = "CALLBK"
    session.external_disposition_label = "callback"
    session.external_disposition_payload = {
        "lead_id": "456",
        "campaign_id": "TESTCAMP",
        "callback_datetime": "2026-07-21 10:00:00",
        "callback_type": "ANYONE",
        "workflow_committed": True,
    }
    original_payload = dict(session.external_disposition_payload)
    context = ToolExecutionContext(
        call_id=session.call_id, session_store=_SessionStore(session)
    )

    callback_result = await SetCallDispositionTool().execute(
        {
            "disposition": "callback",
            "callback_datetime": "2026-07-22T10:00:00-07:00",
        },
        context,
    )
    dnc_result = await SetCallDispositionTool().execute(
        {"disposition": "dnc"}, context
    )

    assert callback_result["status"] == "success"
    assert "already committed" in callback_result["message"]
    assert dnc_result == {
        "status": "failed",
        "message": (
            "A scheduled callback is already committed and cannot be replaced "
            "with DNC on this call."
        ),
    }
    assert session.external_disposition_label == "callback"
    assert session.external_disposition_payload == original_payload


@pytest.mark.asyncio
async def test_dnc_supersedes_and_cancels_a_queued_callback(monkeypatch):
    session = CallSession(
        call_id="ari-callback-to-dnc", caller_channel_id="ari-callback-to-dnc"
    )
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
        lead_id="456",
        campaign_id="TESTCAMP",
        phone_number="13165551212",
    ).to_dict()
    session.external_mapping = {
        **_mapping(),
        "dispositions": {
            **_mapping()["dispositions"],
            "callback": "CBACK",
            "dnc": "DNCLST",
        },
    }
    session.external_connection = _connection()
    session.external_requested_disposition = "CBACK"
    session.external_disposition_label = "callback"
    session.external_disposition_payload = {
        "lead_id": "456",
        "workflow_queued": True,
        "workflow_queue_id": "callback-action-1",
    }
    store = _SessionStore(session)
    context = ToolExecutionContext(call_id=session.call_id, session_store=store)
    canceled = []
    monkeypatch.setattr(
        "src.core.vicidial_store.get_vicidial_store",
        lambda: SimpleNamespace(
            get_pending_action=lambda _action_id: {
                "status": "pending",
                "workflow_completed": False,
            },
            cancel_pending_action=lambda action_id: canceled.append(action_id) or True
        ),
    )

    result = await SetCallDispositionTool().execute({"disposition": "dnc"}, context)

    assert result["status"] == "success"
    assert canceled == ["callback-action-1"]
    assert session.external_disposition_label == "dnc"
    assert "workflow_queue_id" not in session.external_disposition_payload


@pytest.mark.asyncio
async def test_dnc_cannot_supersede_a_callback_with_completed_queued_workflow(
    monkeypatch,
):
    session = CallSession(
        call_id="ari-committed-queued-callback",
        caller_channel_id="ari-committed-queued-callback",
    )
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
        lead_id="456",
        campaign_id="TESTCAMP",
        phone_number="13165551212",
    ).to_dict()
    session.external_mapping = {
        **_mapping(),
        "dispositions": {
            **_mapping()["dispositions"],
            "callback": "CALLBK",
            "dnc": "DNC",
        },
    }
    session.external_connection = _connection()
    session.external_requested_disposition = "CALLBK"
    session.external_disposition_label = "callback"
    session.external_disposition_payload = {
        "lead_id": "456",
        "workflow_queued": True,
        "workflow_queue_id": "callback-action-committed",
    }
    completed = []
    monkeypatch.setattr(
        "src.core.vicidial_store.get_vicidial_store",
        lambda: SimpleNamespace(
            get_pending_action=lambda _action_id: {
                "status": "pending",
                "workflow_completed": True,
            },
            complete_pending_action=lambda action_id: completed.append(action_id),
        ),
    )
    context = ToolExecutionContext(
        call_id=session.call_id, session_store=_SessionStore(session)
    )

    result = await SetCallDispositionTool().execute(
        {"disposition": "dnc"}, context
    )

    assert result["status"] == "failed"
    assert "already committed" in result["message"]
    assert completed == []
    assert session.external_disposition_label == "callback"


@pytest.mark.asyncio
async def test_compliance_dispositions_use_lifecycle_status_overrides():
    session = CallSession(
        call_id="ari-status-override", caller_channel_id="ari-status-override"
    )
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
        campaign_id="TESTCAMP",
        phone_number="13165551212",
    ).to_dict()
    session.external_mapping = {
        **_mapping(),
        "dispositions": {**_mapping()["dispositions"], "dnc": "DNCOLD"},
        "statuses": {"dnc": "DNCNEW"},
    }
    store = _SessionStore(session)
    context = ToolExecutionContext(call_id=session.call_id, session_store=store)

    result = await SetCallDispositionTool().execute({"disposition": "dnc"}, context)

    assert result["vicidial_status"] == "DNCNEW"
    assert session.external_requested_disposition == "DNCNEW"


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
async def test_disposition_cannot_race_terminal_finalization():
    session = CallSession(call_id="ari-race", caller_channel_id="ari-race")
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
    ).to_dict()
    session.external_mapping = _mapping()
    session.external_connection = _connection()
    store = _SessionStore(session)
    context = ToolExecutionContext(call_id=session.call_id, session_store=store)
    lock = vicidial_lifecycle_lock(session.call_id)

    await lock.acquire()
    try:
        pending = asyncio.create_task(
            SetCallDispositionTool().execute({"disposition": "sale"}, context)
        )
        await asyncio.sleep(0)
        assert pending.done() is False
        session.external_finalizing = True
    finally:
        lock.release()

    result = await pending
    assert result == {
        "status": "failed",
        "message": "The VICIdial terminal workflow has already started",
    }
    assert session.external_requested_disposition is None
    assert session.external_events == []


def test_disposition_tool_schema_mandates_dnc_compliance_action():
    definition = SetCallDispositionTool().definition

    assert "MUST call this tool immediately" in definition.description
    assert "never refuse" in definition.description
    disposition = next(param for param in definition.parameters if param.name == "disposition")
    assert "remove-my-number request use dnc" in disposition.description


def test_disposition_tool_schema_prioritizes_native_vicidial_callback():
    definition = SetCallDispositionTool().definition

    assert "MUST use this tool with disposition='callback'" in definition.description
    assert "Do not use a calendar" in definition.description
    callback_parameter = next(
        param for param in definition.parameters if param.name == "callback_datetime"
    )
    assert "date, time, and timezone" in callback_parameter.description


@pytest.mark.asyncio
async def test_inbound_campaign_dnc_uses_mapped_dialing_campaign(monkeypatch):
    session = CallSession(call_id="ari-dnc", caller_channel_id="ari-dnc")
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
        campaign_id="TESTINGROUP",
        phone_number="13165551212",
        direction="inbound",
        metadata={"agent_status": {"campaign_id": "TESTCAMP"}},
    ).to_dict()
    session.external_mapping = {
        **_mapping(),
        "dnc_scope": "campaign",
        "dispositions": {**_mapping()["dispositions"], "dnc": "DNC"},
    }
    session.external_connection = _connection()
    store = _SessionStore(session)
    context = ToolExecutionContext(call_id="ari-dnc", session_store=store)

    class Client:
        def __init__(self, _connection):
            pass

        async def add_dnc_phone(self, **kwargs):
            assert kwargs == {
                "phone_number": "13165551212",
                "campaign_id": "TESTCAMP",
            }
            return VicidialApiResult(True, "add_dnc_phone", "SUCCESS")

    monkeypatch.setattr("src.tools.telephony.vicidial.VicidialApiClient", Client)
    result = await SetCallDispositionTool().execute({"disposition": "dnc"}, context)

    assert result["status"] == "success"
    assert session.external_disposition_payload["campaign_id"] == "TESTCAMP"
    assert await commit_vicidial_disposition_workflow(session) is True
    assert session.external_disposition_payload["workflow_committed"] is True


@pytest.mark.asyncio
async def test_dnc_workflow_retries_idempotent_write(monkeypatch):
    session = CallSession(call_id="ari-dnc-retry", caller_channel_id="ari-dnc-retry")
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
        campaign_id="TESTCAMP",
        phone_number="13165551212",
    ).to_dict()
    session.external_mapping = {
        **_mapping(),
        "dnc_scope": "campaign",
        "dispositions": {**_mapping()["dispositions"], "dnc": "DNC"},
    }
    session.external_connection = _connection()
    session.external_requested_disposition = "DNC"
    session.external_disposition_label = "dnc"
    session.external_disposition_payload = {
        "phone_number": "13165551212",
        "campaign_id": "TESTCAMP",
    }
    attempts = 0

    class Client:
        def __init__(self, _connection):
            pass

        async def add_dnc_phone(self, **_kwargs):
            nonlocal attempts
            attempts += 1
            return VicidialApiResult(
                attempts == 3,
                "add_dnc_phone",
                "SUCCESS" if attempts == 3 else "temporary failure",
            )

    monkeypatch.setattr("src.tools.telephony.vicidial.VicidialApiClient", Client)
    monkeypatch.setattr(
        "src.tools.telephony.vicidial.DNC_RETRY_DELAY_SECONDS", 0
    )

    assert await commit_vicidial_disposition_workflow(session) is True
    assert attempts == 3
    assert [event["attempt"] for event in session.external_events] == [1, 2, 3]
    assert session.external_disposition_payload["workflow_committed"] is True


@pytest.mark.asyncio
async def test_exhausted_dnc_writes_are_durably_queued(monkeypatch):
    session = CallSession(call_id="ari-dnc-queued", caller_channel_id="ari-dnc-queued")
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
        campaign_id="TESTCAMP",
        phone_number="13165551212",
    ).to_dict()
    session.external_mapping = _mapping()
    session.external_connection = {**_connection(), "id": "connection-1"}
    session.external_requested_disposition = "DNC"
    session.external_disposition_label = "dnc"
    session.external_disposition_payload = {
        "phone_number": "13165551212",
        "campaign_id": "TESTCAMP",
    }
    attempts = 0
    queued = {}

    class Client:
        def __init__(self, _connection):
            pass

        async def add_dnc_phone(self, **_kwargs):
            nonlocal attempts
            attempts += 1
            return VicidialApiResult(
                False,
                "add_dnc_phone",
                "temporary failure",
                error_code="timeout",
            )

    class Store:
        def enqueue_pending_action(self, **kwargs):
            queued.update(kwargs)
            return {"id": "action-1", "status": "pending"}

    monkeypatch.setattr("src.tools.telephony.vicidial.VicidialApiClient", Client)
    monkeypatch.setattr("src.tools.telephony.vicidial.DNC_RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(
        "src.core.vicidial_store.get_vicidial_store", lambda: Store()
    )

    assert await commit_vicidial_disposition_workflow(session) is False
    assert attempts == 3
    assert queued["operation"] == "dnc"
    assert queued["connection"]["id"] == "connection-1"
    assert queued["payload"]["phone_number"] == "13165551212"
    assert queued["call_id"] == session.call_id
    assert session.external_disposition_payload["workflow_queued"] is True
    assert session.external_disposition_payload["workflow_queue_id"] == "action-1"
    assert session.external_events[-1]["operation"] == "dnc_queue"
    assert session.external_events[-1]["success"] is True


@pytest.mark.asyncio
async def test_engine_retries_and_completes_queued_dnc(monkeypatch):
    action = {
        "id": "action-1",
        "operation": "dnc",
        "connection": {**_connection(), "id": "connection-1"},
        "payload": {
            "phone_number": "13165551212",
            "campaign_id": "TESTCAMP",
            "retry_terminal": {
                "semantic": "ai_hangup",
                "operation_reason": "hangup-tool",
            },
        },
        "call_id": "ari-dnc-queued",
        "external_call_id": "M4050908070000012345",
        "attempt_count": 2,
        "status": "pending",
    }

    class Store:
        def __init__(self):
            self.completed = []
            self.workflow_completed = []

        def list_pending_actions(self, **_kwargs):
            return [action]

        def get_pending_action(self, action_id):
            assert action_id == action["id"]
            return action

        def get_connection(self, connection_id):
            assert connection_id == "connection-1"
            return action["connection"]

        def complete_pending_action(self, action_id):
            self.completed.append(action_id)
            return True

        def mark_pending_action_workflow_completed(self, action_id):
            self.workflow_completed.append(action_id)
            return True

        def retry_pending_action(self, *_args, **_kwargs):
            raise AssertionError("successful DNC retry must not be deferred")

    store = Store()

    monkeypatch.setattr("src.core.vicidial_store.get_vicidial_store", lambda: store)

    session = CallSession(
        call_id="ari-dnc-queued", caller_channel_id="ari-dnc-queued"
    )
    session.external_platform = "vicidial"
    session.external_disposition_label = "dnc"
    session.external_disposition_payload = {
        "phone_number": "13165551212",
        "campaign_id": "TESTCAMP",
        "workflow_queued": True,
        "workflow_queue_id": "action-1",
    }
    engine = SimpleNamespace(
        session_store=_SessionStore(session),
        _save_session=AsyncMock(),
        _finalize_vicidial_call_locked=AsyncMock(return_value=True),
        _execute_pending_vicidial_workflow=AsyncMock(return_value=True),
    )
    engine._retry_pending_vicidial_action = (
        lambda pending_action, pending_store: Engine._retry_pending_vicidial_action(
            engine, pending_action, pending_store
        )
    )

    assert await Engine._retry_pending_vicidial_actions(engine) == 1
    assert store.workflow_completed == ["action-1"]
    assert store.completed == ["action-1"]
    assert session.external_disposition_payload["workflow_committed"] is True
    assert session.external_disposition_payload["workflow_queued"] is False
    engine._finalize_vicidial_call_locked.assert_awaited_once_with(
        session,
        semantic="ai_hangup",
        operation_reason="hangup-tool",
    )


@pytest.mark.asyncio
async def test_pending_action_keeps_captured_endpoint_when_connection_is_repointed():
    captured_connection = {
        **_connection(),
        "id": "connection-1",
        "agent_api_url": "http://vicidial.test/agc/api.php",
        "non_agent_api_url": "http://vicidial.test/vicidial/non_agent_api.php",
    }
    action = {
        "id": "action-captured-endpoint",
        "operation": "dnc",
        "connection": captured_connection,
        "payload": {
            "phone_number": "13165551212",
            "campaign_id": "TESTCAMP",
        },
        "call_id": "ari-captured-endpoint",
        "status": "pending",
    }
    repointed_connection = {
        **captured_connection,
        "base_url": "http://other-vicidial.test",
        "agent_api_url": "http://other-vicidial.test/agc/api.php",
        "non_agent_api_url": "http://other-vicidial.test/vicidial/non_agent_api.php",
        "username_env": "OTHER_VICI_USER",
        "password_env": "OTHER_VICI_PASS",
    }

    class Store:
        def get_connection(self, connection_id):
            assert connection_id == "connection-1"
            return repointed_connection

        def mark_pending_action_workflow_completed(self, _action_id):
            return True

        def complete_pending_action(self, _action_id):
            return True

    engine = SimpleNamespace(
        _execute_pending_vicidial_workflow=AsyncMock(return_value=True),
        session_store=_SessionStore(
            CallSession(
                call_id="ari-captured-endpoint",
                caller_channel_id="ari-captured-endpoint",
            )
        ),
    )

    await Engine._retry_pending_vicidial_action(engine, action, Store())

    engine._execute_pending_vicidial_workflow.assert_awaited_once_with(
        action, captured_connection
    )


@pytest.mark.asyncio
async def test_pending_action_accepts_credential_rotation_on_same_endpoint():
    captured_connection = {**_connection(), "id": "connection-1"}
    action = {
        "id": "action-rotated-credentials",
        "operation": "dnc",
        "connection": captured_connection,
        "payload": {
            "phone_number": "13165551212",
            "campaign_id": "TESTCAMP",
        },
        "call_id": "ari-rotated-credentials",
        "status": "pending",
    }
    rotated_connection = {
        **captured_connection,
        "username_env": "ROTATED_VICI_USER",
        "password_env": "ROTATED_VICI_PASS",
    }

    class Store:
        def get_connection(self, _connection_id):
            return rotated_connection

        def mark_pending_action_workflow_completed(self, _action_id):
            return True

        def complete_pending_action(self, _action_id):
            return True

    engine = SimpleNamespace(
        _execute_pending_vicidial_workflow=AsyncMock(return_value=True),
        session_store=_SessionStore(
            CallSession(
                call_id="ari-rotated-credentials",
                caller_channel_id="ari-rotated-credentials",
            )
        ),
    )

    await Engine._retry_pending_vicidial_action(engine, action, Store())

    replay_connection = (
        engine._execute_pending_vicidial_workflow.await_args.args[1]
    )
    assert replay_connection["base_url"] == captured_connection["base_url"]
    assert replay_connection["username_env"] == "ROTATED_VICI_USER"
    assert replay_connection["password_env"] == "ROTATED_VICI_PASS"


@pytest.mark.asyncio
async def test_pending_action_stays_open_until_terminal_retry_succeeds(monkeypatch):
    action = {
        "id": "action-terminal-1",
        "operation": "callback",
        "connection": {**_connection(), "id": "connection-1"},
        "payload": {
            "lead_id": "456",
            "campaign_id": "TESTCAMP",
            "callback_datetime": "2026-07-21 10:00:00",
            "callback_type": "ANYONE",
            "retry_terminal": {
                "semantic": "ai_hangup",
                "operation_reason": "hangup-tool",
            },
        },
        "call_id": "ari-callback-terminal",
        "workflow_completed": True,
        "attempt_count": 1,
        "status": "pending",
    }

    class Store:
        def __init__(self):
            self.completed = []
            self.retried = []

        def list_pending_actions(self, **_kwargs):
            return [action]

        def get_pending_action(self, action_id):
            assert action_id == action["id"]
            return action

        def get_connection(self, _connection_id):
            return action["connection"]

        def complete_pending_action(self, action_id):
            self.completed.append(action_id)
            return True

        def retry_pending_action(self, action_id, **kwargs):
            self.retried.append((action_id, kwargs))
            return True

    store = Store()
    monkeypatch.setattr("src.core.vicidial_store.get_vicidial_store", lambda: store)
    session = CallSession(
        call_id="ari-callback-terminal",
        caller_channel_id="ari-callback-terminal",
    )
    session.external_platform = "vicidial"
    session.external_disposition_label = "callback"
    session.external_disposition_payload = {"workflow_queue_id": action["id"]}
    finalizer = AsyncMock(return_value=False)
    engine = SimpleNamespace(
        session_store=_SessionStore(session),
        _save_session=AsyncMock(),
        _finalize_vicidial_call_locked=finalizer,
        _execute_pending_vicidial_workflow=AsyncMock(),
    )
    engine._retry_pending_vicidial_action = (
        lambda pending_action, pending_store: Engine._retry_pending_vicidial_action(
            engine, pending_action, pending_store
        )
    )

    assert await Engine._retry_pending_vicidial_actions(engine) == 0
    assert store.completed == []
    assert store.retried[0][0] == action["id"]
    assert "terminal retry was not confirmed" in store.retried[0][1]["error"]

    finalizer.return_value = True
    store.retried.clear()
    assert await Engine._retry_pending_vicidial_actions(engine) == 1
    assert store.completed == [action["id"]]
    assert store.retried == []


@pytest.mark.asyncio
async def test_missing_session_is_reconstructed_for_terminal_retry(monkeypatch):
    action = {
        "id": "action-reconstructed-1",
        "operation": "dnc",
        "connection": {**_connection(), "id": "connection-1"},
        "payload": {
            "phone_number": "13165551212",
            "campaign_id": "TESTCAMP",
            "requested_status": "DNCLST",
            "retry_terminal": {
                "semantic": "ai_hangup",
                "operation_reason": "hangup-tool",
                "mapping_revision": "revision-1",
                "session": {
                    "external_call_id": "M4050908070000012345",
                    "mapping_id": "map-1",
                    "agent_user": "9001",
                    "campaign_id": "TESTCAMP",
                    "phone_number": "13165551212",
                    "direction": "outbound",
                    "resolution_source": "callid_info",
                },
            },
        },
        "call_id": "ari-missing-session",
        "external_call_id": "M4050908070000012345",
        "workflow_completed": True,
        "attempt_count": 0,
        "status": "pending",
    }

    class Store:
        def __init__(self):
            self.completed = []
            self.retried = []

        def list_pending_actions(self, **_kwargs):
            return [action]

        def get_pending_action(self, _action_id):
            return action

        def get_connection(self, _connection_id):
            return action["connection"]

        def get_mapping(self, mapping_id):
            assert mapping_id == "map-1"
            return {"id": mapping_id, **_mapping()}

        def complete_pending_action(self, action_id):
            self.completed.append(action_id)
            return True

        def retry_pending_action(self, action_id, **kwargs):
            self.retried.append((action_id, kwargs))
            return True

    class MissingSessionStore:
        def __init__(self):
            self.removed = []

        async def get_by_call_id(self, _call_id):
            return None

        async def remove_call(self, call_id):
            self.removed.append(call_id)

    store = Store()
    session_store = MissingSessionStore()
    finalizer = AsyncMock(return_value=False)
    history_store = SimpleNamespace(update_external_lifecycle=AsyncMock(return_value=True))
    engine = SimpleNamespace(
        session_store=session_store,
        _save_session=AsyncMock(),
        _finalize_vicidial_call_locked=finalizer,
        _execute_pending_vicidial_workflow=AsyncMock(),
    )
    engine._retry_pending_vicidial_action = (
        lambda pending_action, pending_store: Engine._retry_pending_vicidial_action(
            engine, pending_action, pending_store
        )
    )
    monkeypatch.setattr(
        "src.core.vicidial_store.get_vicidial_store", lambda: store
    )
    monkeypatch.setattr(
        "src.core.call_history.get_call_history_store", lambda: history_store
    )

    assert await Engine._retry_pending_vicidial_actions(engine) == 0
    assert store.completed == []
    assert store.retried[0][0] == action["id"]
    reconstructed = finalizer.await_args.args[0]
    assert reconstructed.external_requested_disposition == "DNCLST"
    assert reconstructed.external_session["agent_user"] == "9001"
    assert reconstructed.external_session["external_call_id"] == (
        "M4050908070000012345"
    )
    assert session_store.removed == ["ari-missing-session"]
    history_store.update_external_lifecycle.assert_not_awaited()

    async def finalize_retry(reconstructed_session, **_kwargs):
        reconstructed_session.external_disposition = "DNCLST"
        reconstructed_session.external_disposition_label = "dnc"
        reconstructed_session.external_finalized = True
        reconstructed_session.external_events.append(
            {"operation": "hangup", "success": True}
        )
        return True

    finalizer.side_effect = finalize_retry
    history_store.update_external_lifecycle.return_value = False
    store.retried.clear()
    assert await Engine._retry_pending_vicidial_actions(engine) == 0
    assert store.completed == []
    assert "could not be persisted" in store.retried[0][1]["error"]
    assert session_store.removed == [
        "ari-missing-session",
        "ari-missing-session",
    ]

    history_store.update_external_lifecycle.return_value = True
    history_store.update_external_lifecycle.reset_mock()
    store.retried.clear()
    assert await Engine._retry_pending_vicidial_actions(engine) == 1
    assert store.completed == [action["id"]]
    assert session_store.removed == [
        "ari-missing-session",
        "ari-missing-session",
        "ari-missing-session",
    ]
    history_store.update_external_lifecycle.assert_awaited_once()
    history_call = history_store.update_external_lifecycle.await_args
    assert history_call.args == ("ari-missing-session",)
    assert history_call.kwargs["external_disposition"] == "DNCLST"
    assert history_call.kwargs["external_metadata"]["finalized"] is True
    assert history_call.kwargs["external_metadata"]["events"][-1]["operation"] == "hangup"


@pytest.mark.asyncio
async def test_worker_skips_a_callback_canceled_after_due_list(monkeypatch):
    listed = {
        "id": "callback-canceled-1",
        "operation": "callback",
        "connection": {**_connection(), "id": "connection-1"},
        "payload": {
            "lead_id": "456",
            "campaign_id": "TESTCAMP",
            "callback_datetime": "2026-07-21 10:00:00",
        },
        "call_id": "ari-callback-canceled",
        "status": "pending",
    }

    class Store:
        def list_pending_actions(self, **_kwargs):
            return [listed]

        def get_pending_action(self, _action_id):
            return {**listed, "status": "canceled"}

        def retry_pending_action(self, *_args, **_kwargs):
            raise AssertionError("a canceled callback must not be retried")

    engine = SimpleNamespace(
        _retry_pending_vicidial_action=AsyncMock(),
    )
    monkeypatch.setattr(
        "src.core.vicidial_store.get_vicidial_store", lambda: Store()
    )

    assert await Engine._retry_pending_vicidial_actions(engine) == 0
    engine._retry_pending_vicidial_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_inbound_dnc_never_uses_closer_login_mode_as_action_campaign():
    session = CallSession(call_id="ari-dnc", caller_channel_id="ari-dnc")
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
        campaign_id="TESTINGROUP",
        phone_number="13165551212",
        direction="inbound",
        metadata={"agent_status": {"campaign_id": "CLOSER"}},
    ).to_dict()
    session.external_mapping = {
        **_mapping(),
        "campaign_id": None,
        "dnc_scope": "campaign",
        "dispositions": {**_mapping()["dispositions"], "dnc": "DNC"},
    }
    store = _SessionStore(session)
    context = ToolExecutionContext(call_id="ari-dnc", session_store=store)

    result = await SetCallDispositionTool().execute({"disposition": "dnc"}, context)

    assert result == {
        "status": "failed",
        "message": "VICIdial phone/campaign data is unavailable for DNC",
    }
    assert session.external_disposition_payload == {}


@pytest.mark.asyncio
async def test_callback_is_converted_to_vicidial_timezone_and_verified(monkeypatch):
    session = CallSession(call_id="ari-callback", caller_channel_id="ari-callback")
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
        campaign_id="TESTINGROUP",
        lead_id="456",
        direction="inbound",
        metadata={"agent_status": {"campaign_id": "TESTCAMP"}},
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
            assert kwargs["campaign_id"] == "TESTCAMP"
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
async def test_useronly_callback_verification_requires_current_remote_agent(
    monkeypatch,
):
    session = CallSession(
        call_id="ari-useronly-callback",
        caller_channel_id="ari-useronly-callback",
    )
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
        "callback_type": "USERONLY",
        "callback_user": "9001",
    }
    updates = []

    class Client:
        def __init__(self, _connection):
            self.lookup_count = 0

        async def update_lead_callback(self, **kwargs):
            updates.append(kwargs)
            return VicidialApiResult(True, "update_lead", "SUCCESS")

        async def lead_callback_info(self, **_kwargs):
            self.lookup_count += 1
            row = {
                "lead_id": "456",
                "callback_type": "CURRENT",
                "recipient": "USERONLY",
                "callback_status": "ACTIVE",
                "lead_status": "CALLBK",
                "campaign_id": "TESTCAMP",
                "callback_date": "2026-07-19 18:30:00",
                "user": "9002" if self.lookup_count == 1 else "9001",
            }
            return VicidialApiResult(
                True, "lead_callback_info", "verified", rows=[row]
            )

    monkeypatch.setattr("src.tools.telephony.vicidial.VicidialApiClient", Client)

    assert await commit_vicidial_disposition_workflow(session) is True
    assert len(updates) == 1
    assert updates[0]["callback_user"] == "9001"
    assert session.external_disposition_payload["workflow_committed"] is True


@pytest.mark.asyncio
async def test_callback_retry_does_not_mutate_when_preflight_lookup_fails(
    monkeypatch,
):
    session = CallSession(
        call_id="ari-callback-preflight", caller_channel_id="ari-callback-preflight"
    )
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
            raise AssertionError("failed preflight must not create another callback")

        async def lead_callback_info(self, **_kwargs):
            return VicidialApiResult(
                False,
                "lead_callback_info",
                "request timed out",
                error_code="timeout",
            )

    monkeypatch.setattr("src.tools.telephony.vicidial.VicidialApiClient", Client)

    assert (
        await commit_vicidial_disposition_workflow(session, queue_on_failure=False)
        is False
    )
    assert session.external_disposition_payload.get("workflow_committed") is not True
    assert session.external_events[-1]["operation"] == "callback_verify"
    assert session.external_events[-1]["error_code"] == "timeout"


@pytest.mark.asyncio
async def test_callback_preflight_failure_is_durably_queued(monkeypatch):
    session = CallSession(
        call_id="ari-callback-queued", caller_channel_id="ari-callback-queued"
    )
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
        campaign_id="TESTCAMP",
        lead_id="456",
    ).to_dict()
    session.external_mapping = _mapping()
    session.external_connection = {**_connection(), "id": "connection-1"}
    session.external_requested_disposition = "CBNEW"
    session.external_disposition_label = "callback"
    session.external_disposition_payload = {
        "lead_id": "456",
        "campaign_id": "TESTCAMP",
        "callback_datetime": "2026-07-21 10:00:00",
        "callback_type": "ANYONE",
        "callback_user": "9001",
        "comments": "Call tomorrow",
    }
    queued = {}

    class Client:
        def __init__(self, _connection):
            pass

        async def update_lead_callback(self, **_kwargs):
            raise AssertionError("a failed preflight must not mutate callbacks")

        async def lead_callback_info(self, **_kwargs):
            return VicidialApiResult(
                False,
                "lead_callback_info",
                "request timed out",
                error_code="timeout",
            )

    class Store:
        def enqueue_pending_action(self, **kwargs):
            queued.update(kwargs)
            return {"id": "callback-action-1", "status": "pending"}

    monkeypatch.setattr("src.tools.telephony.vicidial.VicidialApiClient", Client)
    monkeypatch.setattr(
        "src.core.vicidial_store.get_vicidial_store", lambda: Store()
    )

    committed = await commit_vicidial_disposition_workflow(
        session,
        retry_terminal={
            "semantic": "ai_hangup",
            "operation_reason": "hangup-tool",
        },
    )

    assert committed is False
    assert queued["operation"] == "callback"
    assert queued["payload"]["lead_id"] == "456"
    assert queued["payload"]["requested_status"] == "CBNEW"
    terminal = queued["payload"]["retry_terminal"]
    assert terminal["semantic"] == "ai_hangup"
    assert terminal["operation_reason"] == "hangup-tool"
    assert terminal["session"]["external_call_id"] == "M4050908070000012345"
    assert terminal["session"]["agent_user"] == "9001"
    assert session.external_disposition_payload["workflow_queued"] is True
    assert session.external_disposition_payload["workflow_queue_id"] == "callback-action-1"
    assert session.external_events[-1]["operation"] == "callback_queue"


@pytest.mark.asyncio
async def test_pending_callback_replay_verifies_before_write(monkeypatch):
    action = {
        "id": "callback-action-1",
        "operation": "callback",
        "connection": {**_connection(), "id": "connection-1"},
        "payload": {
            "lead_id": "456",
            "campaign_id": "TESTCAMP",
            "callback_datetime": "2026-07-21 10:00:00",
            "callback_type": "ANYONE",
            "callback_user": "9001",
            "comments": "Call tomorrow",
            "requested_status": "CBNEW",
        },
        "call_id": "ari-callback-queued",
        "external_call_id": "M4050908070000012345",
    }
    operations = []

    class Client:
        def __init__(self, _connection):
            self.lookups = 0

        async def lead_callback_info(self, **_kwargs):
            self.lookups += 1
            operations.append("lookup")
            rows = []
            if self.lookups == 2:
                rows = [
                    {
                        "lead_id": "456",
                        "callback_type": "CURRENT",
                        "recipient": "ANYONE",
                        "callback_status": "ACTIVE",
                        "lead_status": "CBNEW",
                        "campaign_id": "TESTCAMP",
                        "callback_date": "2026-07-21 10:00:00",
                    }
                ]
            return VicidialApiResult(
                True, "lead_callback_info", "verified", rows=rows
            )

        async def update_lead_callback(self, **kwargs):
            operations.append("update")
            assert kwargs["callback_status"] == "CBNEW"
            return VicidialApiResult(True, "update_lead", "SUCCESS")

    monkeypatch.setattr("src.tools.telephony.vicidial.VicidialApiClient", Client)

    assert (
        await Engine._execute_pending_vicidial_workflow(
            SimpleNamespace(), action, action["connection"]
        )
        is True
    )
    assert operations == ["lookup", "update", "lookup"]


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
async def test_engine_finalizer_uses_ai_failure_status_for_provider_failure(monkeypatch):
    session = CallSession(
        call_id="ari-provider-failure",
        caller_channel_id="ari-provider-failure",
    )
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
    ).to_dict()
    session.external_mapping = _mapping()
    session.external_connection = _connection()
    captured = {}

    class Client:
        def __init__(self, _connection):
            pass

        async def call_control(self, _info, *, stage, status):
            captured.update({"stage": stage, "status": status})
            return VicidialApiResult(True, "ra_call_control", "SUCCESS")

    async def save(saved_session):
        assert saved_session is session

    async def successful_workflow(_session, **_kwargs):
        return True

    monkeypatch.setattr("src.integrations.vicidial.VicidialApiClient", Client)
    monkeypatch.setattr(
        "src.tools.telephony.vicidial.commit_vicidial_disposition_workflow",
        successful_workflow,
    )
    engine = SimpleNamespace(_save_session=save)

    result = await Engine._finalize_vicidial_call(
        engine,
        session,
        semantic="ai_failure",
        operation_reason="provider-start-failed",
    )

    assert result is True
    assert captured == {"stage": "HANGUP", "status": "AIFAIL"}
    assert session.external_disposition == "AIFAIL"


@pytest.mark.asyncio
async def test_stasis_start_exception_requests_forced_caller_hangup():
    engine = Engine.__new__(Engine)
    engine.ari_client = SimpleNamespace(
        send_command=AsyncMock(return_value={}),
        answer_channel=AsyncMock(side_effect=RuntimeError("answer failed")),
    )
    engine.session_store = SimpleNamespace(
        get_by_call_id=AsyncMock(return_value=None),
    )
    engine._cleanup_call = AsyncMock()

    await Engine._handle_caller_stasis_start_hybrid(
        engine,
        "ari-setup-failure",
        {"caller": {"name": "VICIdial", "number": "M4050908070000012345"}},
    )

    engine._cleanup_call.assert_awaited_once_with(
        "ari-setup-failure", force_caller_hangup=True
    )


@pytest.mark.asyncio
async def test_engine_defers_terminal_control_until_dnc_is_confirmed(monkeypatch):
    session = CallSession(
        call_id="ari-dnc-finalize", caller_channel_id="ari-dnc-finalize"
    )
    session.external_platform = "vicidial"
    session.external_session = VicidialSessionInfo(
        external_call_id="M4050908070000012345",
        mapping_id="map-1",
        agent_user="9001",
    ).to_dict()
    session.external_mapping = _mapping()
    session.external_connection = _connection()
    session.external_requested_disposition = "DNC"
    session.external_disposition_label = "dnc"
    session.external_disposition_payload = {
        "phone_number": "13165551212",
        "campaign_id": "TESTCAMP",
    }

    async def save(saved_session):
        assert saved_session is session

    async def fail_workflow(_session, **_kwargs):
        return False

    monkeypatch.setattr(
        "src.tools.telephony.vicidial.commit_vicidial_disposition_workflow",
        fail_workflow,
    )
    engine = SimpleNamespace(_save_session=save)

    result = await Engine._finalize_vicidial_call(
        engine,
        session,
        semantic="ai_hangup",
        operation_reason="test",
    )

    assert result is False
    assert session.external_finalized is False
    assert session.external_finalizing is False
    assert session.external_disposition_label == "dnc"
    assert session.external_disposition_payload["phone_number"] == "13165551212"
    assert session.external_events[-1]["operation"] == "disposition_workflow"
    assert "terminal control deferred" in session.external_events[-1]["message"]


@pytest.mark.asyncio
async def test_failed_vicidial_terminal_request_releases_retry_ownership():
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(audio_transport="audiosocket")
    from src.core.session_store import SessionStore

    engine.session_store = SessionStore()
    engine.conversation_coordinator = None
    engine._wait_for_call_audio_drain = AsyncMock(return_value=True)
    engine._finalize_vicidial_call = AsyncMock(side_effect=[False, True])
    provider = SimpleNamespace(release_terminal_output_protection=Mock())
    session = CallSession(call_id="ari-terminal-retry", caller_channel_id="ari-terminal-retry")
    session.external_platform = "vicidial"
    engine._call_providers = {session.call_id: provider}
    await engine.session_store.upsert_call(session)

    assert await engine._terminate_call_after_audio(
        session.call_id, reason="first-attempt"
    ) is False
    assert session.call_id not in engine._terminal_hangup_started
    assert await engine._terminate_call_after_audio(
        session.call_id, reason="retry"
    ) is True
    assert engine._finalize_vicidial_call.await_count == 2
    provider.release_terminal_output_protection.assert_called_once_with()


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
    session.external_mapping_revision = "revision-1"

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
            return True

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
        "mapping_revision": "revision-1",
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
        ["attended_transfer", "leave_voicemail", "hangup_call"],
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
        "sale": "SALE",
        "dnc": "DNC",
    }
