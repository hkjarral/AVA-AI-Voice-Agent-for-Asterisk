"""LOW-P10: hand-rolled ElevenLabs from_dict loaders should warn (not silently
drop) unknown config keys so misconfiguration is visible."""
import logging

from src.providers.elevenlabs_config import (
    ElevenLabsAgentConfig,
    ElevenLabsTTSConfig,
)


def test_tts_from_dict_warns_on_unknown_key(caplog):
    with caplog.at_level(logging.WARNING):
        cfg = ElevenLabsTTSConfig.from_dict({"voice_id": "abc", "typoed_key": "x"})
    assert cfg.voice_id == "abc"
    assert any("typoed_key" in r.message for r in caplog.records)


def test_agent_from_dict_warns_on_unknown_key(caplog):
    with caplog.at_level(logging.WARNING):
        ElevenLabsAgentConfig.from_dict({"agent_id": "a1", "bogus": True})
    assert any("bogus" in r.message for r in caplog.records)


def test_from_dict_no_warning_for_known_keys(caplog):
    with caplog.at_level(logging.WARNING):
        ElevenLabsTTSConfig.from_dict(
            {"voice_id": "abc", "model_id": "eleven_flash_v2_5", "voice_settings": {}}
        )
    assert not any("unknown config key" in r.message for r in caplog.records)
