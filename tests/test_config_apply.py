from src.config_apply import classify_config_change


def test_no_change_requires_no_apply():
    decision = classify_config_change({"tools": {}}, {"tools": {}})

    assert decision.apply_required is False
    assert decision.restart_required is False
    assert decision.recommended_apply_method == "none"
    assert decision.apply_plan() == []


def test_tools_only_change_uses_hot_reload():
    decision = classify_config_change(
        {"tools": {}},
        {"tools": {"lookup": {"kind": "generic_http_lookup"}}},
    )

    assert decision.apply_required is True
    assert decision.restart_required is False
    assert decision.recommended_apply_method == "hot_reload"
    assert decision.apply_plan()[0]["endpoint"].endswith("/reload")


def test_provider_change_fails_closed_to_restart():
    decision = classify_config_change(
        {"providers": {"openai_realtime": {"enabled": True}}},
        {"providers": {"openai_realtime": {"enabled": False}}},
    )

    assert decision.apply_required is True
    assert decision.restart_required is True
    assert decision.recommended_apply_method == "restart"
    assert decision.apply_plan()[0]["endpoint"].endswith("/restart")


def test_missing_key_differs_from_explicit_none():
    decision = classify_config_change({"tools": None}, {})

    assert decision.changed_keys == frozenset({"tools"})
    assert decision.apply_required is True
    assert decision.restart_required is False
    assert decision.recommended_apply_method == "hot_reload"


def test_invalid_config_input_fails_closed_to_restart():
    decision = classify_config_change({"tools": {}}, None)

    assert decision.apply_required is True
    assert decision.restart_required is True
    assert decision.recommended_apply_method == "restart"
    assert decision.apply_plan()[0]["endpoint"].endswith("/restart")


def test_non_mapping_serializer_result_fails_closed_to_restart():
    class InvalidConfig:
        def model_dump(self):
            return []

    decision = classify_config_change({"tools": {}}, InvalidConfig())

    assert decision.apply_required is True
    assert decision.restart_required is True
    assert decision.recommended_apply_method == "restart"
