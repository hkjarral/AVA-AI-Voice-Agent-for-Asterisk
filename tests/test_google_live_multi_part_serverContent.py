"""Pin Gemini 3.1 multi-part serverContent envelope handling.

Audit follow-up: Gemini 3.1 Flash Live changed the server-content shape so
that a single server event can deliver multiple top-level keys
simultaneously (e.g., `modelTurn` audio AND `outputTranscription` AND
`turnComplete` in one envelope), versus 2.5 where each event was
single-keyed.

AAVA's `_handle_server_content` parser handles this correctly today
because it iterates each top-level key independently with no
short-circuiting early returns. This test pins that property so a future
refactor can't accidentally introduce an early return that skips one of
the keys.

Phase 0 testing on call `1778360966.459` (2026-05-09) observed 94 of these
multi-part envelopes during a 59s call with `gemini-3.1-flash-live-preview`
on the Developer API endpoint, with zero parse errors and zero unhandled
message types. This test makes that empirical observation a unit-level
guarantee.
"""

import asyncio
import base64
from unittest.mock import AsyncMock

import pytest

from src.config import GoogleProviderConfig
from src.providers.google_live import GoogleLiveProvider


def _make_provider() -> GoogleLiveProvider:
    """Build a minimally-instantiated provider for parser testing.

    The GoogleLiveProvider constructor doesn't open any sockets or start any
    tasks — those happen in `start_session`. So we can build one without an
    asyncio loop and exercise `_handle_server_content` in isolation.
    """
    cfg = GoogleProviderConfig(api_key="test-key-not-real")
    on_event = AsyncMock()
    provider = GoogleLiveProvider(cfg, on_event)
    provider._call_id = "test-call-1"
    return provider


def _audio_b64(num_samples: int = 100) -> str:
    """Produce a base64-encoded PCM16 audio chunk for `inlineData` parts."""
    return base64.b64encode(b"\x00\x00" * num_samples).decode("ascii")


# --- Multi-part envelope: modelTurn + outputTranscription in one event ---

@pytest.mark.asyncio
async def test_multi_part_modelTurn_and_outputTranscription_both_processed():
    """The 3.1 multi-part shape: a single envelope with both `modelTurn`
    (audio bytes) and `outputTranscription` (text)."""
    provider = _make_provider()
    audio_handler = AsyncMock()
    provider._handle_audio_output = audio_handler

    initial_buffer = provider._output_transcription_buffer

    envelope = {
        "serverContent": {
            "modelTurn": {
                "parts": [
                    {"inlineData": {"mimeType": "audio/pcm", "data": _audio_b64()}}
                ]
            },
            "outputTranscription": {"text": "Hello"},
        }
    }
    await provider._handle_server_content(envelope)

    # modelTurn.parts → audio handler called
    assert audio_handler.await_count == 1, (
        "Multi-part envelope must process modelTurn audio. "
        "Audio handler call count: " + str(audio_handler.await_count)
    )

    # outputTranscription → buffer mutated
    assert provider._output_transcription_buffer != initial_buffer, (
        "Multi-part envelope must process outputTranscription. "
        "Buffer was not mutated."
    )
    assert "Hello" in provider._output_transcription_buffer


@pytest.mark.asyncio
async def test_multi_part_modelTurn_with_turnComplete_finalizes_assistant_text():
    """Another common 3.1 shape: `modelTurn` plus `turnComplete=True` in
    the same event. Parser must process the audio AND finalize the
    transcript on the same envelope."""
    provider = _make_provider()
    audio_handler = AsyncMock()
    provider._handle_audio_output = audio_handler

    # Pre-populate the output transcription buffer
    provider._output_transcription_buffer = "I am using the Google Gemini Live API."

    envelope = {
        "serverContent": {
            "modelTurn": {
                "parts": [
                    {"inlineData": {"mimeType": "audio/pcm", "data": _audio_b64()}}
                ]
            },
            "turnComplete": True,
        }
    }
    await provider._handle_server_content(envelope)

    # Audio still processed despite turnComplete=True
    assert audio_handler.await_count == 1

    # turnComplete should have finalized the assistant transcript
    # (saving _last_final_assistant_text) and cleared the working buffer.
    assert provider._last_final_assistant_text == "I am using the Google Gemini Live API."
    assert provider._output_transcription_buffer == ""


