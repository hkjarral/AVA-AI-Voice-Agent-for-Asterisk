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

# All fixtures below are synthetic; never open the configured monitor directory.
from types import SimpleNamespace
from unittest.mock import AsyncMock
import os
import io
import shutil

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
import auth


@pytest.fixture
def recording_api(monkeypatch, tmp_path):
    root = tmp_path / 'monitor'
    root.mkdir()
    monkeypatch.setattr(calls, '_RECORDING_BASE', root)
    record = SimpleNamespace(call_id='1779665339.911', start_time=datetime(2026, 5, 24))
    store = SimpleNamespace(get=AsyncMock(return_value=record))
    monkeypatch.setattr(calls, '_get_call_history_store', lambda: store)
    monkeypatch.setattr(auth, 'get_user', lambda username: auth.UserInDB(username=username, hashed_password='synthetic'))
    app = FastAPI()
    app.include_router(calls.router, prefix='/api', dependencies=[Depends(auth.get_current_user)])
    client = TestClient(app)
    client.headers['Authorization'] = 'Bearer ' + auth.create_access_token({'sub': 'synthetic-admin'})
    return client, root, store


def write_pcm(path):
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b'\0\0' * 800)
    return path.read_bytes()


@pytest.mark.parametrize('tail', ['recording', 'recording/audio', 'recording.wav', 'recording/download'])
@pytest.mark.parametrize('token', [None, 'invalid'])
def test_every_recording_endpoint_requires_auth(recording_api, tail, token):
    client, root, store = recording_api
    client.headers.pop('Authorization')
    if token:
        client.headers['Authorization'] = 'Bearer ' + token
    response = client.get('/api/calls/record-1/' + tail)
    assert response.status_code == 401
    store.get.assert_not_called()


@pytest.mark.parametrize('tail', ['recording', 'recording/audio', 'recording.wav', 'recording/download'])
def test_deleted_call_cannot_access_remaining_recording(recording_api, tail):
    client, root, store = recording_api
    write_pcm(root / 'call-1779665339.911.wav')
    store.get.return_value = None
    assert client.get('/api/calls/record-1/' + tail).status_code == 404


def test_late_recording_and_metadata_privacy(recording_api):
    client, root, _ = recording_api
    assert client.get('/api/calls/record-1/recording').json()['has_recording'] is False
    write_pcm(root / 'call-1779665339.911.wav')
    response = client.get('/api/calls/record-1/recording')
    assert response.json()['has_recording'] is True
    assert response.json()['file_path'] is None
    assert str(root) not in response.text


@pytest.mark.parametrize('suffix,size,empty', [('.wav', 44, True), ('.ulaw', 1, False), ('.gsm', 33, False), ('.gsm', 0, True)])
def test_codec_specific_empty_metadata(recording_api, suffix, size, empty):
    client, root, _ = recording_api
    (root / ('call-1779665339.911' + suffix)).write_bytes(b'\xff' * size)
    assert (client.get('/api/calls/record-1/recording').json()['duration_hint'] == 'empty') is empty


@pytest.mark.parametrize('other', ['other-1779665339.911.wav', '2026/05/24/call-1779665339.911.wav', 'call-1779665339.911.WAV'])
def test_ambiguity_across_all_layouts_fails_closed(recording_api, other):
    client, root, _ = recording_api
    write_pcm(root / 'call-1779665339.911.wav')
    candidate = root / other
    candidate.parent.mkdir(parents=True, exist_ok=True)
    # Case-insensitive filesystems cannot represent this particular collision.
    if candidate.exists():
        pytest.skip('case-insensitive filesystem')
    write_pcm(candidate)
    for tail in ['recording', 'recording/audio', 'recording.wav', 'recording/download']:
        assert client.get('/api/calls/record-1/' + tail).status_code == 409


