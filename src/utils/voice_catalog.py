"""Single-source voice catalog shared by the engine and the Admin UI backend.

Pure data + tiny helpers — no heavy imports, safe to import from
admin_ui/backend (via the /app/project mount) and from provider adapters.

Lists are transcribed from the catalogs the repo ships in the Admin UI
provider forms (OpenAIRealtimeProviderForm, GrokProviderForm,
GoogleLiveProviderForm, DeepgramProviderForm). When a provider adds voices,
update BOTH until the provider forms consume /api/config/providers/meta.

Verified complete against official provider docs on 2026-07-02:
developers.openai.com realtime guide (10/10), docs.x.ai voice-agent (5 named
+ custom clone IDs), ai.google.dev speech-generation (30/30),
developers.deepgram.com tts-models (103/103 ID-level match).

voice_mode semantics for the Agent form:
  static           closed list; engine soft-validates and falls back on unknowns
  freeform         curated suggestions, arbitrary values allowed (clone IDs, new names)
  platform_managed voice lives outside AVA (e.g. ElevenLabs agent config)
  unsupported      per-agent voice does not apply to this provider kind
"""
from typing import Any, Dict, List, Optional

OPENAI_VOICES: List[Dict[str, str]] = [
    {"id": 'alloy', "label": 'Alloy — neutral, balanced'},
    {"id": 'ash', "label": 'Ash — clear, direct'},
    {"id": 'ballad', "label": 'Ballad — warm, storytelling'},
    {"id": 'cedar', "label": 'Cedar — warm, natural (Realtime-exclusive, added 2026-05-14)'},
    {"id": 'coral', "label": 'Coral — friendly, conversational'},
    {"id": 'echo', "label": 'Echo — soft, calm'},
    {"id": 'marin', "label": 'Marin — clear, professional (Realtime-exclusive, added 2026-05-14)'},
    {"id": 'sage', "label": 'Sage — wise, authoritative'},
    {"id": 'shimmer', "label": 'Shimmer — bright, optimistic'},
    {"id": 'verse', "label": 'Verse — expressive, dynamic'},
]

# The engine's soft-validation set (OpenAI rejects unknown voices at session
# setup, so unknown agent values fall back to the provider default).
OPENAI_GA_VOICES = {v["id"] for v in OPENAI_VOICES}

GROK_NAMED_VOICES: List[Dict[str, str]] = [
    {"id": 'eve', "label": 'eve — energetic, upbeat'},
    {"id": 'ara', "label": 'ara — warm, friendly'},
    {"id": 'rex', "label": 'rex — confident, clear'},
    {"id": 'sal', "label": 'sal — smooth, balanced'},
    {"id": 'leo', "label": 'leo — authoritative, strong'},
]

GOOGLE_LIVE_VOICES: List[Dict[str, str]] = [
    {"id": 'Achernar', "label": 'Achernar — Soft'},
    {"id": 'Achird', "label": 'Achird — Friendly'},
    {"id": 'Algenib', "label": 'Algenib — Gravelly'},
    {"id": 'Algieba', "label": 'Algieba — Smooth'},
    {"id": 'Alnilam', "label": 'Alnilam — Firm'},
    {"id": 'Aoede', "label": 'Aoede — Breezy'},
    {"id": 'Autonoe', "label": 'Autonoe — Bright'},
    {"id": 'Callirrhoe', "label": 'Callirrhoe — Easy-going'},
    {"id": 'Charon', "label": 'Charon — Informative'},
    {"id": 'Despina', "label": 'Despina — Smooth'},
    {"id": 'Enceladus', "label": 'Enceladus — Breathy'},
    {"id": 'Erinome', "label": 'Erinome — Clear'},
    {"id": 'Fenrir', "label": 'Fenrir — Excitable'},
    {"id": 'Gacrux', "label": 'Gacrux — Mature'},
    {"id": 'Iapetus', "label": 'Iapetus — Clear'},
    {"id": 'Kore', "label": 'Kore — Firm'},
    {"id": 'Laomedeia', "label": 'Laomedeia — Upbeat'},
    {"id": 'Leda', "label": 'Leda — Youthful'},
    {"id": 'Orus', "label": 'Orus — Firm'},
    {"id": 'Puck', "label": 'Puck — Upbeat'},
    {"id": 'Pulcherrima', "label": 'Pulcherrima — Forward'},
    {"id": 'Rasalgethi', "label": 'Rasalgethi — Informative'},
    {"id": 'Sadachbia', "label": 'Sadachbia — Lively'},
    {"id": 'Sadaltager', "label": 'Sadaltager — Knowledgeable'},
    {"id": 'Schedar', "label": 'Schedar — Even'},
    {"id": 'Sulafat', "label": 'Sulafat — Warm'},
    {"id": 'Umbriel', "label": 'Umbriel — Easy-going'},
    {"id": 'Vindemiatrix', "label": 'Vindemiatrix — Gentle'},
    {"id": 'Zephyr', "label": 'Zephyr — Bright'},
    {"id": 'Zubenelgenubi', "label": 'Zubenelgenubi — Casual'},
]

