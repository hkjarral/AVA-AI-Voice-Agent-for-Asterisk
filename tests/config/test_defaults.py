"""
Unit tests for config.defaults module.

Tests cover:
- Transport mode defaults (audio_transport, downstream_mode)
- AudioSocket configuration defaults
- ExternalMedia RTP configuration defaults
- Diagnostic settings
- Barge-in configuration with environment variable overrides
"""

import os
import pytest

from src.config.defaults import (
    apply_transport_defaults,
    apply_audiosocket_defaults,
    apply_externalmedia_defaults,
    apply_diagnostic_defaults,
    apply_barge_in_defaults,
)


class TestApplyTransportDefaults:
    """Tests for apply_transport_defaults function."""
    
    def test_default_values_when_no_env(self):
        """Should use hardcoded defaults when no env vars set."""
        config_data = {}
        apply_transport_defaults(config_data)
        
        assert config_data['audio_transport'] == 'externalmedia'
        assert config_data['downstream_mode'] == 'stream'
    
    def test_env_overrides_audio_transport(self, monkeypatch):
        """Should override audio_transport from environment."""
        monkeypatch.setenv('AUDIO_TRANSPORT', 'audiosocket')
        
        config_data = {}
        apply_transport_defaults(config_data)
        
        assert config_data['audio_transport'] == 'audiosocket'
    
    def test_env_overrides_downstream_mode(self, monkeypatch):
        """Should override downstream_mode from environment."""
        monkeypatch.setenv('DOWNSTREAM_MODE', 'stream')
        
        config_data = {}
        apply_transport_defaults(config_data)
        
        assert config_data['downstream_mode'] == 'stream'
    
    def test_preserve_yaml_values_if_present(self):
        """Should preserve existing YAML values (setdefault behavior)."""
        config_data = {
            'audio_transport': 'yaml_transport',
            'downstream_mode': 'yaml_mode'
        }
        apply_transport_defaults(config_data)
        
        # setdefault should not override existing values
        assert config_data['audio_transport'] == 'yaml_transport'
        assert config_data['downstream_mode'] == 'yaml_mode'


class TestApplyAudiosocketDefaults:
    """Tests for apply_audiosocket_defaults function."""
    
    def test_default_values_when_empty(self):
        """Should apply default values when audiosocket block is empty."""
        config_data = {}
        apply_audiosocket_defaults(config_data)
        
        assert config_data['audiosocket']['host'] == '127.0.0.1'
        assert config_data['audiosocket']['port'] == 8090
        assert config_data['audiosocket']['format'] == 'ulaw'
    
    def test_env_overrides_host(self, monkeypatch):
        """Should override host from environment."""
        monkeypatch.setenv('AUDIOSOCKET_HOST', '0.0.0.0')
        
        config_data = {}
        apply_audiosocket_defaults(config_data)
        
        assert config_data['audiosocket']['host'] == '0.0.0.0'
    
    def test_env_overrides_port(self, monkeypatch):
        """Should override port from environment."""
        monkeypatch.setenv('AUDIOSOCKET_PORT', '9090')
        
        config_data = {}
        apply_audiosocket_defaults(config_data)
        
        assert config_data['audiosocket']['port'] == 9090
    
    def test_env_overrides_format(self, monkeypatch):
        """Should override format from environment."""
        monkeypatch.setenv('AUDIOSOCKET_FORMAT', 'slin16')
        
        config_data = {}
        apply_audiosocket_defaults(config_data)
        
        assert config_data['audiosocket']['format'] == 'slin16'
    
    def test_preserve_yaml_values(self):
        """Should preserve YAML values when set."""
        config_data = {
            'audiosocket': {
                'host': 'yaml_host',
                'port': 7777,
                'format': 'slin'
            }
        }
        apply_audiosocket_defaults(config_data)
        
        assert config_data['audiosocket']['host'] == 'yaml_host'
        assert config_data['audiosocket']['port'] == 7777
        assert config_data['audiosocket']['format'] == 'slin'
    
    def test_invalid_port_uses_default(self, monkeypatch):
        """Should use default port if env var is invalid."""
        monkeypatch.setenv('AUDIOSOCKET_PORT', 'invalid')
        
        config_data = {}
        apply_audiosocket_defaults(config_data)
        
        assert config_data['audiosocket']['port'] == 8090
    
    def test_advertise_host_env_override(self, monkeypatch):
        """Should override advertise_host from environment (NAT support)."""
        monkeypatch.setenv('AUDIOSOCKET_ADVERTISE_HOST', '10.8.0.5')
        
        config_data = {}
        apply_audiosocket_defaults(config_data)
        
        assert config_data['audiosocket']['advertise_host'] == '10.8.0.5'

    def test_advertise_host_env_empty_ignored(self, monkeypatch):
        """Empty advertise_host env var should be ignored."""
        monkeypatch.setenv('AUDIOSOCKET_ADVERTISE_HOST', '   ')
        
        config_data = {}
        apply_audiosocket_defaults(config_data)
        
        assert 'advertise_host' not in config_data['audiosocket']
    
    def test_advertise_host_not_set_by_default(self):
        """advertise_host should not be set when env var is absent (engine falls back to host)."""
        config_data = {}
        apply_audiosocket_defaults(config_data)
        
        # advertise_host should not be in the config unless explicitly set
        assert 'advertise_host' not in config_data['audiosocket']
    
    def test_advertise_host_preserved_from_yaml(self, monkeypatch):
        """Should preserve YAML advertise_host value when env var not set."""
        config_data = {
            'audiosocket': {
                'host': '0.0.0.0',
                'advertise_host': '192.168.1.50'
            }
        }
        apply_audiosocket_defaults(config_data)
        
        # YAML value should be preserved
        assert config_data['audiosocket']['advertise_host'] == '192.168.1.50'
    
    def test_advertise_host_env_overrides_yaml(self, monkeypatch):
        """Environment variable should override YAML advertise_host value."""
        monkeypatch.setenv('AUDIOSOCKET_ADVERTISE_HOST', '10.8.0.5')
        
        config_data = {
            'audiosocket': {
                'host': '0.0.0.0',
                'advertise_host': '192.168.1.50'
            }
        }
        apply_audiosocket_defaults(config_data)
        
        # Env var should override YAML
        assert config_data['audiosocket']['advertise_host'] == '10.8.0.5'