def test_same_stem_format_variants_prefer_wav(recording_api):
    client, root, _ = recording_api
    expected = write_pcm(root / 'call-1779665339.911.wav')
    (root / 'call-1779665339.911.ulaw').write_bytes(b'\xff' * 160)
    (root / 'call-1779665339.911.gsm').write_bytes(b'\0' * 33)
    assert client.get('/api/calls/record-1/recording/download').content == expected


@pytest.mark.parametrize('filename', ['call-1779665339.9112.wav', 'call-x1779665339.911.wav', 'call-1779665339.911x.wav', 'call-1779665339.911.2.wav'])
def test_exact_identifier_tokens(recording_api, filename):
    client, root, _ = recording_api
    write_pcm(root / filename)
    assert client.get('/api/calls/record-1/recording').json()['has_recording'] is False


@pytest.mark.parametrize('call_id', ['../secret', '*', '', 'x/y', 'a\\b'])
def test_unsafe_call_identifiers(recording_api, call_id):
    client, _, store = recording_api
    store.get.return_value.call_id = call_id
    assert client.get('/api/calls/record-1/recording/download').status_code == 409


@pytest.mark.parametrize('inside', [True, False])
def test_file_symlinks_are_rejected(recording_api, inside):
    client, root, _ = recording_api
    target = (root if inside else root.parent) / 'target.wav'
    write_pcm(target)
    (root / 'call-1779665339.911.wav').symlink_to(target)
    response = client.get('/api/calls/record-1/recording/download')
    assert response.status_code == 409
    assert str(root) not in response.text


def test_parent_symlink_is_rejected(recording_api):
    client, root, _ = recording_api
    target = root.parent / 'elsewhere/05/24'
    target.mkdir(parents=True)
    write_pcm(target / 'call-1779665339.911.wav')
    (root / '2026').symlink_to(target.parent.parent, target_is_directory=True)
    assert client.get('/api/calls/record-1/recording/download').status_code == 409


@pytest.mark.parametrize('replacement', ['symlink', 'deleted'])
def test_replacement_between_discovery_and_open(recording_api, monkeypatch, replacement):
    client, root, _ = recording_api
    path = root / 'call-1779665339.911.wav'
    write_pcm(path)
    original = calls._find_recording
    def racing_find(*args):
        result = original(*args)
        path.unlink()
        if replacement == 'symlink':
            target = root.parent / 'secret.wav'
            write_pcm(target)
            path.symlink_to(target)
        return result
    monkeypatch.setattr(calls, '_find_recording', racing_find)
    assert client.get('/api/calls/record-1/recording/download').status_code == (409 if replacement == 'symlink' else 404)


@pytest.mark.parametrize('suffix', ['.wav', '.ulaw', '.gsm'])
def test_download_playback_fidelity_and_range(recording_api, suffix):
    client, root, _ = recording_api
    path = root / ('call-1779665339.911' + suffix)
    pcm = root.parent / 'source.wav'
    expected = write_pcm(pcm)
    if suffix == '.wav':
        path.write_bytes(expected)
    elif suffix == '.ulaw':
        path.write_bytes(b'\xff' * 160)
    else:
        if not shutil.which('sox'):
            pytest.skip('sox required for real GSM codec fixture')
        calls.subprocess.run(['sox', str(pcm), str(path)], check=True)
    stream = client.get('/api/calls/record-1/recording/audio')
    download = client.get('/api/calls/record-1/recording/download')
    legacy = client.get('/api/calls/record-1/recording.wav')
    assert stream.status_code == download.status_code == legacy.status_code == 200
    assert stream.content == download.content == legacy.content
    assert download.content[:4] == b'RIFF'
    with wave.open(io.BytesIO(download.content), 'rb') as decoded:
        assert decoded.getcomptype() == 'NONE'
        assert decoded.getsampwidth() == 2
        assert decoded.getnframes() == (160 if suffix == '.ulaw' else 800)
    assert stream.headers['content-disposition'].startswith('inline;')
    assert download.headers['content-disposition'].startswith('attachment;')
    assert download.headers['cache-control'] == 'private, no-store'
    for range_value, expected_slice in [('bytes=0-15', slice(0, 16)), ('bytes=-16', slice(-16, None))]:
        part = client.get('/api/calls/record-1/recording/download', headers={'Range': range_value})
        assert part.status_code == 206
        assert part.content == download.content[expected_slice]
    assert client.get('/api/calls/record-1/recording/audio', headers={'Range': 'bytes=99999999-'}).status_code == 416


