"""
Integration tests for config loading.

Tests cover:
- Loading actual YAML configuration files
- End-to-end validation with real configs
- Ensuring refactored load_config behaves identically to original
"""

import os
import pytest
from pathlib import Path

from src.config import load_config, AppConfig


class TestConfigLoading:
    """Integration tests for load_config with real YAML files."""
    
    @pytest.fixture(autouse=True)
    def setup_env(self, monkeypatch):
        """Set up required environment variables for tests."""
        # Set minimal required env vars
        monkeypatch.setenv("ASTERISK_ARI_USERNAME", "test_user")
        monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "test_pass")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
        monkeypatch.setenv("TELNYX_API_KEY", "tk-test-key")
    
    def test_load_example_config(self):
        """Should successfully load ai-agent.example.yaml."""
        config = load_config("config/ai-agent.example.yaml")
        
        assert isinstance(config, AppConfig)
        assert config.asterisk.username == "test_user"
        assert config.asterisk.password == "test_pass"
        assert config.llm.api_key == "sk-test-key"
    
    def test_load_golden_openai_config(self):
        """Should successfully load golden OpenAI config."""
        config = load_config("config/ai-agent.golden-openai.yaml")
        
        assert isinstance(config, AppConfig)
        assert config.default_provider == "openai_realtime"
        assert hasattr(config, 'pipelines')

    def test_load_config_retains_raw_contexts_for_migration_hash(
        self, tmp_path, monkeypatch
    ):
        source = Path("config/ai-agent.golden-openai.yaml").read_text()
        original_prompt = (
            "You are a concise voice assistant. Respond clearly and keep answers "
            "under 20 words unless more detail is requested."
        )
        config_path = tmp_path / "ai-agent.yaml"
        config_path.write_text(
            source.replace(original_prompt, "${LEGACY_AGENT_PROMPT}"),
            encoding="utf-8",
        )
        (tmp_path / "contexts").mkdir()
        monkeypatch.setenv("LEGACY_AGENT_PROMPT", original_prompt)

        config = load_config(str(config_path))

        assert config.contexts["default"]["prompt"] == original_prompt
        assert (
            config._legacy_contexts_for_hash["default"]["prompt"]
            == "${LEGACY_AGENT_PROMPT}"
        )
    
    def test_load_golden_deepgram_config(self):
        """Should successfully load golden Deepgram config."""
        config = load_config("config/ai-agent.golden-deepgram.yaml")
        
        assert isinstance(config, AppConfig)
        assert config.default_provider == "deepgram"
    
    def test_load_golden_local_hybrid_config(self):
        """Should successfully load golden local hybrid config."""
        config = load_config("config/ai-agent.golden-local-hybrid.yaml")
        
        assert isinstance(config, AppConfig)
        # Verify pipelines were normalized
        assert hasattr(config, 'pipelines')

    def test_load_golden_telnyx_config(self):
        """Should successfully load golden Telnyx config."""
        config = load_config("config/ai-agent.golden-telnyx.yaml")

        assert isinstance(config, AppConfig)
        assert config.active_pipeline == "telnyx_hybrid"
        assert config.default_provider == "telnyx_hybrid"
    
    def test_config_has_required_sections(self):
        """Should have all required configuration sections."""
        config = load_config("config/ai-agent.example.yaml")
        
        # Core sections
        assert config.asterisk is not None
        assert config.llm is not None
        
        # Optional sections should have defaults
        assert config.audio_transport is not None
        assert config.downstream_mode is not None
    
    def test_credentials_from_env_override_yaml(self, monkeypatch):
        """SECURITY: Environment variables should override YAML credentials."""
        monkeypatch.setenv("ASTERISK_ARI_USERNAME", "env_user")
        monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "env_pass")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key")
        
        config = load_config("config/ai-agent.example.yaml")
        
        # Credentials should come from environment, not YAML
        assert config.asterisk.username == "env_user"
        assert config.asterisk.password == "env_pass"
        assert config.llm.api_key == "sk-env-key"
    
    def test_default_profiles_injected(self):
        """Should inject default telephony profile if missing."""
        config = load_config("config/ai-agent.example.yaml")
        
        assert 'telephony_ulaw_8k' in config.profiles
        assert config.profiles['telephony_ulaw_8k']['internal_rate_hz'] == 8000
    
    def test_pipelines_normalized(self):
        """Should normalize pipeline definitions."""
        config = load_config("config/ai-agent.example.yaml")
        
        assert hasattr(config, 'pipelines')
        assert isinstance(config.pipelines, dict)
        
        # All pipelines should have stt, llm, tts keys
        for pipeline_name, pipeline in config.pipelines.items():
            assert hasattr(pipeline, 'stt')
            assert hasattr(pipeline, 'llm')
            assert hasattr(pipeline, 'tts')
    
    def test_diagnostic_settings_from_env(self, monkeypatch):
        """Should apply diagnostic settings from environment variables."""
        monkeypatch.setenv("STREAMING_LOG_LEVEL", "debug")
        monkeypatch.setenv("DIAG_ENABLE_TAPS", "true")
        monkeypatch.setenv("DIAG_TAP_PRE_SECS", "3")
        monkeypatch.setenv("DIAG_TAP_POST_SECS", "4")
        monkeypatch.setenv("DIAG_TAP_OUTPUT_DIR", "/tmp/integration-taps")
        
        config = load_config("config/ai-agent.example.yaml")
        
        assert config.streaming.logging_level == "debug"
        assert config.streaming.diag_enable_taps is True
        assert config.streaming.diag_pre_secs == 3
        assert config.streaming.diag_post_secs == 4
        assert config.streaming.diag_out_dir == "/tmp/integration-taps"
    
    def test_barge_in_env_overrides(self, monkeypatch):
        """Should apply barge-in env var overrides."""
        monkeypatch.setenv("BARGE_IN_ENABLED", "false")
        monkeypatch.setenv("BARGE_IN_MIN_MS", "500")
        
        config = load_config("config/ai-agent.example.yaml")
        
        assert config.barge_in.enabled is False
        assert config.barge_in.min_ms == 500
    
    def test_nonexistent_file_raises_error(self):
        """Should raise FileNotFoundError for missing config file."""
        with pytest.raises(FileNotFoundError) as exc_info:
            load_config("config/nonexistent.yaml")
        
        assert "not found" in str(exc_info.value).lower()
    
    def test_absolute_path_supported(self):
        """Should support absolute paths to config files."""
        abs_path = (Path(__file__).parent.parent.parent / "config" / "ai-agent.example.yaml").resolve()
        
        config = load_config(str(abs_path))
        
        assert isinstance(config, AppConfig)
    
    def test_config_version_preserved(self):
        """Should preserve config_version if present."""
        config = load_config("config/ai-agent.example.yaml")
        assert config is not None
        assert hasattr(config, "config_version")
        assert isinstance(config.config_version, int)


