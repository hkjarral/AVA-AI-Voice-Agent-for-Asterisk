import asyncio
import base64
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


LOCAL_AI_ROOT = Path(__file__).resolve().parents[1] / "local_ai_server"
sys.path.insert(0, str(LOCAL_AI_ROOT))

from protocol_contract import validate_payload  # noqa: E402
from server import LocalAIServer  # noqa: E402


class _FakeServer:
    def __init__(self):
        self.sent = []
        self.seen = None
        self.stt_backend = "faster_whisper"
        self.faster_whisper_model = "base"
        self._batch_reserved = False
        self._batch_preempted = False
        self._batch_process = None
        self._live_session_ids = set()

    async def _send_json(self, _websocket, payload):
        self.sent.append(payload)

    async def _run_batch_worker(self, audio, language=None):
        self.seen = (audio, language)
        return "hello world"


class _FakeWebSocket:
    def __init__(self, *, closed=False):
        self.closed = closed

    async def wait_closed(self):
        if self.closed:
            return
        await asyncio.Future()


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
        _FakeWebSocket(),
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
        _FakeWebSocket(),
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


@pytest.mark.asyncio
async def test_batch_rejects_second_job_without_queueing():
    server = _FakeServer()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_worker(_pcm16, _language):
        started.set()
        await release.wait()
        return "first"

    server._run_batch_worker = slow_worker
    request = {
        "request_id": "req-first",
        "data": base64.b64encode(b"\0\0").decode("ascii"),
        "encoding": "pcm_s16le",
        "sample_rate_hz": 16000,
    }
    first = asyncio.create_task(
        LocalAIServer._handle_transcribe_request(server, _FakeWebSocket(), request)
    )
    await started.wait()
    await LocalAIServer._handle_transcribe_request(
        server, _FakeWebSocket(), {**request, "request_id": "req-second"}
    )
    assert server.sent[-1]["error"] == "batch_busy"
    release.set()
    await first
    assert server.sent[-1]["text"] == "first"
    assert server._batch_reserved is False


@pytest.mark.asyncio
async def test_disconnect_cancels_batch_without_success():
    server = _FakeServer()
    started = asyncio.Event()
    canceled = asyncio.Event()

    async def slow_worker(_pcm16, _language):
        try:
            started.set()
            await asyncio.Future()
        finally:
            canceled.set()

    class ClosingWebSocket:
        async def wait_closed(self):
            await started.wait()

    server._run_batch_worker = slow_worker
    await LocalAIServer._handle_transcribe_request(
        server,
        ClosingWebSocket(),
        {
            "request_id": "req-disconnect",
            "data": base64.b64encode(b"\0\0").decode("ascii"),
            "encoding": "pcm_s16le",
            "sample_rate_hz": 16000,
        },
    )
    assert canceled.is_set()
    assert server.sent == []
    assert server._batch_reserved is False


@pytest.mark.asyncio
async def test_live_session_preempts_batch_claim():
    server = _FakeServer()
    process = SimpleNamespace(returncode=None, killed=False)

    def kill():
        process.killed = True

    process.kill = kill
    server._batch_reserved = True
    server._batch_process = process
    session = SimpleNamespace()
    LocalAIServer._mark_live_session(server, session)
    assert process.killed is True
    assert server._batch_preempted is True
    assert id(session) in server._live_session_ids
    LocalAIServer._release_live_session(server, session)
    assert server._live_session_ids == set()


@pytest.mark.asyncio
async def test_invalid_language_and_request_id_fail_before_worker():
    server = _FakeServer()
    request = {
        "data": base64.b64encode(b"\0\0").decode("ascii"),
        "encoding": "pcm_s16le",
        "sample_rate_hz": 16000,
    }
    await LocalAIServer._handle_transcribe_request(
        server, _FakeWebSocket(), {**request, "request_id": "../../../"}
    )
    await LocalAIServer._handle_transcribe_request(
        server, _FakeWebSocket(), {**request, "request_id": "valid", "language": "english"}
    )
    assert [item["error"] for item in server.sent] == ["invalid_request_id", "invalid_language"]
    assert server.seen is None


def test_faster_whisper_batch_failure_differs_from_silence():
    from stt_backends import FasterWhisperSTTBackend

    backend = FasterWhisperSTTBackend()
    backend._initialized = True

    class FailingModel:
        def transcribe(self, *_args, **_kwargs):
            raise RuntimeError("model failed")

    backend.model = FailingModel()
    assert backend.transcribe_pcm16(b"\0\0") == ""
    with pytest.raises(RuntimeError, match="model failed"):
        backend.transcribe_pcm16(b"\0\0", raise_on_error=True)

    class SilentModel:
        def transcribe(self, *_args, **_kwargs):
            return ([], None)

    backend.model = SilentModel()
    assert backend.transcribe_pcm16(b"\0\0", raise_on_error=True) == ""


@pytest.mark.asyncio
async def test_live_session_kills_isolated_batch_worker(monkeypatch):
    import server as server_module

    server = _FakeServer()
    server.faster_whisper_backend = object()
    server.faster_whisper_language = "en"
    server.faster_whisper_device = "cpu"
    server.faster_whisper_compute = "int8"
    started = asyncio.Event()

    class Process:
        def __init__(self):
            self.returncode = None
            self.killed = False
            self.done = asyncio.Event()

        async def communicate(self, _audio):
            started.set()
            await self.done.wait()
            self.returncode = -9
            return b"", None

        def kill(self):
            self.killed = True
            self.done.set()

        async def wait(self):
            await self.done.wait()
            self.returncode = -9
            return -9

    process = Process()

    async def spawn(*_args, **_kwargs):
        return process

    monkeypatch.setattr(server_module.asyncio, "create_subprocess_exec", spawn)
    server._batch_reserved = True
    task = asyncio.create_task(
        LocalAIServer._run_batch_worker(server, b"\0\0", None)
    )
    await started.wait()
    LocalAIServer._mark_live_session(server, SimpleNamespace())
    with pytest.raises(RuntimeError, match="preempted_by_live_call"):
        await task
    assert process.killed is True
    assert server._batch_process is None
