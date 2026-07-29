"""Voice catalog (v7.3.0): single source for per-provider voice metadata.

`src/utils/voice_catalog.py` is a pure-data module shared by the engine
(OpenAI soft-validation list) and the Admin UI backend (providers/meta
endpoint). Lists are sourced from the catalogs the repo already ships in the
provider form components; full-agent detection mirrors the frontend's
`isFullAgentProvider` (admin_ui/frontend/src/utils/providerNaming.ts).
"""
from src.utils.voice_catalog import (
    OPENAI_GA_VOICES,
    full_agent_kind,
    provider_voice_meta,
)


# ---------------------------------------------------------------------------
# provider_voice_meta: voice_mode / voices / voice_field per provider kind
# ---------------------------------------------------------------------------

def _ids(meta):
    return [v["id"] for v in meta["voices"]]


def test_openai_meta_is_static_and_matches_engine_validation_set():
    meta = provider_voice_meta("openai_realtime")
    assert meta["voice_mode"] == "static"
    assert set(_ids(meta)) == OPENAI_GA_VOICES
    assert meta["voice_field"] == "voice"


def test_openai_catalog_matches_provider_module_constant():
    # Single source: the engine's soft-validation set must be THIS set.
    from src.providers.openai_realtime import OPENAI_GA_VOICES as ENGINE_SET
    assert ENGINE_SET == OPENAI_GA_VOICES


def test_grok_meta_is_freeform_with_named_voices():
    meta = provider_voice_meta("grok")
    assert meta["voice_mode"] == "freeform"  # custom clone IDs allowed
    assert set(_ids(meta)) == {"eve", "ara", "rex", "sal", "leo"}
    assert meta["voice_field"] == "voice"


def test_google_live_meta_is_static_with_prebuilt_names():
    # Runtime validates against the verified prebuilt catalog (closed set),
    # so the UI must offer a dropdown, not invite free-text values the engine
    # would drop (Codex round-3 on #503).
    meta = provider_voice_meta("google_live")
    assert meta["voice_mode"] == "static"
    ids = _ids(meta)
    assert {"Aoede", "Kore", "Charon", "Puck", "Zephyr"}.issubset(set(ids))
    assert len(ids) == 30
    assert meta["voice_field"] == "tts_voice_name"


def test_deepgram_meta_is_static_with_aura_models():
    # Same as Google: the Aura catalog is validated at runtime — closed set.
    meta = provider_voice_meta("deepgram")
    assert meta["voice_mode"] == "static"
    ids = set(_ids(meta))
    assert "aura-2-thalia-en" in ids       # aura-2 generation
    assert "aura-asteria-en" in ids        # legacy aura, still supported
    assert meta["voice_field"] == "tts_model"


def test_elevenlabs_agent_is_platform_managed():
    meta = provider_voice_meta("elevenlabs_agent")
    assert meta["voice_mode"] == "platform_managed"
    assert meta["voices"] == []
    assert meta["voice_field"] is None


def test_local_and_unknown_kinds_are_unsupported():
    assert provider_voice_meta("local")["voice_mode"] == "unsupported"
    assert provider_voice_meta("something_else")["voice_mode"] == "unsupported"
    assert provider_voice_meta(None)["voice_mode"] == "unsupported"


# ---------------------------------------------------------------------------
# full_agent_kind: mirrors frontend isFullAgentProvider semantics
# ---------------------------------------------------------------------------

def test_explicit_type_wins_for_multi_instance_entries():
    assert full_agent_kind("acme_grok", {"type": "grok"}) == "grok"
    assert full_agent_kind("backup_openai", {"type": "openai_realtime"}) == "openai_realtime"


def test_legacy_canonical_key_with_no_type():
    assert full_agent_kind("openai_realtime", {}) == "openai_realtime"
    assert full_agent_kind("deepgram", {}) == "deepgram"


def test_type_full_resolves_via_canonical_key():
    assert full_agent_kind("google_live", {"type": "full"}) == "google_live"
    # Non-canonical key with bare type=full: not recognizable as a full agent kind.
    assert full_agent_kind("mystery", {"type": "full"}) is None


def test_modular_adapters_are_not_full_agents():
    assert full_agent_kind("local_stt", {}) is None
    assert full_agent_kind("azure_tts", {"type": "azure"}) is None
    assert full_agent_kind("groq", {"type": "llm"}) is None
