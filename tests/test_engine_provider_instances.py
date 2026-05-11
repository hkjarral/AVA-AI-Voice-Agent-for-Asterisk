from datetime import datetime, timezone

import pytest

from src.core.models import CallSession
from src.engine import Engine


@pytest.mark.asyncio
async def test_call_history_persists_provider_instance_key(monkeypatch):
    captured = {}

    class FakeCallHistoryStore:
        _enabled = True

        async def save(self, record):
            captured["record"] = record
            return True

        async def get_by_call_id(self, call_id):
            return captured["record"]

    monkeypatch.setattr(
        "src.core.call_history.get_call_history_store",
        lambda: FakeCallHistoryStore(),
    )

    engine = Engine.__new__(Engine)
    session = CallSession(
        call_id="call-1",
        caller_channel_id="call-1",
        provider_name="acme_google_live",
        provider_kind="google_live",
        start_time=datetime.now(timezone.utc),
        conversation_history=[{"role": "user", "content": "hello"}],
    )

    await engine._persist_call_history(session, "call-1")

    assert captured["record"].provider_name == "acme_google_live"
