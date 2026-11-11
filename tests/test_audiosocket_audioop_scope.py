"""AudioSocket ingress must use the module-level audioop throughout.

A function-local ``import audioop`` makes the name local to the whole of
``Engine._audiosocket_handle_audio``. Every use above the import then raises
UnboundLocalError, which the surrounding ``except Exception`` blocks swallow,
so the first-frame probe, the RMS readings and energy barge-in fail silently.
With those paths live again, the probe must not byte-swap AudioSocket audio,
and the per-frame DC removal stays off (INBOUND_DC_REMOVAL_ENABLED).
"""
import audioop
import math
import random
import struct
import symtable
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src import engine as engine_module
from src.audio.audiosocket_protocol import AudioSocketAudioFrame
from tests.test_google_long_audio_playback import ingress_engine

DC = 1500
AMPLITUDE = 6000


def biased_frame(amplitude=AMPLITUDE, dc=DC):
    """20 ms of 8 kHz slin: a square wave riding on a DC offset."""
    pcm = struct.pack("<hh", dc + amplitude, dc - amplitude) * 80
    return AudioSocketAudioFrame(pcm, 0x10, "slin", 8000)


BIASED_RMS = audioop.rms(biased_frame().payload, 2)


def non_continuous_engine(monkeypatch):
    """A provider that takes gated audio, like the pipeline-style providers."""
    e, s, _ = ingress_engine(rate=8000)
    p = SimpleNamespace(
        send_audio=AsyncMock(),
        get_capabilities=lambda: SimpleNamespace(requires_continuous_audio=False),
        config=SimpleNamespace(continuous_input=False),
    )
    s.provider_name = "gated"
    e.provider_kinds["gated"] = "gated"
    e._call_providers[s.call_id] = p
    e._apply_barge_in_action = AsyncMock()
    log = Mock()
    monkeypatch.setattr(engine_module, "logger", log)
    return e, s, p, log


def info_calls(log, event):
    return [c.kwargs for c in log.info.call_args_list if c.args and c.args[0] == event]


def test_audioop_is_not_a_local_name_in_any_engine_function():
    table = symtable.symtable(
        Path(engine_module.__file__).read_text(), engine_module.__file__, "exec"
    )
    shadowed = []

    def walk(scope):
        for child in scope.get_children():
            if child.get_type() == "function":
                try:
                    if child.lookup("audioop").is_local():
                        shadowed.append(f"{child.get_name()}:{child.get_lineno()}")
                except KeyError:
                    pass
            walk(child)

    walk(table)
    assert shadowed == []


@pytest.mark.asyncio
async def test_gated_provider_gets_unaltered_audio_and_real_guard_rms(monkeypatch):
    e, s, p, log = non_continuous_engine(monkeypatch)
    await e.session_store.upsert_call(s)
    await e._audiosocket_handle_audio("ingress", biased_frame())
    assert p.send_audio.await_args.args[0] == biased_frame().payload
    assert info_calls(log, "Inbound PCM guard RMS") == [{
        "call_id": s.call_id,
        "pre_guard_pcm_rms": BIASED_RMS,
        "post_guard_pcm_rms": BIASED_RMS,
    }]


@pytest.mark.asyncio
async def test_first_frame_probe_logs_once(monkeypatch):
    e, s, _, log = non_continuous_engine(monkeypatch)
    s.vad_state.pop("format_probe_done")
    await e.session_store.upsert_call(s)
    for _ in range(2):
        await e._audiosocket_handle_audio("ingress", biased_frame())
    probes = info_calls(log, "AudioSocket frame probe")
    assert len(probes) == 1
    assert probes[0]["rms_native"] > 0
    assert s.vad_state["format_probe_done"] is True
    assert s.vad_state["pcm16_inbound_swap"] is False


@pytest.mark.asyncio
async def test_gated_provider_energy_fallback_barges_in_during_tts(monkeypatch):
    e, s, _, _ = non_continuous_engine(monkeypatch)
    s.audio_capture_enabled = False
    await e.session_store.upsert_call(s)
    for _ in range(13):  # min_ms 250 at 20 ms per frame
        await e._audiosocket_handle_audio("ingress", biased_frame())
    e._apply_barge_in_action.assert_awaited_once_with(
        s.call_id, source="local_vad", reason="tts_overlap",
    )


