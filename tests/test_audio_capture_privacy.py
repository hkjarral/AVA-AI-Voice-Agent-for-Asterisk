import asyncio
import stat
import wave
from unittest.mock import AsyncMock, Mock

import pytest

import src.utils.audio_capture as capture_module
from src.config import AppConfig, load_config
from src.core.models import CallSession
from src.core.session_store import SessionStore
from src.core.streaming_playback_manager import StreamingPlaybackManager
from src.engine import Engine, _cleanup_in_progress
from src.utils.audio_capture import AudioCaptureManager
from src.utils.diagnostic_paths import (
    DEFAULT_DIAGNOSTIC_CAPTURE_DIR,
    UnsafeDiagnosticPathError,
)


_CONFIG = {
    "default_provider": "local",
    "providers": {"local": {"enabled": True}},
    "asterisk": {
        "host": "127.0.0.1",
        "port": 8088,
        "username": "u",
        "password": "p",
        "app_name": "ai-voice-agent",
    },
    "llm": {"initial_greeting": "", "prompt": "test", "model": "test"},
    "audio_transport": "audiosocket",
}


class _FailOnLock:
    def __enter__(self):
        raise AssertionError("disabled capture acquired its lock")

    def __exit__(self, *_args):
        return False


class _DummyARI:
    pass


@pytest.mark.parametrize(
    ("stream_name", "method"),
    [
        ("caller_inbound", "pcm16"),
        ("caller_to_provider", "encoded"),
        ("agent_from_provider", "encoded"),
        ("agent_out_to_caller", "encoded"),
    ],
)
def test_disabled_capture_is_noop_for_every_stream(
    tmp_path, monkeypatch, stream_name, method
):
    root = tmp_path / "captures"
    historical = root / "historical-call" / "caller_inbound.wav"
    historical.parent.mkdir(parents=True)
    historical.write_bytes(b"existing-artifact")

    manager = AudioCaptureManager(
        base_dir=str(root),
        keep_files=False,
        enabled=False,
    )
    manager._lock = _FailOnLock()

    def fail(*_args, **_kwargs):
        raise AssertionError("disabled capture performed filesystem or conversion work")

    # Patch after initialization: the permission-restricted empty root is an
    # allowed installation artifact, but per-call operations must do no work.
    monkeypatch.setattr(capture_module.audioop, "ulaw2lin", fail)
    monkeypatch.setattr(capture_module.wave, "open", fail)
    monkeypatch.setattr(capture_module.os, "makedirs", fail)
    monkeypatch.setattr(capture_module.os, "chmod", fail)
    monkeypatch.setattr(capture_module.os, "listdir", fail)
    monkeypatch.setattr(capture_module.os, "remove", fail)
    monkeypatch.setattr(capture_module.os, "rmdir", fail)

    if method == "pcm16":
        manager.append_pcm16("disabled-call", stream_name, b"\x00\x00" * 80, 8000)
    else:
        manager.append_encoded("disabled-call", stream_name, b"\xff" * 80, "ulaw", 8000)
    manager.close_call("disabled-call")

    assert historical.read_bytes() == b"existing-artifact"
    assert not (root / "disabled-call").exists()
    assert manager._handles == {}


def test_capture_root_may_exist_while_disabled_and_is_restricted(tmp_path):
    root = tmp_path / "captures"

    AudioCaptureManager(base_dir=str(root), keep_files=False, enabled=False)

    assert root.is_dir()
    assert stat.S_IMODE(root.stat().st_mode) == 0o700


def test_enabled_capture_rejects_symlink_root_without_writing(tmp_path):
    outside = tmp_path / "attacker-controlled"
    outside.mkdir()
    root = tmp_path / "captures"
    root.symlink_to(outside, target_is_directory=True)

    manager = AudioCaptureManager(base_dir=str(root), keep_files=True, enabled=True)
    manager.append_pcm16("call-1", "caller_inbound", b"\x00\x00" * 80, 8000)

    assert manager.enabled is False
    assert manager.storage_ready is False
    assert list(outside.iterdir()) == []


