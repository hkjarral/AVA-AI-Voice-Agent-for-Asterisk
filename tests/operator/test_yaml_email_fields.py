"""Tests for per-agent email fields honored on the YAML / fallback path (#437).

_load_contexts() and _yaml_context_config() must both read email_recipient,
email_from, and email_enabled from the YAML context dict into the ContextConfig.
email_enabled is tri-state: None means inherit (key absent stays None).
"""

from unittest.mock import patch

from src.core.agent_store import AgentStoreReadError
from src.core.transport_orchestrator import TransportOrchestrator, ContextConfig


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
    # DB absent => YAML path
    with patch.object(orch.agent_store, "available", return_value=False):
        cc = orch.get_context_config("sales")
    assert cc is not None
    assert cc.email_recipient == "sales@x.test"


def test_load_contexts_reads_email_from():
    orch = _orch_with_email()
    with patch.object(orch.agent_store, "available", return_value=False):
        cc = orch.get_context_config("sales")
    assert cc is not None
    assert cc.email_from == "from@x.test"


def test_load_contexts_reads_email_enabled_true():
    orch = _orch_with_email()
    with patch.object(orch.agent_store, "available", return_value=False):
        cc = orch.get_context_config("sales")
    assert cc is not None
    assert cc.email_enabled is True


def test_load_contexts_reads_email_enabled_false():
    orch = _orch_email_disabled()
    with patch.object(orch.agent_store, "available", return_value=False):
        cc = orch.get_context_config("support")
    assert cc is not None
    assert cc.email_enabled is False


def test_load_contexts_absent_email_keys_stay_none():
    """Key absent in YAML => ContextConfig fields remain None (inherit)."""
    orch = _orch_email_absent()
    with patch.object(orch.agent_store, "available", return_value=False):
        cc = orch.get_context_config("billing")
    assert cc is not None
    assert cc.email_recipient is None
    assert cc.email_from is None
    assert cc.email_enabled is None  # tri-state: None means inherit, not False


# ---------------------------------------------------------------------------
# _yaml_context_config path (fallback when DB is present but unreadable)
# ---------------------------------------------------------------------------

def test_yaml_context_config_fallback_reads_email_recipient():
    """When agents.db is unreadable, fallback to YAML must still carry email_recipient."""
    orch = _orch_with_email()
    with patch.object(orch.agent_store, "available", return_value=True), \
         patch.object(orch.agent_store, "resolve", side_effect=AgentStoreReadError("locked")):
        cc = orch.get_context_config("sales")
    assert cc is not None
    assert cc.email_recipient == "sales@x.test"


def test_yaml_context_config_fallback_reads_email_from():
    orch = _orch_with_email()
    with patch.object(orch.agent_store, "available", return_value=True), \
         patch.object(orch.agent_store, "resolve", side_effect=AgentStoreReadError("locked")):
        cc = orch.get_context_config("sales")
    assert cc is not None
    assert cc.email_from == "from@x.test"


def test_yaml_context_config_fallback_reads_email_enabled_true():
    orch = _orch_with_email()
    with patch.object(orch.agent_store, "available", return_value=True), \
         patch.object(orch.agent_store, "resolve", side_effect=AgentStoreReadError("locked")):
        cc = orch.get_context_config("sales")
    assert cc is not None
    assert cc.email_enabled is True


def test_yaml_context_config_fallback_absent_email_stays_none():
    """Fallback path: absent email keys must stay None, not coerced to False."""
    orch = _orch_email_absent()
    with patch.object(orch.agent_store, "available", return_value=True), \
         patch.object(orch.agent_store, "resolve", side_effect=AgentStoreReadError("locked")):
        cc = orch.get_context_config("billing")
    assert cc is not None
    assert cc.email_enabled is None


def test_yaml_and_fallback_paths_return_identical_email_fields():
    """Both paths (YAML-only and DB-fallback) must resolve email fields identically."""
    orch = _orch_with_email()

    # YAML-only path (no DB)
    with patch.object(orch.agent_store, "available", return_value=False):
        cc_yaml = orch.get_context_config("sales")

    # DB-fallback path (DB present but unreadable)
    with patch.object(orch.agent_store, "available", return_value=True), \
         patch.object(orch.agent_store, "resolve", side_effect=AgentStoreReadError("corrupt")):
        cc_fallback = orch.get_context_config("sales")

    assert cc_yaml is not None and cc_fallback is not None
    assert cc_yaml.email_recipient == cc_fallback.email_recipient
    assert cc_yaml.email_from == cc_fallback.email_from
    assert cc_yaml.email_enabled == cc_fallback.email_enabled


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
    with patch.object(orch.agent_store, "available", return_value=False):
        cc = orch.get_context_config("sales_exported")
    assert cc is not None
    assert cc.email_enabled is True  # strict identity, not == True


