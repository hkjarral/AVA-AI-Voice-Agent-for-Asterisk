#!/usr/bin/env python3
"""Local mock of the Fish Audio TTS API, to exercise the provider without an account.

It mirrors the documented surface of ``POST /v1/tts``: Bearer authentication, the
``model`` header, the JSON body fields, format and sample-rate validation, and a
chunked audio response. Audio is a short generated tone by default, so the mock
is self-contained; point ``FISH_MOCK_LOCAL_WS`` at a local AI server websocket to
hear real speech instead.

Usage:
    python scripts/fish_audio_mock.py &
    FISH_AUDIO_API_KEY=mock-key FISH_AUDIO_BASE_URL=http://127.0.0.1:8788/v1 \
        pytest -m integration tests/test_pipeline_fish_audio_adapters.py

Environment:
    FISH_MOCK_HOST            bind address (default 127.0.0.1)
    FISH_MOCK_PORT            bind port (default 8788)
    FISH_MOCK_FIRST_BYTE_SEC  delay before the first audio byte (default 0.12)
    FISH_MOCK_CHUNK_MS        audio duration per HTTP chunk (default 40)
    FISH_MOCK_LOCAL_WS        optional local AI server websocket for real speech

Hooks, triggered by the request text, to exercise failure handling:
    FISH_MOCK_401     answer 401 Unauthorized
    FISH_MOCK_SLOW    wait 3 s before the first byte
    FISH_MOCK_EMPTY   answer 200 with no audio
"""
from __future__ import annotations

import io
import json
import math
import os
import struct
import sys
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.getenv("FISH_MOCK_HOST", "127.0.0.1")
PORT = int(os.getenv("FISH_MOCK_PORT", "8788"))
FIRST_BYTE_DELAY_SEC = float(os.getenv("FISH_MOCK_FIRST_BYTE_SEC", "0.12"))
CHUNK_MS = int(os.getenv("FISH_MOCK_CHUNK_MS", "40"))
LOCAL_AI_WS = os.getenv("FISH_MOCK_LOCAL_WS", "")

# Mirrors the documented Fish Audio contract.
SUPPORTED_RATES = (8000, 16000, 24000, 32000, 44100, 48000)
SUPPORTED_FORMATS = ("pcm", "wav", "mp3", "opus")
SERVED_FORMATS = ("pcm", "wav")
# Rates a local AI server typically offers, when one is configured.
LOCAL_AI_RATES = (8000, 16000, 24000)


def log(message: str) -> None:
    print(time.strftime("%H:%M:%S"), message, flush=True)


def _speech_from_local_ai(text: str, sample_rate: int) -> bytes:
    """Optional: fetch PCM16 speech from a local AI server websocket."""
    import asyncio
    import base64

    import websockets

    async def fetch() -> bytes:
        async with websockets.connect(LOCAL_AI_WS, ping_interval=None, max_size=None) as websocket:
            await websocket.send(json.dumps(
                {"type": "set_mode", "mode": "tts", "call_id": "fish-audio-mock"}))
            while True:
                message = json.loads(await asyncio.wait_for(websocket.recv(), 10))
                if message.get("type") == "mode_ready":
                    break
            await websocket.send(json.dumps({
                "type": "tts_request",
                "mode": "tts",
                "call_id": "fish-audio-mock",
                "text": text,
                "output_encoding": "linear16",
                "output_sample_rate_hz": sample_rate,
            }))
            while True:
                raw = await asyncio.wait_for(websocket.recv(), 30)
                if isinstance(raw, bytes):
                    continue
                message = json.loads(raw)
                if message.get("type") == "tts_response":
                    return base64.b64decode(message.get("audio_data", ""))

    return asyncio.run(fetch())


def _generated_tone(text: str, sample_rate: int) -> bytes:
    """Self-contained audio: one short warbling syllable per word."""
    samples = []
    syllable = int(sample_rate * 0.18)
    gap = int(sample_rate * 0.05)
    for index, word in enumerate(text.split() or ["mock"]):
        frequency = 180 + (len(word) * 17) + (index % 3) * 40
        for position in range(syllable):
            envelope = math.sin(math.pi * position / syllable)
            samples.append(int(9000 * envelope * math.sin(
                2 * math.pi * frequency * position / sample_rate)))
        samples.extend([0] * gap)
    return struct.pack("<" + "h" * len(samples), *samples)


