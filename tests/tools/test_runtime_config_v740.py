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


def test_unknown_scope_rejected():
    with pytest.raises(ToolConfigPolicyError):
        normalize_agent_tool_configs({"calendar": {"policy": "none"}})


def test_generation_builds_an_isolated_registry():
    first = ToolRuntimeGeneration.build(generation_id=1, config=GLOBAL)
    second = ToolRuntimeGeneration.build(generation_id=2, config=GLOBAL)
    assert first.registry is not second.registry
    assert first.registry.get("blind_transfer") is not None
    assert first.config_hash == second.config_hash
