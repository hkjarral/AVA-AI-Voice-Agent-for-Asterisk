"""Canonical audio baselines used by config recovery actions.

These values describe the effective, shipped audio contracts.  The Admin UI
reset endpoints consume this registry directly; frontend fallback values must
not become a second source of truth.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from src.config.provider_instances import FULL_AGENT_KINDS


# Fields shared by full-agent providers.  A reset removes every managed field
# before applying the provider-kind baseline, so stale troubleshooting fields
# cannot continue to override profile inheritance.
FULL_AGENT_AUDIO_FIELDS = frozenset(
    {
        "input_encoding",
        "input_sample_rate_hz",
        "provider_input_encoding",
        "provider_input_sample_rate_hz",
        "output_encoding",
        "output_sample_rate_hz",
        "target_encoding",
        "target_sample_rate_hz",
        "output_resampler",
    }
)


PROVIDER_AUDIO_BASELINES: Mapping[str, Mapping[str, Any]] = {
    "deepgram": {
        "input_encoding": "mulaw",
        "input_sample_rate_hz": 8000,
        "output_encoding": "mulaw",
        "output_sample_rate_hz": 8000,
        "output_resampler": "inherit",
    },
    "google_live": {
        "input_encoding": "ulaw",
        "input_sample_rate_hz": 8000,
        "provider_input_encoding": "linear16",
        "provider_input_sample_rate_hz": 16000,
        "output_encoding": "linear16",
        "output_sample_rate_hz": 24000,
        "target_encoding": "ulaw",
        "target_sample_rate_hz": 8000,
        "output_resampler": "inherit",
    },
    "local": {
        "target_encoding": "mulaw",
        "target_sample_rate_hz": 8000,
    },
    "openai_realtime": {
        "input_encoding": "ulaw",
        "input_sample_rate_hz": 8000,
        "provider_input_encoding": "linear16",
        "provider_input_sample_rate_hz": 24000,
        "output_encoding": "linear16",
        "output_sample_rate_hz": 24000,
        "target_encoding": "mulaw",
        "target_sample_rate_hz": 8000,
        "output_resampler": "inherit",
    },
    "elevenlabs_agent": {
        "input_encoding": "ulaw",
        "input_sample_rate_hz": 8000,
        "provider_input_encoding": "pcm16",
        "provider_input_sample_rate_hz": 16000,
        "output_encoding": "pcm16",
        "output_sample_rate_hz": 16000,
        "target_encoding": "ulaw",
        "target_sample_rate_hz": 8000,
        "output_resampler": "inherit",
    },
    "grok": {
        "input_encoding": "ulaw",
        "input_sample_rate_hz": 8000,
        "provider_input_encoding": "ulaw",
        "provider_input_sample_rate_hz": 8000,
        "output_encoding": "linear16",
        "output_sample_rate_hz": 24000,
        "target_encoding": "ulaw",
        "target_sample_rate_hz": 8000,
        "output_resampler": "inherit",
    },
    # Shipped modular providers with operator-facing audio controls.
    "groq_tts": {
        "response_format": "wav",
        "target_encoding": "mulaw",
        "target_sample_rate_hz": 8000,
        "output_resampler": "inherit",
    },
    "azure_tts": {
        "output_format": "riff-8khz-16bit-mono-pcm",
        "target_encoding": "mulaw",
        "target_sample_rate_hz": 8000,
        "output_resampler": "inherit",
    },
    "elevenlabs_tts": {
        "output_format": "ulaw_8000",
        "output_resampler": "inherit",
    },
    "openai_stt": {
        "input_encoding": "linear16",
        "input_sample_rate_hz": 16000,
    },
    "openai_tts": {
        "response_format": "wav",
        "target_encoding": "mulaw",
        "target_sample_rate_hz": 8000,
        "output_resampler": "inherit",
    },
}


# Additional kind-specific fields that are audio formats but are not safe to
# treat as universal (for example, STT response_format is JSON, not audio).
PROVIDER_AUDIO_EXTRA_FIELDS: Mapping[str, frozenset[str]] = {
    "groq_tts": frozenset({"response_format", "output_format"}),
    "azure_tts": frozenset({"output_format", "response_format"}),
    "elevenlabs_tts": frozenset({"output_format", "response_format"}),
    "openai_tts": frozenset({"response_format", "output_format"}),
}


BUILTIN_PROFILE_BASELINES: Mapping[str, Mapping[str, Any]] = {
    "openai_realtime_24k": {
        "output_resampler": "linear",
        "chunk_ms": 20,
        "idle_cutoff_ms": 0,
        "internal_rate_hz": 24000,
        "provider_pref": {
            "input_encoding": "pcm16",
            "input_sample_rate_hz": 24000,
            "output_encoding": "pcm16",
            "output_sample_rate_hz": 24000,
        },
        "transport_out": {"encoding": "slin", "sample_rate_hz": 8000},
    },
    "telephony_responsive": {
        "output_resampler": "linear",
        "chunk_ms": "auto",
        "idle_cutoff_ms": 600,
        "internal_rate_hz": 8000,
        "provider_pref": {
            "input_encoding": "mulaw",
            "input_sample_rate_hz": 8000,
            "output_encoding": "mulaw",
            "output_sample_rate_hz": 8000,
        },
        "transport_out": {"encoding": "slin", "sample_rate_hz": 8000},
    },
    "telephony_ulaw_8k": {
        "output_resampler": "linear",
        "chunk_ms": "auto",
        "idle_cutoff_ms": 800,
        "internal_rate_hz": 8000,
        "provider_pref": {
            "input_encoding": "mulaw",
            "input_sample_rate_hz": 8000,
            "output_encoding": "mulaw",
            "output_sample_rate_hz": 8000,
        },
        "transport_out": {"encoding": "ulaw", "sample_rate_hz": 8000},
    },
    "telephony_enhanced_8k": {
        "output_resampler": "bandlimited",
        "chunk_ms": "auto",
        "idle_cutoff_ms": 800,
        "internal_rate_hz": 8000,
        "provider_pref": {
            "input_encoding": "mulaw",
            "input_sample_rate_hz": 8000,
            "output_encoding": "mulaw",
            "output_sample_rate_hz": 8000,
        },
        "transport_out": {"encoding": "ulaw", "sample_rate_hz": 8000},
    },
    "wideband_pcm_16k": {
        "output_resampler": "linear",
        "talk_detect_talking_threshold": 1000,
        "chunk_ms": "auto",
        "idle_cutoff_ms": 1200,
        "internal_rate_hz": 16000,
        "provider_pref": {
            "input_encoding": "linear16",
            "input_sample_rate_hz": 16000,
            "output_encoding": "linear16",
            "output_sample_rate_hz": 16000,
        },
        "transport_out": {"encoding": "slin16", "sample_rate_hz": 16000},
    },
}


STANDARD_TELEPHONY_PROFILE = "telephony_ulaw_8k"


PIPELINE_AUDIO_FIELDS: Mapping[str, frozenset[str]] = {
    "stt": frozenset(
        {
            "encoding",
            "sample_rate",
            "sample_rate_hz",
            "input_encoding",
            "input_sample_rate_hz",
            "format",
            "source_format",
        }
    ),
    "tts": frozenset(
        {
            "encoding",
            "sample_rate",
            "sample_rate_hz",
            "output_encoding",
            "output_sample_rate_hz",
            "target_encoding",
            "target_sample_rate_hz",
            "output_resampler",
            "format",
            "source_format",
        }
    ),
}


def provider_audio_baseline(kind: str) -> dict[str, Any] | None:
    """Return an isolated copy of the canonical baseline for ``kind``."""
    baseline = PROVIDER_AUDIO_BASELINES.get(str(kind))
    return deepcopy(dict(baseline)) if baseline is not None else None


def provider_audio_fields(kind: str) -> frozenset[str]:
    """Return every provider field managed by an audio baseline reset."""
    kind = str(kind)
    baseline = PROVIDER_AUDIO_BASELINES.get(kind)
    if baseline is None:
        return frozenset()
    if kind in FULL_AGENT_KINDS:
        return FULL_AGENT_AUDIO_FIELDS
    return frozenset(baseline) | PROVIDER_AUDIO_EXTRA_FIELDS.get(kind, frozenset())


def profile_audio_baseline(profile_name: str) -> dict[str, Any]:
    """Return a built-in profile baseline or the standard custom fallback."""
    baseline = BUILTIN_PROFILE_BASELINES.get(profile_name)
    if baseline is None:
        baseline = BUILTIN_PROFILE_BASELINES[STANDARD_TELEPHONY_PROFILE]
    return deepcopy(dict(baseline))
