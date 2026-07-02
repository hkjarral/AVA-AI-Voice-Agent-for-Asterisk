"""Per-agent voice override (v7.3.0).

Voice precedence: per-call override > agent/context voice > provider config voice.
The agents.db ``voice`` column (collected by the Admin UI since v7.0.0 but never
read at runtime) becomes runtime-active; the provider-level voice is the fallback.

Seeded by PR #497 (foytech) — generalized across full-agent providers with soft
validation: an unrecognized voice on a closed-list provider falls back to the
provider default and never fails the call.
"""
import sqlite3

import pytest

from src.core.agent_store import EngineAgentStore
from src.core.transport_orchestrator import (
    ContextConfig,
    TransportOrchestrator,
    resolve_effective_voice,
)


# ---------------------------------------------------------------------------
# ContextConfig + engine-side precedence resolver
# ---------------------------------------------------------------------------

def test_context_config_voice_defaults_to_none():
    assert ContextConfig().voice is None


def test_per_call_override_beats_agent_voice():
    cfg = ContextConfig(voice="marin")
    voice, source = resolve_effective_voice({"voice": "cedar"}, cfg)
    assert voice == "cedar"
    assert source == "override"


def test_agent_voice_used_when_no_override():
    cfg = ContextConfig(voice="  marin  ")
    voice, source = resolve_effective_voice({}, cfg)
    assert voice == "marin"
    assert source == "agent"


def test_no_voice_anywhere_falls_to_provider_default():
    voice, source = resolve_effective_voice({}, ContextConfig())
    assert voice is None
    assert source == "provider-default"


def test_blank_values_are_ignored():
    cfg = ContextConfig(voice="   ")
    voice, source = resolve_effective_voice({"voice": ""}, cfg)
    assert voice is None
    assert source == "provider-default"


def test_none_context_config_is_safe():
    voice, source = resolve_effective_voice({}, None)
    assert voice is None
    assert source == "provider-default"


def test_non_string_override_is_ignored():
    voice, source = resolve_effective_voice({"voice": 42}, ContextConfig(voice="marin"))
    assert voice == "marin"
    assert source == "agent"


# ---------------------------------------------------------------------------
# agents.db: voice column reaches ContextConfig
# ---------------------------------------------------------------------------

_CREATE = """CREATE TABLE agents (
    id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
    extension TEXT, role_label TEXT, provider TEXT, voice TEXT, greeting TEXT,
    prompt TEXT, tools_json TEXT, mcp_json TEXT, audio_profile TEXT, extra_json TEXT,
    is_operator_managed INTEGER DEFAULT 1, is_active INTEGER DEFAULT 1,
    is_default INTEGER DEFAULT 0, source_file TEXT, created_at TEXT, updated_at TEXT,
    notes TEXT)"""


def _seed(db_path, slug, voice):
    conn = sqlite3.connect(db_path)
    conn.execute(_CREATE)
    conn.execute(
        "INSERT INTO agents (id,slug,display_name,provider,voice,prompt,is_active,is_default) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("1", slug, slug, "openai", voice, "hi", 1, 1),
    )
    conn.commit()
    conn.close()


def test_agent_store_extracts_voice(tmp_path):
    db = str(tmp_path / "agents.db")
    _seed(db, "front_desk", "marin")
    cfg = EngineAgentStore(db_path=db).resolve("front_desk")
    assert cfg.voice == "marin"


def test_agent_store_null_voice_stays_none(tmp_path):
    db = str(tmp_path / "agents.db")
    _seed(db, "front_desk", None)
    cfg = EngineAgentStore(db_path=db).resolve("front_desk")
    assert cfg.voice is None


# ---------------------------------------------------------------------------
# YAML contexts (headless fallback path) support a voice key
# ---------------------------------------------------------------------------

def test_yaml_context_voice_loads():
    orch = object.__new__(TransportOrchestrator)  # _load_contexts only reads its arg
    contexts = orch._load_contexts(
        {"contexts": {"support": {"prompt": "hi", "voice": "marin"}}}
    )
    assert contexts["support"].voice == "marin"


# ---------------------------------------------------------------------------
# OpenAI Realtime: session voice with closed-list soft validation
# ---------------------------------------------------------------------------

from src.config import OpenAIRealtimeProviderConfig
from src.providers.openai_realtime import OPENAI_GA_VOICES, OpenAIRealtimeProvider


@pytest.fixture
def openai_config():
    return OpenAIRealtimeProviderConfig(
        api_key="test-key",
        model="gpt-test",
        voice="alloy",
        base_url="wss://api.openai.com/v1/realtime",
        input_encoding="ulaw",
        input_sample_rate_hz=8000,
        provider_input_encoding="linear16",
        provider_input_sample_rate_hz=24000,
        output_encoding="linear16",
        output_sample_rate_hz=24000,
        target_encoding="mulaw",
        target_sample_rate_hz=8000,
        response_modalities=["audio"],
    )


def test_openai_ga_voice_catalog_is_complete():
    # The 10 GA voices per OpenAI docs (mirrors the Admin UI dropdown).
    assert OPENAI_GA_VOICES == {
        "alloy", "ash", "ballad", "cedar", "coral",
        "echo", "marin", "sage", "shimmer", "verse",
    }


