import asyncio
import base64
import io
import json
import struct
import wave

import aiohttp
import pytest

from src.audio import convert_pcm16le_to_target_format, resample_audio
from src.config import AppConfig, SixtyDBProviderConfig, load_config
from src.config.security import inject_provider_api_keys
from src.pipelines.orchestrator import PipelineOrchestrator, PipelineOrchestratorError
from src.pipelines.sixtydb import SixtyDBTTSAdapter


PCM = struct.pack("<" + "h" * 640, *([1000, -1000] * 320))


def encoded(data):
    return base64.b64encode(data).decode()


def ndjson(records):
    return b"\n".join(json.dumps(record).encode() for record in records)


def wav_bytes(rate=16000, channels=1):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(PCM)
    return output.getvalue()


def app_config(provider=None, key="sixtydb_tts"):
    return AppConfig(
        default_provider="local", providers={key: provider or {"api_key": "test-key", "voice_id": "workspace-id"}, "local": {"ws_url": "ws://127.0.0.1:8765", "connect_timeout_sec": 5, "response_timeout_sec": 30, "chunk_ms": 20}},
        asterisk={"host": "127.0.0.1", "username": "ari", "password": "test"},
        llm={"initial_greeting": "Hi", "prompt": "Speak clearly", "model": "gpt-4o"},
        audio_transport="audiosocket", downstream_mode="stream",
        pipelines={"sixtydb_pipeline": {"stt": "local_stt", "llm": "local_llm", "tts": key}},
        active_pipeline="sixtydb_pipeline",
    )


class Response:
    def __init__(self, body=b"", content_type="application/x-ndjson", status=200, started=None):
        self.body = body
        self.headers = {"Content-Type": content_type}
        self.status = status
        self.exited = False
        self.content = self
        self.started = started

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.exited = True

    async def __aiter__(self):
        if self.started is not None:
            self.started.set()
            await asyncio.Event().wait()
        for line in self.body.splitlines():
            yield line

    async def iter_chunked(self, size):
        for offset in range(0, len(self.body), size):
            yield self.body[offset:offset + size]

    async def read(self):
        return self.body

    async def json(self):
        return json.loads(self.body)


class Session:
    def __init__(self, response):
        self.response = response
        self.requests = []
        self.closed = False

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self.response

    async def close(self):
        self.closed = True


def adapter(response, options=None):
    config = app_config()
    session = Session(response)
    instance = SixtyDBTTSAdapter("sixtydb_tts", config, SixtyDBProviderConfig(**config.providers["sixtydb_tts"]), options, session_factory=lambda: session)
    return instance, session


@pytest.mark.asyncio
async def test_ndjson_pcm_double_encoding_contract_and_telephony_conversion():
    wrapped = encoded(json.dumps({"result": {"audioContent": encoded(PCM[:161])}}).encode())
    response = Response(ndjson([
        {"type": "meta", "encoding": "LINEAR16", "sample_rate": 16000},
        {"result": {"audioContent": wrapped}},
        {"result": {"audioContent": encoded(PCM[161:])}},
    ]))
    instance, session = adapter(response)
    chunks = [chunk async for chunk in instance.synthesize("call", "Hello", {"model_id": "60db-fast-v01", "speed": 1.5})]
    assert [len(chunk) for chunk in chunks] == [160, 160]
    first, state = resample_audio(PCM[:640], 16000, 8000)
    second, _ = resample_audio(PCM[640:], 16000, 8000, state=state)
    assert b"".join(chunks) == convert_pcm16le_to_target_format(first + second, "mulaw")
    url, request = session.requests[0]
    assert url == "https://api.60db.ai/tts-synthesize"
    assert request["json"] == {
        "text": "Hello", "voice_id": "workspace-id", "speed": 1.5, "model_id": "60db-fast-v01",
        "audio_config": {"audio_encoding": "LINEAR16", "sample_rate_hertz": 16000},
        "output_format": "wav", "timestamp_type": "NONE",
    }
    assert request["headers"]["Authorization"] == "Bearer test-key"
    assert request["allow_redirects"] is False
    assert request["timeout"].sock_read == 30
    assert request["timeout"].total is None
    assert response.exited
    await instance.stop()
    assert session.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("body, content_type", [
    (wav_bytes(), "audio/wav"),
    (json.dumps({"success": True, "audio_base64": encoded(wav_bytes()), "sample_rate": 16000, "output_format": "wav"}).encode(), "application/json"),
    (json.dumps({"success": True, "backendResponse": {"success": True, "audio_base64": encoded(PCM), "encoding": "LINEAR16"}}).encode(), "application/json"),
])
async def test_wideband_wav_and_json_yield_only_pcm(body, content_type):
    instance, _ = adapter(Response(body, content_type))
    chunks = [chunk async for chunk in instance.synthesize("call", "Hello", {"format": {"encoding": "linear16", "sample_rate": 16000}})]
    assert [len(chunk) for chunk in chunks] == [640, 640]
    assert b"".join(chunks) == PCM
    assert instance.wideband_output_format["sample_rate"] == 16000
    await instance.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    Response(status=401), Response(status=302), Response(),
    Response(b'{"success":false}'), Response(b'{"type":"error"}'),
    Response(b'{"result":{"success":false}}'), Response(b'{"audioContent":"bad!"}'),
    Response(b'{"audioContent":"AA=="}'), Response(b'{"encoding":"mp3"}'),
    Response(b'{"sample_rate":24000}'), Response(b'{"audio_config":{"audio_encoding":"OGG_OPUS"}}'),
    Response(b'{"type":"meta"}'), Response(b'{bad'), Response(b'[]', "application/json"),
    Response(b'ID3notpcm', "application/octet-stream"), Response(PCM, "audio/mpeg"),
    Response(wav_bytes(rate=24000), "audio/wav"), Response(wav_bytes(channels=2), "audio/wav"),
    Response(wav_bytes()[:-2], "audio/wav"),
])
async def test_failures_and_incompatible_audio_raise_and_release_response(response):
    instance, _ = adapter(response)
    with pytest.raises((RuntimeError, ValueError)):
        [chunk async for chunk in instance.synthesize("call", "Hello", {})]
    assert response.exited
    await instance.stop()