@pytest.mark.asyncio
async def test_multi_part_inputTranscription_and_outputTranscription_both_processed():
    """Both transcription channels in one envelope (rare but observed)."""
    provider = _make_provider()
    initial_in = provider._input_transcription_buffer
    initial_out = provider._output_transcription_buffer

    envelope = {
        "serverContent": {
            "inputTranscription": {"text": "What time is it?"},
            "outputTranscription": {"text": "It's 3pm."},
        }
    }
    await provider._handle_server_content(envelope)

    assert "What time is it?" in provider._input_transcription_buffer
    assert "It's 3pm." in provider._output_transcription_buffer
    assert provider._input_transcription_buffer != initial_in
    assert provider._output_transcription_buffer != initial_out


# --- Multi-part envelope: modelTurn with multiple parts in one envelope ---

@pytest.mark.asyncio
async def test_modelTurn_with_multiple_audio_parts_processes_all():
    """3.1 can deliver multiple `parts` inside one `modelTurn`. Audio
    handler must be called for each part — confirming the inner `for part in
    model_parts:` loop processes every entry."""
    provider = _make_provider()
    audio_handler = AsyncMock()
    provider._handle_audio_output = audio_handler

    envelope = {
        "serverContent": {
            "modelTurn": {
                "parts": [
                    {"inlineData": {"mimeType": "audio/pcm", "data": _audio_b64(50)}},
                    {"inlineData": {"mimeType": "audio/pcm", "data": _audio_b64(60)}},
                    {"inlineData": {"mimeType": "audio/pcm", "data": _audio_b64(70)}},
                ]
            }
        }
    }
    await provider._handle_server_content(envelope)

    assert audio_handler.await_count == 3, (
        "All audio parts in a modelTurn must be processed. Got "
        + str(audio_handler.await_count)
    )


# --- 2.5 single-keyed envelopes still work (no regression) ---

@pytest.mark.asyncio
async def test_single_keyed_modelTurn_envelope_still_processes_audio():
    """2.5-style single-keyed `modelTurn` envelope must still work."""
    provider = _make_provider()
    audio_handler = AsyncMock()
    provider._handle_audio_output = audio_handler

    envelope = {
        "serverContent": {
            "modelTurn": {
                "parts": [{"inlineData": {"mimeType": "audio/pcm", "data": _audio_b64()}}]
            }
        }
    }
    await provider._handle_server_content(envelope)
    assert audio_handler.await_count == 1


@pytest.mark.asyncio
async def test_single_keyed_outputTranscription_envelope_still_buffers_text():
    provider = _make_provider()
    envelope = {"serverContent": {"outputTranscription": {"text": "fragment"}}}
    await provider._handle_server_content(envelope)
    assert "fragment" in provider._output_transcription_buffer


# --- Empty/edge envelopes don't crash ---

@pytest.mark.asyncio
async def test_empty_serverContent_envelope_does_not_crash():
    """Heartbeat-style empty serverContent envelopes (observed in 3.1 logs)
    must be handled gracefully."""
    provider = _make_provider()
    envelope = {"serverContent": {}}
    await provider._handle_server_content(envelope)
    # No assertion — just must not raise.


@pytest.mark.asyncio
async def test_modelTurn_with_no_parts_does_not_crash():
    """Defensive: `modelTurn: {}` (no `parts` key) must not crash."""
    provider = _make_provider()
    audio_handler = AsyncMock()
    provider._handle_audio_output = audio_handler
    envelope = {"serverContent": {"modelTurn": {}}}
    await provider._handle_server_content(envelope)
    assert audio_handler.await_count == 0


@pytest.mark.asyncio
async def test_modelTurn_with_text_parts_only_buffers_model_text():
    """text-only modelTurn parts go into `_model_text_buffer` (not
    `_output_transcription_buffer` — see comment at google_live.py:1620)."""
    provider = _make_provider()
    initial = provider._model_text_buffer
    envelope = {
        "serverContent": {
            "modelTurn": {"parts": [{"text": "internal reasoning"}]}
        }
    }
    await provider._handle_server_content(envelope)
    assert "internal reasoning" in provider._model_text_buffer
    assert provider._model_text_buffer != initial
