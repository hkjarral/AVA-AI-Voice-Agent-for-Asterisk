import base64
import re
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.audio.transports.identity import new_websocket_channel_id
from src.audio.transports.legacy import WebSocketRuntime


def test_websocket_channel_id_preserves_uuid_entropy_and_fits_freepbx(monkeypatch):
    value = uuid.uuid4()
    monkeypatch.setattr("src.audio.transports.identity.uuid.uuid4", lambda: value)
    channel_id = new_websocket_channel_id()
    assert len(channel_id) == 30
    assert re.fullmatch(r"aava-ws-[A-Za-z0-9_-]{22}", channel_id)
    assert base64.urlsafe_b64decode(channel_id[8:] + "==") == value.bytes


@pytest.mark.asyncio
async def test_prepared_websocket_request_uses_freepbx_safe_id():
    runtime = WebSocketRuntime.__new__(WebSocketRuntime)
    runtime._config = SimpleNamespace(asterisk=SimpleNamespace(app_name="test-app"))
    runtime._section = SimpleNamespace(fallback_format="ulaw", connection_name="test-media")
    runtime.server = SimpleNamespace(register_call=Mock(return_value="nonce"))
    request = await runtime.prepare_call(SimpleNamespace(call_id="test-call"))
    assert len(request.channel_id) <= 32
    assert request.ari_params["channel_id"] == request.channel_id
    runtime.server.register_call.assert_called_once_with("test-call", request.channel_id, "ulaw", control_format="json")
