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


@pytest.mark.asyncio
async def test_reconnect_timeout_is_hard_ceiling_on_slow_inner_attempt():
    """Codex P2: the configured bound must cap the WHOLE reconnect effort.

    A single `_reconnect()` call carries its own multi-attempt backoff
    (~157s). If the loop only checks elapsed-vs-bound *between* attempts, a
    slow inner attempt leaves the caller deaf far past
    `mid_call_reconnect_timeout_sec`. The bound must be a hard ceiling: the
    loop must give up (and emit ProviderDisconnected) within roughly the
    bound even when an inner attempt hangs.
    """
    events = []

    async def on_event(event):
        events.append(event)

    provider = LocalProvider(
        LocalProviderConfig(mid_call_reconnect_timeout_sec=1),
        on_event=on_event,
    )
    provider._was_connected = True
    provider._active_call_id = "call-slow-inner"

    # Inner reconnect hangs far longer than the bound (simulates the real
    # backoff schedule running for minutes).
    async def _hang():
        await asyncio.sleep(60)
        return True

    provider._reconnect = _hang  # type: ignore[assignment]

    loop = asyncio.get_event_loop()
    started = loop.time()
    # Generous wrapper timeout: a regression (waiting for the 60s inner
    # attempt) hangs past this and fails; a correct hard ceiling returns
    # within a few seconds of the 1s bound.
    await asyncio.wait_for(provider._background_reconnect_loop(), timeout=15.0)
    duration = loop.time() - started

    # Threshold is relative to the configured bound plus a small jitter allowance,
    # so a regression that waits on the 60s inner attempt is caught instead of
    # slipping under a loose fixed ceiling.
    allowed = provider.config.mid_call_reconnect_timeout_sec + 2.0
    assert duration <= allowed, (
        f"reconnect effort ran {duration:.1f}s — bound "
        f"({provider.config.mid_call_reconnect_timeout_sec}s) was not a hard ceiling"
    )
    disconnect_events = [e for e in events if e.get("type") == "ProviderDisconnected"]
    assert disconnect_events, f"expected ProviderDisconnected give-up, got {events}"
    assert disconnect_events[-1].get("call_id") == "call-slow-inner"