@pytest.mark.asyncio
async def test_snapshot_cleanup_when_send_fails(recording_api):
    _, root, _ = recording_api
    write_pcm(root / 'call-1779665339.911.wav')
    response = calls._prepare_recording('1779665339.911', None, False)
    snapshot = Path(response.path)
    assert snapshot.exists()
    async def send(message):
        raise RuntimeError('synthetic disconnect')
    with pytest.raises(RuntimeError):
        await response({'type': 'http', 'method': 'GET', 'headers': [], 'asgi': {'spec_version': '2.4'}}, AsyncMock(), send)
    assert not snapshot.parent.exists()


def test_snapshot_survives_original_deletion(recording_api):
    _, root, _ = recording_api
    original = root / 'call-1779665339.911.wav'
    expected = write_pcm(original)
    response = calls._prepare_recording('1779665339.911', None, False)
    try:
        original.unlink()
        assert Path(response.path).read_bytes() == expected
    finally:
        response.directory.cleanup()


@pytest.mark.parametrize('tail', ['recording', 'recording/audio', 'recording.wav', 'recording/download'])
def test_password_rotation_gate_applies_to_recordings(recording_api, monkeypatch, tail):
    client, _, store = recording_api
    monkeypatch.setattr(auth, 'get_user', lambda username: auth.UserInDB(username=username, hashed_password='synthetic', must_change_password=True))
    assert client.get('/api/calls/record-1/' + tail).status_code == 403
    store.get.assert_not_called()


@pytest.mark.parametrize('failure,status', [('missing_sox', 415), ('timeout', 504), ('decode', 422)])
def test_conversion_errors_remove_snapshot(recording_api, monkeypatch, tmp_path, failure, status):
    client, root, _ = recording_api
    (root / 'call-1779665339.911.gsm').write_bytes(b'\0' * 33)
    snapshots = tmp_path / 'snapshots'
    snapshots.mkdir()
    original = calls.tempfile.TemporaryDirectory
    monkeypatch.setattr(calls.tempfile, 'TemporaryDirectory', lambda **kwargs: original(dir=snapshots, **kwargs))
    monkeypatch.setattr(calls.shutil, 'which', lambda _: None if failure == 'missing_sox' else '/synthetic/sox')
    def run(*args, **kwargs):
        if failure == 'timeout':
            raise calls.subprocess.TimeoutExpired('sox', 1)
        return SimpleNamespace(returncode=1, stdout=b'', stderr=b'synthetic failure')
    monkeypatch.setattr(calls.subprocess, 'run', run)
    response = client.get('/api/calls/record-1/recording/download')
    assert response.status_code == status
    assert list(snapshots.iterdir()) == []
    assert str(root) not in response.text


def test_file_changes_during_copy_fail_closed(recording_api, monkeypatch):
    client, root, _ = recording_api
    path = root / 'call-1779665339.911.wav'
    write_pcm(path)
    original_fstat = os.fstat
    reads = 0
    def racing_stat(fd):
        nonlocal reads
        value = original_fstat(fd)
        if value.st_ino == path.stat().st_ino:
            reads += 1
            # Discovery's type check, open's type check, initial size, final size.
            if reads == 4:
                with path.open('ab') as output:
                    output.write(b'\0\0')
                return original_fstat(fd)
        return value
    monkeypatch.setattr(calls.os, 'fstat', racing_stat)
    assert client.get('/api/calls/record-1/recording/download').status_code == 409