@pytest.mark.asyncio
async def test_openai_ga_session_uses_agent_voice(openai_config):
    openai_config.api_version = "ga"
    provider = OpenAIRealtimeProvider(openai_config, on_event=None)
    provider._set_session_voice_from_context({"voice": "marin"})

    captured = {}

    async def fake_send(payload):
        captured.update(payload)

    provider._send_json = fake_send  # type: ignore

    await provider._send_session_update()

    session = captured.get("session", {})
    assert session.get("audio", {}).get("output", {}).get("voice") == "marin"


@pytest.mark.asyncio
async def test_openai_ga_session_falls_back_to_config_voice(openai_config):
    openai_config.api_version = "ga"
    provider = OpenAIRealtimeProvider(openai_config, on_event=None)
    provider._set_session_voice_from_context({})  # no agent voice

    captured = {}

    async def fake_send(payload):
        captured.update(payload)

    provider._send_json = fake_send  # type: ignore

    await provider._send_session_update()

    session = captured.get("session", {})
    assert session.get("audio", {}).get("output", {}).get("voice") == "alloy"


@pytest.mark.asyncio
async def test_openai_unknown_voice_falls_back_softly(openai_config):
    # Pre-7.3.0 the agent voice field was display-only free text; a stale value
    # like "Jenny - British accent" must degrade to the provider default, never
    # reach the OpenAI session, and never fail the call.
    openai_config.api_version = "ga"
    provider = OpenAIRealtimeProvider(openai_config, on_event=None)
    provider._set_session_voice_from_context({"voice": "Jenny - British accent"})

    assert provider._session_voice is None

    captured = {}

    async def fake_send(payload):
        captured.update(payload)

    provider._send_json = fake_send  # type: ignore

    await provider._send_session_update()

    session = captured.get("session", {})
    assert session.get("audio", {}).get("output", {}).get("voice") == "alloy"


@pytest.mark.asyncio
async def test_openai_beta_session_uses_agent_voice(openai_config):
    openai_config.api_version = "beta"
    openai_config.model = "gpt-4o-realtime-preview"
    provider = OpenAIRealtimeProvider(openai_config, on_event=None)
    provider._set_session_voice_from_context({"voice": "cedar"})

    captured = {}

    async def fake_send(payload):
        captured.update(payload)

    provider._send_json = fake_send  # type: ignore

    await provider._send_session_update()

    assert captured.get("session", {}).get("voice") == "cedar"


def test_openai_voice_case_is_normalized(openai_config):
    provider = OpenAIRealtimeProvider(openai_config, on_event=None)
    provider._set_session_voice_from_context({"voice": "  Marin "})
    assert provider._session_voice == "marin"


# ---------------------------------------------------------------------------
# Grok: named voices AND custom cloned voice IDs — pass-through, no validation
# ---------------------------------------------------------------------------

from src.config import GrokProviderConfig
from src.providers.grok import GrokProvider


@pytest.fixture
def grok_config():
    return GrokProviderConfig(
        api_key="test-xai-key",
        model="grok-voice-latest",
        voice="eve",
        base_url="wss://api.x.ai/v1/realtime",
        input_encoding="ulaw",
        input_sample_rate_hz=8000,
        provider_input_encoding="ulaw",
        provider_input_sample_rate_hz=8000,
        output_encoding="ulaw",
        output_sample_rate_hz=8000,
        target_encoding="ulaw",
        target_sample_rate_hz=8000,
    )


@pytest.mark.asyncio
async def test_grok_session_uses_agent_voice(grok_config):
    provider = GrokProvider(grok_config, on_event=None)
    provider._set_session_voice_from_context({"voice": "rex"})

    captured = {}

    async def fake_send(payload):
        captured.update(payload)

    provider._send_json = fake_send  # type: ignore

    await provider._send_session_update()

    assert captured.get("session", {}).get("voice") == "rex"


@pytest.mark.asyncio
async def test_grok_custom_clone_id_passes_through(grok_config):
    # xAI accepts custom cloned-voice IDs, so Grok must NOT hard-validate.
    provider = GrokProvider(grok_config, on_event=None)
    provider._set_session_voice_from_context({"voice": "myCloneID_123"})

    captured = {}

    async def fake_send(payload):
        captured.update(payload)

    provider._send_json = fake_send  # type: ignore

    await provider._send_session_update()

    assert captured.get("session", {}).get("voice") == "myCloneID_123"


@pytest.mark.asyncio
async def test_grok_falls_back_to_config_voice(grok_config):
    provider = GrokProvider(grok_config, on_event=None)
    provider._set_session_voice_from_context({})

    captured = {}

    async def fake_send(payload):
        captured.update(payload)

    provider._send_json = fake_send  # type: ignore

    await provider._send_session_update()

    assert captured.get("session", {}).get("voice") == "eve"


# ---------------------------------------------------------------------------
# Deepgram Voice Agent: speak model resolution (primary AND retry payloads
# consume the same resolved value — see _configure_agent's speak_model local)
# ---------------------------------------------------------------------------

from src.providers.deepgram import resolve_speak_model


def test_deepgram_agent_voice_wins():
    assert resolve_speak_model("aura-luna-en", "aura-asteria-en") == "aura-luna-en"


def test_deepgram_falls_back_to_configured_model():
    assert resolve_speak_model(None, "aura-orion-en") == "aura-orion-en"


def test_deepgram_blank_session_voice_ignored():
    assert resolve_speak_model("   ", "aura-orion-en") == "aura-orion-en"


def test_deepgram_default_when_nothing_configured():
    assert resolve_speak_model(None, None) == "aura-asteria-en"