@pytest.mark.asyncio
async def test_pipeline_local_energy_barges_in_without_talk_detect(monkeypatch):
    e, s, _, _ = non_continuous_engine(monkeypatch)
    e._pipeline_forced[s.call_id] = True
    s.audio_capture_enabled = False
    await e.session_store.upsert_call(s)
    for _ in range(6):  # pipeline_min_ms 120 at 20 ms per frame
        await e._audiosocket_handle_audio("ingress", biased_frame())
    e._apply_barge_in_action.assert_awaited_once_with(
        s.call_id, source="local_vad", reason="pipeline_tts_overlap",
    )


@pytest.mark.asyncio
async def test_google_live_gets_unaltered_audio():
    e, s, p = ingress_engine(rate=8000)
    e._maybe_provider_barge_in_fallback = AsyncMock()
    await e.session_store.upsert_call(s)
    await e._audiosocket_handle_audio("ingress", biased_frame())
    assert p.send_audio.await_args.args[0] == biased_frame().payload
    assert e._maybe_provider_barge_in_fallback.await_args.kwargs["pcm16"] == biased_frame().payload


@pytest.mark.asyncio
async def test_dc_removal_code_still_works_when_enabled(monkeypatch):
    monkeypatch.setattr(engine_module, "INBOUND_DC_REMOVAL_ENABLED", True)
    e, s, p = ingress_engine(rate=8000)
    e._maybe_provider_barge_in_fallback = AsyncMock()
    await e.session_store.upsert_call(s)
    await e._audiosocket_handle_audio("ingress", biased_frame())
    sent = p.send_audio.await_args.args[0]
    assert audioop.avg(sent, 2) == 0
    assert audioop.rms(sent, 2) == AMPLITUDE


@pytest.mark.asyncio
@pytest.mark.parametrize("protection", ["below_threshold", "initial_window"])
async def test_pipeline_local_energy_respects_its_gates(monkeypatch, protection):
    e, s, _, _ = non_continuous_engine(monkeypatch)
    e._pipeline_forced[s.call_id] = True
    s.audio_capture_enabled = False
    frame = biased_frame()
    if protection == "below_threshold":
        frame = biased_frame(200, dc=0)  # pipeline_energy_threshold is 300
    else:
        s.tts_started_ts = time.time()  # inside initial_protection_ms
    await e.session_store.upsert_call(s)
    for _ in range(25):
        await e._audiosocket_handle_audio("ingress", frame)
    e._apply_barge_in_action.assert_not_awaited()
    assert s.barge_in_candidate_ms == 0


def slin_frame(samples, rate):
    pcm = struct.pack(f"<{len(samples)}h", *samples)
    if rate == 16000:
        return AudioSocketAudioFrame(pcm, 0x12, "slin16", rate)
    return AudioSocketAudioFrame(pcm, 0x10, "slin", rate)


@pytest.mark.asyncio
@pytest.mark.parametrize("rate", [8000, 16000])
async def test_quiet_first_frame_does_not_byte_swap_the_call(rate):
    """Line noise on the first frame looks loud when byte-swapped; it must not stick."""
    e, s, p = ingress_engine(rate=rate)
    e._maybe_provider_barge_in_fallback = AsyncMock()
    s.vad_state.pop("format_probe_done")
    await e.session_store.upsert_call(s)
    n = rate // 50
    rng = random.Random(7)
    noise = [int(rng.gauss(0, 20)) for _ in range(n)]
    speech = [int(3000 * math.sin(2 * math.pi * 300 * i / rate)) for i in range(n)]
    await e._audiosocket_handle_audio("ingress", slin_frame(noise, rate))
    await e._audiosocket_handle_audio("ingress", slin_frame(speech, rate))
    first, second = (c.args[0] for c in p.send_audio.await_args_list)
    assert audioop.rms(first, 2) < 100
    assert second == struct.pack(f"<{n}h", *speech)
    assert s.vad_state["format_probe_done"] is True
    assert s.vad_state["pcm16_inbound_swap"] is False
