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