def test_load_contexts_int_0_coerced_to_false():
    """YAML email_enabled: 0 (int from export) must resolve to False (not int 0).

    The dispatch gate uses `is False` strict identity; an uncoerced int 0
    satisfies `== False` but fails `is False`, silently enabling email when
    it should be suppressed.
    """
    orch = _orch_email_int_disabled()
    with patch.object(orch.agent_store, "available", return_value=False):
        cc = orch.get_context_config("support_exported")
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
    with patch.object(orch.agent_store, "available", return_value=False):
        cc = orch.get_context_config("ctx")
    assert cc is not None
    assert cc.email_enabled is False


def test_string_zero_coerced_to_false():
    """email_enabled: '0' (quoted YAML scalar) must resolve to False, not True."""
    orch = _orch_email_string("0")
    with patch.object(orch.agent_store, "available", return_value=False):
        cc = orch.get_context_config("ctx")
    assert cc is not None
    assert cc.email_enabled is False


def test_string_true_coerced_to_true():
    """email_enabled: 'true' (quoted YAML scalar) must resolve to True."""
    orch = _orch_email_string("true")
    with patch.object(orch.agent_store, "available", return_value=False):
        cc = orch.get_context_config("ctx")
    assert cc is not None
    assert cc.email_enabled is True


def test_string_one_coerced_to_true():
    """email_enabled: '1' (quoted YAML scalar) must resolve to True."""
    orch = _orch_email_string("1")
    with patch.object(orch.agent_store, "available", return_value=False):
        cc = orch.get_context_config("ctx")
    assert cc is not None
    assert cc.email_enabled is True


def test_string_yes_coerced_to_true():
    """email_enabled: 'yes' must resolve to True."""
    orch = _orch_email_string("yes")
    with patch.object(orch.agent_store, "available", return_value=False):
        cc = orch.get_context_config("ctx")
    assert cc is not None
    assert cc.email_enabled is True


def test_string_no_coerced_to_false():
    """email_enabled: 'no' must resolve to False."""
    orch = _orch_email_string("no")
    with patch.object(orch.agent_store, "available", return_value=False):
        cc = orch.get_context_config("ctx")
    assert cc is not None
    assert cc.email_enabled is False


def test_unrecognized_string_coerced_to_none():
    """email_enabled: 'maybe' (unrecognized string) must resolve to None (inherit).

    Safest default: don't force-enable email for unknown string values.
    """
    orch = _orch_email_string("maybe")
    with patch.object(orch.agent_store, "available", return_value=False):
        cc = orch.get_context_config("ctx")
    assert cc is not None
    assert cc.email_enabled is None


def test_unexpected_int_coerced_to_none():
    """email_enabled: 2 (typo / unexpected numeric) must resolve to None (inherit),
    not True. Only 0/1 are recognized; never force-enable on a garbage numeric."""
    orch = _orch_email_string(2)
    with patch.object(orch.agent_store, "available", return_value=False):
        cc = orch.get_context_config("ctx")
    assert cc is not None
    assert cc.email_enabled is None


def test_unexpected_float_coerced_to_none():
    """email_enabled: 0.5 (unexpected numeric) must resolve to None (inherit)."""
    orch = _orch_email_string(0.5)
    with patch.object(orch.agent_store, "available", return_value=False):
        cc = orch.get_context_config("ctx")
    assert cc is not None
    assert cc.email_enabled is None


def test_blank_string_coerced_to_none():
    """email_enabled: '' (cleared field / blank override) must resolve to None
    (inherit), not False — a blank value must not become an explicit disable."""
    for blank in ("", "   "):
        orch = _orch_email_string(blank)
        with patch.object(orch.agent_store, "available", return_value=False):
            cc = orch.get_context_config("ctx")
        assert cc is not None
        assert cc.email_enabled is None