@pytest.mark.asyncio
async def test_partial_stream_error_propagates_after_already_yielded_audio():
    response = Response(ndjson([{"audioContent": encoded(PCM[:640])}, {"type": "error"}]))
    instance, _ = adapter(response)
    stream = instance.synthesize("call", "Hello", {})
    assert len(await stream.__anext__()) == 160
    with pytest.raises(RuntimeError, match="synthesis failure"):
        await stream.__anext__()
    assert response.exited
    await instance.stop()


@pytest.mark.asyncio
async def test_generator_close_releases_response_and_no_later_chunks():
    response = Response(ndjson([{"audioContent": encoded(PCM)}]))
    instance, _ = adapter(response)
    stream = instance.synthesize("call", "Hello", {})
    assert len(await stream.__anext__()) == 160
    await stream.aclose()
    assert response.exited
    with pytest.raises(StopAsyncIteration):
        await stream.__anext__()
    await instance.stop()


@pytest.mark.asyncio
async def test_task_cancellation_releases_stalled_response():
    started = asyncio.Event()
    response = Response(started=started)
    instance, _ = adapter(response)
    async def consume():
        return [chunk async for chunk in instance.synthesize("call", "Hello", {})]
    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert response.exited
    await instance.stop()


@pytest.mark.asyncio
async def test_validation_precedes_http_request_and_runtime_voice_overrides():
    instance, session = adapter(Response(ndjson([{"audioContent": encoded(PCM)}])))
    for text, options in [(" ", {}), ("x" * 5001, {}), ("Hello", {"voice_id": ""}), ("Hello", {"api_key": ""}), ("Hello", {"speed": float("nan")}), ("Hello", {"format": {"encoding": "mp3"}}), ("Hello", {"format": {"sample_rate": 24000}})]:
        with pytest.raises((RuntimeError, ValueError)):
            [chunk async for chunk in instance.synthesize("call", text, options)]
    assert session.requests == []
    [chunk async for chunk in instance.synthesize("call", "Hello", {"voice_id": "other-workspace-id"})]
    assert session.requests[0][1]["json"]["voice_id"] == "other-workspace-id"
    await instance.stop()


