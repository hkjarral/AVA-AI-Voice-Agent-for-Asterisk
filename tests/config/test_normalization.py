"""
Unit tests for config.normalization module.

Tests cover:
- Pipeline definition normalization
- Default profile and context injection
- Provider-specific token sanitization
"""

import pytest

from src.config.normalization import (
    _compose_provider_components,
    _generate_default_pipeline,
    normalize_pipelines,
    normalize_profiles,
    normalize_local_provider_tokens,
)


class TestComposeProviderComponents:
    """Tests for _compose_provider_components helper."""
    
    def test_openai_realtime_components(self):
        """Should generate component names for OpenAI Realtime."""
        result = _compose_provider_components("openai_realtime")
        
        assert result['stt'] == "openai_realtime_stt"
        assert result['llm'] == "openai_realtime_llm"
        assert result['tts'] == "openai_realtime_tts"
        assert result['options'] == {}
    
    def test_deepgram_components(self):
        """Should generate component names for Deepgram."""
        result = _compose_provider_components("deepgram")
        
        assert result['stt'] == "deepgram_stt"
        assert result['llm'] == "deepgram_llm"
        assert result['tts'] == "deepgram_tts"
    
    def test_local_components(self):
        """Should generate component names for local provider."""
        result = _compose_provider_components("local")
        
        assert result['stt'] == "local_stt"
        assert result['llm'] == "local_llm"
        assert result['tts'] == "local_tts"


class TestGenerateDefaultPipeline:
    """Tests for _generate_default_pipeline function."""
    
    def test_creates_default_pipeline_when_missing(self):
        """Should create default pipeline when none exists."""
        config_data = {'default_provider': 'openai_realtime'}
        _generate_default_pipeline(config_data)
        
        assert 'pipelines' in config_data
        assert 'default' in config_data['pipelines']
        assert config_data['pipelines']['default']['stt'] == 'openai_realtime_stt'
        assert config_data['active_pipeline'] == 'default'
    
    def test_uses_default_provider_fallback(self):
        """Should use openai_realtime as fallback provider."""
        config_data = {}
        _generate_default_pipeline(config_data)
        
        assert config_data['pipelines']['default']['stt'] == 'openai_realtime_stt'
    
    def test_converts_string_entry_to_dict(self):
        """Should convert string pipeline entry to dict."""
        config_data = {
            'default_provider': 'openai_realtime',
            'pipelines': {'default': 'deepgram'}
        }
        _generate_default_pipeline(config_data)
        
        # Should convert string to components
        assert config_data['pipelines']['default']['stt'] == 'deepgram_stt'
        assert config_data['pipelines']['default']['llm'] == 'deepgram_llm'
    
    def test_fills_missing_dict_fields(self):
        """Should fill in missing fields in dict entry."""
        config_data = {
            'default_provider': 'openai_realtime',
            'pipelines': {
                'default': {
                    'stt': 'custom_stt'
                }
            }
        }
        _generate_default_pipeline(config_data)
        
        # Should preserve existing stt but fill in llm/tts
        assert config_data['pipelines']['default']['stt'] == 'custom_stt'
        assert config_data['pipelines']['default']['llm'] == 'openai_realtime_llm'
        assert config_data['pipelines']['default']['tts'] == 'openai_realtime_tts'


