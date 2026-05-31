from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config import validate_production_config
from src.tools.context import ToolExecutionContext
from src.tools.telephony.unified_transfer import UnifiedTransferTool
from src.tools.telephony.vicidial import (
    VicidialAgentApiClient,
    VicidialApiResult,
    VicidialConfigError,
    VicidialSession,
    build_session_from_channel_vars,
    filter_tool_names_for_vicidial_session,
    is_expected_test_connection_response,
    read_vicidial_channel_vars,
    validate_vicidial_config,
)


def _config(**overrides):
    vicidial = {
        "enabled": True,
        "api_url": "https://vicidial.example.com/agc/api.php",
        "source": "aava",
        "user": "apiuser",
        "pass": "apipass",
        "timeout_ms": 5000,
        "verify_ssl": True,
        "fallback_to_ari_on_hangup_failure": False,
        "default_agent_user": "",
        "status_codes": {
            "ai_hangup": "AIHU",
            "ai_ingroup_transfer": "AIXFR",
            "ai_extension_transfer": "AIEXT",
        },
        "default_live_agent_destination": "default_ingroup",
        "destinations": {
            "default_ingroup": {
                "type": "ingroup",
                "ingroup_choices": "DEFAULTINGROUP",
                "description": "Default ViciDial ingroup",
            },
            "tier2_extension": {
                "type": "extension",
                "phone_number": "16005551212",
                "description": "Tier 2 extension",
            },
        },
    }
    vicidial.update(overrides)
    return {"integrations": {"vicidial": vicidial}}


def test_vicidial_validation_requires_resolved_credentials(monkeypatch):
    monkeypatch.delenv("VICIDIAL_API_USER", raising=False)
    cfg = _config(user="${VICIDIAL_API_USER}")

    with pytest.raises(VicidialConfigError, match="user must resolve"):
        validate_vicidial_config(cfg)

    monkeypatch.setenv("VICIDIAL_API_USER", "apiuser")
    validate_vicidial_config(_config(user="${VICIDIAL_API_USER}", **{}))


def test_vicidial_validation_supports_env_default_syntax(monkeypatch):
    monkeypatch.delenv("VICIDIAL_API_USER", raising=False)

    validate_vicidial_config(_config(user="${VICIDIAL_API_USER:-apiuser}"))


def test_vicidial_validation_checks_status_length_and_destinations():
    cfg = _config(
        status_codes={
            "ai_hangup": "TOOLONG",
            "ai_ingroup_transfer": "AIXFR",
            "ai_extension_transfer": "AIEXT",
        }
    )

    with pytest.raises(VicidialConfigError, match="at most 6"):
        validate_vicidial_config(cfg)

    cfg = _config(destinations={"bad": {"type": "ingroup"}})
    with pytest.raises(VicidialConfigError, match="ingroup_choices"):
        validate_vicidial_config(cfg)


def test_build_session_requires_explicit_call_id_and_agent_user():
    cfg = _config()

    assert build_session_from_channel_vars({"CALLERID(name)": "Y0315"}, cfg) is None
    assert build_session_from_channel_vars({"VICIDIAL_RA_CALL_ID": "Y0315"}, cfg) is None

    session = build_session_from_channel_vars(
        {
            "VICIDIAL_RA_CALL_ID": "Y0315201639000402027",
            "VICIDIAL_RA_AGENT_USER": "1028",
        },
        cfg,
    )

    assert session == VicidialSession(call_id="Y0315201639000402027", agent_user="1028")


def test_removed_vicidial_outbound_env_fails_production_validation(monkeypatch):
    monkeypatch.setenv("AAVA_OUTBOUND_PBX_TYPE", "vicidial")

    errors, warnings = validate_production_config(SimpleNamespace(config_version=6))

    assert any("AAVA_OUTBOUND_PBX_TYPE=vicidial has been removed" in error for error in errors)


def test_vicidial_tool_filter_hides_ari_transfer_surfaces():
    session = SimpleNamespace(vicidial_session=VicidialSession(call_id="Y0315", agent_user="1028"))

    filtered = filter_tool_names_for_vicidial_session(
        ["hangup_call", "attended_transfer", "transfer_call", "transfer_to_queue", "blind_transfer", "cancel_transfer"],
        session,
    )

    assert filtered == ["hangup_call", "blind_transfer", "cancel_transfer"]


def test_vicidial_tool_filter_noops_for_normal_calls():
    names = ["attended_transfer", "transfer_to_queue", "blind_transfer"]

    assert filter_tool_names_for_vicidial_session(names, SimpleNamespace(vicidial_session=None)) == names


def test_default_agent_user_fallback_is_lab_only():
    cfg = _config(default_agent_user="labagent")

    session = build_session_from_channel_vars({"VICIDIAL_RA_CALL_ID": "Y0315"}, cfg)

    assert session == VicidialSession(call_id="Y0315", agent_user="labagent", source="default_agent_user")


class _FakeResponse:
    def __init__(self, text):
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return self._text


class _FakeHttpSession:
    last = None

    def __init__(self, raw="SUCCESS: hangup"):
        self.raw = raw
        self.calls = []
        _FakeHttpSession.last = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _FakeResponse(self.raw)


@pytest.mark.asyncio
async def test_agent_api_call_control_builds_expected_params():
    client = VicidialAgentApiClient(_config(), session_factory=_FakeHttpSession)

    result = await client.call_control(
        VicidialSession(call_id="Y0315", agent_user="1028"),
        stage="INGROUPTRANSFER",
        status="AIXFR",
        ingroup_choices="SUPPORT",
    )

    assert result.success is True
    url, kwargs = _FakeHttpSession.last.calls[0]
    assert url == "https://vicidial.example.com/agc/api.php"
    assert kwargs["params"] == {
        "source": "aava",
        "user": "apiuser",
        "pass": "apipass",
        "agent_user": "1028",
        "function": "ra_call_control",
        "value": "Y0315",
        "stage": "INGROUPTRANSFER",
        "status": "AIXFR",
        "ingroup_choices": "SUPPORT",
    }


