import pytest

from src.tools.runtime_config import (
    ToolConfigPolicyError,
    ToolRuntimeGeneration,
    normalize_agent_tool_configs,
    resolve_agent_tool_config,
)


GLOBAL = {
    "tools": {
        "transfer": {
            "enabled": True,
            "live_agent_destination_key": "sales",
            "destinations": {
                "sales": {"type": "extension", "target": "6001"},
                "support": {"type": "queue", "target": "700"},
            },
        },
        "extensions": {
            "internal": {
                "6001": {"name": "Sales", "live_agent": True},
                "6002": {"name": "Billing", "live_agent": True},
            }
        },
        "google_calendar": {
            "enabled": True,
            "calendars": {
                "sales": {"calendar_id": "sales@example.com"},
                "support": {"calendar_id": "support@example.com"},
            },
        },
        "microsoft_calendar": {
            "enabled": True,
            "accounts": {
                "dispatch": {"calendar_id": "dispatch"},
                "billing": {"calendar_id": "billing"},
            },
        },
        "leave_voicemail": {
            "enabled": True,
            "default_mailbox_key": "sales",
            "mailboxes": {
                "sales": {"name": "Sales", "extension": "2001"},
                "support": {"name": "Support", "extension": "2002"},
            },
        },
    },
    "in_call_tools": {},
}


def test_inherit_preserves_global_inventory_without_mutation():
    effective = resolve_agent_tool_config(GLOBAL, None)
    assert set(effective.config["tools"]["transfer"]["destinations"]) == {"sales", "support"}
    effective.config["tools"]["transfer"]["destinations"].clear()
    assert set(GLOBAL["tools"]["transfer"]["destinations"]) == {"sales", "support"}


def test_selected_filters_destinations_and_related_extensions():
    effective = resolve_agent_tool_config(
        GLOBAL,
        {"transfer": {"destination_policy": "selected", "destination_keys": ["sales"]}},
    )
    assert effective.effective_destination_keys == ("sales",)
    assert set(effective.config["tools"]["extensions"]["internal"]) == {"6001"}
    assert effective.config["tools"]["transfer"]["live_agent_destination_key"] == "sales"


def test_none_fails_closed_for_routes_and_live_agents():
    effective = resolve_agent_tool_config(
        GLOBAL, {"transfer": {"destination_policy": "none"}}
    )
    assert effective.config["tools"]["transfer"]["destinations"] == {}
    assert effective.config["tools"]["extensions"]["internal"] == {}
    assert "live_agent_destination_key" not in effective.config["tools"]["transfer"]


def test_stale_selected_key_is_reported_and_not_exposed():
    effective = resolve_agent_tool_config(
        GLOBAL,
        {"transfer": {"destination_policy": "selected", "destination_keys": ["missing"]}},
    )
    assert effective.stale_destination_keys == ("missing",)
    assert effective.effective_destination_keys == ()


def test_calendar_and_voicemail_resources_are_filtered_per_agent():
    effective = resolve_agent_tool_config(
        GLOBAL,
        {
            "google_calendar": {
                "calendar_policy": "selected",
                "calendar_keys": ["sales"],
            },
            "microsoft_calendar": {
                "account_policy": "selected",
                "account_keys": ["dispatch"],
            },
            "voicemail": {
                "mailbox_policy": "selected",
                "mailbox_key": "support",
            },
        },
    )
    tools = effective.config["tools"]
    assert tools["google_calendar"]["selected_calendars"] == ["sales"]
    assert tools["microsoft_calendar"]["selected_accounts"] == ["dispatch"]
    assert tools["leave_voicemail"]["extension"] == "2002"
    assert tools["leave_voicemail"]["selected_mailbox_key"] == "support"
    assert effective.effective_resource_keys["google_calendar"] == ("sales",)
    assert effective.effective_resource_keys["microsoft_calendar"] == ("dispatch",)
    assert effective.effective_resource_keys["voicemail"] == ("support",)


def test_none_and_stale_resource_policies_fail_closed():
    effective = resolve_agent_tool_config(
        GLOBAL,
        {
            "google_calendar": {"calendar_policy": "none", "calendar_keys": []},
            "microsoft_calendar": {
                "account_policy": "selected",
                "account_keys": ["missing"],
            },
            "voicemail": {
                "mailbox_policy": "selected",
                "mailbox_key": "missing",
            },
        },
    )
    tools = effective.config["tools"]
    assert tools["google_calendar"]["selected_calendars"] == []
    assert tools["microsoft_calendar"]["selected_accounts"] == []
    assert "extension" not in tools["leave_voicemail"]
    assert tools["leave_voicemail"]["mailboxes"] == {}
    assert effective.stale_resource_keys["microsoft_calendar"] == ("missing",)
    assert effective.stale_resource_keys["voicemail"] == ("missing",)


def test_legacy_single_voicemail_extension_is_default_inventory():
    config = {"tools": {"leave_voicemail": {"enabled": True, "extension": "2765"}}}
    effective = resolve_agent_tool_config(
        config,
        {"voicemail": {"mailbox_policy": "selected", "mailbox_key": "default"}},
    )
    assert effective.config["tools"]["leave_voicemail"]["extension"] == "2765"


def test_unknown_scope_rejected():
    with pytest.raises(ToolConfigPolicyError):
        normalize_agent_tool_configs({"calendar": {"policy": "none"}})


def test_generation_builds_an_isolated_registry():
    first = ToolRuntimeGeneration.build(generation_id=1, config=GLOBAL)
    second = ToolRuntimeGeneration.build(generation_id=2, config=GLOBAL)
    assert first.registry is not second.registry
    assert first.registry.get("blind_transfer") is not None
    assert first.config_hash == second.config_hash
