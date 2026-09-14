"""Secret-free, immutable diagnostics captured for one call.

The support bundle must describe the configuration that actually handled the
call, not whatever happens to be configured when an operator downloads it.
Only explicitly allow-listed operational fields are copied here.
"""

from __future__ import annotations

import os
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Iterable


def _get(source: Any, key: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _pick(source: Any, keys: Iterable[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in keys:
        value = _get(source, key)
        if value is not None and value != "":
            out[key] = deepcopy(value)
    return out


PROVIDER_FIELDS = (
    "type",
    "model",
    "llm_model",
    "stt_model",
    "tts_model",
    "stt_backend",
    "tts_backend",
    "stt_language",
    "agent_language",
    "input_encoding",
    "input_sample_rate_hz",
    "provider_input_encoding",
    "provider_input_sample_rate_hz",
    "output_encoding",
    "output_sample_rate_hz",
    "target_encoding",
    "target_sample_rate_hz",
    "output_resampler",
    "continuous_input",
    "allow_output_autodetect",
    "turn_detection",
    "response_modalities",
    "chunk_ms",
    "temperature",
    "max_tokens",
    "mode",
    "voice",
    "language",
    "agent_id",
)

STREAMING_FIELDS = (
    "sample_rate",
    "jitter_buffer_ms",
    "keepalive_interval_ms",
    "connection_timeout_ms",
    "fallback_timeout_ms",
    "chunk_size_ms",
    "min_start_ms",
    "low_watermark_ms",
    "provider_grace_ms",
    "greeting_min_start_ms",
    "greeting_rtp_wait_ms",
    "egress_swap_mode",
    "egress_force_mulaw",
    "pipeline_streaming_overlap",
    "pipeline_filler_enabled",
    "logging_level",
    "diag_enable_taps",
    "diag_pre_secs",
    "diag_post_secs",
)

VAD_FIELDS = (
    "vad_mode",
    "enhanced_enabled",
    "webrtc_aggressiveness",
    "webrtc_start_frames",
    "webrtc_end_silence_frames",
    "energy_threshold",
    "confidence_threshold",
    "adaptive_threshold_enabled",
    "min_utterance_duration_ms",
    "max_utterance_duration_ms",
    "utterance_padding_ms",
    "fallback_enabled",
    "fallback_interval_ms",
    "upstream_squelch_enabled",
    "upstream_squelch_base_rms",
    "upstream_squelch_noise_factor",
    "upstream_squelch_min_speech_frames",
    "upstream_squelch_end_silence_frames",
    "noise_adaptation_rate",
    "upstream_squelch_noise_ema_alpha",
    "fallback_buffer_size",
)

BARGE_FIELDS = (
    "enabled",
    "initial_protection_ms",
    "min_ms",
    "energy_threshold",
    "cooldown_ms",
    "pipeline_min_ms",
    "pipeline_energy_threshold",
    "pipeline_talk_detect_enabled",
    "pipeline_talk_detect_silence_ms",
    "pipeline_talk_detect_talking_threshold",
    "talk_detect_initial_protection_ms",
    "post_tts_end_protection_ms",
    "greeting_protection_ms",
    "provider_fallback_enabled",
    "provider_output_suppress_ms",
    "provider_output_suppress_extend_ms",
    "provider_output_suppress_chunk_extend_ms",
    "provider_fallback_providers",
    "provider_fallback_min_ms_by_provider",
)

TRANSPORT_FIELDS = (
    "profile_name",
    "wire_encoding",
    "wire_sample_rate",
    "provider_input_encoding",
    "provider_input_sample_rate",
    "provider_output_encoding",
    "provider_output_sample_rate",
    "internal_rate",
    "chunk_ms",
    "idle_cutoff_ms",
    "output_resampler",
    "output_resampler_source",
    "talk_detect_talking_threshold",
)

AUDIO_SOCKET_FIELDS = ("host", "advertise_host", "port", "format")
EXTERNAL_MEDIA_FIELDS = (
    "rtp_host", "advertise_host", "rtp_port", "port_range", "codec",
    "direction", "format", "sample_rate", "allowed_remote_hosts",
    "lock_remote_endpoint",
)
WEBSOCKET_MEDIA_FIELDS = (
    "connection_mode", "connection_name", "bind_host", "advertise_host",
    "port", "path", "format_policy", "fallback_format", "control_format",
    "direction", "handshake_timeout_ms", "media_start_timeout_ms",
    "drain_timeout_ms", "pre_start_buffer_ms", "max_connections",
    "max_input_queue_frames", "max_message_bytes", "allowed_remote_hosts",
)


def _selected_transport_config(config: Any, transport_kind: str) -> Dict[str, Any]:
    if transport_kind == "audiosocket":
        return _pick(_get(config, "audiosocket"), AUDIO_SOCKET_FIELDS)
    if transport_kind == "externalmedia":
        return _pick(_get(config, "external_media"), EXTERNAL_MEDIA_FIELDS)
    if transport_kind == "websocket":
        websocket = _get(config, "websocket_media")
        selected = _pick(websocket, WEBSOCKET_MEDIA_FIELDS)
        selected["auth_required"] = bool(_get(_get(websocket, "auth"), "required", True))
        selected["tls_enabled"] = bool(_get(_get(websocket, "tls"), "enabled", False))
        return selected
    return {}


def build_call_diagnostics_snapshot(
    config: Any,
    session: Any,
    *,
    replace_configured: bool = False,
) -> Dict[str, Any]:
    """Create or finalize a call-owned settings and runtime snapshot.

    Configured/resolved sections are frozen on the first call. Repeated calls
    update only call-owned resolved values that may become known later (voice)
    and runtime observations accumulated during the call. During call setup,
    ``replace_configured`` replaces the early failure-path snapshot once the
    effective provider, pipeline, and audio profile have been resolved.
    """

    existing = deepcopy(_get(session, "diagnostics_snapshot", {}) or {})
    if replace_configured:
        existing = {}
    provider_name = str(_get(session, "provider_name", "") or "")
    providers = _get(config, "providers", {}) or {}
    provider_cfg = providers.get(provider_name, {}) if isinstance(providers, dict) else {}
    transport = _get(session, "transport_profile")
    transport_kind = str(_get(config, "audio_transport", "") or "")

    if not existing:
        existing = {
            "schema_version": 1,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "configured": {
                "audio_transport": transport_kind,
                "downstream_mode": _get(config, "downstream_mode"),
                "selected_transport": _selected_transport_config(config, transport_kind),
                "provider": _pick(provider_cfg, PROVIDER_FIELDS),
                "streaming": _pick(_get(config, "streaming"), STREAMING_FIELDS),
                "vad": _pick(_get(config, "vad"), VAD_FIELDS),
                "barge_in": _pick(_get(config, "barge_in"), BARGE_FIELDS),
                "logging": {
                    "level": os.getenv("LOG_LEVEL", str(_get(_get(config, "logging"), "level", "info"))),
                    "format": os.getenv("LOG_FORMAT", "json"),
                    "streaming_level": os.getenv("STREAMING_LOG_LEVEL", "info"),
                },
            },
            "resolved": {
                "agent": _get(session, "context_name"),
                "routing_method": _get(session, "routing_method"),
                "provider_name": provider_name,
                "provider_kind": _get(session, "provider_kind"),
                "pipeline_name": _get(session, "pipeline_name"),
                "pipeline_components": deepcopy(dict(_get(session, "pipeline_components", {}) or {})),
                "audio_profile": _get(transport, "profile_name"),
                "transport_profile": _pick(transport, TRANSPORT_FIELDS),
                "tool_generation_id": _get(session, "tool_generation_id"),
                "tool_config_hash": _get(session, "tool_config_hash"),
                "no_input_policy": _pick(
                    _get(session, "no_input_policy", {}),
                    ("enabled", "initial_timeout_sec", "grace_timeout_sec", "max_check_ins"),
                ),
            },
        }

    resolved = existing.setdefault("resolved", {})
    resolved["voice"] = _get(session, "session_voice")
    resolved["voice_source"] = _get(session, "voice_source")
    resolved["provider_name"] = provider_name
    resolved["provider_kind"] = _get(session, "provider_kind")
    resolved["pipeline_name"] = _get(session, "pipeline_name")
    resolved["pipeline_components"] = deepcopy(dict(_get(session, "pipeline_components", {}) or {}))
    resolved["allowed_tools"] = sorted(str(name) for name in (_get(session, "allowed_tools", []) or []))

    existing["runtime"] = {
        "media_transport_kind": _get(session, "media_transport_kind"),
        "media_connection_state": _get(session, "media_connection_state"),
        "negotiated_encoding": _get(session, "negotiated_encoding"),
        "negotiated_sample_rate": _get(session, "negotiated_sample_rate"),
        "media_packetization_ms": _get(session, "media_packetization_ms"),
        "media_optimal_frame_size": _get(session, "media_optimal_frame_size"),
        "media_rx_confirmed": bool(_get(session, "media_rx_confirmed", False)),
        "provider_session_active": bool(_get(session, "provider_session_active", False)),
        "codec_alignment_ok": bool(_get(session, "codec_alignment_ok", False)),
        "codec_alignment_message": _get(session, "codec_alignment_message"),
        "caller_audio_format": _get(session, "caller_audio_format"),
        "caller_sample_rate": _get(session, "caller_sample_rate"),
        "streaming_started": bool(_get(session, "streaming_started", False)),
        "streaming_bytes_sent": int(_get(session, "streaming_bytes_sent", 0) or 0),
        "streaming_fallback_count": int(_get(session, "streaming_fallback_count", 0) or 0),
        "streaming_keepalive_timeouts": int(_get(session, "streaming_keepalive_timeouts", 0) or 0),
        "last_streaming_error": _get(session, "last_streaming_error"),
        "websocket_input_rejections": deepcopy(dict(_get(session, "websocket_input_rejections", {}) or {})),
    }
    return existing
