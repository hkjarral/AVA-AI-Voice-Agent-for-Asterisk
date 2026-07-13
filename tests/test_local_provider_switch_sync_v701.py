"""MED-R4: prompt-digest sync must be gated on a confirmed switch_response.

The Local AI Server WebSocket is reused across calls. `_apply_system_prompt`
sends a session-scoped `switch_model` request to push the per-call system prompt
and records a sha256 digest so it doesn't re-send an unchanged prompt. Previously
the digest was set the instant the send returned, without waiting for the server's
`switch_response`. If the server-side apply failed on a reused socket, the digest
was updated anyway, so the next call skipped the re-send and ran with the PREVIOUS
call's prompt still live (cross-call prompt leak).

Fail-closed contract: update the digest ONLY on a confirmed successful
switch_response. On failure/timeout, leave the digest untouched so the next send
re-applies the prompt (or the caller aborts).
"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from src.config import LocalProviderConfig
from src.providers.local import LocalProvider


class _FakeState:
    name = "OPEN"


class _FakeWebSocket:
    """Minimal websocket stub that records sent frames."""

    def __init__(self):
        self.state = _FakeState()
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


async def _noop_event(_event):
    return None


def _make_provider():
    provider = LocalProvider(LocalProviderConfig(response_timeout_sec=1), on_event=_noop_event)
    provider.websocket = _FakeWebSocket()
    return provider


async def _drive_switch_response(provider, status):
    """Simulate the receive loop delivering a switch_response of `status`."""
    # Wait until _apply_system_prompt has registered its pending future.
    for _ in range(200):
        fut = getattr(provider, "_pending_switch_future", None)
        if fut is not None and not fut.done():
            fut.set_result({
                "type": "switch_response",
                "status": status,
                "request_id": provider._pending_switch_request_id,
                "call_id": provider._pending_switch_call_id,
            })
            return
        await asyncio.sleep(0)
    raise AssertionError("provider never registered a pending switch future")


@pytest.mark.asyncio
async def test_digest_not_updated_on_switch_failure():
    provider = _make_provider()

    driver = asyncio.create_task(_drive_switch_response(provider, "error"))
    ok = await provider._apply_system_prompt("call A prompt", call_id="call-a")
    await driver

    assert ok is False, "failed switch must be reported as failure (fail-closed)"
    assert provider._last_system_prompt_digest is None, (
        "digest must NOT be set on switch failure, or the next call leaks this prompt"
    )

    # Because the digest was not recorded, a subsequent send must re-apply.
    provider.websocket.sent.clear()
    driver2 = asyncio.create_task(_drive_switch_response(provider, "success"))
    ok2 = await provider._apply_system_prompt("call A prompt", call_id="call-a-retry")
    await driver2
    assert ok2 is True
    assert provider.websocket.sent, "prompt must be re-sent after a prior failure"


@pytest.mark.asyncio
async def test_digest_updated_on_switch_success():
    provider = _make_provider()

    driver = asyncio.create_task(_drive_switch_response(provider, "success"))
    ok = await provider._apply_system_prompt("call B prompt", call_id="call-b")
    await driver

    assert ok is True
    assert provider._last_system_prompt_digest is not None, (
        "digest must be set on confirmed success so we don't re-spam switch_model"
    )

    sent = json.loads(provider.websocket.sent[-1])
    assert sent["scope"] == "session"
    assert sent["call_id"] == "call-b"
    assert sent["request_id"].startswith("prompt-sync-")
    assert sent["protocol_version"] == 2

    # Same prompt and same call must short-circuit (no new frame).
    provider.websocket.sent.clear()
    ok2 = await provider._apply_system_prompt("call B prompt", call_id="call-b")
    assert ok2 is True
    assert not provider.websocket.sent, "unchanged prompt must not re-send switch_model"


@pytest.mark.asyncio
async def test_same_prompt_is_resent_for_new_call_to_reset_session_history():
    provider = _make_provider()
    driver = asyncio.create_task(_drive_switch_response(provider, "success"))
    assert await provider._apply_system_prompt("shared prompt", call_id="call-one") is True
    await driver

    provider.websocket.sent.clear()
    driver2 = asyncio.create_task(_drive_switch_response(provider, "success"))
    assert await provider._apply_system_prompt("shared prompt", call_id="call-two") is True
    await driver2
    assert json.loads(provider.websocket.sent[-1])["call_id"] == "call-two"


@pytest.mark.asyncio
async def test_digest_not_updated_on_switch_timeout():
    provider = _make_provider()

    # No driver resolves the future -> bounded wait must time out and fail closed.
    ok = await provider._apply_system_prompt("call C prompt", call_id="call-c")

    assert ok is False, "timeout waiting for switch_response must fail closed"
    assert provider._last_system_prompt_digest is None, (
        "digest must NOT be set when the switch_response never arrives"
    )


@pytest.mark.asyncio
async def test_cancelled_switch_future_fails_closed_without_cancelling_waiter():
    provider = _make_provider()
    task = asyncio.create_task(
        provider._apply_system_prompt("call D prompt", call_id="call-d")
    )
    for _ in range(200):
        future = provider._pending_switch_future
        if future is not None:
            future.cancel()
            break
        await asyncio.sleep(0)
    else:
        raise AssertionError("provider never registered a pending switch future")

    assert await task is False
    assert task.cancelled() is False


@pytest.mark.asyncio
async def test_stale_switch_response_cannot_resolve_new_waiter():
    provider = _make_provider()
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    provider._pending_switch_future = future
    provider._pending_switch_request_id = "prompt-sync-new"
    provider._pending_switch_call_id = "call-new"

    class _ResponseSocket(_FakeWebSocket):
        def __init__(self):
            super().__init__()
            self._messages = [
                json.dumps({
                    "type": "switch_response",
                    "status": "success",
                    "request_id": "prompt-sync-old",
                    "call_id": "call-old",
                }),
                json.dumps({
                    "type": "switch_response",
                    "status": "success",
                    "request_id": "prompt-sync-new",
                    "call_id": "call-new",
                }),
            ]

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._messages:
                raise StopAsyncIteration
            return self._messages.pop(0)

    provider.websocket = _ResponseSocket()
    await provider._receive_loop()

    assert future.done()
    assert future.result()["request_id"] == "prompt-sync-new"


@pytest.mark.asyncio
async def test_reconnect_resets_prompt_digest_for_new_websocket():
    provider = _make_provider()
    provider._last_system_prompt_digest = "old-socket-digest"
    provider.websocket = None
    provider.is_connected = lambda: False
    provider._connect_ws = AsyncMock(return_value=_FakeWebSocket())
    provider._receive_loop = AsyncMock()
    provider._send_loop = AsyncMock()

    assert await provider._reconnect_locked() is True
    await asyncio.sleep(0)

    assert provider._last_system_prompt_digest is None


@pytest.mark.asyncio
async def test_legacy_switch_response_is_correlated_by_call_id():
    provider = _make_provider()
    future = asyncio.get_running_loop().create_future()
    provider._pending_switch_future = future
    provider._pending_switch_request_id = "prompt-sync-new"
    provider._pending_switch_call_id = "call-legacy"

    class _LegacyResponseSocket(_FakeWebSocket):
        def __init__(self):
            super().__init__()
            self._messages = [json.dumps({
                "type": "switch_response",
                "status": "success",
                "call_id": "call-legacy",
            })]

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._messages:
                raise StopAsyncIteration
            return self._messages.pop(0)

    provider.websocket = _LegacyResponseSocket()
    await provider._receive_loop()

    assert future.done()
    assert future.result()["call_id"] == "call-legacy"
