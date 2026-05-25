import importlib.util
import sys
import wave
from datetime import datetime
from pathlib import Path

import pytest

# admin_ui backend imports fastapi at module load. Skip the whole module on
# environments that don't have it (CI's `build` job runs the engine-only
# test set without admin_ui deps).
if importlib.util.find_spec("fastapi") is None:
    pytest.skip("fastapi not installed; admin_ui call-recording tests skipped", allow_module_level=True)

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "admin_ui" / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from api import calls  # noqa: E402  (skip-gated import)
from fastapi import Response  # noqa: E402  (skip-gated import)


def test_find_recording_accepts_uppercase_wav_in_date_dir(monkeypatch, tmp_path):
    call_id = "1779665339.911"
    date_dir = tmp_path / "2026" / "05" / "24"
    date_dir.mkdir(parents=True)
    recording = date_dir / f"in-15551234567-15557654321-20260524-162910-{call_id}.WAV"
    recording.write_bytes(b"RIFF" + b"\0" * 128)

    monkeypatch.setattr(calls, "_RECORDING_BASE", tmp_path)

    found = calls._find_recording(call_id, datetime(2026, 5, 24, 23, 29, 10))

    assert found == recording


def test_find_recording_accepts_ulaw_and_rejects_prefix_false_match(monkeypatch, tmp_path):
    call_id = "1779665339.911"
    date_dir = tmp_path / "2026" / "05" / "24"
    date_dir.mkdir(parents=True)
    false_match = date_dir / f"in-15551234567-15557654321-20260524-162910-{call_id}2.ulaw"
    true_match = date_dir / f"in-15551234567-15557654321-20260524-162910-{call_id}.ulaw"
    false_match.write_bytes(b"\xff" * 160)
    true_match.write_bytes(b"\xff" * 160)

    monkeypatch.setattr(calls, "_RECORDING_BASE", tmp_path)

    found = calls._find_recording(call_id, datetime(2026, 5, 24, 23, 29, 10))

    assert found == true_match


def test_ulaw_recording_response_wraps_audio_as_wav(tmp_path):
    recording = tmp_path / "recording-1779665339.911.ulaw"
    recording.write_bytes(b"\xff" * 160)

    response = calls._recording_response(recording)

    assert isinstance(response, Response)
    assert response.media_type == "audio/wav"
    assert bytes(response.body).startswith(b"RIFF")
    assert b"WAVE" in bytes(response.body[:16])


def test_pcm_wav_does_not_require_transcode(tmp_path):
    recording = tmp_path / "recording-1779665339.911.wav"
    with wave.open(str(recording), "wb") as wavf:
        wavf.setnchannels(1)
        wavf.setsampwidth(2)
        wavf.setframerate(8000)
        wavf.writeframes(b"\0" * 160)

    assert calls._wav_recording_requires_transcode(recording) is False


def test_uppercase_wav_requires_transcode(tmp_path):
    recording = tmp_path / "recording-1779665339.911.WAV"
    recording.write_bytes(b"RIFF" + b"\0" * 128)

    assert calls._wav_recording_requires_transcode(recording) is True


def test_uppercase_pcm_wav_does_not_require_transcode(tmp_path):
    """Pin the v6.5.2 fix: a valid PCM WAV with uppercase .WAV ext must
    NOT require sox transcode. Header-based probing supersedes case
    sensitivity (CodeRabbit on PR #396)."""
    recording = tmp_path / "recording-1779665339.911.WAV"
    with wave.open(str(recording), "wb") as wavf:
        wavf.setnchannels(1)
        wavf.setsampwidth(2)
        wavf.setframerate(8000)
        wavf.writeframes(b"\0" * 160)

    assert calls._wav_recording_requires_transcode(recording) is False
