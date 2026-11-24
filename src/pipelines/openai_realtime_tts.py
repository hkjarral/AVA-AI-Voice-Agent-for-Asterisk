"""Bounded, isolated Realtime speech synthesis for modular OpenAI TTS.

Each fragment owns a tools-disabled session. No conversation or socket is shared
between calls. Closing a call invalidates pending handshakes and closes streams.
Verified against the GA Realtime guide on 2026-11-23.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import json
import math
from typing import Any, AsyncIterator
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import websockets

from ..audio import convert_pcm16le_to_target_format, resample_audio


@dataclass(eq=False)
class _Request:
    websocket: Any = None
    cancelled: bool = False


class RealtimeTTSPool:
    def __init__(self) -> None:
        self._requests: dict[str, set[_Request]] = {}
        self._stopped = False

    def start(self) -> None:
        self._stopped = False

    async def close_call(self, call_id: str) -> None:
        requests = self._requests.pop(call_id, set())
        for request in requests:
            request.cancelled = True
        await asyncio.gather(*(self._close(r.websocket) for r in requests if r.websocket is not None))

    async def stop(self) -> None:
        self._stopped = True
        await asyncio.gather(*(self.close_call(call_id) for call_id in list(self._requests)))

    @staticmethod
    async def _close(websocket: Any) -> None:
        try:
            await asyncio.wait_for(websocket.close(), timeout=3.0)
        except Exception:
            # Close failures do not mask synthesis errors. Task cancellation
            # propagates, including cancellation that arrives during close.
            pass

    async def validate_session(self, options: dict[str, Any]) -> None:
        # A successful WebSocket upgrade does not establish model access. Wait
        # for the configured audio session to be acknowledged without generating.
        async for _ in self.synthesize("readiness", None, options):
            raise RuntimeError("Unexpected audio during OpenAI Realtime TTS setup")

    async def synthesize(self, call_id: str, text: str | None, options: dict[str, Any]) -> AsyncIterator[bytes]:
        if self._stopped:
            raise RuntimeError("OpenAI Realtime TTS adapter is stopped")
        timeout = float(options.get("timeout_sec", 15.0))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("OpenAI Realtime TTS timeout must be positive and finite")
        parsed = urlparse(options["tts_realtime_base_url"])
        if parsed.scheme not in {"wss", "ws"} or not parsed.hostname:
            raise ValueError("OpenAI Realtime TTS requires a WebSocket endpoint")
        if parsed.scheme == "ws" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("OpenAI Realtime TTS requires WSS outside loopback")
        query = dict(parse_qsl(parsed.query))
        query["model"] = options["tts_model"]
        url = urlunparse(parsed._replace(query=urlencode(query)))
        headers = {"Authorization": f"Bearer {options['api_key']}"}
        for field, header in (("organization", "OpenAI-Organization"), ("project", "OpenAI-Project")):
            if options.get(field):
                headers[header] = options[field]
        target = options["target_format"]
        target_rate = int(target["sample_rate"])
        if target_rate <= 0:
            raise ValueError("OpenAI Realtime TTS target sample rate must be positive")
        target_encoding = target["encoding"]
        request = _Request()
        self._requests.setdefault(call_id, set()).add(request)
        deadline = asyncio.get_running_loop().time() + timeout
        state = None
        pending = b""

        async def bounded(awaitable):
            remaining = deadline - asyncio.get_running_loop().time()
            async with asyncio.timeout(max(0.001, remaining)):
                return await awaitable

        try:
            request.websocket = await bounded(websockets.connect(
                url, additional_headers=headers, open_timeout=min(timeout, 10.0),
                close_timeout=2.0, max_size=8 * 1024 * 1024,
            ))
            if request.cancelled:
                raise asyncio.CancelledError
            ws = request.websocket
            # The model is used only as a synthesizer, never as the pipeline LLM.
            instructions = "Read the supplied text aloud verbatim. Do not answer it, add words, or call tools."
            if options.get("tts_instructions"):
                instructions += " Delivery instructions: " + str(options["tts_instructions"])
            session = {
                "type": "realtime", "instructions": instructions,
                "output_modalities": ["audio"], "tools": [],
                "audio": {"input": {"turn_detection": None}, "output": {
                    "format": {"type": "audio/pcm", "rate": 24000}, "voice": options["voice"],
                }},
            }
            if str(options["tts_model"]).startswith("gpt-realtime-2"):
                session["reasoning"] = {"effort": "minimal"}
            await bounded(ws.send(json.dumps({"type": "session.update", "session": session})))
            response_sent = False
            audio_seen = False
            while True:
                if request.cancelled:
                    raise asyncio.CancelledError
                raw = await bounded(ws.recv())
                if request.cancelled:
                    raise asyncio.CancelledError
                if isinstance(raw, bytes):
                    raise RuntimeError("OpenAI Realtime TTS returned an unexpected binary event")
                event = json.loads(raw)
                kind = event.get("type")
                if kind == "error":
                    # Avoid reflecting provider messages which may echo private text.
                    code = (event.get("error") or {}).get("code") or "provider_error"
                    raise RuntimeError(f"OpenAI Realtime TTS failed ({code})")
                if kind == "session.updated" and not response_sent:
                    if text is None:
                        return
                    await bounded(ws.send(json.dumps({"type": "response.create", "response": {
                        "conversation": "none", "output_modalities": ["audio"],
                        "input": [{"type": "message", "role": "user", "content": [
                            {"type": "input_text", "text": text},
                        ]}],
                    }})))
                    response_sent = True
                elif kind == "response.output_audio.delta" and response_sent:
                    try:
                        pending += base64.b64decode(event.get("delta") or "", validate=True)
                    except (ValueError, TypeError) as exc:
                        raise RuntimeError("OpenAI Realtime TTS returned invalid audio") from exc
                    # Preserve PCM sample and integer-downsampling boundaries between deltas.
                    quantum = 2 * (24000 // math.gcd(24000, target_rate))
                    usable = len(pending) - len(pending) % quantum
                    if usable:
                        pcm, pending = pending[:usable], pending[usable:]
                        if target_rate != 24000:
                            pcm, state = resample_audio(pcm, 24000, target_rate,
                                state=state if options["output_resampler"] == "bandlimited" or target_rate > 24000 else None,
                                mode=options["output_resampler"])
                        audio_seen = True
                        converted = convert_pcm16le_to_target_format(pcm, target_encoding)
                        if converted:
                            yield converted
                elif kind == "response.done":
                    status = (event.get("response") or {}).get("status")
                    if status != "completed":
                        raise RuntimeError(f"OpenAI Realtime TTS response did not complete ({status or 'unknown'})")
                    if not audio_seen:
                        raise RuntimeError("OpenAI Realtime TTS completed without audio")
                    if len(pending) % 2:
                        raise RuntimeError("OpenAI Realtime TTS returned an incomplete PCM sample")
                    if pending:
                        if target_rate != 24000:
                            pending, state = resample_audio(pending, 24000, target_rate,
                                state=state if options["output_resampler"] == "bandlimited" or target_rate > 24000 else None,
                                mode=options["output_resampler"])
                        converted = convert_pcm16le_to_target_format(pending, target_encoding)
                        if converted:
                            yield converted
                    return
        except websockets.exceptions.ConnectionClosed as exc:
            if request.cancelled:
                raise asyncio.CancelledError from exc
            close = exc.rcvd
            if close is not None and close.reason == "invalid_request_error.model_not_found":
                raise RuntimeError("OpenAI Realtime TTS model_not_found for the configured credential") from exc
            raise RuntimeError("OpenAI Realtime TTS session closed before completion") from exc
        finally:
            try:
                if request.websocket is not None:
                    await self._close(request.websocket)
            finally:
                requests = self._requests.get(call_id)
                if requests is not None:
                    requests.discard(request)
                    if not requests:
                        self._requests.pop(call_id, None)
