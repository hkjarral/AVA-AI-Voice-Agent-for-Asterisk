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