class TestNormalizePipelines:
    """Tests for normalize_pipelines function."""
    
    def test_empty_pipelines_generates_default(self):
        """Should generate default pipeline when pipelines is empty."""
        config_data = {'default_provider': 'deepgram'}
        normalize_pipelines(config_data)
        
        assert 'pipelines' in config_data
        assert 'default' in config_data['pipelines']
    
    def test_none_entry_uses_default_provider(self):
        """Should use default provider for None entries."""
        config_data = {
            'default_provider': 'openai_realtime',
            'pipelines': {'custom': None}
        }
        normalize_pipelines(config_data)
        
        assert config_data['pipelines']['custom']['stt'] == 'openai_realtime_stt'
    
    def test_string_entry_converts_to_components(self):
        """Should convert string entries to component dicts."""
        config_data = {
            'default_provider': 'openai_realtime',
            'pipelines': {'custom': 'deepgram'}
        }
        normalize_pipelines(config_data)
        
        assert config_data['pipelines']['custom']['stt'] == 'deepgram_stt'
        assert config_data['pipelines']['custom']['llm'] == 'deepgram_llm'
        assert config_data['pipelines']['custom']['tts'] == 'deepgram_tts'
    
    def test_dict_entry_with_provider_hint(self):
        """Should use provider hint for missing components."""
        config_data = {
            'default_provider': 'openai_realtime',
            'pipelines': {
                'custom': {
                    'provider': 'deepgram',
                    'stt': 'custom_stt'
                }
            }
        }
        normalize_pipelines(config_data)
        
        # Should use custom stt but fill llm/tts from provider hint
        assert config_data['pipelines']['custom']['stt'] == 'custom_stt'
        assert config_data['pipelines']['custom']['llm'] == 'deepgram_llm'
        assert config_data['pipelines']['custom']['tts'] == 'deepgram_tts'
    
    def test_dict_entry_with_options(self):
        """Should preserve options block."""
        config_data = {
            'default_provider': 'openai_realtime',
            'pipelines': {
                'custom': {
                    'stt': 'vosk_stt',
                    'llm': 'openai_llm',
                    'tts': 'piper_tts',
                    'options': {
                        'stt': {'model': 'vosk-en'},
                        'llm': {'temperature': 0.7}
                    }
                }
            }
        }
        normalize_pipelines(config_data)
        
        assert config_data['pipelines']['custom']['options']['stt']['model'] == 'vosk-en'
        assert config_data['pipelines']['custom']['options']['llm']['temperature'] == 0.7
    
    def test_sets_active_pipeline_to_first_key(self):
        """Should set active_pipeline to first pipeline key."""
        config_data = {
            'default_provider': 'openai_realtime',
            'pipelines': {'first': 'deepgram', 'second': 'local'}
        }
        normalize_pipelines(config_data)
        
        assert config_data['active_pipeline'] == 'first'
    
    def test_invalid_options_type_raises_error(self):
        """Should raise TypeError for invalid options type."""
        config_data = {
            'default_provider': 'openai_realtime',
            'pipelines': {
                'custom': {
                    'stt': 'vosk_stt',
                    'options': 'not_a_dict'  # Invalid
                }
            }
        }
        
        with pytest.raises(TypeError) as exc_info:
            normalize_pipelines(config_data)
        
        assert 'options type' in str(exc_info.value).lower()
    
    def test_invalid_entry_type_raises_error(self):
        """Should raise TypeError for invalid entry type."""
        config_data = {
            'default_provider': 'openai_realtime',
            'pipelines': {
                'custom': 123  # Invalid type
            }
        }
        
        with pytest.raises(TypeError) as exc_info:
            normalize_pipelines(config_data)
        
        assert 'unsupported pipeline definition' in str(exc_info.value).lower()


class TestNormalizeProfiles:
    """Tests for normalize_profiles function."""
    
    def test_creates_default_telephony_profile(self):
        """Should create default telephony_ulaw_8k profile."""
        config_data = {}
        normalize_profiles(config_data)
        
        assert 'profiles' in config_data
        assert 'telephony_ulaw_8k' in config_data['profiles']
        
        profile = config_data['profiles']['telephony_ulaw_8k']
        assert profile['internal_rate_hz'] == 8000
        assert profile['transport_out']['encoding'] == 'ulaw'
        assert profile['idle_cutoff_ms'] == 1200
        assert profile['output_resampler'] == 'linear'
        assert profile['provider_pref'] == {
            'input_encoding': 'mulaw',
            'input_sample_rate_hz': 8000,
            'output_encoding': 'mulaw',
            'output_sample_rate_hz': 8000,
            'preferred_chunk_ms': 20,
        }

        enhanced = config_data['profiles']['telephony_enhanced_8k']
        assert enhanced['transport_out'] == {'encoding': 'ulaw', 'sample_rate_hz': 8000}
        assert enhanced['output_resampler'] == 'bandlimited'
    
    def test_sets_default_profile_selector(self):
        """Should set default profile selector if missing."""
        config_data = {}
        normalize_profiles(config_data)
        
        assert config_data['profiles']['default'] == 'telephony_ulaw_8k'
    
    def test_preserves_existing_profiles(self):
        """Should preserve existing profiles."""
        config_data = {
            'profiles': {
                'custom_profile': {
                    'internal_rate_hz': 16000
                }
            }
        }
        normalize_profiles(config_data)
        
        # Should preserve custom profile and add default
        assert 'custom_profile' in config_data['profiles']
        assert 'telephony_ulaw_8k' in config_data['profiles']
        assert 'telephony_enhanced_8k' in config_data['profiles']
    
    def test_preserves_existing_default_selector(self):
        """Should preserve existing default selector."""
        config_data = {
            'profiles': {
                'default': 'custom_profile'
            }
        }
        normalize_profiles(config_data)
        
        assert config_data['profiles']['default'] == 'custom_profile'
    
    def test_creates_empty_contexts_block(self):
        """Should create empty contexts block if missing."""
        config_data = {}
        normalize_profiles(config_data)
        
        assert 'contexts' in config_data
        assert config_data['contexts'] == {}
    
    def test_preserves_existing_contexts(self):
        """Should preserve existing contexts."""
        config_data = {
            'contexts': {
                'sales': {'greeting': 'Sales greeting'}
            }
        }
        normalize_profiles(config_data)
        
        assert config_data['contexts']['sales']['greeting'] == 'Sales greeting'


