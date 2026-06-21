"""Finding 2 (Codex P2 re-review): a streaming-only OR vad-only save must recommend
RESTART, not hot_reload. StreamingPlaybackManager AND the VAD/gating managers
(EnhancedVADManager, webrtcvad.Vad, _vad_mode, AudioGatingManager) read their params
at construction in Engine.__init__, and _reload_handler does not rebuild them. Only
barge_in is read live per-call (getattr(self.config, "barge_in", None)), so it stays
hot_reload."""
import pytest

from api import config as config_api
from api.config import ConfigUpdate, update_yaml_config


def _patch_io(monkeypatch, old_merged, new_parsed):
    """Stub the filesystem/validation helpers so update_yaml_config exercises only
    the apply-method decision over old_merged vs new_parsed."""
    monkeypatch.setattr(config_api, "_assert_tool_emails_valid", lambda content: None)
    monkeypatch.setattr(config_api, "_validate_ai_agent_config", lambda content: {"warnings": []})
    monkeypatch.setattr(config_api, "_safe_load_no_duplicates", lambda content: new_parsed)
    monkeypatch.setattr(config_api, "_migrate_inline_provider_secrets", lambda parsed: False)
    monkeypatch.setattr(config_api, "_read_merged_config_dict", lambda: old_merged)
    monkeypatch.setattr(config_api, "_read_base_config_dict", lambda: {})
    monkeypatch.setattr(config_api, "_compute_local_override", lambda base, parsed: parsed)
    monkeypatch.setattr(config_api, "_write_local_config", lambda content: None)


@pytest.mark.asyncio
async def test_streaming_only_save_recommends_restart(monkeypatch):
    old = {"streaming": {"jitter_buffer_ms": 50}}
    new = {"streaming": {"jitter_buffer_ms": 120}}
    _patch_io(monkeypatch, old, new)
    resp = await update_yaml_config(ConfigUpdate(content="x"))
    assert resp["recommended_apply_method"] == "restart"
    assert resp["restart_required"] is True


@pytest.mark.asyncio
async def test_vad_only_save_recommends_restart(monkeypatch):
    # vad is manager-backed (built once in Engine.__init__); reload can't apply it.
    old = {"vad": {"energy_threshold": 1500}}
    new = {"vad": {"energy_threshold": 1800}}
    _patch_io(monkeypatch, old, new)
    resp = await update_yaml_config(ConfigUpdate(content="x"))
    assert resp["recommended_apply_method"] == "restart"
    assert resp["restart_required"] is True


@pytest.mark.asyncio
async def test_barge_in_only_save_still_hot_reloads(monkeypatch):
    old = {"barge_in": {"post_tts_end_protection_ms": 100}}
    new = {"barge_in": {"post_tts_end_protection_ms": 200}}
    _patch_io(monkeypatch, old, new)
    resp = await update_yaml_config(ConfigUpdate(content="x"))
    assert resp["recommended_apply_method"] == "hot_reload"


@pytest.mark.asyncio
async def test_streaming_plus_vad_save_recommends_restart(monkeypatch):
    # Any streaming change drags the whole save onto the restart path.
    old = {"vad": {"energy_threshold": 1500}, "streaming": {"jitter_buffer_ms": 50}}
    new = {"vad": {"energy_threshold": 1800}, "streaming": {"jitter_buffer_ms": 120}}
    _patch_io(monkeypatch, old, new)
    resp = await update_yaml_config(ConfigUpdate(content="x"))
    assert resp["recommended_apply_method"] == "restart"