@pytest.mark.asyncio
async def test_actual_factory_canonical_custom_secret_and_disabled_paths(tmp_path, monkeypatch):
    monkeypatch.delenv("SIXTYDB_API_KEY", raising=False)
    for key in ["sixtydb_tts", "customer_voice_tts"]:
        secret = tmp_path / "api-key"
        secret.write_text("managed-key")
        config = app_config({"type": "sixtydb", "voice_id": "workspace-id", "api_key_file": str(secret)}, key=key)
        orchestrator = PipelineOrchestrator(config)
        await orchestrator.start()
        resolution = orchestrator.get_pipeline("call")
        assert isinstance(resolution.tts_adapter, SixtyDBTTSAdapter)
        assert resolution.tts_adapter._provider_config.api_key == "managed-key"
        await orchestrator.stop()
    for provider in [{"enabled": False, "api_key": "key", "voice_id": "id"}, {"voice_id": "id"}, {"api_key": "key"}]:
        orchestrator = PipelineOrchestrator(app_config(provider))
        with pytest.raises(PipelineOrchestratorError, match="sixtydb_tts"):
            await orchestrator.start()
        await orchestrator.stop()


def test_secret_injection_strips_inline_key_and_preserves_managed_paths(monkeypatch):
    data = {"providers": {"tenant_tts": {"type": "sixtydb", "api_key": "unsafe-inline", "api_key_env": "TENANT_KEY"}}}
    monkeypatch.delenv("SIXTYDB_API_KEY", raising=False)
    inject_provider_api_keys(data)
    assert "api_key" not in data["providers"]["tenant_tts"]
    assert data["providers"]["tenant_tts"]["api_key_env"] == "TENANT_KEY"
    monkeypatch.setenv("SIXTYDB_API_KEY", "env-key")
    inject_provider_api_keys(data)
    assert data["providers"]["tenant_tts"]["api_key"] == "env-key"


@pytest.mark.asyncio
async def test_real_yaml_loader_secret_injection_and_factory_path(tmp_path, monkeypatch):
    monkeypatch.setenv("SIXTYDB_API_KEY", "env-key")
    monkeypatch.setenv("ASTERISK_ARI_USERNAME", "ari")
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "test")
    import yaml
    path = tmp_path / "agent.yaml"
    path.write_text(yaml.safe_dump(app_config({"type": "sixtydb", "api_key": "unsafe-inline", "voice_id": "workspace-id"}).model_dump(mode="json")))
    config = load_config(str(path))
    assert config.providers["sixtydb_tts"]["api_key"] == "env-key"
    orchestrator = PipelineOrchestrator(config)
    await orchestrator.start()
    assert isinstance(orchestrator.get_pipeline("call").tts_adapter, SixtyDBTTSAdapter)
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_response_budget_and_nested_wrapper_limits(monkeypatch):
    import src.pipelines.sixtydb as module
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 10)
    for response in (Response(PCM, "application/octet-stream"), Response(ndjson([{"audioContent": encoded(PCM)}]))):
        instance, _ = adapter(response)
        with pytest.raises(RuntimeError, match="exceeds"):
            [chunk async for chunk in instance.synthesize("call", "Hello", {})]
        assert response.exited
        await instance.stop()
    record = {"audio_base64": encoded(PCM)}
    for _ in range(6):
        record = {"audio_base64": encoded(json.dumps(record).encode())}
    with pytest.raises(RuntimeError, match="deeply nested"):
        SixtyDBTTSAdapter._decode_record(record)


@pytest.mark.asyncio
async def test_real_http_response_and_stalled_request_cancellation():
    from aiohttp import web
    started, release = asyncio.Event(), asyncio.Event()
    requests = []
    async def handle(request):
        body = await request.json()
        requests.append((request.headers.get("Authorization"), body))
        if body["text"] == "cancel":
            response = web.StreamResponse(headers={"Content-Type": "application/x-ndjson"})
            await response.prepare(request)
            started.set()
            await release.wait()
            return response
        return web.Response(body=wav_bytes(), content_type="audio/wav")
    app = web.Application()
    app.router.add_post("/tts", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    session = aiohttp.ClientSession()
    post = session.post
    session.post = lambda _url, **options: post(f"http://127.0.0.1:{port}/tts", **options)
    config = app_config()
    instance = SixtyDBTTSAdapter("sixtydb_tts", config, SixtyDBProviderConfig(api_key="test-key", voice_id="workspace-id"), session_factory=lambda: session)
    try:
        audio = [part async for part in instance.synthesize("call", "Hello", {"format": {"encoding": "linear16", "sample_rate": 16000}})]
        assert b"".join(audio) == PCM
        assert requests[0][0] == "Bearer test-key"
        async def consume():
            return [part async for part in instance.synthesize("call", "cancel", {})]
        task = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    finally:
        release.set()
        await instance.stop()
        await runner.cleanup()
    assert session.closed
