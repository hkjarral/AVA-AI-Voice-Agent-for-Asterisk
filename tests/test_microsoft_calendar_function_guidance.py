def test_calendar_actions_are_not_exposed_as_function_names():
    from src.tools.runtime_guidance import build_in_call_tool_runtime_guidance

    g = build_in_call_tool_runtime_guidance(
        {"tools": {"microsoft_calendar": {}}}, ["microsoft_calendar"]
    )
    assert "only calendar function name is microsoft_calendar" in g
    for action in ["check_availability", "reschedule_event", "delete_event"]:
        assert f"call {action}" not in g
        assert f"action={action}" in g
