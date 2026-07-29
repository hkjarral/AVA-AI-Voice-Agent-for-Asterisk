"""Tests for legacy email-field parsing used by the v7.4 Context importer (#437).

_load_contexts() and _yaml_context_config() must both read email_recipient,
email_from, and email_enabled from the YAML context dict into the ContextConfig.
email_enabled is tri-state: None means inherit (key absent stays None).
"""

from unittest.mock import patch

from src.core.agent_store import AgentStoreReadError
from src.core.transport_orchestrator import TransportOrchestrator


def _orch_with_email():
    """Orchestrator whose YAML 'sales' context has all three email fields set."""
    return TransportOrchestrator({
        "contexts": {
            "sales": {
                "provider": "local",
                "prompt": "you are sales",
                "email_recipient": "sales@x.test",
                "email_from": "from@x.test",
                "email_enabled": True,
            }
        }
    })


def _orch_email_disabled():
    """Orchestrator with email_enabled explicitly False."""
    return TransportOrchestrator({
        "contexts": {
            "support": {
                "provider": "local",
                "email_recipient": "support@x.test",
                "email_from": "support-from@x.test",
                "email_enabled": False,
            }
        }
    })


def _orch_email_absent():
    """Orchestrator with no email keys in the context — fields should stay None."""
    return TransportOrchestrator({
        "contexts": {
            "billing": {
                "provider": "local",
                "prompt": "billing prompt",
            }
        }
    })


# ---------------------------------------------------------------------------
# _load_contexts path (YAML-only, no DB)
# ---------------------------------------------------------------------------

def test_load_contexts_reads_email_recipient():
    orch = _orch_with_email()
    cc = orch._yaml_context_config("sales")
    assert cc is not None
    assert cc.email_recipient == "sales@x.test"


def test_load_contexts_reads_email_from():
    orch = _orch_with_email()
    cc = orch._yaml_context_config("sales")
    assert cc is not None
    assert cc.email_from == "from@x.test"


def test_load_contexts_reads_email_enabled_true():
    orch = _orch_with_email()
    cc = orch._yaml_context_config("sales")
    assert cc is not None
    assert cc.email_enabled is True


def test_load_contexts_reads_email_enabled_false():
    orch = _orch_email_disabled()
    cc = orch._yaml_context_config("support")
    assert cc is not None
    assert cc.email_enabled is False


def test_load_contexts_absent_email_keys_stay_none():
    """Key absent in YAML => ContextConfig fields remain None (inherit)."""
    orch = _orch_email_absent()
    cc = orch._yaml_context_config("billing")
    assert cc is not None
    assert cc.email_recipient is None
    assert cc.email_from is None
    assert cc.email_enabled is None  # tri-state: None means inherit, not False


# ---------------------------------------------------------------------------
# Runtime fail-closed contract after Context removal
# ---------------------------------------------------------------------------

def test_unreadable_agent_store_does_not_resurrect_yaml_email_configuration():
    orch = _orch_with_email()
    with patch.object(orch.agent_store, "available", return_value=True), \
         patch.object(orch.agent_store, "resolve", side_effect=AgentStoreReadError("locked")):
        assert orch.get_context_config("sales") is None


def test_absent_agent_store_does_not_route_legacy_yaml_email_configuration():
    orch = _orch_with_email()
    with patch.object(orch.agent_store, "available", return_value=False):
        assert orch.get_context_config("sales") is None


# ---------------------------------------------------------------------------
# Integer coercion: exported 0/1 from disaster-recovery YAML export (#437 P2)
# ---------------------------------------------------------------------------

def _orch_email_int_enabled():
    """Orchestrator with email_enabled set to integer 1 (as emitted by the YAML export)."""
    return TransportOrchestrator({
        "contexts": {
            "sales_exported": {
                "provider": "local",
                "email_recipient": "sales@x.test",
                "email_from": "from@x.test",
                "email_enabled": 1,  # integer, as emitted by export_agents_yaml
            }
        }
    })


def _orch_email_int_disabled():
    """Orchestrator with email_enabled set to integer 0 (as emitted by the YAML export)."""
    return TransportOrchestrator({
        "contexts": {
            "support_exported": {
                "provider": "local",
                "email_recipient": "support@x.test",
                "email_from": "support-from@x.test",
                "email_enabled": 0,  # integer, as emitted by export_agents_yaml
            }
        }
    })