class TestApplyExternalmediaDefaults:
    """Tests for apply_externalmedia_defaults function."""
    
    def test_default_rtp_host(self):
        """Should use default RTP host when not set."""
        config_data = {}
        apply_externalmedia_defaults(config_data)
        
        assert config_data['external_media']['rtp_host'] == '127.0.0.1'
    
    def test_env_overrides_rtp_host(self, monkeypatch):
        """Should override RTP host from environment."""
        monkeypatch.setenv('EXTERNAL_MEDIA_RTP_HOST', '0.0.0.0')
        
        config_data = {}
        apply_externalmedia_defaults(config_data)
        
        assert config_data['external_media']['rtp_host'] == '0.0.0.0'
    
    def test_preserve_yaml_rtp_host(self):
        """Should preserve YAML RTP host value."""
        config_data = {
            'external_media': {
                'rtp_host': 'yaml_host'
            }
        }
        apply_externalmedia_defaults(config_data)
        
        assert config_data['external_media']['rtp_host'] == 'yaml_host'
    
    def test_advertise_host_env_override(self, monkeypatch):
        """Should override advertise_host from environment (NAT support)."""
        monkeypatch.setenv('EXTERNAL_MEDIA_ADVERTISE_HOST', '10.8.0.5')
        
        config_data = {}
        apply_externalmedia_defaults(config_data)
        
        assert config_data['external_media']['advertise_host'] == '10.8.0.5'

    def test_advertise_host_env_empty_ignored(self, monkeypatch):
        """Empty advertise_host env var should be ignored."""
        monkeypatch.setenv('EXTERNAL_MEDIA_ADVERTISE_HOST', '   ')
        
        config_data = {}
        apply_externalmedia_defaults(config_data)
        
        assert 'advertise_host' not in config_data['external_media']
    
    def test_advertise_host_not_set_by_default(self):
        """advertise_host should not be set when env var is absent (engine falls back to rtp_host)."""
        config_data = {}
        apply_externalmedia_defaults(config_data)
        
        # advertise_host should not be in the config unless explicitly set
        assert 'advertise_host' not in config_data['external_media']
    
    def test_advertise_host_preserved_from_yaml(self):
        """Should preserve YAML advertise_host value when env var not set."""
        config_data = {
            'external_media': {
                'rtp_host': '0.0.0.0',
                'advertise_host': '192.168.1.50'
            }
        }
        apply_externalmedia_defaults(config_data)
        
        # YAML value should be preserved
        assert config_data['external_media']['advertise_host'] == '192.168.1.50'
    
    def test_advertise_host_env_overrides_yaml(self, monkeypatch):
        """Environment variable should override YAML advertise_host value."""
        monkeypatch.setenv('EXTERNAL_MEDIA_ADVERTISE_HOST', '10.8.0.5')
        
        config_data = {
            'external_media': {
                'rtp_host': '0.0.0.0',
                'advertise_host': '192.168.1.50'
            }
        }
        apply_externalmedia_defaults(config_data)
        
        # Env var should override YAML
        assert config_data['external_media']['advertise_host'] == '10.8.0.5'


