from unittest.mock import patch
from src.core.transport_orchestrator import TransportOrchestrator, ContextConfig


def _orch():
    # Build an orchestrator whose YAML contexts contain "sales" with prompt "from-yaml"
    return TransportOrchestrator({"contexts": {"sales": {"provider": "p", "prompt": "from-yaml"}}})


def test_db_agent_wins_over_yaml():
    orch = _orch()
    db_cc = ContextConfig(prompt="from-db", provider="p")
    # DB present + slug resolves => DB config wins over the same-named YAML context.
    with patch.object(orch.agent_store, "available", return_value=True), \
         patch.object(orch.agent_store, "resolve", return_value=db_cc):
        assert orch.get_context_config("sales").prompt == "from-db"


def test_yaml_fallback_when_db_absent():
    orch = _orch()
    # DB absent => fall back to the legacy YAML context (headless / pre-migration).
    with patch.object(orch.agent_store, "available", return_value=False):
        cc = orch.get_context_config("sales")
        assert cc is not None and cc.prompt == "from-yaml"


def test_inactive_or_unknown_slug_not_routable_when_db_present():
    # DB present but slug is inactive/unknown (resolve() returns None): the resolver
    # must NOT fall through to the same-named legacy YAML context — a deactivated or
    # deleted agent must stop routing (agents.db is the source of truth).
    orch = _orch()
    with patch.object(orch.agent_store, "available", return_value=True), \
         patch.object(orch.agent_store, "resolve", return_value=None):
        assert orch.get_context_config("sales") is None


def test_none_context_returns_none():
    orch = _orch()
    assert orch.get_context_config(None) is None


def test_resolved_context_applies_agent_audio_profile():
    # AI_AGENT / DB-default calls expose no AI_CONTEXT channel var. The agent's
    # audio_profile must still apply when the resolved context is passed explicitly.
    orch = TransportOrchestrator({
        "profiles": {
            "agent_profile": {"internal_rate_hz": 16000, "transport_out": {}, "provider_pref": {}},
        },
    })
    db_cc = ContextConfig(prompt="p", provider="prov", profile="agent_profile")
    with patch.object(orch.agent_store, "available", return_value=True), \
         patch.object(orch.agent_store, "resolve", return_value=db_cc):
        # channel_vars has NO AI_CONTEXT — only the caller-resolved context is supplied.
        transport = orch.resolve_transport(
            provider_name="prov",
            provider_caps=None,
            channel_vars={},
            resolved_context="sales-agent",
        )
    assert transport.profile_name == "agent_profile"
    assert transport.context == "sales-agent"
