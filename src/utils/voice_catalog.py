"""Single-source voice catalog shared by the engine and the Admin UI backend.

Pure data + tiny helpers — no heavy imports, safe to import from
admin_ui/backend (via the /app/project mount) and from provider adapters.

The Grok provider and Agent forms consume this catalog via the Admin API.
Grok IDs verified 2026-11-21 against the xAI voice table shared by its realtime
and TTS APIs: https://docs.x.ai/developers/model-capabilities/audio/text-to-speech#voices
All model/voice catalogs originate in provider_catalog.json; the frontend consumes
a deterministic generated copy checked by CI.

voice_mode semantics for the Agent form:
  static           closed list; each provider enforces its documented failure policy
  freeform         curated suggestions, arbitrary values allowed (clone IDs, new names)
  platform_managed voice lives outside AVA (e.g. ElevenLabs agent config)
  unsupported      per-agent voice does not apply to this provider kind
"""
from typing import Any, Dict, List, Optional
from .provider_catalog import provider_voices

OPENAI_VOICES: List[Dict[str, str]] = provider_voices('openai_realtime', 'realtime')

# The engine's soft-validation set (OpenAI rejects unknown voices at session
# setup, so unknown agent values fall back to the provider default).
OPENAI_GA_VOICES = {v["id"] for v in OPENAI_VOICES}

GROK_NAMED_VOICES: List[Dict[str, str]] = provider_voices('grok', 'realtime')

GOOGLE_LIVE_VOICES: List[Dict[str, str]] = provider_voices('google_live', 'live')

DEEPGRAM_AURA_VOICES: List[Dict[str, str]] = provider_voices('deepgram', 'tts')

# Only recognized foreign IDs are incompatible. Other IDs may be private xAI
# clones or newly released voices. A shared built-in name belongs to Grok too.
DEEPGRAM_FLUX_VOICES: List[Dict[str, str]] = provider_voices("deepgram", "flux_tts")
DEEPGRAM_TTS_VOICES = DEEPGRAM_AURA_VOICES + DEEPGRAM_FLUX_VOICES

GROK_BUILTIN_IDS = frozenset(v["id"] for v in GROK_NAMED_VOICES)
GROK_FOREIGN_VOICE_IDS = frozenset(
    v["id"].lower()
    for voices in (GOOGLE_LIVE_VOICES, OPENAI_VOICES, DEEPGRAM_TTS_VOICES)
    for v in voices
).difference(GROK_BUILTIN_IDS)

# Mirrors admin_ui/frontend/src/utils/providerNaming.ts
_CANONICAL_FULL_AGENT_KEYS = {
    "local", "openai_realtime", "deepgram", "google_live", "elevenlabs_agent", "grok",
}
_FULL_AGENT_TYPES = {
    "openai_realtime", "deepgram", "google_live", "elevenlabs_agent", "grok", "local",
}
_MODULAR_KEY_SUFFIXES = ("_stt", "_llm", "_tts")

_VOICE_META = {
    "openai_realtime": {"voice_mode": "static", "voices": OPENAI_VOICES, "voice_field": "voice"},
    "grok": {"voice_mode": "freeform", "voices": GROK_NAMED_VOICES, "voice_field": "voice"},
    # google_live/deepgram are STATIC, matching the runtime: the engine
    # validates overrides against these verified catalogs (known_voice_map),
    # so the UI must not invite free-text values the runtime would reject.
    # Grok stays freeform — custom cloned-voice IDs are pass-through.
    "google_live": {"voice_mode": "static", "voices": GOOGLE_LIVE_VOICES, "voice_field": "tts_voice_name"},
    "deepgram": {"voice_mode": "static", "voices": DEEPGRAM_TTS_VOICES, "voice_field": "tts_model"},
    "elevenlabs_agent": {"voice_mode": "platform_managed", "voices": [], "voice_field": None},
}

_UNSUPPORTED = {"voice_mode": "unsupported", "voices": [], "voice_field": None}


def provider_voice_meta(kind: Optional[str]) -> Dict[str, Any]:
    """Voice metadata for a full-agent provider kind (see module docstring)."""
    return dict(_VOICE_META.get(kind or "", _UNSUPPORTED))


def known_voice_map(kind: Optional[str]) -> Optional[Dict[str, str]]:
    """lowercase id → canonical id for kinds with a closed, verified catalog.

    Returns None for open kinds (Grok accepts arbitrary cloned-voice IDs) and
    for kinds without per-agent voice. Callers choose whether an unknown value
    falls back or fails closed. Deepgram fails closed because silently changing
    a customer's configured voice would violate its language contract.
    """
    if kind == "openai_realtime":
        return {v: v for v in OPENAI_GA_VOICES}
    if kind == "google_live":
        return {v["id"].lower(): v["id"] for v in GOOGLE_LIVE_VOICES}
    if kind == "deepgram":
        return {v["id"].lower(): v["id"] for v in DEEPGRAM_TTS_VOICES}
    return None


def full_agent_kind(key: str, entry: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return the full-agent kind for a YAML provider entry, or None.

    Mirrors the frontend's isFullAgentProvider (providerNaming.ts): explicit
    full-agent `type` wins (multi-instance form); `type: full` resolves via a
    canonical key; a canonical key with no `type` is the legacy
    single-instance form; `_stt/_llm/_tts` key suffixes are always modular.
    """
    entry = entry or {}
    key = key or ""
    if any(key.endswith(s) for s in _MODULAR_KEY_SUFFIXES):
        return None
    kind = str(entry.get("type") or "").lower()
    if kind in _FULL_AGENT_TYPES:
        return kind
    if kind == "full":
        return key if key in _CANONICAL_FULL_AGENT_KEYS else None
    caps = entry.get("capabilities") or []
    if all(c in caps for c in ("stt", "llm", "tts")):
        return key if key in _CANONICAL_FULL_AGENT_KEYS else None
    if not kind and key in _CANONICAL_FULL_AGENT_KEYS:
        return key
    return None
