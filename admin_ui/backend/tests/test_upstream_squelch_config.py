"""Keep the existing UI/YAML VAD contract and restart path for #799."""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from api import config
from src.config import VADConfig


@pytest.mark.parametrize("enabled,factor,alpha", [(True, 2.5, 0.06), (False, 0.0, 0.0)])
def test_squelch_save_reopen_preserves_values_and_requires_restart(
    tmp_path, monkeypatch, enabled, factor, alpha,
):
    original = yaml.safe_load(Path(config.settings.CONFIG_PATH).read_text())
    base = tmp_path / "ai-agent.yaml"
    local = tmp_path / "ai-agent.local.yaml"
    env = tmp_path / ".env"
    base.write_text(yaml.safe_dump(original))
    env.write_text("ASTERISK_ARI_USERNAME=test\nASTERISK_ARI_PASSWORD=test\n")
    monkeypatch.setattr(config.settings, "CONFIG_PATH", str(base))
    monkeypatch.setattr(config.settings, "LOCAL_CONFIG_PATH", str(local))
    monkeypatch.setattr(config.settings, "ENV_PATH", str(env))
    monkeypatch.setenv("AGENTS_DB_PATH", str(tmp_path / "absent-agents.db"))
    desired = deepcopy(original)
    values = {
        "upstream_squelch_enabled": enabled,
        "upstream_squelch_base_rms": 123,
        "upstream_squelch_noise_factor": factor,
        "upstream_squelch_noise_ema_alpha": alpha,
        "upstream_squelch_min_speech_frames": 3,
        "upstream_squelch_end_silence_frames": 7,
    }
    desired.setdefault("vad", {}).update(values)
    result = config.persist_config_content(yaml.safe_dump(desired))
    assert result["restart_required"] is True
    assert result["recommended_apply_method"] == "restart"
    reopened = config._read_merged_config_dict()
    assert {key: reopened["vad"][key] for key in values} == values
    loaded = VADConfig(**reopened["vad"])
    assert {key: getattr(loaded, key) for key in values} == values
    assert reopened["providers"] == original["providers"]
    assert yaml.safe_load(base.read_text()) == original
    assert not any("upstream_squelch" in warning for warning in result["warnings"])
