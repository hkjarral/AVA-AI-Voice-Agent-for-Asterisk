"""Google input frame continuity and connection/rate boundaries (#856)."""

import audioop
import base64
import struct
from unittest.mock import AsyncMock

import pytest

from src.config import GoogleProviderConfig
from src.providers.google_live import GoogleLiveProvider


def provider():
    result = GoogleLiveProvider(config=GoogleProviderConfig(api_key="synthetic-test-key"), on_event=AsyncMock())
    result.websocket = AsyncMock()
    result._setup_complete = True
    result._call_id = "input-resampling"
    result._send_message = AsyncMock(return_value=True)
    return result


def pcm(samples):
    return struct.pack(f"<{len(samples)}h", *samples)


def sent_pcm(instance, index=-1):
    payload = instance._send_message.await_args_list[index].args[0]
    return base64.b64decode(payload["realtimeInput"]["audio"]["data"])


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["pcm16", "ulaw"])
async def test_consecutive_8k_frames_interpolate_across_input_boundary(encoding):
    instance = provider()
    first = pcm([i * 100 for i in range(160)])
    second = pcm([18000 + i * 80 for i in range(160)])
    if encoding == "ulaw":
        first, second = audioop.lin2ulaw(first, 2), audioop.lin2ulaw(second, 2)
        decoded_first, decoded_second = audioop.ulaw2lin(first, 2), audioop.ulaw2lin(second, 2)
    else:
        decoded_first, decoded_second = first, second
    await instance.send_audio(first, 8000, encoding)
    await instance.send_audio(second, 8000, encoding)
    previous = struct.unpack_from("<h", decoded_first, len(decoded_first) - 2)[0]
    current = struct.unpack_from("<h", decoded_second)[0]
    assert instance._send_message.await_count == 2
    assert len(sent_pcm(instance)) == 640
    assert struct.unpack_from("<hh", sent_pcm(instance)) == (int((previous + current) / 2), current)


@pytest.mark.asyncio
async def test_partial_input_frames_keep_history_until_provider_frame_is_complete():
    instance = provider()
    await instance.send_audio(pcm([i * 100 for i in range(40)]), 8000, "pcm16")
    instance._send_message.assert_not_awaited()
    await instance.send_audio(pcm([i * 100 for i in range(40, 160)]), 8000, "pcm16")
    assert instance._send_message.await_count == 1
    data = sent_pcm(instance)
    assert len(data) == 640
    assert struct.unpack_from("<hh", data, 80 * 2) == (3950, 4000)


@pytest.mark.asyncio
async def test_engine_encoded_16k_input_is_passed_through_unchanged():
    instance = provider()
    data = pcm(list(range(320)))
    await instance.send_audio(data, 16000, "pcm16")
    assert sent_pcm(instance) == data


@pytest.mark.asyncio
async def test_input_history_does_not_cross_provider_instances():
    first, second = provider(), provider()
    await first.send_audio(pcm([10000] * 160), 8000, "pcm16")
    await second.send_audio(pcm([-10000] * 160), 8000, "pcm16")
    assert sent_pcm(second) == pcm([-10000] * 320)


@pytest.mark.asyncio
async def test_source_rate_change_discards_old_interpolation_history():
    instance = provider()
    await instance.send_audio(pcm([-10000] * 160), 8000, "pcm16")
    await instance.send_audio(pcm([0] * 320), 16000, "pcm16")
    await instance.send_audio(pcm([20000] * 160), 8000, "pcm16")
    assert sent_pcm(instance) == pcm([20000] * 320)


@pytest.mark.asyncio
async def test_provider_rate_change_discards_pending_old_rate_audio():
    instance = provider()
    await instance.send_audio(pcm([-10000] * 40), 8000, "pcm16")
    instance._send_message.assert_not_awaited()
    instance.config.provider_input_sample_rate_hz = 8000
    data = pcm([20000] * 160)
    await instance.send_audio(data, 8000, "pcm16")
    assert sent_pcm(instance) == data
    assert instance._send_message.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["disconnect", "stop", "start_failure"])
async def test_new_connection_discards_pending_audio_and_resampling_history(monkeypatch, boundary):
    instance = provider()
    # Leave a partial frame queued and interpolation history from the old connection.
    await instance.send_audio(pcm([-10000] * 40), 8000, "pcm16")
    if boundary == "disconnect":
        instance._mark_ws_disconnected()
    elif boundary == "stop":
        await instance.stop_session()
    else:
        monkeypatch.setattr("src.providers.google_live.connect_with_recovery", AsyncMock(side_effect=OSError("synthetic connection failure")))
        with pytest.raises(OSError):
            await instance.start_session("new-call")
    instance.websocket = AsyncMock()
    instance._setup_complete = True
    instance._closing = instance._closed = False
    instance._send_message.reset_mock()
    await instance.send_audio(pcm([20000] * 160), 8000, "pcm16")
    assert instance._send_message.await_count == 1
    assert sent_pcm(instance) == pcm([20000] * 320)