@pytest.mark.asyncio
async def test_test_connection_accepts_expected_invalid_call_response():
    class InvalidCallSession(_FakeHttpSession):
        def __init__(self):
            super().__init__("ERROR: no active call for value")

    client = VicidialAgentApiClient(_config(), session_factory=InvalidCallSession)

    result = await client.test_connection()

    assert result.success is True
    assert "reachable" in result.message


def test_test_connection_response_heuristic_rejects_auth_errors():
    assert is_expected_test_connection_response("ERROR: no active call for value") is True
    assert is_expected_test_connection_response("ERROR: user not logged in") is False
    assert is_expected_test_connection_response("ERROR: source not authorized") is False
    assert is_expected_test_connection_response("SUCCESS: hangup complete") is False


@pytest.mark.asyncio
async def test_vicidial_channel_var_detection_short_circuits_without_call_id():
    calls = []

    async def send_command(method, resource, params=None, tolerate_statuses=None):
        calls.append(params["variable"])
        return {"value": ""}

    engine = SimpleNamespace(ari_client=SimpleNamespace(send_command=AsyncMock(side_effect=send_command)))

    result = await read_vicidial_channel_vars(engine.ari_client, "PJSIP/caller-0001")

    assert result == {}
    assert calls == ["VICIDIAL_RA_CALL_ID"]


@pytest.mark.asyncio
async def test_vicidial_channel_var_detection_reads_metadata_after_call_id():
    values = {
        "VICIDIAL_RA_CALL_ID": "Y0315",
        "VICIDIAL_RA_AGENT_USER": "1028",
        "VICIDIAL_SOURCE": "source-a",
        "VICIDIAL_CAMPAIGN_ID": "",
        "VICIDIAL_INGROUP": "SUPPORT",
    }

    async def send_command(method, resource, params=None, tolerate_statuses=None):
        return {"value": values[params["variable"]]}

    engine = SimpleNamespace(ari_client=SimpleNamespace(send_command=AsyncMock(side_effect=send_command)))

    result = await read_vicidial_channel_vars(engine.ari_client, "PJSIP/caller-0001")

    assert result == {
        "VICIDIAL_RA_CALL_ID": "Y0315",
        "VICIDIAL_RA_AGENT_USER": "1028",
        "VICIDIAL_SOURCE": "source-a",
        "VICIDIAL_INGROUP": "SUPPORT",
    }



@pytest.mark.asyncio
async def test_unified_transfer_delegates_ingroup_to_vicidial(monkeypatch, tool_context):
    calls = []

    class FakeClient:
        def __init__(self, config):
            self.config = config

        async def call_control(self, session, **kwargs):
            calls.append((session, kwargs))
            return VicidialApiResult(success=True, status="success", message="SUCCESS: transfer", raw="SUCCESS: transfer")

    monkeypatch.setattr("src.tools.telephony.vicidial.VicidialAgentApiClient", FakeClient)
    tool_context.config = _config()
    tool_context.vicidial_session = VicidialSession(call_id="Y0315", agent_user="1028")

    result = await UnifiedTransferTool().execute({"destination": "default_ingroup"}, tool_context)

    assert result["status"] == "success"
    assert result["type"] == "vicidial_ingroup"
    assert calls[0][1] == {
        "stage": "INGROUPTRANSFER",
        "ingroup_choices": "DEFAULTINGROUP",
        "status": "AIXFR",
    }


@pytest.mark.asyncio
async def test_unified_transfer_delegates_extension_to_vicidial(monkeypatch, tool_context):
    calls = []

    class FakeClient:
        def __init__(self, config):
            self.config = config

        async def call_control(self, session, **kwargs):
            calls.append((session, kwargs))
            return VicidialApiResult(success=True, status="success", message="SUCCESS: transfer", raw="SUCCESS: transfer")

    monkeypatch.setattr("src.tools.telephony.vicidial.VicidialAgentApiClient", FakeClient)
    tool_context.config = _config()
    tool_context.vicidial_session = VicidialSession(call_id="Y0315", agent_user="1028")

    result = await UnifiedTransferTool().execute({"destination": "tier2_extension"}, tool_context)

    assert result["status"] == "success"
    assert result["type"] == "vicidial_extension"
    assert calls[0][1] == {
        "stage": "EXTENSIONTRANSFER",
        "phone_number": "16005551212",
        "status": "AIEXT",
    }


@pytest.mark.asyncio
async def test_vicidial_transfer_failure_does_not_use_ari(monkeypatch, mock_ari_client):
    class FakeClient:
        def __init__(self, config):
            self.config = config

        async def call_control(self, session, **kwargs):
            return VicidialApiResult(success=False, status="error", message="ERROR: bad call", raw="ERROR: bad call")

    monkeypatch.setattr("src.tools.telephony.vicidial.VicidialAgentApiClient", FakeClient)
    context = ToolExecutionContext(
        ari_client=mock_ari_client,
        session_store=None,
        config=_config(),
        call_id="call",
        caller_channel_id="PJSIP/caller-0001",
        vicidial_session=VicidialSession(call_id="Y0315", agent_user="1028"),
    )

    result = await UnifiedTransferTool().execute({"destination": "default_ingroup"}, context)

    assert result["status"] == "failed"
    mock_ari_client.send_command.assert_not_called()