def test_enabled_capture_rejects_world_writable_ancestor(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o777)
    shared.chmod(0o777)

    manager = AudioCaptureManager(
        base_dir=str(shared / "captures"),
        keep_files=True,
        enabled=True,
    )

    assert manager.enabled is False
    assert manager.storage_ready is False
    assert not (shared / "captures").exists()


def test_runtime_path_rejection_disables_capture_without_retry(tmp_path, monkeypatch):
    manager = AudioCaptureManager(
        base_dir=str(tmp_path / "captures"),
        keep_files=True,
        enabled=True,
    )
    attempts = 0

    def reject_path(_path):
        nonlocal attempts
        attempts += 1
        raise UnsafeDiagnosticPathError("unsafe test path")

    monkeypatch.setattr(capture_module, "prepare_private_diagnostic_dir", reject_path)
    monkeypatch.setattr(
        capture_module.wave,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsafe path reached wave.open")
        ),
    )

    manager.append_pcm16("call-1", "caller_inbound", b"\x00\x00" * 80, 8000)
    manager.append_pcm16("call-1", "caller_inbound", b"\x00\x00" * 80, 8000)

    assert manager.enabled is False
    assert manager._handles == {}
    assert attempts == 1


def test_runtime_wave_open_failure_disables_capture_without_retry(tmp_path, monkeypatch):
    manager = AudioCaptureManager(
        base_dir=str(tmp_path / "captures"),
        keep_files=True,
        enabled=True,
    )
    attempts = 0

    def fail_open(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise OSError("test file-open failure")

    monkeypatch.setattr(capture_module.wave, "open", fail_open)

    manager.append_pcm16("call-1", "caller_inbound", b"\x00\x00" * 80, 8000)
    manager.append_pcm16("call-1", "caller_inbound", b"\x00\x00" * 80, 8000)

    assert manager.enabled is False
    assert manager._handles == {}
    assert attempts == 1


def test_capture_manager_defaults_fail_closed(tmp_path):
    root = tmp_path / "captures"
    manager = AudioCaptureManager(base_dir=str(root))

    manager.append_pcm16("call-1", "caller_inbound", b"\x00\x00" * 80, 8000)

    assert manager.enabled is False
    assert not (root / "call-1").exists()


def test_enabled_capture_writes_all_streams_with_restricted_permissions(tmp_path):
    root = tmp_path / "captures"
    call_id = "enabled-call"
    manager = AudioCaptureManager(
        base_dir=str(root),
        keep_files=True,
        enabled=True,
    )

    manager.append_pcm16(call_id, "caller_inbound", b"\x00\x00" * 80, 8000)
    for stream_name in (
        "caller_to_provider",
        "agent_from_provider",
        "agent_out_to_caller",
    ):
        manager.append_encoded(call_id, stream_name, b"\xff" * 80, "ulaw", 8000)
    manager.close_call(call_id)

    call_dir = root / call_id
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(call_dir.stat().st_mode) == 0o700
    for stream_name in (
        "caller_inbound",
        "caller_to_provider",
        "agent_from_provider",
        "agent_out_to_caller",
    ):
        path = call_dir / f"{stream_name}.wav"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        with wave.open(str(path), "rb") as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2
            assert wav_file.getframerate() == 8000
            assert wav_file.getnframes() == 80


@pytest.mark.parametrize("enabled", [False, True])
def test_engine_passes_resolved_capture_enablement(monkeypatch, tmp_path, enabled):
    captured = {}

    class _RecordingCaptureManager:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def append_encoded(self, *_args, **_kwargs):
            return None

        def append_pcm16(self, *_args, **_kwargs):
            return None

        def close_call(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr("src.engine.AudioCaptureManager", _RecordingCaptureManager)
    taps_dir = tmp_path / "playback-taps"
    config_data = dict(_CONFIG)
    config_data["streaming"] = {
        "diag_enable_taps": enabled,
        "diag_out_dir": str(taps_dir),
    }
    config = AppConfig(**config_data)

    engine = Engine(config)

    assert captured["enabled"] is enabled
    assert captured["keep_files"] is enabled
    assert captured["base_dir"] == DEFAULT_DIAGNOSTIC_CAPTURE_DIR
    assert engine.streaming_playback_manager.diag_out_dir == str(taps_dir)


@pytest.mark.parametrize(("raw", "enabled"), [("false", False), ("true", True)])
def test_resolved_diag_environment_survives_config_validation(
    monkeypatch, raw, enabled
):
    monkeypatch.setenv("DIAG_ENABLE_TAPS", raw)
    monkeypatch.setenv("ASTERISK_ARI_USERNAME", "test-user")
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "test-password")

    config = load_config("config/ai-agent.example.yaml")

    assert config.streaming.diag_enable_taps is enabled


@pytest.mark.asyncio
async def test_call_cleanup_finalizes_capture_after_earlier_failure(monkeypatch):
    engine = Engine(AppConfig(**_CONFIG))
    call_id = "capture-cleanup-finally"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    await engine.session_store.upsert_call(session)

    close_call = Mock()
    engine.audio_capture.close_call = close_call
    engine.ari_client.hangup_channel = AsyncMock(return_value=True)
    engine._persist_call_history = AsyncMock()
    engine._execute_post_call_tools = AsyncMock()
    monkeypatch.setattr(
        engine.session_store,
        "remove_call",
        AsyncMock(side_effect=RuntimeError("failure before capture finalization")),
    )
    _cleanup_in_progress.discard(call_id)

    await engine._cleanup_call(call_id)

    close_call.assert_called_once_with(call_id)
    assert call_id not in _cleanup_in_progress


@pytest.mark.asyncio
async def test_duplicate_cleanup_cannot_close_capture_owned_by_first_cleanup(
    monkeypatch,
):
    engine = Engine(AppConfig(**_CONFIG))
    call_id = "capture-cleanup-owner"
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    await engine.session_store.upsert_call(session)

    close_call = Mock()
    engine.audio_capture.close_call = close_call
    engine.ari_client.hangup_channel = AsyncMock(return_value=True)
    engine._persist_call_history = AsyncMock()
    engine._execute_post_call_tools = AsyncMock()
    remove_started = asyncio.Event()
    allow_remove_failure = asyncio.Event()

    async def fail_remove_after_duplicate_arrives(*_args, **_kwargs):
        remove_started.set()
        await allow_remove_failure.wait()
        raise RuntimeError("failure before capture finalization")

    monkeypatch.setattr(engine.session_store, "remove_call", fail_remove_after_duplicate_arrives)
    _cleanup_in_progress.discard(call_id)

    first = asyncio.create_task(engine._cleanup_call(call_id))
    await asyncio.wait_for(remove_started.wait(), timeout=2)
    await engine._cleanup_call(call_id)

    close_call.assert_not_called()
    assert call_id in _cleanup_in_progress

    allow_remove_failure.set()
    await asyncio.wait_for(first, timeout=2)

    close_call.assert_called_once_with(call_id)
    assert call_id not in _cleanup_in_progress


@pytest.mark.asyncio
async def test_disabled_playback_taps_do_not_write_or_delete_stale_files(
    tmp_path, monkeypatch
):
    call_id = "disabled-tap-call"
    stream_id = "stream-1"
    existing = tmp_path / f"pre_compand_pcm16_{call_id}_historical.wav"
    existing.write_bytes(b"historical-tap")
    manager = StreamingPlaybackManager(
        session_store=SessionStore(),
        ari_client=_DummyARI(),
        conversation_coordinator=None,
        streaming_config={
            "diag_enable_taps": False,
            "diag_out_dir": str(tmp_path),
            "provider_grace_ms": 0,
        },
    )
    manager.active_streams[call_id] = {
        "stream_id": stream_id,
        # Simulate stale per-stream state from an earlier enabled setting. The
        # manager's current disabled flag remains authoritative.
        "diag_enabled": True,
        "tap_pre_pcm16": bytearray(b"\x00\x00" * 80),
        "tap_post_pcm16": bytearray(b"\x00\x00" * 80),
        "tap_rate": 8000,
        "start_time": 0.0,
    }

    def fail(*_args, **_kwargs):
        raise AssertionError("disabled playback taps touched a WAV artifact")

    monkeypatch.setattr("src.core.streaming_playback_manager.wave.open", fail)
    monkeypatch.setattr("src.core.streaming_playback_manager.os.chmod", fail)
    monkeypatch.setattr("src.core.streaming_playback_manager.os.remove", fail)

    await manager._cleanup_stream(call_id, stream_id)

    assert existing.read_bytes() == b"historical-tap"


def test_enabled_playback_taps_reject_symlink_root(tmp_path):
    outside = tmp_path / "attacker-controlled-taps"
    outside.mkdir()
    root = tmp_path / "taps"
    root.symlink_to(outside, target_is_directory=True)

    manager = StreamingPlaybackManager(
        session_store=SessionStore(),
        ari_client=_DummyARI(),
        conversation_coordinator=None,
        streaming_config={
            "diag_enable_taps": True,
            "diag_out_dir": str(root),
        },
    )

    assert manager.diag_enable_taps is False
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("transport", ["audiosocket", "externalmedia"])
@pytest.mark.asyncio
async def test_disabled_diagnostics_are_noop_on_transport_output(
    tmp_path, monkeypatch, transport
):
    call_id = f"disabled-{transport}"
    stream_id = "stream-1"
    session_store = SessionStore()
    session = CallSession(call_id=call_id, caller_channel_id=call_id)
    session.audiosocket_conn_id = "conn-1"
    session.external_media_codec = "ulaw"
    await session_store.upsert_call(session)

    capture = AudioCaptureManager(
        base_dir=str(tmp_path / "captures"),
        keep_files=False,
        enabled=False,
    )
    capture._lock = _FailOnLock()
    manager = StreamingPlaybackManager(
        session_store=session_store,
        ari_client=_DummyARI(),
        conversation_coordinator=None,
        streaming_config={
            "diag_enable_taps": False,
            "diag_out_dir": str(tmp_path / "taps"),
        },
        audio_transport=transport,
        audio_capture_manager=capture,
    )
    manager.active_streams[call_id] = {
        "stream_id": stream_id,
        "diag_enabled": True,
        "tap_pre_pcm16": bytearray(b"\x00\x00" * 80),
        "tap_post_pcm16": bytearray(b"\x00\x00" * 80),
        "target_format": "ulaw",
        "target_sample_rate": 8000,
    }

    class _AudioSocketServer:
        send_audio = AsyncMock(return_value=True)

    class _RTPServer:
        send_audio = AsyncMock(return_value=True)

    manager.audiosocket_server = _AudioSocketServer()
    manager.rtp_server = _RTPServer()

    def fail(*_args, **_kwargs):
        raise AssertionError("disabled transport diagnostics touched a WAV artifact")

    monkeypatch.setattr("src.core.streaming_playback_manager.wave.open", fail)
    monkeypatch.setattr("src.core.streaming_playback_manager.os.chmod", fail)

    sent = await manager._send_audio_chunk(
        call_id,
        stream_id,
        b"\xff" * 160,
        target_fmt="ulaw",
        target_rate=8000,
    )

    assert sent is True
    assert not (tmp_path / "captures" / call_id).exists()
    assert not (tmp_path / "taps").exists()
