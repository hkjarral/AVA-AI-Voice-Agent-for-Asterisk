from __future__ import annotations

import asyncio
import importlib
import sys
import time
from pathlib import Path

import pytest


LOCAL_AI_DIR = str(Path(__file__).resolve().parents[1] / "local_ai_server")


def _server_module():
    if LOCAL_AI_DIR not in sys.path:
        sys.path.insert(0, LOCAL_AI_DIR)
    return importlib.import_module("server")


class _SlowMelo:
    def synthesize(self, _text: str) -> bytes:
        time.sleep(0.12)
        return b"\x00\x00" * 80


class _AudioProcessor:
    def pcm16_to_ulaw_8k(self, _audio: bytes, _sample_rate: int) -> bytes:
        return b"ulaw"


@pytest.mark.asyncio
async def test_melotts_synthesis_does_not_block_event_loop():
    server_mod = _server_module()
    instance = object.__new__(server_mod.LocalAIServer)
    instance.melotts_backend = _SlowMelo()
    instance.audio_processor = _AudioProcessor()
    instance._tts_lock = asyncio.Lock()
    ticks = 0

    async def ticker():
        nonlocal ticks
        deadline = asyncio.get_running_loop().time() + 0.1
        while asyncio.get_running_loop().time() < deadline:
            ticks += 1
            await asyncio.sleep(0.005)

    audio, _ = await asyncio.gather(instance._process_tts_melotts("hello"), ticker())
    assert audio == b"ulaw"
    assert ticks >= 8, "blocking synthesis starved the WebSocket event loop"


@pytest.mark.asyncio
async def test_shared_tts_backend_access_is_serialized():
    server_mod = _server_module()
    instance = object.__new__(server_mod.LocalAIServer)
    instance.melotts_backend = _SlowMelo()
    instance.audio_processor = _AudioProcessor()
    instance._tts_lock = asyncio.Lock()
    active = 0
    peak = 0

    class _TrackedMelo:
        def synthesize(self, _text: str) -> bytes:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            time.sleep(0.04)
            active -= 1
            return b"\x00\x00"

    instance.melotts_backend = _TrackedMelo()
    await asyncio.gather(
        instance._process_tts_melotts("one"),
        instance._process_tts_melotts("two"),
    )
    assert peak == 1
