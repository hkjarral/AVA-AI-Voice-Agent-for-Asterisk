import pytest

from src.core.models import CallSession
from src.tools.telephony.attended_transfer import AttendedTransferTool


class _FakeAriClient:
    def __init__(self, *, originate_response=None):
        self.calls = []
        self._originate_response = originate_response

    async def send_command(self, *, method: str, resource: str, data=None, params=None):
        self.calls.append(
            {
                "method": method,
                "resource": resource,
                "data": data,
                "params": params,
            }
        )
        if method == "POST" and resource == "channels":
            return self._originate_response
        return {"ok": True}


class _FakeSessionStore:
    def __init__(self):
        self.sessions = {}

    async def upsert_call(self, session: CallSession) -> None:
        self.sessions[session.call_id] = session


class _FakeContext:
    def __init__(self, *, config: dict, caller_channel_id: str, ari_client: _FakeAriClient, session: CallSession):
        self._config = config
        self.caller_channel_id = caller_channel_id
        self.ari_client = ari_client
        self.session_store = _FakeSessionStore()
        self._session = session

    def get_config_value(self, key: str, default=None):
        cur = self._config
        for part in (key or "").split("."):
            if not part:
                continue
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    async def get_session(self) -> CallSession:
        return self._session


def test_destination_key_resolution_prefers_exact_and_target_matches():
    tool = AttendedTransferTool()
    destinations = {
        "support_agent": {"type": "extension", "target": "6000", "description": "Support agent", "attended_allowed": True},
        "sales_agent": {"type": "extension", "target": "6001", "description": "Sales agent", "attended_allowed": True},
        "support_team": {"type": "ringgroup", "target": "601", "description": "Support team", "attended_allowed": True},
    }
    allowed = tool._allowed_attended_destinations(destinations)

    assert tool._resolve_destination_key("support_agent", destinations, allowed) == "support_agent"
    assert tool._resolve_destination_key("SUPPORT_AGENT", destinations, allowed) == "support_agent"
    assert tool._resolve_destination_key("6000", destinations, allowed) == "support_agent"
    assert tool._resolve_destination_key("support", destinations, allowed) == "support_agent"


def test_resolve_dial_endpoint_honors_dial_string_then_internal_mapping_then_technology():
    tool = AttendedTransferTool()

    # 1) Destination dial_string wins.
    context = _FakeContext(
        config={"tools": {"extensions": {"internal": {"6000": {"dial_string": "PJSIP/override-6000"}}}}},
        caller_channel_id="caller-1",
        ari_client=_FakeAriClient(),
        session=CallSession(call_id="caller-1", caller_channel_id="caller-1"),
    )
    dial = tool._resolve_dial_endpoint(
        "6000",
        {"type": "extension", "target": "6000", "dial_string": "SIP/6000"},
        {"technology": "PJSIP"},
        context,
    )
    assert dial == "SIP/6000"

    # 2) Internal extension mapping used when destination has no dial_string.
    dial = tool._resolve_dial_endpoint(
        "6000",
        {"type": "extension", "target": "6000"},
        {"technology": "SIP"},
        context,
    )
    assert dial == "PJSIP/override-6000"

    # 3) Technology falls back to PJSIP when nothing else is configured.
    context2 = _FakeContext(
        config={},
        caller_channel_id="caller-2",
        ari_client=_FakeAriClient(),
        session=CallSession(call_id="caller-2", caller_channel_id="caller-2"),
    )
    dial = tool._resolve_dial_endpoint(
        "6000",
        {"type": "extension", "target": "6000"},
        {},
        context2,
    )
    assert dial == "PJSIP/6000"


@pytest.mark.asyncio
async def test_execute_success_sets_action_and_originates_agent_leg():
    tool = AttendedTransferTool()
    call_id = "1760000000.0000"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    ari = _FakeAriClient(originate_response={"id": "agent-chan-1"})
    context = _FakeContext(
        config={
            "asterisk": {"app_name": "asterisk-ai-voice-agent"},
            "tools": {
                "ai_identity": {"name": "Ava", "number": "6789"},
                "attended_transfer": {"enabled": True, "moh_class": "default", "dial_timeout_seconds": 30},
                "transfer": {
                    "technology": "SIP",
                    "destinations": {
                        "support_agent": {
                            "type": "extension",
                            "target": "6000",
                            "description": "Support agent",
                            "attended_allowed": True,
                        }
                    },
                },
            },
        },
        caller_channel_id=call_id,
        ari_client=ari,
        session=session,
    )

    result = await tool.execute({"destination": "support_agent"}, context)
    assert result["status"] == "success"
    assert result["type"] == "attended_transfer"

    # MOH start + originate were requested.
    assert any(c["method"] == "POST" and c["resource"].endswith(f"channels/{call_id}/moh") for c in ari.calls)
    assert any(c["method"] == "POST" and c["resource"] == "channels" for c in ari.calls)

    # Session action and capture gating are set.
    assert session.current_action and session.current_action.get("type") == "attended_transfer"
    assert session.audio_capture_enabled is False


@pytest.mark.asyncio
async def test_execute_originate_failure_stops_moh_and_clears_action():
    tool = AttendedTransferTool()
    call_id = "1760000000.0001"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    ari = _FakeAriClient(originate_response=None)
    context = _FakeContext(
        config={
            "asterisk": {"app_name": "asterisk-ai-voice-agent"},
            "tools": {
                "attended_transfer": {"enabled": True, "moh_class": "default", "dial_timeout_seconds": 1},
                "transfer": {
                    "technology": "SIP",
                    "destinations": {
                        "support_agent": {
                            "type": "extension",
                            "target": "6000",
                            "description": "Support agent",
                            "attended_allowed": True,
                        }
                    },
                },
            },
        },
        caller_channel_id=call_id,
        ari_client=ari,
        session=session,
    )

    result = await tool.execute({"destination": "support_agent"}, context)
    assert result["status"] == "failed"
    assert "Unable to place the transfer call" in result["message"]

    # Cleanup issued a MOH stop.
    assert any(c["method"] == "DELETE" and c["resource"].endswith(f"channels/{call_id}/moh") for c in ari.calls)
    assert session.current_action is None
