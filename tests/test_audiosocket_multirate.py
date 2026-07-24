import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.audio.audiosocket_protocol import (
    AUDIO_TYPE_TO_FORMAT,
    AudioSocketAudioFrame,
    audio_message_type,
    supports_multirate_audiosocket,
)
from src.audio.audiosocket_server import AudioSocketServer, TYPE_TERMINATE, TYPE_UUID
from src.core.streaming_playback_manager import StreamingPlaybackManager
from src.core.transport_orchestrator import TransportOrchestrator
from src.engine import Engine


class _Writer:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


@pytest.mark.parametrize(
    ("message_type", "encoding", "sample_rate"),
    [(message_type, *audio_format) for message_type, audio_format in AUDIO_TYPE_TO_FORMAT.items()],
)
def test_audio_message_types_cover_all_supported_signed_linear_rates(
    message_type, encoding, sample_rate
):
    assert audio_message_type(encoding, sample_rate) == message_type


def test_linear16_alias_uses_explicit_sample_rate_not_name_suffix():
    assert audio_message_type("linear16", 8000) == 0x10
    assert audio_message_type("linear16", 16000) == 0x12


@pytest.mark.parametrize(
    ("version", "supported"),
    [
        ("Asterisk 18.26.4", False),
        ("20.16.1", False),
        ("20.17.0", True),
        ("21.11.0", False),
        ("21.12.0", True),
        ("22.7.0", True),
        ("23.1.0", True),
        ("24.0.0", True),
        (None, False),
        ("custom-build", False),
    ],
)
def test_multirate_version_gate(version, supported):
    assert supports_multirate_audiosocket(version) is supported


@pytest.mark.asyncio
async def test_server_decodes_slin16_header_metadata_after_fragmented_reads():
    received = []
    on_uuid = AsyncMock(return_value=True)

    async def on_audio(conn_id, frame):
        received.append((conn_id, frame))

    server = AudioSocketServer("127.0.0.1", 0, on_uuid=on_uuid, on_audio=on_audio)
    reader = asyncio.StreamReader()
    writer = _Writer()
    call_uuid = uuid.uuid4()
    payload = b"\x01\x02" * 320
    wire = (
        bytes([TYPE_UUID])
        + (16).to_bytes(2, "big")
        + call_uuid.bytes
        + bytes([0x12])
        + len(payload).to_bytes(2, "big")
        + payload
        + bytes([TYPE_TERMINATE, 0, 0])
    )
    for chunk in (wire[:2], wire[2:11], wire[11:25], wire[25:200], wire[200:]):
        reader.feed_data(chunk)
    reader.feed_eof()

    await server._connection_loop("conn-16k", reader, writer)

    on_uuid.assert_awaited_once_with("conn-16k", str(call_uuid))
    assert received == [
        (
            "conn-16k",
            AudioSocketAudioFrame(payload, 0x12, "slin16", 16000),
        )
    ]


@pytest.mark.asyncio
async def test_server_writes_rate_specific_type_and_rejects_oversized_frame():
    server = AudioSocketServer(
        "127.0.0.1",
        0,
        on_uuid=AsyncMock(return_value=True),
        on_audio=AsyncMock(),
    )
    writer = _Writer()
    server._writers["conn"] = writer
    payload = b"\x00\x01" * 320

    assert await server.send_audio(
        "conn", payload, encoding="slin16", sample_rate=16000
    )
    assert writer.data[:3] == bytes([0x12]) + len(payload).to_bytes(2, "big")
    assert writer.data[3:] == payload
    assert not await server.send_audio("conn", b"x" * 65536)


def _orchestrator_config():
    return {
        "audio_transport": "audiosocket",
        "audiosocket": {"format": "slin"},
        "profiles": {
            "default": "telephony_ulaw_8k",
            "telephony_ulaw_8k": {
                "internal_rate_hz": 8000,
                "transport_out": {"encoding": "ulaw", "sample_rate_hz": 8000},
                "provider_pref": {},
            },
            "wideband_pcm_16k": {
                "internal_rate_hz": 16000,
                "transport_out": {"encoding": "slin16", "sample_rate_hz": 16000},
                "provider_pref": {},
            },
        },
    }


def test_audiosocket_profile_selects_wideband_without_changing_legacy_profile():
    orchestrator = TransportOrchestrator(_orchestrator_config())

    legacy = orchestrator.resolve_transport("test", None, {})
    wideband = orchestrator.resolve_transport(
        "test", None, {"AI_AUDIO_PROFILE": "wideband_pcm_16k"}
    )

    assert (legacy.wire_encoding, legacy.wire_sample_rate) == ("slin", 8000)
    assert (wideband.wire_encoding, wideband.wire_sample_rate) == ("slin16", 16000)


@pytest.mark.asyncio
async def test_streaming_manager_keeps_audiosocket_framing_per_call():
    sessions = {
        "call-8k": SimpleNamespace(audiosocket_conn_id="conn-8k"),
        "call-16k": SimpleNamespace(audiosocket_conn_id="conn-16k"),
    }
    session_store = SimpleNamespace(
        get_by_call_id=AsyncMock(side_effect=lambda call_id: sessions[call_id])
    )
    audio_server = SimpleNamespace(send_audio=AsyncMock(return_value=True))
    manager = StreamingPlaybackManager(
        session_store,
        ari_client=SimpleNamespace(),
        streaming_config={"sample_rate": 8000},
        audio_transport="audiosocket",
        audiosocket_server=audio_server,
    )

    assert await manager._send_audio_chunk(
        "call-8k", "stream-8k", b"a" * 320, target_fmt="slin", target_rate=8000
    )
    assert await manager._send_audio_chunk(
        "call-16k", "stream-16k", b"b" * 640, target_fmt="slin16", target_rate=16000
    )

    assert audio_server.send_audio.await_args_list[0].kwargs == {
        "encoding": "slin",
        "sample_rate": 8000,
    }
    assert audio_server.send_audio.await_args_list[1].kwargs == {
        "encoding": "slin16",
        "sample_rate": 16000,
    }


@pytest.mark.asyncio
async def test_engine_originates_wideband_channel_from_call_profile():
    session = SimpleNamespace(
        transport_profile=SimpleNamespace(
            wire_encoding="slin16", wire_sample_rate=16000
        ),
        audiosocket_uuid=None,
    )
    engine = Engine.__new__(Engine)
    engine.config = SimpleNamespace(
        audiosocket=SimpleNamespace(
            host="127.0.0.1", advertise_host=None, port=8090, format="slin"
        ),
        asterisk=SimpleNamespace(app_name="asterisk-ai-voice-agent"),
    )
    engine.session_store = SimpleNamespace(get_by_call_id=AsyncMock(return_value=session))
    engine.ari_client = SimpleNamespace(
        send_command=AsyncMock(return_value={"id": "audiosocket-channel"})
    )
    engine.pending_audiosocket_channels = {}
    engine.uuidext_to_channel = {}
    engine._save_session = AsyncMock()

    await Engine._originate_audiosocket_channel_hybrid(engine, "caller-channel")

    params = engine.ari_client.send_command.await_args.kwargs["params"]
    assert params["endpoint"].startswith("AudioSocket/127.0.0.1:8090/")
    assert params["endpoint"].endswith("/c(slin16)")
    assert engine.pending_audiosocket_channels["audiosocket-channel"] == "caller-channel"
