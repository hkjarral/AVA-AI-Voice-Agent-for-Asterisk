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
        "check_extension_status": {
            "restrict_to_configured_extensions": False,
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
    assert effective.config["tools"]["check_extension_status"][
        "restrict_to_configured_extensions"
    ] is False
    effective.config["tools"]["transfer"]["destinations"].clear()
    assert set(GLOBAL["tools"]["transfer"]["destinations"]) == {"sales", "support"}


def test_inherit_filters_placeholder_transfer_destinations():
    effective = resolve_agent_tool_config(
        {
            "tools": {
                "transfer": {
                    "destinations": {
                        "sales": {"type": "extension", "target": "6001"},
                        "support_queue": None,
                    }
                }
            }
        },
        None,
    )

    assert effective.config["tools"]["transfer"]["destinations"] == {
        "sales": {"type": "extension", "target": "6001"}
    }
    assert effective.effective_destination_keys == ("sales",)


def test_selected_filters_destinations_and_related_extensions():
    effective = resolve_agent_tool_config(
        GLOBAL,
        {"transfer": {"destination_policy": "selected", "destination_keys": ["sales"]}},
    )
    assert effective.effective_destination_keys == ("sales",)
    assert set(effective.config["tools"]["extensions"]["internal"]) == {"6001"}
    assert effective.config["tools"]["transfer"]["live_agent_destination_key"] == "sales"
    assert effective.config["tools"]["check_extension_status"][
        "restrict_to_configured_extensions"
    ] is True


def test_none_fails_closed_for_routes_and_live_agents():
    effective = resolve_agent_tool_config(
        GLOBAL, {"transfer": {"destination_policy": "none"}}
    )
    assert effective.config["tools"]["transfer"]["destinations"] == {}
    assert effective.config["tools"]["extensions"]["internal"] == {}
    assert "live_agent_destination_key" not in effective.config["tools"]["transfer"]
    assert effective.config["tools"]["check_extension_status"][
        "restrict_to_configured_extensions"
    ] is True


def test_stale_selected_key_is_reported_and_not_exposed():
    effective = resolve_agent_tool_config(
        GLOBAL,
        {"transfer": {"destination_policy": "selected", "destination_keys": ["missing"]}},
    )
    assert effective.stale_destination_keys == ("missing",)
    assert effective.effective_destination_keys == ()


def test_selected_placeholder_destination_is_stale_and_not_exposed():
    effective = resolve_agent_tool_config(
        {"tools": {"transfer": {"destinations": {"support_queue": None}}}},
        {
            "transfer": {
                "destination_policy": "selected",
                "destination_keys": ["support_queue"],
            }
        },
    )

    assert effective.config["tools"]["transfer"]["destinations"] == {}
    assert effective.stale_destination_keys == ("support_queue",)
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


def test_startup_skips_only_bad_http_templates_and_never_restores_preserved_copies(caplog):
    from src.tools.http.template import TemplateError
    from src.tools.registry import ToolRegistry
    config = {
        "tools": {
            "valid_pre": {"kind": "generic_http_lookup", "url": "https://example.invalid"},
            "bad_pre": {"kind": "generic_http_lookup", "url": "https://example.invalid", "method": "POST", "body_template": '{"custom":"{crm}"}'},
            "valid_post": {"kind": "generic_webhook", "url": "https://example.invalid"},
            "bad_post": {"kind": "generic_webhook", "url": "https://example.invalid", "payload_template": '{"bad":'},
        },
        "in_call_tools": {
            "valid_in": {"kind": "in_call_http_lookup", "url": "https://example.invalid"},
            "bad_in": {"url": "https://example.invalid", "body_template": '{"bad":', "method": "POST"},
        },
    }
    preserved = ToolRegistry.isolated()
    preserved.initialize_http_tools_from_config({"bad_post": {"kind": "generic_webhook", "url": "https://example.invalid"}})
    generation = ToolRuntimeGeneration.build(generation_id=1, config=config, preserved_tools=preserved.get_all(),
                                              skip_invalid_http_templates=True)
    assert generation.registry.has("valid_pre") and generation.registry.has("valid_in")
    assert generation.registry.has("valid_post") and not generation.registry.has("bad_pre")
    assert generation.registry.has("blind_transfer")
    assert not generation.registry.has("bad_post") and not generation.registry.has("bad_in")
    assert "bad_post" not in generation.config["tools"]
    assert "bad_in" not in generation.for_agent(None).config["in_call_tools"]
    assert "bad_post" in config["tools"]  # Operator config is never rewritten.
    assert "error_code=invalid_json_template" in caplog.text
    with pytest.raises(TemplateError):
        ToolRuntimeGeneration.build(generation_id=2, config=config)
    assert generation.registry.has("valid_pre") and not generation.registry.has("bad_post")


def test_startup_http_policy_does_not_relax_handoff_or_pushover_validation():
    with pytest.raises(ValueError, match="message is required"):
        ToolRuntimeGeneration.build(generation_id=1, skip_invalid_http_templates=True,
                                    config={"tools": {"transfer": {"handoff": {"enabled": True}}}})
    with pytest.raises(ToolConfigPolicyError):
        ToolRuntimeGeneration.build(generation_id=1, skip_invalid_http_templates=True,
                                    config={"tools": {"notify": {"kind": "pushover", "phase": "invalid"}}})