class TestNormalizeLocalProviderTokens:
    """Tests for normalize_local_provider_tokens function."""
    
    def test_extracts_bash_style_defaults(self):
        """Should extract defaults from ${VAR:-default} tokens."""
        config_data = {
            'providers': {
                'local': {
                    'base_url': '${LOCAL_WS_URL:-ws://127.0.0.1:8765}',
                    'ws_url': '${LOCAL_WS_URL:-ws://127.0.0.1:8765}',
                    'auth_token': '${LOCAL_WS_AUTH_TOKEN:-secret-token}',
                    'connect_timeout_sec': '${CONNECT_TIMEOUT:-5.0}',
                    'response_timeout_sec': '${RESPONSE_TIMEOUT:-5.0}',
                    'chunk_ms': '${CHUNK_MS:-200}'
                }
            }
        }
        normalize_local_provider_tokens(config_data)
        
        local = config_data['providers']['local']
        assert local['base_url'] == 'ws://127.0.0.1:8765'
        assert local['ws_url'] == 'ws://127.0.0.1:8765'
        assert local['auth_token'] == 'secret-token'
        assert local['connect_timeout_sec'] == 5.0
        assert local['response_timeout_sec'] == 5.0
        assert local['chunk_ms'] == 200
    
    def test_coerces_numeric_strings_to_types(self):
        """Should coerce numeric strings to float/int."""
        config_data = {
            'providers': {
                'local': {
                    'ws_url': 'ws://localhost:8765',
                    'connect_timeout_sec': '10.5',
                    'response_timeout_sec': '3.0',
                    'chunk_ms': '160'
                }
            }
        }
        normalize_local_provider_tokens(config_data)
        
        local = config_data['providers']['local']
        assert isinstance(local['connect_timeout_sec'], float)
        assert local['connect_timeout_sec'] == 10.5
        assert isinstance(local['response_timeout_sec'], float)
        assert local['response_timeout_sec'] == 3.0
        assert isinstance(local['chunk_ms'], int)
        assert local['chunk_ms'] == 160
    
    def test_uses_defaults_for_invalid_numeric_conversion(self):
        """Should use default values if numeric conversion fails."""
        config_data = {
            'providers': {
                'local': {
                    'ws_url': 'ws://localhost:8765',
                    'connect_timeout_sec': 'invalid',
                    'response_timeout_sec': 'bad',
                    'chunk_ms': 'wrong'
                }
            }
        }
        normalize_local_provider_tokens(config_data)
        
        local = config_data['providers']['local']
        assert local['connect_timeout_sec'] == 5.0
        assert local['response_timeout_sec'] == 5.0
        assert local['chunk_ms'] == 200
    
    def test_handles_missing_local_provider(self):
        """Should handle config without local provider gracefully."""
        config_data = {'providers': {}}
        normalize_local_provider_tokens(config_data)
        
        # Should not raise an error
        assert 'providers' in config_data
    
    def test_handles_missing_providers_block(self):
        """Should handle config without providers block gracefully."""
        config_data = {}
        normalize_local_provider_tokens(config_data)
        
        # Should not raise an error
        assert True  # Just verify no exception
    
    def test_preserves_non_token_values(self):
        """Should preserve values that are not tokens."""
        config_data = {
            'providers': {
                'local': {
                    'ws_url': 'ws://custom:9999',
                    'connect_timeout_sec': 15.0,
                    'response_timeout_sec': 8.0,
                    'chunk_ms': 300
                }
            }
        }
        normalize_local_provider_tokens(config_data)
        
        local = config_data['providers']['local']
        assert local['ws_url'] == 'ws://custom:9999'
        assert local['connect_timeout_sec'] == 15.0
        assert local['response_timeout_sec'] == 8.0
        assert local['chunk_ms'] == 300