class TestConfigIntegrity:
    """Tests to ensure refactored config behaves identically to original."""
    
    @pytest.fixture(autouse=True)
    def setup_env(self, monkeypatch):
        """Set up required environment variables."""
        monkeypatch.setenv("ASTERISK_ARI_USERNAME", "test_user")
        monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "test_pass")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
        monkeypatch.setenv("TELNYX_API_KEY", "tk-test-key")
    
    def test_all_example_configs_load_successfully(self):
        """All example configs should load without errors."""
        example_configs = [
            "config/ai-agent.example.yaml",
            "config/ai-agent.golden-openai.yaml",
            "config/ai-agent.golden-deepgram.yaml",
            "config/ai-agent.golden-local-hybrid.yaml",
            "config/ai-agent.golden-telnyx.yaml",
        ]
        
        for config_path in example_configs:
            try:
                config = load_config(config_path)
                assert isinstance(config, AppConfig)
            except Exception as e:
                pytest.fail(f"Failed to load {config_path}: {str(e)}")
    
    def test_provider_configs_preserved(self):
        """Provider configurations should be preserved correctly."""
        config = load_config("config/ai-agent.example.yaml")
        
        # Providers dict should exist
        assert hasattr(config, 'providers')
        assert isinstance(config.providers, dict)
    
    def test_streaming_config_complete(self):
        """Streaming configuration should have all expected fields."""
        config = load_config("config/ai-agent.example.yaml")
        
        assert config.streaming is not None
        assert hasattr(config.streaming, 'sample_rate')
        assert hasattr(config.streaming, 'jitter_buffer_ms')
        assert hasattr(config.streaming, 'logging_level')
    
    def test_contexts_block_initialized(self):
        """Contexts block should be initialized (even if empty)."""
        config = load_config("config/ai-agent.example.yaml")
        
        assert hasattr(config, 'contexts')
        assert isinstance(config.contexts, dict)