DEEPGRAM_AURA_VOICES: List[Dict[str, str]] = [
    {"id": 'aura-2-thalia-en', "label": 'Thalia (EN)'},
    {"id": 'aura-2-asteria-en', "label": 'Asteria (EN)'},
    {"id": 'aura-2-luna-en', "label": 'Luna (EN)'},
    {"id": 'aura-2-athena-en', "label": 'Athena (EN)'},
    {"id": 'aura-2-hera-en', "label": 'Hera (EN)'},
    {"id": 'aura-2-andromeda-en', "label": 'Andromeda (EN)'},
    {"id": 'aura-2-aurora-en', "label": 'Aurora (EN)'},
    {"id": 'aura-2-callista-en', "label": 'Callista (EN)'},
    {"id": 'aura-2-cora-en', "label": 'Cora (EN)'},
    {"id": 'aura-2-cordelia-en', "label": 'Cordelia (EN)'},
    {"id": 'aura-2-delia-en', "label": 'Delia (EN)'},
    {"id": 'aura-2-electra-en', "label": 'Electra (EN)'},
    {"id": 'aura-2-harmonia-en', "label": 'Harmonia (EN)'},
    {"id": 'aura-2-helena-en', "label": 'Helena (EN)'},
    {"id": 'aura-2-iris-en', "label": 'Iris (EN)'},
    {"id": 'aura-2-juno-en', "label": 'Juno (EN)'},
    {"id": 'aura-2-minerva-en', "label": 'Minerva (EN)'},
    {"id": 'aura-2-ophelia-en', "label": 'Ophelia (EN)'},
    {"id": 'aura-2-pandora-en', "label": 'Pandora (EN)'},
    {"id": 'aura-2-phoebe-en', "label": 'Phoebe (EN)'},
    {"id": 'aura-2-selene-en', "label": 'Selene (EN)'},
    {"id": 'aura-2-theia-en', "label": 'Theia (EN)'},
    {"id": 'aura-2-vesta-en', "label": 'Vesta (EN)'},
    {"id": 'aura-2-amalthea-en', "label": 'Amalthea (EN)'},
    {"id": 'aura-2-orion-en', "label": 'Orion (EN)'},
    {"id": 'aura-2-arcas-en', "label": 'Arcas (EN)'},
    {"id": 'aura-2-orpheus-en', "label": 'Orpheus (EN)'},
    {"id": 'aura-2-zeus-en', "label": 'Zeus (EN)'},
    {"id": 'aura-2-apollo-en', "label": 'Apollo (EN)'},
    {"id": 'aura-2-aries-en', "label": 'Aries (EN)'},
    {"id": 'aura-2-atlas-en', "label": 'Atlas (EN)'},
    {"id": 'aura-2-draco-en', "label": 'Draco (EN)'},
    {"id": 'aura-2-hermes-en', "label": 'Hermes (EN)'},
    {"id": 'aura-2-hyperion-en', "label": 'Hyperion (EN)'},
    {"id": 'aura-2-janus-en', "label": 'Janus (EN)'},
    {"id": 'aura-2-jupiter-en', "label": 'Jupiter (EN)'},
    {"id": 'aura-2-mars-en', "label": 'Mars (EN)'},
    {"id": 'aura-2-neptune-en', "label": 'Neptune (EN)'},
    {"id": 'aura-2-odysseus-en', "label": 'Odysseus (EN)'},
    {"id": 'aura-2-pluto-en', "label": 'Pluto (EN)'},
    {"id": 'aura-2-saturn-en', "label": 'Saturn (EN)'},
    {"id": 'aura-2-celeste-es', "label": 'Celeste (ES) ⭐'},
    {"id": 'aura-2-estrella-es', "label": 'Estrella (ES) ⭐'},
    {"id": 'aura-2-nestor-es', "label": 'Nestor (ES) ⭐'},
    {"id": 'aura-2-diana-es', "label": 'Diana (ES) 🔄'},
    {"id": 'aura-2-javier-es', "label": 'Javier (ES) 🔄'},
    {"id": 'aura-2-selena-es', "label": 'Selena (ES) 🔄'},
    {"id": 'aura-2-aquila-es', "label": 'Aquila (ES) 🔄'},
    {"id": 'aura-2-carina-es', "label": 'Carina (ES) 🔄'},
    {"id": 'aura-2-agustina-es', "label": 'Agustina (ES)'},
    {"id": 'aura-2-antonia-es', "label": 'Antonia (ES)'},
    {"id": 'aura-2-gloria-es', "label": 'Gloria (ES)'},
    {"id": 'aura-2-olivia-es', "label": 'Olivia (ES)'},
    {"id": 'aura-2-silvia-es', "label": 'Silvia (ES)'},
    {"id": 'aura-2-sirio-es', "label": 'Sirio (ES)'},
    {"id": 'aura-2-alvaro-es', "label": 'Alvaro (ES)'},
    {"id": 'aura-2-luciano-es', "label": 'Luciano (ES)'},
    {"id": 'aura-2-valerio-es', "label": 'Valerio (ES)'},
    {"id": 'aura-2-julius-de', "label": 'Julius (DE) ⭐'},
    {"id": 'aura-2-viktoria-de', "label": 'Viktoria (DE) ⭐'},
    {"id": 'aura-2-elara-de', "label": 'Elara (DE)'},
    {"id": 'aura-2-aurelia-de', "label": 'Aurelia (DE)'},
    {"id": 'aura-2-lara-de', "label": 'Lara (DE)'},
    {"id": 'aura-2-fabian-de', "label": 'Fabian (DE)'},
    {"id": 'aura-2-kara-de', "label": 'Kara (DE)'},
    {"id": 'aura-2-agathe-fr', "label": 'Agathe (FR) ⭐'},
    {"id": 'aura-2-hector-fr', "label": 'Hector (FR) ⭐'},
    {"id": 'aura-2-livia-it', "label": 'Livia (IT) ⭐'},
    {"id": 'aura-2-dionisio-it', "label": 'Dionisio (IT) ⭐'},
    {"id": 'aura-2-melia-it', "label": 'Melia (IT)'},
    {"id": 'aura-2-elio-it', "label": 'Elio (IT)'},
    {"id": 'aura-2-flavio-it', "label": 'Flavio (IT)'},
    {"id": 'aura-2-maia-it', "label": 'Maia (IT)'},
    {"id": 'aura-2-cinzia-it', "label": 'Cinzia (IT)'},
    {"id": 'aura-2-cesare-it', "label": 'Cesare (IT)'},
    {"id": 'aura-2-perseo-it', "label": 'Perseo (IT)'},
    {"id": 'aura-2-demetra-it', "label": 'Demetra (IT)'},
    {"id": 'aura-2-rhea-nl', "label": 'Rhea (NL) ⭐'},
    {"id": 'aura-2-sander-nl', "label": 'Sander (NL) ⭐'},
    {"id": 'aura-2-beatrix-nl', "label": 'Beatrix (NL) ⭐'},
    {"id": 'aura-2-daphne-nl', "label": 'Daphne (NL)'},
    {"id": 'aura-2-cornelia-nl', "label": 'Cornelia (NL)'},
    {"id": 'aura-2-hestia-nl', "label": 'Hestia (NL)'},
    {"id": 'aura-2-lars-nl', "label": 'Lars (NL)'},
    {"id": 'aura-2-roman-nl', "label": 'Roman (NL)'},
    {"id": 'aura-2-leda-nl', "label": 'Leda (NL)'},
    {"id": 'aura-2-fujin-ja', "label": 'Fujin (JA) ⭐'},
    {"id": 'aura-2-izanami-ja', "label": 'Izanami (JA) ⭐'},
    {"id": 'aura-2-uzume-ja', "label": 'Uzume (JA)'},
    {"id": 'aura-2-ebisu-ja', "label": 'Ebisu (JA)'},
    {"id": 'aura-2-ama-ja', "label": 'Ama (JA)'},
    {"id": 'aura-asteria-en', "label": 'Asteria (EN Legacy)'},
    {"id": 'aura-luna-en', "label": 'Luna (EN Legacy)'},
    {"id": 'aura-stella-en', "label": 'Stella (EN Legacy)'},
    {"id": 'aura-athena-en', "label": 'Athena (EN Legacy)'},
    {"id": 'aura-hera-en', "label": 'Hera (EN Legacy)'},
    {"id": 'aura-orion-en', "label": 'Orion (EN Legacy)'},
    {"id": 'aura-arcas-en', "label": 'Arcas (EN Legacy)'},
    {"id": 'aura-perseus-en', "label": 'Perseus (EN Legacy)'},
    {"id": 'aura-angus-en', "label": 'Angus (EN Legacy)'},
    {"id": 'aura-orpheus-en', "label": 'Orpheus (EN Legacy)'},
    {"id": 'aura-helios-en', "label": 'Helios (EN Legacy)'},
    {"id": 'aura-zeus-en', "label": 'Zeus (EN Legacy)'},
]

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
    # so the UI must not invite free-text values the runtime would drop.
    # Grok stays freeform — custom cloned-voice IDs are pass-through.
    "google_live": {"voice_mode": "static", "voices": GOOGLE_LIVE_VOICES, "voice_field": "tts_voice_name"},
    "deepgram": {"voice_mode": "static", "voices": DEEPGRAM_AURA_VOICES, "voice_field": "tts_model"},
    "elevenlabs_agent": {"voice_mode": "platform_managed", "voices": [], "voice_field": None},
}

_UNSUPPORTED = {"voice_mode": "unsupported", "voices": [], "voice_field": None}


def provider_voice_meta(kind: Optional[str]) -> Dict[str, Any]:
    """Voice metadata for a full-agent provider kind (see module docstring)."""
    return dict(_VOICE_META.get(kind or "", _UNSUPPORTED))


def known_voice_map(kind: Optional[str]) -> Optional[Dict[str, str]]:
    """lowercase id → canonical id for kinds with a closed, verified catalog.

    Returns None for open kinds (Grok accepts arbitrary cloned-voice IDs) and
    for kinds without per-agent voice. Used to soft-validate agent voice
    overrides — an unknown value falls back to the provider default instead of
    reaching the provider API, where OpenAI/Google/Deepgram all reject unknown
    voice/model names at session setup.
    """
    if kind == "openai_realtime":
        return {v: v for v in OPENAI_GA_VOICES}
    if kind == "google_live":
        return {v["id"].lower(): v["id"] for v in GOOGLE_LIVE_VOICES}
    if kind == "deepgram":
        return {v["id"].lower(): v["id"] for v in DEEPGRAM_AURA_VOICES}
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
