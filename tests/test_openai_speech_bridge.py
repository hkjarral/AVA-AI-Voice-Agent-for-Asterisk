import base64
import sys
from pathlib import Path

import pytest


LOCAL_AI_ROOT = Path(__file__).resolve().parents[1] / "local_ai_server"
sys.path.insert(0, str(LOCAL_AI_ROOT))

from protocol_contract import validate_payload  # noqa: E402
from server import LocalAIServer  # noqa: E402


class _FakeServer:
    def __init__(self):
        self.sent = []
        self.seen = None

    async def _send_json(self, _websocket, payload):
        self.sent.append(payload)

    async def transcribe_pcm16_once(self, audio, *, language=None):
        self.seen = (audio, language)
        return "hello world"


def test_transcribe_protocol_contract_accepts_request_and_response():
    validate_payload(
        {
            "type": "transcribe_request",
            "request_id": "req-1",
            "data": base64.b64encode(b"\0\0").decode("ascii"),
            "encoding": "pcm_s16le",
            "sample_rate_hz": 16000,
            "language": "en",
        }
    )
    validate_payload(
        {
            "type": "transcribe_response",
            "request_id": "req-1",
            "status": "ok",
            "text": "hello world",
        }
    )


@pytest.mark.asyncio
async def test_batch_transcription_is_correlated_and_does_not_echo_audio():
    server = _FakeServer()
    await LocalAIServer._handle_transcribe_request(
        server,
        object(),
        {
            "type": "transcribe_request",
            "request_id": "req-1",
            "data": base64.b64encode(b"\0\0\1\0").decode("ascii"),
            "encoding": "pcm_s16le",
            "sample_rate_hz": 16000,
            "language": "en",
        },
    )
    assert server.seen == (b"\0\0\1\0", "en")
    assert server.sent == [
        {
            "type": "transcribe_response",
            "status": "ok",
            "text": "hello world",
            "request_id": "req-1",
        }
    ]
    assert "data" not in server.sent[0]


@pytest.mark.asyncio
async def test_batch_transcription_rejects_arbitrary_audio_shape():
    server = _FakeServer()
    await LocalAIServer._handle_transcribe_request(
        server,
        object(),
        {
            "type": "transcribe_request",
            "request_id": "req-2",
            "data": base64.b64encode(b"odd").decode("ascii"),
            "encoding": "pcm_s16le",
            "sample_rate_hz": 8000,
        },
    )
    assert server.seen is None
    assert server.sent[0]["status"] == "error"
    assert server.sent[0]["error"] == "unsupported_audio_format"
