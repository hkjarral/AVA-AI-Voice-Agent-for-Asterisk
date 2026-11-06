"""Caller-stated name and reason on attended_transfer reach the agent briefing only."""

from types import SimpleNamespace

import pytest

from src.config import AppConfig
from src.core.models import CallSession
from src.engine import Engine
from src.tools.adapters.sanitize import sanitize_tool_result_for_json_string
from src.tools.execution_history import build_in_call_tool_record
from src.tools.telephony import attended_transfer as attended_module
from src.tools.telephony import deferred_transfer as deferred_module
from src.tools.telephony.attended_transfer import (
    AttendedTransferTool,
    _caller_briefing_from_parameters,
    _normalize_caller_statement,
)
from src.tools.telephony.deferred_transfer import DEFERRED_TRANSFER_RESULT_KEY, commit_deferred_transfer_action

CALLER_NAME = "Taylor Fixture"
CALL_REASON = "disputed invoice from March"


class _FakeAriClient:
    def __init__(self, *, originate_response=None, engine=None):
        self.calls = []
        self._originate_response = originate_response
        self.engine = engine

    async def send_command(self, *, method: str, resource: str, data=None, params=None):
        self.calls.append({"method": method, "resource": resource, "data": data, "params": params})
        if method == "POST" and resource == "channels":
            return self._originate_response
        return {"ok": True}


class _FakeSessionStore:
    def __init__(self):
        self.sessions = {}

    async def upsert_call(self, session: CallSession) -> None:
        self.sessions[session.call_id] = session

    async def get_by_call_id(self, call_id):
        return self.sessions.get(call_id)


class _FakeContext:
    def __init__(self, *, config: dict, session: CallSession, ari_client: _FakeAriClient):
        self._config = config
        self.call_id = session.call_id
        self.caller_channel_id = session.caller_channel_id
        self.ari_client = ari_client
        self.session_store = _FakeSessionStore()
        self.tool_registry = None
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


class _FakeEngine:
    def __init__(self):
        self.tasks = []

    def _fire_and_forget_for_call(self, call_id, coro, *, name=None):
        self.tasks.append((call_id, name))
        coro.close()


class _LogCapture:
    def __init__(self):
        self.records = []

    def __getattr__(self, level):
        def _log(event, *args, **fields):
            self.records.append({"level": level, "event": str(event), **fields})
        return _log


@pytest.fixture
def logs(monkeypatch):
    capture = _LogCapture()
    for module in (attended_module, deferred_module):
        monkeypatch.setattr(module, "logger", capture)
    return capture


