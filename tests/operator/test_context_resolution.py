from unittest.mock import patch
from src.core.agent_store import AgentStoreReadError
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


def test_corrupt_db_falls_back_to_yaml():
    # HIGH-9: DB present but unreadable (corrupt/locked) => resolve() raises
    # AgentStoreReadError and the orchestrator falls back to the legacy YAML context,
    # so a corrupted agents.db doesn't take routing down entirely.
    orch = _orch()
    with patch.object(orch.agent_store, "available", return_value=True), \
         patch.object(orch.agent_store, "resolve", side_effect=AgentStoreReadError("corrupt")):
        cc = orch.get_context_config("sales")
        assert cc is not None and cc.prompt == "from-yaml"


def test_none_context_returns_none():
    orch = _orch()
    assert orch.get_context_config(None) is None


def test_routing_method_threads_prefer_to_resolve():
    # Finding 1: get_context_config must translate the dialplan channel-variable
    # INTENT (session.routing_method) into the agent_store.resolve prefer arg.
    orch = _orch()
    db_cc = ContextConfig(prompt="p", provider="p")
    with patch.object(orch.agent_store, "available", return_value=True), \
         patch.object(orch.agent_store, "resolve", return_value=db_cc) as resolve:
        # AI_CONTEXT (legacy original-name selector) => display_name-first.
        orch.get_context_config("sales", "ai_context")
        assert resolve.call_args.kwargs["prefer"] == "display_name"
        # AI_AGENT (canonical slug selector) => slug-first (anti-shadow).
        orch.get_context_config("sales", "ai_agent")
        assert resolve.call_args.kwargs["prefer"] == "slug"
        # default / unknown / None => slug-first (safest canonical).
        orch.get_context_config("sales", "default")
        assert resolve.call_args.kwargs["prefer"] == "slug"
        orch.get_context_config("sales")
        assert resolve.call_args.kwargs["prefer"] == "slug"


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


def test_resolve_transport_audio_profile_honors_ai_context_intent():
    # Finding 1: the audio-profile lookup (resolve_transport -> _resolve_profile_name
    # -> get_context_config) previously omitted routing_method, so for a disambiguated
    # AI_CONTEXT collision it would slug-first resolve to the WRONG agent's profile
    # while prompt/tools used the right one. resolve_transport must thread the routing
    # INTENT so the profile path resolves the SAME agent (prefer=display_name).
    orch = TransportOrchestrator({
        "profiles": {
            "east_profile": {"internal_rate_hz": 16000, "transport_out": {}, "provider_pref": {}},
            "plain_profile": {"internal_rate_hz": 8000, "transport_out": {}, "provider_pref": {}},
        },
    })

    # Two agents whose collision was disambiguated by migration: slug "sales_east"
    # (display_name "Sales-East", east_profile) and slug "sales_east_2" (display_name
    # "sales_east", plain_profile). An AI_CONTEXT=sales_east call must reach the agent
    # whose ORIGINAL name was "sales_east" (plain_profile), matching prompt/tools.
    def fake_resolve(name, prefer="slug"):
        if prefer == "display_name" and name == "sales_east":
            return ContextConfig(prompt="plain", provider="prov", profile="plain_profile")
        return ContextConfig(prompt="east", provider="prov", profile="east_profile")

    with patch.object(orch.agent_store, "available", return_value=True), \
         patch.object(orch.agent_store, "resolve", side_effect=fake_resolve):
        # ai_context intent -> display_name-first -> plain_profile (the right agent)
        t_ctx = orch.resolve_transport(
            provider_name="prov", provider_caps=None, channel_vars={},
            resolved_context="sales_east", routing_method="ai_context",
        )
        assert t_ctx.profile_name == "plain_profile"

        # ai_agent intent -> slug-first -> east_profile (canonical, anti-shadow)
        t_agent = orch.resolve_transport(
            provider_name="prov", provider_caps=None, channel_vars={},
            resolved_context="sales_east", routing_method="ai_agent",
        )
        assert t_agent.profile_name == "east_profile"
