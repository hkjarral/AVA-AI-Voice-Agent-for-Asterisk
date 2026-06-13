from unittest.mock import patch
from src.core.transport_orchestrator import TransportOrchestrator, ContextConfig


def _orch():
    # Build an orchestrator whose YAML contexts contain "sales" with prompt "from-yaml"
    return TransportOrchestrator({"contexts": {"sales": {"provider": "p", "prompt": "from-yaml"}}})


def test_db_agent_wins_over_yaml():
    orch = _orch()
    db_cc = ContextConfig(prompt="from-db", provider="p")
    with patch.object(orch.agent_store, "resolve", return_value=db_cc):
        assert orch.get_context_config("sales").prompt == "from-db"


def test_yaml_fallback_when_db_absent():
    orch = _orch()
    with patch.object(orch.agent_store, "resolve", return_value=None):
        cc = orch.get_context_config("sales")
        assert cc is not None and cc.prompt == "from-yaml"


def test_none_context_returns_none():
    orch = _orch()
    with patch.object(orch.agent_store, "resolve", return_value=None):
        assert orch.get_context_config(None) is None