def _config(*, defer: bool, screening_mode: str = "basic_tts") -> dict:
    return {
        "asterisk": {"app_name": "asterisk-ai-voice-agent"},
        "tools": {
            "ai_identity": {"name": "Ava", "number": "6789"},
            "attended_transfer": {
                "enabled": True,
                "moh_class": "default",
                "dial_timeout_seconds": 30,
                "screening_mode": screening_mode,
            },
            "transfer": {
                "defer_until_playback_complete": defer,
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
    }


def _session(call_id: str) -> CallSession:
    return CallSession(call_id=call_id, caller_channel_id=call_id, caller_name="CNAM Name", context_name="support")


def _context(config: dict, session: CallSession, engine=None) -> _FakeContext:
    ari = _FakeAriClient(originate_response={"id": "agent-chan"}, engine=engine)
    return _FakeContext(config=config, session=session, ari_client=ari)


def _assert_no_statement(blob) -> None:
    text = repr(blob)
    assert CALLER_NAME not in text
    assert CALL_REASON not in text


def _build_engine() -> Engine:
    return Engine(AppConfig(**{
        "default_provider": "local",
        "providers": {"local": {"enabled": True}},
        "asterisk": {"host": "127.0.0.1", "port": 8088, "username": "u", "password": "p", "app_name": "ai-voice-agent"},
        "llm": {"initial_greeting": "hi", "prompt": "You are helpful", "model": "gpt-4o"},
        "pipelines": {"local_only": {}},
        "active_pipeline": "local_only",
        "audio_transport": "audiosocket",
        "external_media": {"rtp_host": "127.0.0.1", "rtp_port": 18080, "advertise_host": "127.0.0.1",
                           "port_range": "18080-18090", "codec": "ulaw", "format": "slin16", "sample_rate": 16000},
        "audiosocket": {"host": "127.0.0.1", "port": 9092, "format": "ulaw"},
        "tools": {"attended_transfer": {"enabled": True}},
    }))


def test_definition_exposes_optional_caller_statements():
    params = {p.name: p for p in AttendedTransferTool().definition.parameters}
    for name in ("caller_name", "call_reason"):
        assert params[name].type == "string"
        assert params[name].required is False
        assert "never guess" in params[name].description.lower()
    assert params["destination"].required is True


@pytest.mark.parametrize(
    ("value", "max_len", "expected"),
    [
        ("Taylor Fixture", 80, "Taylor Fixture"),
        ("  Taylor \n\t Fixture  ", 80, "Taylor Fixture"),
        ("Tay\x00lor​ Fix\x7fture", 80, "Taylor Fixture"),
        ("José Ñúñez", 80, "José Ñúñez"),
        ("نام\u200cخانوادگی", 80, "نام\u200cخانوادگی"),
        ("क\u200dष", 80, "क\u200dष"),
        ("a\u200eb\u202ec\u2066d\ufeffe", 80, "abcde"),
        ("alpha beta gamma", 12, "alpha beta"),
        ("alpha beta gamma", 10, "alpha beta"),
        ("x" * 100, 80, "x" * 80),
        ("x" * 80, 80, "x" * 80),
        ("", 80, None),
        ("   \n", 80, None),
        ("\x00\x01​", 80, None),
        ("\u200d", 80, None),
        ("\u200c\u200d", 80, None),
        (" \u200c \u200d ", 80, None),
        (None, 80, None),
        (42, 80, None),
        (["Taylor"], 80, None),
        ({"name": "Taylor"}, 80, None),
    ],
)
def test_normalize_caller_statement(value, max_len, expected):
    assert _normalize_caller_statement(value, max_len) == expected


@pytest.mark.parametrize(
    "filler",
    ["unknown", "Unknown", "UNKNOWN", "n/a", "N/A", "none", "None", "not provided",
     "Not Provided", "not given", "-", "  N/A \n", "unknown."],
)
def test_filler_values_are_treated_as_missing(filler):
    assert _caller_briefing_from_parameters({"caller_name": filler, "call_reason": filler}) == {}


def test_filler_does_not_discard_real_values():
    params = {"caller_name": "Nana Unknown", "call_reason": "none of my invoices match"}
    assert _caller_briefing_from_parameters(params) == {
        "screened_caller_name": "Nana Unknown",
        "screened_call_reason": "none of my invoices match",
    }


@pytest.mark.parametrize("surname", ["Na", "NA", "na"])
def test_na_is_a_real_surname_and_is_kept(surname):
    assert _caller_briefing_from_parameters({"caller_name": surname}) == {"screened_caller_name": surname}


@pytest.mark.asyncio
async def test_filler_values_keep_caller_id_and_context_fallbacks():
    engine = _build_engine()
    session = _session("1760000000.1020")
    await AttendedTransferTool().execute(
        {"destination": "support_agent", "caller_name": "Unknown", "call_reason": "N/A"},
        _context(_config(defer=False), session),
    )
    rendered = engine._apply_prompt_template_substitution(
        "Transferring {screened_caller_display} about {screened_reason_display}.", session,
        extra_substitutions=engine._build_attended_transfer_template_vars(session, destination_description="Support agent"),
    )
    assert rendered == "Transferring CNAM Name about support."


@pytest.mark.asyncio
async def test_immediate_commit_writes_normalized_briefing():
    session = _session("1760000000.1001")
    context = _context(_config(defer=False), session)
    long_reason = "word " * 100

    result = await AttendedTransferTool().execute(
        {"destination": "support_agent", "caller_name": f"  {CALLER_NAME}\n", "call_reason": long_reason},
        context,
    )

    assert result["status"] == "success"
    briefing = session.current_action["briefing"]
    assert briefing["screened_caller_name"] == CALLER_NAME
    assert len(briefing["screened_call_reason"]) <= 200
    assert briefing["screened_call_reason"].endswith("word")
    assert "_briefing" not in session.current_action


@pytest.mark.asyncio
async def test_only_given_statements_are_written():
    session = _session("1760000000.1002")
    context = _context(_config(defer=False), session)
    await AttendedTransferTool().execute({"destination": "support_agent", "caller_name": CALLER_NAME, "call_reason": "  "}, context)
    assert session.current_action["briefing"] == {"screened_caller_name": CALLER_NAME}

    session = _session("1760000000.1003")
    context = _context(_config(defer=False), session)
    await AttendedTransferTool().execute({"destination": "support_agent"}, context)
    assert "briefing" not in session.current_action


@pytest.mark.asyncio
async def test_deferred_action_keeps_briefing_private_until_commit():
    tool = AttendedTransferTool()
    session = _session("1760000000.1004")
    context = _context(_config(defer=True), session)
    context.tool_registry = SimpleNamespace(get=lambda name: tool if name == "attended_transfer" else None)

    result = await tool.execute(
        {"destination": "support_agent", "caller_name": CALLER_NAME, "call_reason": CALL_REASON}, context
    )

    assert session.current_action is None
    assert "_briefing" not in result[DEFERRED_TRANSFER_RESULT_KEY]
    _assert_no_statement(result)
    assert session.pending_deferred_transfer["_briefing"] == {
        "screened_caller_name": CALLER_NAME,
        "screened_call_reason": CALL_REASON,
    }

    commit = await commit_deferred_transfer_action(session.pending_deferred_transfer, context)

    assert commit["status"] == "success"
    _assert_no_statement(commit)
    assert session.current_action["briefing"] == {
        "screened_caller_name": CALLER_NAME,
        "screened_call_reason": CALL_REASON,
    }


@pytest.mark.asyncio
async def test_caller_recording_mode_ignores_model_statements():
    session = _session("1760000000.1005")
    context = _context(_config(defer=False, screening_mode="caller_recording"), session, engine=_FakeEngine())
    await AttendedTransferTool().execute(
        {"destination": "support_agent", "caller_name": CALLER_NAME, "call_reason": CALL_REASON}, context
    )
    assert session.current_action["screening_mode"] == "caller_recording"
    assert "briefing" not in session.current_action


@pytest.mark.asyncio
async def test_announcement_renders_caller_statements_and_keeps_fallbacks():
    engine = _build_engine()
    template = "Transferring {screened_caller_display} about {screened_reason_display}. CNAM={caller_name}."

    session = _session("1760000000.1006")
    await AttendedTransferTool().execute(
        {"destination": "support_agent", "caller_name": CALLER_NAME, "call_reason": CALL_REASON},
        _context(_config(defer=False), session),
    )
    rendered = engine._apply_prompt_template_substitution(
        template, session,
        extra_substitutions=engine._build_attended_transfer_template_vars(session, destination_description="Support agent"),
    )
    assert rendered == f"Transferring {CALLER_NAME} about {CALL_REASON}. CNAM=CNAM Name."

    bare = _session("1760000000.1007")
    await AttendedTransferTool().execute({"destination": "support_agent"}, _context(_config(defer=False), bare))
    rendered = engine._apply_prompt_template_substitution(
        template, bare,
        extra_substitutions=engine._build_attended_transfer_template_vars(bare, destination_description="Support agent"),
    )
    assert rendered == "Transferring CNAM Name about support. CNAM=CNAM Name."


@pytest.mark.asyncio
async def test_ai_briefing_summary_yields_to_model_reason_and_stays_available():
    engine = _build_engine()
    template = "{screened_reason_display} | {screening_summary}"
    summary = "Caller asked about a late shipment."

    session = _session("1760000000.1021")
    await AttendedTransferTool().execute(
        {"destination": "support_agent", "call_reason": CALL_REASON},
        _context(_config(defer=False, screening_mode="ai_briefing"), session),
    )
    session.current_action["screening_payload"] = {"kind": "ai_briefing", "text": summary}
    rendered = engine._apply_prompt_template_substitution(
        template, session,
        extra_substitutions=engine._build_attended_transfer_template_vars(session, destination_description="Support agent"),
    )
    assert rendered == f"{CALL_REASON} | {summary}"

    bare = _session("1760000000.1022")
    await AttendedTransferTool().execute(
        {"destination": "support_agent"}, _context(_config(defer=False, screening_mode="ai_briefing"), bare),
    )
    bare.current_action["screening_payload"] = {"kind": "ai_briefing", "text": summary}
    rendered = engine._apply_prompt_template_substitution(
        template, bare,
        extra_substitutions=engine._build_attended_transfer_template_vars(bare, destination_description="Support agent"),
    )
    assert rendered == f"{summary} | {summary}"


@pytest.mark.asyncio
async def test_results_wire_payload_and_logs_never_carry_statements(logs):
    tool = AttendedTransferTool()
    session = _session("1760000000.1008")
    context = _context(_config(defer=True), session)
    context.tool_registry = SimpleNamespace(get=lambda name: tool if name == "attended_transfer" else None)

    result = await tool.execute(
        {"destination": "support_agent", "caller_name": CALLER_NAME, "call_reason": CALL_REASON}, context
    )
    wire = sanitize_tool_result_for_json_string(result, tool_name="attended_transfer")
    commit = await commit_deferred_transfer_action(session.pending_deferred_transfer, context)

    assert logs.records, "expected the tool and deferred helpers to log"
    for blob in (result, wire, commit, logs.records):
        _assert_no_statement(blob)


@pytest.mark.asyncio
async def test_missing_commit_tool_log_omits_private_briefing(logs):
    action = {"id": "a1", "commit_tool": "attended_transfer", "_briefing": {"screened_caller_name": CALLER_NAME}}
    context = SimpleNamespace(call_id="c1", tool_registry=SimpleNamespace(get=lambda name: None))

    result = await commit_deferred_transfer_action(action, context)

    assert result["status"] == "error"
    assert logs.records[0]["action"] == {"id": "a1", "commit_tool": "attended_transfer"}
    _assert_no_statement(logs.records)


@pytest.mark.parametrize("mode", ["strict", "show_routing"])
def test_call_history_redacts_caller_statements(mode):
    record = build_in_call_tool_record(
        call_id="call-briefing",
        tool_call_id="tool-briefing",
        tool_name="attended_transfer",
        parameters={"destination": "support_agent", "caller_name": CALLER_NAME, "call_reason": CALL_REASON},
        result={"status": "success", "message": "Please hold while I connect you to Support agent."},
        redaction_mode=mode,
    )
    assert record["params"]["caller_name"] == "***REDACTED***"
    assert record["params"]["call_reason"] == "***REDACTED***"
    assert "params.call_reason" in record["redacted_fields"]
    _assert_no_statement(record)
