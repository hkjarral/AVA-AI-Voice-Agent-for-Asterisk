from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tools.telephony.voicemail import VoicemailTool


def test_legacy_extension_remains_the_default_mailbox():
    assert VoicemailTool.resolve_mailbox({"extension": "2765"}) == ("default", "2765")


def test_configured_default_mailbox_is_resolved():
    assert VoicemailTool.resolve_mailbox({
        "default_mailbox_key": "sales",
        "mailboxes": {
            "sales": {"extension": "2001"},
            "support": {"extension": "2002"},
        },
    }) == ("sales", "2001")


def test_multiple_mailboxes_without_default_fail_closed():
    assert VoicemailTool.resolve_mailbox({
        "mailboxes": {
            "sales": {"extension": "2001"},
            "support": {"extension": "2002"},
        },
    }) == ("", "")


def test_agent_selected_mailbox_wins():
    assert VoicemailTool.resolve_mailbox({
        "selected_mailbox_key": "support",
        "mailboxes": {"support": {"extension": "2002"}},
        "extension": "2002",
    }) == ("support", "2002")


@pytest.mark.asyncio
async def test_execute_fails_closed_when_voicemail_is_globally_disabled():
    context = SimpleNamespace(
        call_id="call-disabled-voicemail",
        caller_channel_id="channel-disabled-voicemail",
        get_config_value=MagicMock(
            return_value={"enabled": False, "extension": "2765"}
        ),
        update_session=AsyncMock(),
        ari_client=SimpleNamespace(send_command=AsyncMock()),
    )

    result = await VoicemailTool().execute({}, context)

    assert result == {
        "status": "failed",
        "message": "Voicemail is not available",
    }
    context.update_session.assert_not_awaited()
    context.ari_client.send_command.assert_not_awaited()