class TestApplyDiagnosticDefaults:
    """Tests for apply_diagnostic_defaults function."""
    
    def test_default_diagnostic_settings(self):
        """Should apply default diagnostic settings."""
        config_data = {}
        apply_diagnostic_defaults(config_data)
        
        streaming = config_data['streaming']
        assert streaming['egress_swap_mode'] == 'none'
        assert streaming['egress_force_mulaw'] is False
        assert streaming['attack_ms'] == 0
        assert streaming['diag_enable_taps'] is False
        assert streaming['diag_pre_secs'] == 1
        assert streaming['diag_post_secs'] == 1
        assert streaming['diag_out_dir'] == '/tmp/ai-engine-taps'
        assert streaming['logging_level'] == 'info'
    
    def test_env_overrides_egress_swap_mode(self, monkeypatch):
        """Should override egress swap mode from environment."""
        monkeypatch.setenv('DIAG_EGRESS_SWAP_MODE', 'test_mode')
        
        config_data = {}
        apply_diagnostic_defaults(config_data)
        
        assert config_data['streaming']['egress_swap_mode'] == 'test_mode'
    
    def test_env_enables_force_mulaw_true(self, monkeypatch):
        """Should enable force mulaw when env var is 'true'."""
        monkeypatch.setenv('DIAG_EGRESS_FORCE_MULAW', 'true')
        
        config_data = {}
        apply_diagnostic_defaults(config_data)
        
        assert config_data['streaming']['egress_force_mulaw'] is True
    
    def test_env_enables_force_mulaw_1(self, monkeypatch):
        """Should enable force mulaw when env var is '1'."""
        monkeypatch.setenv('DIAG_EGRESS_FORCE_MULAW', '1')
        
        config_data = {}
        apply_diagnostic_defaults(config_data)
        
        assert config_data['streaming']['egress_force_mulaw'] is True
    
    def test_env_overrides_attack_ms(self, monkeypatch):
        """Should override attack ms from environment."""
        monkeypatch.setenv('DIAG_ATTACK_MS', '100')
        
        config_data = {}
        apply_diagnostic_defaults(config_data)
        
        assert config_data['streaming']['attack_ms'] == 100
    
    def test_env_enables_taps(self, monkeypatch):
        """Should enable diagnostic taps when env var is 'true'."""
        monkeypatch.setenv('DIAG_ENABLE_TAPS', 'true')
        
        config_data = {}
        apply_diagnostic_defaults(config_data)
        
        assert config_data['streaming']['diag_enable_taps'] is True
    
    def test_env_overrides_tap_durations(self, monkeypatch):
        """Should override tap pre/post durations from environment."""
        monkeypatch.setenv('DIAG_TAP_PRE_SECS', '3')
        monkeypatch.setenv('DIAG_TAP_POST_SECS', '5')
        
        config_data = {}
        apply_diagnostic_defaults(config_data)
        
        assert config_data['streaming']['diag_pre_secs'] == 3
        assert config_data['streaming']['diag_post_secs'] == 5
    
    def test_env_overrides_tap_output_dir(self, monkeypatch):
        """Should override tap output directory from environment."""
        monkeypatch.setenv('DIAG_TAP_OUTPUT_DIR', '/custom/tap/dir')
        
        config_data = {}
        apply_diagnostic_defaults(config_data)
        
        assert config_data['streaming']['diag_out_dir'] == '/custom/tap/dir'
    
    def test_env_overrides_streaming_log_level(self, monkeypatch):
        """Should override streaming log level from environment."""
        monkeypatch.setenv('STREAMING_LOG_LEVEL', 'debug')
        
        config_data = {}
        apply_diagnostic_defaults(config_data)
        
        assert config_data['streaming']['logging_level'] == 'debug'
    
    def test_creates_streaming_block_if_missing(self):
        """Should create streaming block if it doesn't exist."""
        config_data = {}
        apply_diagnostic_defaults(config_data)
        
        assert 'streaming' in config_data
        assert isinstance(config_data['streaming'], dict)


