import audioop
import struct

import numpy as np
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


def test_bandlimited_downsample_24k_to_8k_exact_size():
    """The opt-in FIR path preserves the 20 ms telephony frame duration."""
    pcm_24k = b"\x00\x01" * 480
    out, state = resample_audio(pcm_24k, 24000, 8000, mode="bandlimited")
    assert len(out) == 320
    assert state is not None


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


def test_stateful_and_stateless_produce_valid_equal_length_output():
    """Stateful and stateless resampling both produce valid output of equal length."""
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


def test_bandlimited_streaming_matches_one_shot_for_irregular_chunks():
    """FIR history and decimation phase must make chunking sample-exact."""
    rng = np.random.default_rng(20260721)
    samples = rng.integers(-20000, 20001, size=2407, dtype=np.int16)
    pcm = samples.astype("<i2", copy=False).tobytes()

    one_shot, _ = resample_audio(pcm, 24000, 8000, mode="bandlimited")

    state = None
    chunks = []
    offset = 0
    for sample_count in (17, 301, 2, 479, 91, 603, 914):
        chunk = pcm[offset * 2 : (offset + sample_count) * 2]
        converted, state = resample_audio(
            chunk,
            24000,
            8000,
            state=state,
            mode="bandlimited",
        )
        chunks.append(converted)
        offset += sample_count

    assert offset == len(samples)
    assert b"".join(chunks) == one_shot


def test_bandlimited_downsample_suppresses_above_nyquist_alias():
    """A 5 kHz provider tone must not fold into the 8 kHz phone band at 3 kHz."""
    source_rate = 24000
    target_rate = 8000
    duration_samples = source_rate // 2
    positions = np.arange(duration_samples, dtype=np.float64)
    tone = np.rint(
        20000.0 * np.sin(2.0 * np.pi * 5000.0 * positions / source_rate)
    ).astype("<i2")

    legacy, _ = resample_audio(tone.tobytes(), source_rate, target_rate)
    filtered, _ = resample_audio(
        tone.tobytes(), source_rate, target_rate, mode="bandlimited"
    )
    # Ignore causal FIR startup while its zero-filled history is flushed.
    legacy_samples = np.frombuffer(legacy, dtype="<i2").astype(np.float64)[256:]
    filtered_samples = np.frombuffer(filtered, dtype="<i2").astype(np.float64)[256:]
    legacy_rms = np.sqrt(np.mean(np.square(legacy_samples)))
    filtered_rms = np.sqrt(np.mean(np.square(filtered_samples)))

    attenuation_db = 20.0 * np.log10(max(filtered_rms, 1e-12) / legacy_rms)
    assert attenuation_db < -60.0


def test_bandlimited_downsample_preserves_telephone_passband_tone():
    """A 3 kHz tone remains within 1 dB after alias-safe conversion."""
    source_rate = 24000
    target_rate = 8000
    duration_samples = source_rate // 2
    positions = np.arange(duration_samples, dtype=np.float64)
    tone = np.rint(
        20000.0 * np.sin(2.0 * np.pi * 3000.0 * positions / source_rate)
    ).astype("<i2")

    filtered, _ = resample_audio(
        tone.tobytes(), source_rate, target_rate, mode="bandlimited"
    )
    filtered_samples = np.frombuffer(filtered, dtype="<i2").astype(np.float64)[256:]
    filtered_rms = np.sqrt(np.mean(np.square(filtered_samples)))
    expected_rms = 20000.0 / np.sqrt(2.0)
    gain_db = 20.0 * np.log10(filtered_rms / expected_rms)

    assert abs(gain_db) < 1.0


def test_bandlimited_mode_falls_back_to_legacy_for_upsampling():
    """The candidate must not change inbound/upstream sample-rate conversion."""
    pcm = np.arange(-160, 160, dtype="<i2").tobytes()
    legacy, legacy_state = resample_audio(pcm, 8000, 24000)
    candidate, candidate_state = resample_audio(
        pcm, 8000, 24000, mode="bandlimited"
    )
    assert candidate == legacy
    assert candidate_state == legacy_state


# ── Edge cases ────────────────────────────────────────────────────────


def test_empty_input_returns_empty():
    out, state = resample_audio(b"", 8000, 16000)
    assert out == b""
    assert state is None


def test_resample_rejects_unknown_mode():
    with pytest.raises(ValueError, match="Unsupported resample mode"):
        resample_audio(b"\x00\x00" * 20, 24000, 8000, mode="unknown")


def test_single_sample_upsample():
    """A single sample (2 bytes) should produce a valid output."""
    pcm = struct.pack("<h", 1000)
    out, state = resample_audio(pcm, 8000, 16000)
    assert len(out) == 4  # 1 sample @ 8k → 2 samples @ 16k = 4 bytes
    assert state is not None


# ── Mono-only / PCM16-only guards (LOW-R3) ────────────────────────────


def test_resample_rejects_non_mono():
    """channels != 1 must raise rather than silently corrupt interleaved audio."""
    pcm = b"\x00\x01" * 160
    with pytest.raises(ValueError, match="mono-only"):
        resample_audio(pcm, 8000, 16000, channels=2)


def test_resample_rejects_non_pcm16_width():
    """sample_width other than 2 must raise."""
    pcm = b"\x00\x01" * 160
    with pytest.raises(ValueError, match="PCM16-only"):
        resample_audio(pcm, 8000, 16000, sample_width=1)


def test_resample_mono_explicit_args_still_works():
    """Explicit mono/PCM16 args resample exactly as the defaults do."""
    pcm_8k = b"\x00\x01" * 160
    out, _ = resample_audio(pcm_8k, 8000, 16000, sample_width=2, channels=1)
    assert len(out) == 640
