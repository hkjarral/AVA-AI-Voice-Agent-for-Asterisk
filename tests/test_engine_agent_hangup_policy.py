from copy import deepcopy
from types import SimpleNamespace

from src.core.models import CallSession
from src.engine import Engine


def _generation(global_config):
    def for_agent(_policy):
        return SimpleNamespace(
            config=deepcopy(global_config),
            policy="inherit",
            requested_destination_keys=(),
            effective_destination_keys=(),
            stale_destination_keys=(),
            policies={"transfer": "inherit"},
            effective_resource_keys={"transfer": ()},
            stale_resource_keys={},
        )

    return SimpleNamespace(
        generation_id=7,
        config_hash="global-hash",
        registry=SimpleNamespace(),
        for_agent=for_agent,
    )


def _resolve(agent_hangup_policy):
    engine = Engine.__new__(Engine)
    engine._tool_generation = _generation(
        {
            "tools": {
                "hangup_call": {
                    "policy": {"markers": {"end_call": ["goodbye"]}}
                }
            }
        }
    )
    session = CallSession(call_id="call-1", caller_channel_id="call-1")
    context = SimpleNamespace(
        tool_configs=None,
        hangup_policy=agent_hangup_policy,
        in_call_http_tools=None,
    )
    Engine._resolve_session_tool_runtime(engine, session, context)
    return session


def test_agent_extend_markers_are_captured_in_call_snapshot():
    session = _resolve({"strategy": "extend", "end_call": ["да", "нет"]})
    markers = session.tool_runtime_config["tools"]["hangup_call"]["policy"][
        "markers"
    ]["end_call"]
    assert markers == ["goodbye", "да", "нет"]
    assert session.hangup_marker_policy["source"] == "agent_extend"
    assert session.hangup_marker_policy["count"] == 3


def test_agent_replace_markers_excludes_global_values():
    session = _resolve({"strategy": "replace", "end_call": ["до свидания"]})
    markers = session.tool_runtime_config["tools"]["hangup_call"]["policy"][
        "markers"
    ]["end_call"]
    assert markers == ["до свидания"]
    assert session.hangup_marker_policy["source"] == "agent_replace"


def test_agent_without_override_inherits_global_markers():
    session = _resolve(None)
    markers = session.tool_runtime_config["tools"]["hangup_call"]["policy"][
        "markers"
    ]["end_call"]
    assert markers == ["goodbye"]
    assert session.hangup_marker_policy["source"] == "global"
