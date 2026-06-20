"""H5: per-agent email recipient/from/enable honored at dispatch.

Precedence at dispatch is agent (session.email_*) -> per-context map -> global default.
email_enabled is tri-state: None preserves today's behavior, only explicit False skips.
"""

from types import SimpleNamespace

import pytest

from src.tools.business.email_summary import SendEmailSummaryTool


def _session(**overrides):
    base = dict(
        context_name="sales",
        called_number="100",
        caller_name="Alice",
        caller_number="555",
        call_outcome="Completed",
        start_time=None,
        conversation_history=[],
        email_recipient=None,
        email_from=None,
        email_enabled=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _config(**overrides):
    base = {
        "enabled": True,
        "admin_email": "global@x.test",
        "admin_email_by_context": {"sales": "ctx@x.test"},
        "from_email": "globalfrom@x.test",
        "from_email_by_context": {"sales": "ctxfrom@x.test"},
    }
    base.update(overrides)
    return base


def test_agent_recipient_beats_context_and_global():
    tool = SendEmailSummaryTool()
    session = _session(email_recipient="agent@x.test")
    data = tool._prepare_email_data(session, _config(), "call-1")
    assert data["to"] == "agent@x.test"


def test_agent_from_beats_context_and_global():
    tool = SendEmailSummaryTool()
    session = _session(email_from="agentfrom@x.test")
    data = tool._prepare_email_data(session, _config(), "call-1")
    assert "agentfrom@x.test" in data["from"]


def test_falls_back_to_context_map_when_no_agent_value():
    tool = SendEmailSummaryTool()
    session = _session()  # email_recipient/from None
    data = tool._prepare_email_data(session, _config(), "call-1")
    assert data["to"] == "ctx@x.test"
    assert "ctxfrom@x.test" in data["from"]


def test_falls_back_to_global_when_no_agent_and_no_context_map():
    tool = SendEmailSummaryTool()
    session = _session(context_name="unmapped")
    cfg = _config()
    data = tool._prepare_email_data(session, cfg, "call-1")
    assert data["to"] == "global@x.test"
    assert "globalfrom@x.test" in data["from"]


def test_email_enabled_false_skips_send():
    tool = SendEmailSummaryTool()
    session = _session(email_enabled=False)
    assert tool._should_send(session, _config()) is False


def test_email_enabled_none_preserves_global_enabled():
    tool = SendEmailSummaryTool()
    session = _session(email_enabled=None)
    # config-enabled True -> send proceeds
    assert tool._should_send(session, _config(enabled=True)) is True
    # config-disabled -> still skips (today's behavior)
    assert tool._should_send(session, _config(enabled=False)) is False


def test_email_enabled_true_does_not_override_config_disabled():
    """Per-agent True doesn't force-send when the tool is globally disabled."""
    tool = SendEmailSummaryTool()
    session = _session(email_enabled=True)
    assert tool._should_send(session, _config(enabled=False)) is False


# --- Finding 1 (Codex P2): per-agent email must reach the session on the
# PIPELINE resolution path, not only the monolithic-provider path. A pipeline
# provider (e.g. local_hybrid) is NOT in engine.providers, so _resolve_audio_profile
# returns at the provider lookup before the monolithic email block. The fix copies
# the three email fields onto the session up-front, before that early return.


@pytest.mark.asyncio
async def test_pipeline_path_copies_per_agent_email_onto_session():
    from src.engine import Engine

    agent_ctx = SimpleNamespace(
        email_recipient="agent-pipe@x.test",
        email_from="agentfrom-pipe@x.test",
        email_enabled=True,
        provider="local_hybrid",  # a pipeline name, deliberately NOT in providers
    )

    class _ARI:
        async def send_command(self, method, path, params=None, tolerate_statuses=None):
            # AI_AGENT selects the agent context; everything else unset.
            if params and params.get("variable") == "AI_AGENT":
                return {"value": "sales"}
            return {"value": ""}

    class _Orchestrator:
        agent_store = SimpleNamespace(default_slug=lambda: None)

        def get_context_config(self, name, routing_method=None):
            return agent_ctx if name == "sales" else None

    saved = []

    class _StubEngine:
        ari_client = _ARI()
        transport_orchestrator = _Orchestrator()
        providers = {}  # pipeline provider absent -> early return after email copy
        config = SimpleNamespace(default_provider="local_hybrid")

        async def _save_session(self, session, *, new=False):
            saved.append(session)

    session = SimpleNamespace(
        call_id="call-pipe-1",
        context_name=None,
        routing_method=None,
        provider_name=None,
        email_recipient=None,
        email_from=None,
        email_enabled=None,
    )

    await Engine._resolve_audio_profile(_StubEngine(), session, "chan-1")

    assert session.email_recipient == "agent-pipe@x.test"
    assert session.email_from == "agentfrom-pipe@x.test"
    assert session.email_enabled is True
