"""MED-R6: LLM tuning-only switch in minimal (no-GPU) runtime mode must fail
explicitly instead of falsely reporting success.

In minimal mode local-ai-server runs with llm_model=None, so context/max_tokens/
filler tweaks have no effect. The verify path only checks llm.loaded when a
model_path is set, so without an explicit guard a tuning-only switch would pass
verification and claim success on a server that never loaded an LLM.
"""

import json
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

pytest.importorskip("fastapi")

from api import local_ai  # noqa: E402
from api.local_ai import SwitchModelRequest, switch_model  # noqa: E402


class _FakeWS:
    """Async-context-manager websocket double that replies based on the last
    message it received: status -> status_response (with runtime_mode),
    switch_model -> a successful switch_response."""

    def __init__(self, runtime_mode: str):
        self._runtime_mode = runtime_mode
        self._last_type = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, raw):
        try:
            self._last_type = json.loads(raw).get("type")
        except Exception:
            self._last_type = None

    async def recv(self):
        if self._last_type == "auth":
            return json.dumps({"type": "auth_response", "status": "ok"})
        if self._last_type == "switch_model":
            return json.dumps({"type": "switch_response", "status": "success"})
        return json.dumps(
            {
                "type": "status_response",
                "status": "ok",
                "models": {
                    "llm": {
                        "loaded": self._runtime_mode != "minimal",
                        # Report the context the full-mode test requests so the
                        # verify path (_status_matches) accepts the hot-switch.
                        "config": {"context": 2048},
                    }
                },
                "config": {"runtime_mode": self._runtime_mode},
            }
        )


def _connect_factory(runtime_mode: str):
    def _connect(*_args, **_kwargs):
        return _FakeWS(runtime_mode)

    return _connect


@pytest.mark.asyncio
async def test_llm_tuning_only_in_minimal_mode_returns_error(monkeypatch):
    monkeypatch.setattr(local_ai.websockets, "connect", _connect_factory("minimal"))

    req = SwitchModelRequest(
        model_type="llm",
        model_path=None,  # tuning-only
        llm_context=2048,
        llm_max_tokens=64,
        force_incompatible_apply=True,  # skip active-call gate
    )

    resp = await switch_model(req)

    assert resp.success is False
    assert "minimal" in resp.message.lower()
    assert resp.requires_restart is False


@pytest.mark.asyncio
async def test_llm_tuning_only_in_full_mode_does_not_short_circuit(monkeypatch):
    """In full mode the minimal guard must NOT fire; the switch proceeds via the
    normal hot-switch + verify path and succeeds."""
    monkeypatch.setattr(local_ai.websockets, "connect", _connect_factory("full"))
    monkeypatch.setattr(local_ai, "_update_env_file", lambda *a, **k: None)

    req = SwitchModelRequest(
        model_type="llm",
        model_path=None,
        llm_context=2048,
        force_incompatible_apply=True,
    )

    resp = await switch_model(req)

    # Guard did not fire (would have been success=False with a minimal message);
    # the hot-switch verified instead.
    assert resp.success is True
    assert "minimal" not in resp.message.lower()
