"""A YAML/agents.db mismatch must fail before a generic provider starts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.models import CallSession
from src.engine import Engine


@pytest.mark.asyncio
async def test_shadowed_yaml_context_records_failure_before_provider_resolution():
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(default_provider="local")
    engine.providers = {"local": object()}
    engine.transport_orchestrator = MagicMock()
    engine.transport_orchestrator.agent_store.default_slug.return_value = None
    engine.transport_orchestrator.yaml_context_shadowed_by_agent_db.return_value = True
    engine._save_session = AsyncMock()
    engine._configure_no_input_watchdog = AsyncMock()
    engine.ari_client = MagicMock()

    values = {
        "AI_CONTEXT": "demo_local_full",
        "AI_AGENT": "",
        "AI_PROVIDER": "",
        "AI_AUDIO_PROFILE": "",
    }

    async def send_command(_method, _resource, **kwargs):
        variable = (kwargs.get("params") or {}).get("variable")
        return {"value": values.get(variable, "")}

    engine.ari_client.send_command = AsyncMock(side_effect=send_command)
    session = CallSession(call_id="call-shadowed", caller_channel_id="chan-shadowed")

    await Engine._resolve_audio_profile(engine, session, "chan-shadowed")

    assert session.context_name == "demo_local_full"
    assert session.routing_method == "ai_context"
    assert "authoritative agents.db" in session.context_resolution_error
    assert session.error_message == session.context_resolution_error
    engine.transport_orchestrator.resolve_transport.assert_not_called()
