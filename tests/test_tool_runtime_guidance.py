from datetime import datetime

import pytest


@pytest.mark.unit
def test_runtime_guidance_includes_live_agents_and_transfer_targets():
    from src.tools.runtime_guidance import build_in_call_tool_runtime_guidance

    config = {
        "tools": {
            "extensions": {
                "internal": {
                    "2765": {
                        "name": "Live Agent 2",
                        "aliases": ["support", "haider"],
                        "transfer": True,
                    },
                    "2785": {
                        "name": "Disabled Agent",
                        "aliases": ["disabled"],
                        "transfer": False,
                    },
                }
            },
            "transfer": {
                "destinations": {
                    "sales_agent": {
                        "type": "extension",
                        "target": "6000",
                        "description": "Sales Agent",
                        "attended_allowed": True,
                    },
                    "sales_queue": {
                        "type": "queue",
                        "target": "600",
                        "description": "Sales Queue",
                    },
                }
            },
            "leave_voicemail": {
                "extension": "9999",
            },
        }
    }

    guidance = build_in_call_tool_runtime_guidance(
        config,
        ["check_extension_status", "live_agent_transfer", "blind_transfer", "attended_transfer", "leave_voicemail"],
    )

    assert "Never invent extension numbers" in guidance
    assert "`2765`" in guidance
    assert "Live Agent 2" in guidance
    assert "support, haider" in guidance
    assert "`2785`" not in guidance
    assert "`sales_agent`" in guidance
    assert "target: 6000" in guidance
    assert "`sales_queue`" in guidance
    assert "attended_transfer: allowed" in guidance
    assert "voicemail box `9999`" in guidance


@pytest.mark.unit
def test_runtime_guidance_warns_when_transfer_tools_have_no_configured_targets():
    from src.tools.runtime_guidance import build_in_call_tool_runtime_guidance

    guidance = build_in_call_tool_runtime_guidance(
        {"tools": {}},
        ["live_agent_transfer", "blind_transfer", "attended_transfer"],
    )

    assert "None configured. Do not call `live_agent_transfer`" in guidance
    assert "None configured. Do not call `blind_transfer`" in guidance
    assert "None configured. Do not call `attended_transfer`" in guidance


@pytest.mark.unit
def test_runtime_guidance_includes_check_extension_status_allowlist_from_transfer_destinations():
    from src.tools.runtime_guidance import build_in_call_tool_runtime_guidance

    config = {
        "tools": {
            "extensions": {"internal": {}},
            "transfer": {
                "destinations": {
                    "sales_agent": {
                        "type": "extension",
                        "target": "6000",
                        "description": "Sales Agent",
                    },
                }
            },
        }
    }

    guidance = build_in_call_tool_runtime_guidance(config, ["check_extension_status"])

    assert "Configured extensions allowed for `check_extension_status`:" in guidance
    assert "`6000`" in guidance
    assert "Only query the listed configured extensions" in guidance


@pytest.mark.unit
def test_runtime_guidance_omits_unrelated_sections():
    from src.tools.runtime_guidance import build_in_call_tool_runtime_guidance

    config = {
        "tools": {
            "extensions": {
                "internal": {
                    "2765": {
                        "name": "Live Agent 2",
                        "transfer": True,
                    }
                }
            },
            "leave_voicemail": {"extension": "9999"},
        }
    }

    guidance = build_in_call_tool_runtime_guidance(config, ["leave_voicemail"])

    assert "Configured live agents:" not in guidance
    assert "Configured blind-transfer destinations:" not in guidance
    assert "Configured voicemail target:" in guidance


@pytest.mark.unit
def test_runtime_guidance_includes_vicidial_disposition_allowlist_and_compliance_rules(
    monkeypatch,
):
    from src.tools.runtime_guidance import build_in_call_tool_runtime_guidance

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 20, 23, 30, 45, tzinfo=tz)

    monkeypatch.setattr("src.tools.runtime_guidance.datetime", FixedDateTime)

    config = {
        "tools": {
            "vicidial": {
                "timezone": "America/Chicago",
                "dispositions": {
                    "sale": "SALE",
                    "dnc": "DNC",
                    "callback": "CALLBK",
                }
            }
        }
    }

    guidance = build_in_call_tool_runtime_guidance(config, ["set_call_disposition"])

    assert "Configured VICIdial dispositions:" in guidance
    assert "`dnc` (VICIdial status `DNC`)" in guidance
    assert "compliance request" in guidance
    assert "do not refuse" in guidance
    assert "include `callback_datetime`" in guidance
    assert "native VICIdial callback" in guidance
    assert "VICIdial callback timezone: `America/Chicago`" in guidance
    assert "2026-07-20T23:30:45-05:00" in guidance
    assert "relative requests such as today or tomorrow" in guidance
    assert "offset-aware ISO 8601" in guidance
    assert "Do not use a calendar" in guidance
    assert "does not end the call" in guidance


@pytest.mark.unit
def test_runtime_guidance_requires_explicit_callback_clock_when_timezone_missing():
    from src.tools.runtime_guidance import build_in_call_tool_runtime_guidance

    guidance = build_in_call_tool_runtime_guidance(
        {
            "tools": {
                "vicidial": {
                    "dispositions": {"callback": "CALLBK"},
                }
            }
        },
        ["set_call_disposition"],
    )

    assert "timezone is unavailable" in guidance
    assert "Do not infer relative dates" in guidance
    assert "offset-aware ISO 8601" in guidance


@pytest.mark.unit
def test_microsoft_calendar_guidance_covers_exact_requests_consent_and_same_call_scope():
    from src.tools.runtime_guidance import build_in_call_tool_runtime_guidance
    config = {"tools": {"microsoft_calendar": {"invitations_enabled": True, "selected_accounts": ["dispatch"], "accounts": {"dispatch": {"timezone": "America/Phoenix"}, "private": {"timezone": "UTC"}}}}}
    guidance = build_in_call_tool_runtime_guidance(config, ["microsoft_calendar"])
    assert "check_availability" in guidance and "suggestion subset" in guidance
    assert "invitation_confirmed=true" in guidance and "reschedule_event" in guidance
    assert "Later-call bookings" in guidance and "America/Phoenix" in guidance
    assert "`private`" not in guidance
    assert "Microsoft Calendar booking rules" not in build_in_call_tool_runtime_guidance(config, ["hangup_call"])


@pytest.mark.unit
@pytest.mark.parametrize("selected,expected", [
    (None, {"dispatch", "billing"}),
    (("dispatch",), {"dispatch"}),
    (["billing", "stale"], {"billing"}),
    ([], set()),
    ("dispatch", set()),
])
def test_calendar_guidance_clock_selection_matches_tool_scope(selected, expected):
    from src.tools.business.microsoft_calendar import MicrosoftCalendarTool
    from src.tools.runtime_guidance import build_in_call_tool_runtime_guidance
    calendar = {"selected_accounts": selected, "accounts": {
        "dispatch": {"timezone": "America/Phoenix"},
        "billing": {"timezone": "Europe/London"},
    }}
    guidance = build_in_call_tool_runtime_guidance(
        {"tools": {"microsoft_calendar": calendar}}, ["microsoft_calendar"]
    )
    assert set(MicrosoftCalendarTool()._selected_account_keys(calendar)) == expected
    for key, account in calendar["accounts"].items():
        assert (f"Calendar account `{key}`" in guidance) is (key in expected)
        assert (f"timezone `{account['timezone']}`" in guidance) is (key in expected)
    assert "`stale`" not in guidance