class TestApplyBargeInDefaults:
    """Tests for apply_barge_in_defaults function."""
    
    def test_empty_barge_in_block_when_no_env(self):
        """Should preserve empty barge_in block when no env vars set."""
        config_data = {}
        apply_barge_in_defaults(config_data)
        
        # Should create empty barge_in block
        assert config_data['barge_in'] == {}
    
    def test_preserve_yaml_values_when_no_env(self):
        """Should preserve YAML values when no env vars set."""
        config_data = {
            'barge_in': {
                'enabled': False,
                'min_ms': 300
            }
        }
        apply_barge_in_defaults(config_data)
        
        assert config_data['barge_in']['enabled'] is False
        assert config_data['barge_in']['min_ms'] == 300
    
    def test_env_overrides_enabled(self, monkeypatch):
        """Should override enabled from environment."""
        monkeypatch.setenv('BARGE_IN_ENABLED', 'false')
        
        config_data = {'barge_in': {'enabled': True}}
        apply_barge_in_defaults(config_data)
        
        assert config_data['barge_in']['enabled'] is False
    
    def test_env_overrides_initial_protection_ms(self, monkeypatch):
        """Should override initial protection ms from environment."""
        monkeypatch.setenv('BARGE_IN_INITIAL_PROTECTION_MS', '300')
        
        config_data = {}
        apply_barge_in_defaults(config_data)
        
        assert config_data['barge_in']['initial_protection_ms'] == 300
    
    def test_env_overrides_min_ms(self, monkeypatch):
        """Should override min ms from environment."""
        monkeypatch.setenv('BARGE_IN_MIN_MS', '400')
        
        config_data = {}
        apply_barge_in_defaults(config_data)
        
        assert config_data['barge_in']['min_ms'] == 400
    
    def test_env_overrides_energy_threshold(self, monkeypatch):
        """Should override energy threshold from environment."""
        monkeypatch.setenv('BARGE_IN_ENERGY_THRESHOLD', '2000')
        
        config_data = {}
        apply_barge_in_defaults(config_data)
        
        assert config_data['barge_in']['energy_threshold'] == 2000
    
    def test_env_overrides_cooldown_ms(self, monkeypatch):
        """Should override cooldown ms from environment."""
        monkeypatch.setenv('BARGE_IN_COOLDOWN_MS', '1000')
        
        config_data = {}
        apply_barge_in_defaults(config_data)
        
        assert config_data['barge_in']['cooldown_ms'] == 1000
    
    def test_env_overrides_post_tts_protection(self, monkeypatch):
        """Should override post TTS protection from environment."""
        monkeypatch.setenv('BARGE_IN_POST_TTS_END_PROTECTION_MS', '500')
        
        config_data = {}
        apply_barge_in_defaults(config_data)
        
        assert config_data['barge_in']['post_tts_end_protection_ms'] == 500
    
    def test_multiple_env_overrides(self, monkeypatch):
        """Should handle multiple environment variable overrides."""
        monkeypatch.setenv('BARGE_IN_ENABLED', 'true')
        monkeypatch.setenv('BARGE_IN_MIN_MS', '350')
        monkeypatch.setenv('BARGE_IN_ENERGY_THRESHOLD', '1500')
        
        config_data = {}
        apply_barge_in_defaults(config_data)
        
        assert config_data['barge_in']['enabled'] is True
        assert config_data['barge_in']['min_ms'] == 350
        assert config_data['barge_in']['energy_threshold'] == 1500
    
    def test_invalid_int_preserves_yaml(self, monkeypatch):
        """Should preserve YAML values if env var has invalid integer."""
        monkeypatch.setenv('BARGE_IN_MIN_MS', 'invalid')
        
        config_data = {'barge_in': {'min_ms': 250}}
        apply_barge_in_defaults(config_data)
        
        # Should keep YAML value since env var is invalid
        assert config_data['barge_in']['min_ms'] == 250
    
    def test_only_override_if_env_set(self):
        """Should only override specific keys when env vars are set."""
        config_data = {
            'barge_in': {
                'enabled': True,
                'min_ms': 250,
                'cooldown_ms': 500
            }
        }
        apply_barge_in_defaults(config_data)
        
        # All YAML values should be preserved since no env vars set
        assert config_data['barge_in']['enabled'] is True
        assert config_data['barge_in']['min_ms'] == 250
        assert config_data['barge_in']['cooldown_ms'] == 500