def test_load_contexts_int_1_coerced_to_true():
    """YAML email_enabled: 1 (int from export) must resolve to True (not int 1).

    The dispatch gate uses `is True` / `is False` strict identity; an uncoerced
    int 1 satisfies `== True` but fails `is True`, silently disabling email.
    """
    orch = _orch_email_int_enabled()
    cc = orch._yaml_context_config("sales_exported")
    assert cc is not None
    assert cc.email_enabled is True  # strict identity, not == True


def test_load_contexts_int_0_coerced_to_false():
    """YAML email_enabled: 0 (int from export) must resolve to False (not int 0).

    The dispatch gate uses `is False` strict identity; an uncoerced int 0
    satisfies `== False` but fails `is False`, silently enabling email when
    it should be suppressed.
    """
    orch = _orch_email_int_disabled()
    cc = orch._yaml_context_config("support_exported")
    assert cc is not None
    assert cc.email_enabled is False  # strict identity, not == False


# ---------------------------------------------------------------------------
# String coercion: quoted YAML scalars (Codex P2, PR #473)
# ---------------------------------------------------------------------------

def _orch_email_string(value: str, context_name: str = "ctx"):
    """Helper: build an orchestrator with email_enabled set to a string value."""
    return TransportOrchestrator({
        "contexts": {
            context_name: {
                "provider": "local",
                "email_recipient": "test@x.test",
                "email_enabled": value,
            }
        }
    })


def test_string_false_coerced_to_false():
    """email_enabled: 'false' (quoted YAML scalar) must resolve to False, not True.

    bool('false') == True in Python; _coerce_optional_bool must handle this.
    """
    orch = _orch_email_string("false")
    cc = orch._yaml_context_config("ctx")
    assert cc is not None
    assert cc.email_enabled is False


def test_string_zero_coerced_to_false():
    """email_enabled: '0' (quoted YAML scalar) must resolve to False, not True."""
    orch = _orch_email_string("0")
    cc = orch._yaml_context_config("ctx")
    assert cc is not None
    assert cc.email_enabled is False


def test_string_true_coerced_to_true():
    """email_enabled: 'true' (quoted YAML scalar) must resolve to True."""
    orch = _orch_email_string("true")
    cc = orch._yaml_context_config("ctx")
    assert cc is not None
    assert cc.email_enabled is True


def test_string_one_coerced_to_true():
    """email_enabled: '1' (quoted YAML scalar) must resolve to True."""
    orch = _orch_email_string("1")
    cc = orch._yaml_context_config("ctx")
    assert cc is not None
    assert cc.email_enabled is True


def test_string_yes_coerced_to_true():
    """email_enabled: 'yes' must resolve to True."""
    orch = _orch_email_string("yes")
    cc = orch._yaml_context_config("ctx")
    assert cc is not None
    assert cc.email_enabled is True


def test_string_no_coerced_to_false():
    """email_enabled: 'no' must resolve to False."""
    orch = _orch_email_string("no")
    cc = orch._yaml_context_config("ctx")
    assert cc is not None
    assert cc.email_enabled is False


def test_unrecognized_string_coerced_to_none():
    """email_enabled: 'maybe' (unrecognized string) must resolve to None (inherit).

    Safest default: don't force-enable email for unknown string values.
    """
    orch = _orch_email_string("maybe")
    cc = orch._yaml_context_config("ctx")
    assert cc is not None
    assert cc.email_enabled is None


def test_unexpected_int_coerced_to_none():
    """email_enabled: 2 (typo / unexpected numeric) must resolve to None (inherit),
    not True. Only 0/1 are recognized; never force-enable on a garbage numeric."""
    orch = _orch_email_string(2)
    cc = orch._yaml_context_config("ctx")
    assert cc is not None
    assert cc.email_enabled is None


def test_unexpected_float_coerced_to_none():
    """email_enabled: 0.5 (unexpected numeric) must resolve to None (inherit)."""
    orch = _orch_email_string(0.5)
    cc = orch._yaml_context_config("ctx")
    assert cc is not None
    assert cc.email_enabled is None


def test_blank_string_coerced_to_none():
    """email_enabled: '' (cleared field / blank override) must resolve to None
    (inherit), not False — a blank value must not become an explicit disable."""
    for blank in ("", "   "):
        orch = _orch_email_string(blank)
        cc = orch._yaml_context_config("ctx")
        assert cc is not None
        assert cc.email_enabled is None