def synthesize(text: str, sample_rate: int) -> bytes:
    if LOCAL_AI_WS:
        source_rate = sample_rate if sample_rate in LOCAL_AI_RATES else 16000
        try:
            pcm = _speech_from_local_ai(text, source_rate)
            if pcm and source_rate == sample_rate:
                return pcm
            if pcm:
                import audioop

                return audioop.ratecv(pcm, 2, 1, source_rate, sample_rate, None)[0]
        except Exception as exc:  # noqa: BLE001 - the mock must stay up
            log("local AI server unavailable (%s); using a generated tone" % type(exc).__name__)
    return _generated_tone(text, sample_rate)


def wav_container(pcm: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


class FishAudioMockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FishAudioMock/1.0"

    def log_message(self, fmt, *args):  # silence the default access log
        pass

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - http.server API
        if self.path.startswith("/v1/wallet"):
            self._json(200, {"credit": "42.0"})
            return
        self._json(404, {"detail": "Not Found"})

    def do_POST(self):  # noqa: N802 - http.server API
        if not self.path.startswith("/v1/tts"):
            self._json(404, {"detail": "Not Found"})
            return

        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer ") or len(authorization) <= len("Bearer "):
            log("rejected: missing bearer token")
            self._json(401, {"detail": "Unauthorized"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(422, {"detail": "invalid JSON body"})
            return

        model = self.headers.get("model", "(none)")
        text = str(body.get("text") or "")
        audio_format = str(body.get("format") or "mp3").lower()
        sample_rate = body.get("sample_rate")

        log("POST /v1/tts model=%s format=%s sample_rate=%s latency=%s reference_id=%s text=%r"
            % (model, audio_format, sample_rate, body.get("latency", "normal"),
               body.get("reference_id"), text[:60]))

        if audio_format not in SUPPORTED_FORMATS:
            self._json(422, {"detail": "unsupported format: %s" % audio_format})
            return
        if audio_format not in SERVED_FORMATS:
            self._json(422, {"detail": "this mock only serves pcm and wav"})
            return
        sample_rate = int(sample_rate or 44100)
        if sample_rate not in SUPPORTED_RATES:
            self._json(422, {"detail": "unsupported sample_rate: %s" % sample_rate})
            return
        if not text:
            self._json(422, {"detail": "text is required"})
            return
        if "FISH_MOCK_401" in text:
            log("hook: answering 401")
            self._json(401, {"detail": "Unauthorized (mock hook)"})
            return

        audio = synthesize(text, sample_rate)
        if audio_format == "wav":
            audio = wav_container(audio, sample_rate)
        if "FISH_MOCK_EMPTY" in text:
            log("hook: answering without audio")
            audio = b""

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        if "FISH_MOCK_SLOW" in text:
            log("hook: waiting 3 s before the first byte")
            time.sleep(3.0)
        else:
            time.sleep(FIRST_BYTE_DELAY_SEC)

        chunk_bytes = max(2, int(sample_rate * (CHUNK_MS / 1000.0)) * 2)
        sent = 0
        started = time.perf_counter()
        try:
            for offset in range(0, len(audio), chunk_bytes):
                chunk = audio[offset:offset + chunk_bytes]
                self.wfile.write(b"%x\r\n" % len(chunk))
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
                sent += len(chunk)
                # Stream faster than real time, like a remote provider would.
                time.sleep(CHUNK_MS / 4000.0)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            log("client closed the stream after %d bytes" % sent)
            return
        log("sent %d bytes (%.0f ms)" % (sent, (time.perf_counter() - started) * 1000))


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), FishAudioMockHandler)
    log("Fish Audio mock listening on http://%s:%d/v1%s"
        % (HOST, PORT, " (speech from %s)" % LOCAL_AI_WS if LOCAL_AI_WS else " (generated tone)"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
