"""MED-R3: bound the mid-call WebSocket-drop mute window, then signal hangup.

When the Local AI Server WebSocket drops mid-call, the provider's background
reconnect loop must not retry for ~12 minutes while the caller hears silence.
It must give up after `mid_call_reconnect_timeout_sec` and emit a terminal
`ProviderDisconnected` event so the engine plays an apology + hangs up.
"""

import asyncio

import pytest

from src.config import LocalProviderConfig
from src.providers.local import LocalProvider


@pytest.mark.asyncio
async def test_background_reconnect_gives_up_within_timeout_and_signals_hangup():
    events = []

    async def on_event(event):
        events.append(event)

    provider = LocalProvider(
        LocalProviderConfig(mid_call_reconnect_timeout_sec=1),
        on_event=on_event,
    )
    provider._was_connected = True
    provider._active_call_id = "call-med-r3"

    # Force every reconnect attempt to fail so the loop must hit the time bound.
    async def _always_fail():
        return False

    provider._reconnect = _always_fail  # type: ignore[assignment]

    # The loop must give up well within the (tiny) bound — far short of the old
    # 12-minute ceiling. Wrap in a hard timeout so a regression hangs the test.
    await asyncio.wait_for(provider._background_reconnect_loop(), timeout=10.0)

    disconnect_events = [e for e in events if e.get("type") == "ProviderDisconnected"]
    assert disconnect_events, f"expected a terminal ProviderDisconnected event, got {events}"
    evt = disconnect_events[-1]
    assert evt.get("call_id") == "call-med-r3"


@pytest.mark.asyncio
async def test_background_reconnect_success_does_not_signal_hangup():
    events = []

    async def on_event(event):
        events.append(event)

    provider = LocalProvider(
        LocalProviderConfig(mid_call_reconnect_timeout_sec=1),
        on_event=on_event,
    )
    provider._was_connected = True
    provider._active_call_id = "call-ok"

    async def _succeed():
        return True

    provider._reconnect = _succeed  # type: ignore[assignment]

    await asyncio.wait_for(provider._background_reconnect_loop(), timeout=10.0)

    assert not [e for e in events if e.get("type") == "ProviderDisconnected"]
