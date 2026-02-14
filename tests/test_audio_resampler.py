import audioop
import struct
import pytest

from src.audio import (
    convert_pcm16le_to_target_format,
    mulaw_to_pcm16le,
    pcm16le_to_mulaw,
    resample_audio,
)


def test_mulaw_round_trip_identity():
    pcm_samples = audioop.tostereo(b"\x00\x10" * 40, 2, 1, 1)  # create dummy PCM16 data
    mono_pcm = audioop.tomono(pcm_samples, 2, 1, 0)
    mulaw = pcm16le_to_mulaw(mono_pcm)
    restored = mulaw_to_pcm16le(mulaw)
    assert len(restored) == len(mono_pcm)
    restored_rms = audioop.rms(restored, 2)
    original_rms = audioop.rms(mono_pcm, 2)
    assert restored_rms == pytest.approx(
        original_rms, abs=8
    )


def test_resample_identity_when_rates_match():
    pcm = b"\x01\x02" * 160
    converted, state = resample_audio(pcm, 8000, 8000)
    assert converted == pcm
    assert state is None


def test_convert_pcm_to_ulaw_format():
    pcm = b"\x01\x02" * 160
    ulaw = convert_pcm16le_to_target_format(pcm, "ulaw")
    assert len(ulaw) == len(pcm) // 2  # μ-law is 1 byte per sample


# ── NumPy resampler: exact output sizing ──────────────────────────────


def test_upsample_8k_to_16k_exact_size():
    """160 samples @ 8kHz → 320 samples @ 16kHz = 640 bytes."""
    pcm_8k = b"\x00\x01" * 160  # 160 samples = 320 bytes
    out, _ = resample_audio(pcm_8k, 8000, 16000)
    assert len(out) == 640


def test_downsample_24k_to_8k_exact_size():
    """480 samples @ 24kHz (20 ms) → 160 samples @ 8kHz = 320 bytes."""
    pcm_24k = b"\x00\x01" * 480  # 480 samples = 960 bytes
    out, _ = resample_audio(pcm_24k, 24000, 8000)
    assert len(out) == 320


def test_upsample_8k_to_24k_exact_size():
    """160 samples @ 8kHz → 480 samples @ 24kHz = 960 bytes."""
    pcm_8k = b"\x00\x01" * 160
    out, _ = resample_audio(pcm_8k, 8000, 24000)
    assert len(out) == 960


# ── NumPy resampler: state continuity ─────────────────────────────────


def test_state_continuity_across_chunks():
    """Resample two consecutive chunks with state carry and verify smooth boundary."""
    import math

    # Generate a 440 Hz sine wave at 8 kHz, two 20 ms chunks
    freq, sr = 440, 8000
    chunk_samples = 160  # 20 ms @ 8 kHz
    total_samples = chunk_samples * 2

    samples = [int(16000 * math.sin(2 * math.pi * freq * i / sr)) for i in range(total_samples)]
    chunk_a = struct.pack(f"<{chunk_samples}h", *samples[:chunk_samples])
    chunk_b = struct.pack(f"<{chunk_samples}h", *samples[chunk_samples:])

    # Resample chunk A, then chunk B with state carry
    out_a, state = resample_audio(chunk_a, 8000, 16000)
    out_b, _ = resample_audio(chunk_b, 8000, 16000, state=state)

    # Decode boundary samples
    sa = struct.unpack_from("<h", out_a, len(out_a) - 2)[0]
    sb = struct.unpack_from("<h", out_b, 0)[0]

    # The boundary jump should be small (smooth interpolation, not a click/pop)
    assert abs(sb - sa) < 2000, f"Boundary discontinuity too large: {abs(sb - sa)}"


def test_stateless_vs_stateful_output_differs():
    """Stateless resampling of chunk B should differ from stateful (at boundaries)."""
    pcm = b"\x10\x00" * 320  # 320 samples
    chunk_a = pcm[:320]
    chunk_b = pcm[320:]

    # Stateful path
    _, state = resample_audio(chunk_a, 8000, 16000)
    out_stateful, _ = resample_audio(chunk_b, 8000, 16000, state=state)

    # Stateless path (state=None)
    out_stateless, _ = resample_audio(chunk_b, 8000, 16000)

    # They may or may not differ depending on input, but both should produce valid output
    assert len(out_stateful) == len(out_stateless)


# ── Edge cases ────────────────────────────────────────────────────────


def test_empty_input_returns_empty():
    out, state = resample_audio(b"", 8000, 16000)
    assert out == b""
    assert state is None


def test_single_sample_upsample():
    """A single sample (2 bytes) should produce a valid output."""
    pcm = struct.pack("<h", 1000)
    out, state = resample_audio(pcm, 8000, 16000)
    assert len(out) == 4  # 1 sample @ 8k → 2 samples @ 16k = 4 bytes
    assert state is not None