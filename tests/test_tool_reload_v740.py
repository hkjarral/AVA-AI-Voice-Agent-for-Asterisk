import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.core.models import CallSession
from src.engine import Engine
from src.tools.runtime_config import ToolRuntimeGeneration


def _config(destination: str):
    payload = {
        "tools": {
            "transfer": {
                "enabled": True,
                "destinations": {
                    destination: {"type": "extension", "target": "6000"}
                },
            }
        },
        "providers": {},
        "contexts": {},
    }
    return SimpleNamespace(
        dict=lambda: payload,
        providers={},
        contexts={},
        prompts={},
        mcp=None,
    )


def _engine(old_generation):
    engine = Engine.__new__(Engine)
    engine.config = _config("old")
    engine.providers = {}
    engine._tool_generation = old_generation
    engine._next_tool_generation_id = old_generation.generation_id + 1
    engine._tool_reload_lock = asyncio.Lock()
    engine._is_request_authorized = lambda _request: True
    engine._config_hash = "old-hash"
    engine._compute_config_hash = lambda _config=None: "new-hash"
    return engine


@pytest.mark.asyncio
async def test_reload_publishes_new_generation_without_mutating_active_call():
    old = ToolRuntimeGeneration.build(generation_id=1, config=_config("old").dict())
    new = ToolRuntimeGeneration.build(generation_id=2, config=_config("new").dict())
    engine = _engine(old)
    active_call = CallSession(call_id="active", caller_channel_id="active")
    active_call.tool_runtime_generation = old
    active_call.tool_runtime_registry = old.registry
    active_call.tool_runtime_config = old.config
    engine._build_tool_generation = lambda _config: new

    offloaded = []

    async def run_in_thread(function, *args):
        offloaded.append(function)
        return function(*args)

    with patch("src.config.load_config", return_value=_config("new")), patch(
        "src.engine.TransportOrchestrator", return_value=SimpleNamespace()
    ), patch("src.engine.asyncio.to_thread", side_effect=run_in_thread):
        response = await Engine._reload_handler(engine, SimpleNamespace())

    payload = json.loads(response.text)
    assert response.status == 200
    assert payload["tool_generation"] == 2
    assert engine._tool_generation is new
    assert engine._next_tool_generation_id == 3
    assert Engine._tool_registry_for_session(engine, active_call) is old.registry
    assert Engine._tool_config_for_session(engine, active_call)["tools"]["transfer"]["destinations"] == {
        "old": {"type": "extension", "target": "6000"}
    }
    assert Engine._tool_registry_for_session(engine, None) is new.registry
    assert offloaded == [engine._build_tool_generation]
    assert not engine._tool_reload_lock.locked()


@pytest.mark.asyncio
async def test_reload_validation_failure_keeps_previous_generation():
    old = ToolRuntimeGeneration.build(generation_id=7, config=_config("old").dict())
    engine = _engine(old)
    engine._build_tool_generation = lambda _config: (_ for _ in ()).throw(ValueError("bad tool config"))

    with patch("src.config.load_config", return_value=_config("new")):
        response = await Engine._reload_handler(engine, SimpleNamespace())

    payload = json.loads(response.text)
    assert response.status == 500
    assert payload["running_tool_generation"] == 7
    assert engine._tool_generation is old
    assert engine._next_tool_generation_id == 8
    assert not engine._tool_reload_lock.locked()


@pytest.mark.asyncio
async def test_concurrent_reload_returns_conflict_without_releasing_owner_lock():
    old = ToolRuntimeGeneration.build(generation_id=1, config=_config("old").dict())
    engine = _engine(old)
    await engine._tool_reload_lock.acquire()
    try:
        response = await Engine._reload_handler(engine, SimpleNamespace())
        assert response.status == 409
        assert engine._tool_reload_lock.locked()
    finally:
        engine._tool_reload_lock.release()
