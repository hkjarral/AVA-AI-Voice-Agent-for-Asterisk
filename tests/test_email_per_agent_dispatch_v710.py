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
