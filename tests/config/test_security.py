"""
Unit tests for config.security module.

Tests cover:
- Asterisk credential injection (environment variables only)
- LLM config merge (YAML + environment variables)
- Provider API key injection
- String token expansion
"""

import os
import pytest

from src.config.security import (
    _is_nonempty_string,
    expand_string_tokens,
    inject_asterisk_credentials,
    inject_llm_config,
    inject_provider_api_keys,
)


class TestIsNonemptyString:
    """Tests for _is_nonempty_string helper."""
    
    def test_valid_string_returns_true(self):
        """Non-empty string should return True."""
        assert _is_nonempty_string("hello") is True
        assert _is_nonempty_string("test value") is True
    
    def test_empty_string_returns_false(self):
        """Empty string should return False."""
        assert _is_nonempty_string("") is False
    
    def test_whitespace_only_returns_false(self):
        """Whitespace-only string should return False."""
        assert _is_nonempty_string("   ") is False
        assert _is_nonempty_string("\t\n") is False
    
    def test_non_string_returns_false(self):
        """Non-string values should return False."""
        assert _is_nonempty_string(None) is False
        assert _is_nonempty_string(42) is False
        assert _is_nonempty_string([]) is False
        assert _is_nonempty_string({}) is False


class TestExpandStringTokens:
    """Tests for expand_string_tokens function."""
    
    def test_expand_dollar_brace(self, monkeypatch):
        """Should expand ${VAR} tokens."""
        monkeypatch.setenv("TEST_VAR", "test_value")
        result = expand_string_tokens("prefix_${TEST_VAR}_suffix")
        assert result == "prefix_test_value_suffix"
    
    def test_expand_dollar_only(self, monkeypatch):
        """Should expand $VAR tokens."""
        monkeypatch.setenv("MY_VAR", "my_value")
        result = expand_string_tokens("value: $MY_VAR")
        assert result == "value: my_value"
    
    def test_undefined_var_left_unchanged(self):
        """Undefined variables should be left unchanged."""
        result = expand_string_tokens("${UNDEFINED_VAR}")
        assert result == "${UNDEFINED_VAR}"
    
    def test_empty_string_returns_empty(self):
        """Empty string should return empty string."""
        result = expand_string_tokens("")
        assert result == ""
    
    def test_none_returns_empty(self):
        """None should return empty string."""
        result = expand_string_tokens(None)
        assert result == ""


class TestInjectAsteriskCredentials:
    """Tests for inject_asterisk_credentials function."""
    
    def test_inject_credentials_from_env(self, monkeypatch):
        """Should inject Asterisk credentials from environment."""
        monkeypatch.setenv("ASTERISK_HOST", "192.168.1.10")
        monkeypatch.setenv("ASTERISK_ARI_USERNAME", "test_user")
        monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "test_pass")
        
        config_data = {}
        inject_asterisk_credentials(config_data)
        
        assert config_data['asterisk']['host'] == "192.168.1.10"
        assert config_data['asterisk']['username'] == "test_user"
        assert config_data['asterisk']['password'] == "test_pass"
        assert config_data['asterisk']['app_name'] == "asterisk-ai-voice-agent"
    
    def test_use_ari_prefix_fallback(self, monkeypatch):
        """Should fall back to ARI_ prefix for username/password."""
        monkeypatch.setenv("ARI_USERNAME", "fallback_user")
        monkeypatch.setenv("ARI_PASSWORD", "fallback_pass")
        
        config_data = {}
        inject_asterisk_credentials(config_data)
        
        assert config_data['asterisk']['username'] == "fallback_user"
        assert config_data['asterisk']['password'] == "fallback_pass"
    
    def test_default_host_if_not_set(self):
        """Should use 127.0.0.1 as default host."""
        config_data = {}
        inject_asterisk_credentials(config_data)
        
        assert config_data['asterisk']['host'] == "127.0.0.1"
    
    def test_preserve_app_name_from_yaml(self):
        """Should preserve app_name from YAML if present."""
        config_data = {
            'asterisk': {
                'app_name': 'custom-app-name'
            }
        }
        inject_asterisk_credentials(config_data)
        
        assert config_data['asterisk']['app_name'] == 'custom-app-name'
    
    def test_overwrite_yaml_credentials(self, monkeypatch):
        """SECURITY: Should overwrite YAML credentials with env vars."""
        monkeypatch.setenv("ASTERISK_HOST", "env_host")
        monkeypatch.setenv("ASTERISK_ARI_USERNAME", "env_user")
        monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "env_pass")
        
        config_data = {
            'asterisk': {
                'host': 'yaml_host',
                'username': 'yaml_user',
                'password': 'yaml_pass'
            }
        }
        inject_asterisk_credentials(config_data)
        
        # Environment variables should take precedence
        assert config_data['asterisk']['host'] == "env_host"
        assert config_data['asterisk']['username'] == "env_user"
        assert config_data['asterisk']['password'] == "env_pass"


class TestInjectLlmConfig:
    """Tests for inject_llm_config function."""
    
    def test_use_yaml_values_when_present(self):
        """Should use YAML values when they are non-empty."""
        config_data = {
            'llm': {
                'initial_greeting': 'YAML greeting',
                'prompt': 'YAML prompt',
                'model': 'gpt-4'
            }
        }
        inject_llm_config(config_data)
        
        assert config_data['llm']['initial_greeting'] == 'YAML greeting'
        assert config_data['llm']['prompt'] == 'YAML prompt'
        assert config_data['llm']['model'] == 'gpt-4'
    
    def test_fallback_to_env_vars(self, monkeypatch):
        """Should fall back to environment variables when YAML is empty."""
        monkeypatch.setenv("GREETING", "Env greeting")
        monkeypatch.setenv("AI_ROLE", "Env role")
        
        config_data = {'llm': {}}
        inject_llm_config(config_data)
        
        assert config_data['llm']['initial_greeting'] == "Env greeting"
        assert config_data['llm']['prompt'] == "Env role"
    
    def test_use_hardcoded_defaults(self):
        """Should use hardcoded defaults when neither YAML nor env is set."""
        config_data = {}
        inject_llm_config(config_data)
        
        assert config_data['llm']['initial_greeting'] == "Hello, how can I help you?"
        assert config_data['llm']['prompt'] == "You are a helpful assistant."
        assert config_data['llm']['model'] == "gpt-4o"
    
    def test_api_key_from_env_only(self, monkeypatch):
        """SECURITY: API key should ONLY come from environment."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key")
        
        config_data = {
            'llm': {
                'api_key': 'sk-yaml-key'  # This should be ignored
            }
        }
        inject_llm_config(config_data)
        
        # Environment variable should take precedence
        assert config_data['llm']['api_key'] == "sk-env-key"
    
    def test_expand_env_tokens_in_greeting(self, monkeypatch):
        """Should expand ${VAR} tokens in greeting."""
        monkeypatch.setenv("COMPANY_NAME", "Acme Corp")
        
        config_data = {
            'llm': {
                'initial_greeting': 'Welcome to ${COMPANY_NAME}!'
            }
        }
        inject_llm_config(config_data)
        
        assert config_data['llm']['initial_greeting'] == "Welcome to Acme Corp!"
    
    def test_expand_env_tokens_in_prompt(self, monkeypatch):
        """Should expand ${VAR} tokens in prompt."""
        monkeypatch.setenv("AGENT_NAME", "Ava")
        
        config_data = {
            'llm': {
                'prompt': 'You are ${AGENT_NAME}, a helpful assistant.'
            }
        }
        inject_llm_config(config_data)
        
        assert config_data['llm']['prompt'] == "You are Ava, a helpful assistant."


class TestInjectProviderApiKeys:
    """Tests for inject_provider_api_keys function."""
    
    def test_inject_openai_key(self, monkeypatch):
        """Should inject OpenAI API key from environment."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
        
        config_data = {'providers': {'openai': {}}}
        inject_provider_api_keys(config_data)
        
        assert config_data['providers']['openai']['api_key'] == "sk-openai-test"
    
    def test_inject_deepgram_key(self, monkeypatch):
        """Should inject Deepgram API key from environment."""
        monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test-key")
        
        config_data = {'providers': {'deepgram': {}}}
        inject_provider_api_keys(config_data)
        
        assert config_data['providers']['deepgram']['api_key'] == "dg-test-key"
    
    def test_inject_google_key(self, monkeypatch):
        """Should inject Google API key from environment."""
        monkeypatch.setenv("GOOGLE_API_KEY", "google-test-key")
        
        config_data = {'providers': {'google_live': {}}}
        inject_provider_api_keys(config_data)
        
        assert config_data['providers']['google_live']['api_key'] == "google-test-key"
    
    def test_inject_multiple_keys(self, monkeypatch):
        """Should inject multiple provider keys at once."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-key")
        monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
        
        config_data = {
            'providers': {
                'openai': {},
                'deepgram': {},
                'google_live': {}
            }
        }
        inject_provider_api_keys(config_data)
        
        assert config_data['providers']['openai']['api_key'] == "sk-openai"
        assert config_data['providers']['deepgram']['api_key'] == "dg-key"
        assert config_data['providers']['google_live']['api_key'] == "google-key"
    
    def test_handle_missing_providers_block(self):
        """Should handle config without providers block gracefully."""
        config_data = {}
        inject_provider_api_keys(config_data)
        
        # Should not raise an error
        assert 'providers' in config_data
    
    def test_overwrite_yaml_api_keys(self, monkeypatch):
        """SECURITY: Should overwrite YAML API keys with env vars."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key")
        
        config_data = {
            'providers': {
                'openai': {
                    'api_key': 'sk-yaml-key'  # This should be overwritten
                }
            }
        }
        inject_provider_api_keys(config_data)

        # Environment variable should take precedence
        assert config_data['providers']['openai']['api_key'] == "sk-env-key"

    def test_strip_inline_deepgram_key_when_env_unset(self, monkeypatch):
        """SECURITY: inline deepgram api_key is stripped when env var is unset.

        Provider keys come only from env; a YAML-embedded api_key must never
        become an active credential.
        """
        monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)

        config_data = {'providers': {'deepgram': {'api_key': 'dg-yaml-key'}}}
        inject_provider_api_keys(config_data)

        assert 'api_key' not in config_data['providers']['deepgram']

    def test_strip_inline_google_live_key_when_env_unset(self, monkeypatch):
        """SECURITY: inline google_live api_key is stripped when env var unset."""
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

        config_data = {'providers': {'google_live': {'api_key': 'g-yaml-key'}}}
        inject_provider_api_keys(config_data)

        assert 'api_key' not in config_data['providers']['google_live']


class TestEnvOnlyKeyStrippingAllProviders:
    """Finding 3: EVERY provider this function handles strips an inline api_key when
    its env var is unset (env-only contract), injects it when set, and NEVER touches
    the file-backed api_key_file / agent_id_file credential fields."""

    # provider key, instance config, env var name
    _CASES = [
        ("openai", {}, "OPENAI_API_KEY"),
        ("openai_realtime", {}, "OPENAI_API_KEY"),
        ("groq_llm", {}, "GROQ_API_KEY"),
        ("minimax_llm", {}, "MINIMAX_API_KEY"),
        ("telnyx_llm", {}, "TELNYX_API_KEY"),
        ("azure_stt", {}, "AZURE_SPEECH_KEY"),
        ("grok", {}, "XAI_API_KEY"),
        ("elevenlabs_agent", {}, "ELEVENLABS_API_KEY"),
    ]

    @pytest.mark.parametrize("pkey,extra,env_name", _CASES)
    def test_env_set_injects_inline_key(self, monkeypatch, pkey, extra, env_name):
        # Other provider env vars must not leak into this instance.
        for _, _, e in self._CASES:
            monkeypatch.delenv(e, raising=False)
        monkeypatch.setenv(env_name, "from-env")
        cfg = {'providers': {pkey: dict(extra)}}
        inject_provider_api_keys(cfg)
        assert cfg['providers'][pkey].get('api_key') == "from-env"

    @pytest.mark.parametrize("pkey,extra,env_name", _CASES)
    def test_env_unset_strips_inline_key(self, monkeypatch, pkey, extra, env_name):
        for _, _, e in self._CASES:
            monkeypatch.delenv(e, raising=False)
        cfg = {'providers': {pkey: {**extra, 'api_key': 'yaml-literal'}}}
        inject_provider_api_keys(cfg)
        assert 'api_key' not in cfg['providers'][pkey]

    @pytest.mark.parametrize("pkey,extra,env_name", _CASES)
    def test_api_key_file_preserved_when_env_unset(self, monkeypatch, pkey, extra, env_name):
        # SAFETY: only the inline literal is popped; the file-backed credential field
        # survives so resolve_secret_value can read it in the builder chain.
        for _, _, e in self._CASES:
            monkeypatch.delenv(e, raising=False)
        cfg = {'providers': {pkey: {**extra, 'api_key': 'yaml-literal',
                                    'api_key_file': '/run/secrets/key',
                                    'api_key_env': 'CUSTOM_KEY_ENV'}}}
        inject_provider_api_keys(cfg)
        assert 'api_key' not in cfg['providers'][pkey]
        assert cfg['providers'][pkey]['api_key_file'] == '/run/secrets/key'
        assert cfg['providers'][pkey]['api_key_env'] == 'CUSTOM_KEY_ENV'

    def test_multi_instance_blocks_all_handled(self, monkeypatch):
        # Multi-instance / custom_<provider> blocks of the same kind are ALL handled,
        # not just the canonical key.
        for _, _, e in self._CASES:
            monkeypatch.delenv(e, raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        cfg = {'providers': {
            'openai_llm': {},
            'custom_openai': {'type': 'openai', 'base_url': 'https://api.openai.com/v1'},
            'openai_realtime': {'api_key': 'should-be-replaced'},
        }}
        inject_provider_api_keys(cfg)
        assert cfg['providers']['openai_llm']['api_key'] == "k"
        assert cfg['providers']['custom_openai']['api_key'] == "k"
        assert cfg['providers']['openai_realtime']['api_key'] == "k"

    def test_multi_instance_google_live_blocks_all_handled(self, monkeypatch):
        # Codex P2: a custom multi-instance google_live block (matched by name suffix
        # or by type) must follow the env-only contract just like the canonical block.
        for _, _, e in self._CASES:
            monkeypatch.delenv(e, raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "g-from-env")
        cfg = {'providers': {
            'google_live': {'api_key': 'should-be-replaced'},
            'acme_google_live': {'api_key': 'should-be-replaced'},
            'custom_voice': {'type': 'google_live', 'api_key': 'should-be-replaced'},
        }}
        inject_provider_api_keys(cfg)
        assert cfg['providers']['google_live']['api_key'] == "g-from-env"
        assert cfg['providers']['acme_google_live']['api_key'] == "g-from-env"
        assert cfg['providers']['custom_voice']['api_key'] == "g-from-env"

    def test_multi_instance_google_live_inline_key_stripped_when_env_unset(self, monkeypatch):
        # Custom google_live instance keeps no inline key when GOOGLE_API_KEY is unset,
        # but its file-backed api_key_file survives.
        for _, _, e in self._CASES:
            monkeypatch.delenv(e, raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        cfg = {'providers': {
            'acme_google_live': {'api_key': 'yaml-literal',
                                 'api_key_file': '/run/secrets/google'},
            'custom_voice': {'type': 'google_live', 'api_key': 'yaml-literal'},
        }}
        inject_provider_api_keys(cfg)
        assert 'api_key' not in cfg['providers']['acme_google_live']
        assert cfg['providers']['acme_google_live']['api_key_file'] == '/run/secrets/google'
        assert 'api_key' not in cfg['providers']['custom_voice']

    def test_elevenlabs_agent_id_env_only(self, monkeypatch):
        # Finding 3: inline agent_id follows the same env-only contract as api_key,
        # but agent_id_file is preserved.
        for _, _, e in self._CASES:
            monkeypatch.delenv(e, raising=False)
        monkeypatch.delenv("ELEVENLABS_AGENT_ID", raising=False)
        cfg = {'providers': {'elevenlabs_agent': {
            'api_key': 'yaml-key', 'agent_id': 'yaml-agent',
            'agent_id_file': '/run/secrets/agent'}}}
        inject_provider_api_keys(cfg)
        assert 'api_key' not in cfg['providers']['elevenlabs_agent']
        assert 'agent_id' not in cfg['providers']['elevenlabs_agent']
        assert cfg['providers']['elevenlabs_agent']['agent_id_file'] == '/run/secrets/agent'

    def test_elevenlabs_agent_id_injected_when_env_set(self, monkeypatch):
        for _, _, e in self._CASES:
            monkeypatch.delenv(e, raising=False)
        monkeypatch.setenv("ELEVENLABS_AGENT_ID", "agent-from-env")
        cfg = {'providers': {'elevenlabs_agent': {}}}
        inject_provider_api_keys(cfg)
        assert cfg['providers']['elevenlabs_agent']['agent_id'] == "agent-from-env"


class TestCustomTypedInstancesDataDriven:
    """The env-only contract is data-driven by ``type``: a custom-named instance
    identified ONLY by its ``type`` field (e.g. ``globex_openai_realtime``) must be
    injected/stripped exactly like the canonical block, with file-backed credential
    fields preserved. This ends the per-provider whack-a-mole (Codex re-review)."""

    # custom instance key, type, env var name. Each block is named so it does NOT
    # match by name prefix — only ``type`` can identify it, exercising the loop.
    # NOTE: bare ``type: openai`` is intentionally host-gated (must point at
    # api.openai.com) to avoid stomping OpenAI-compatible providers, so it is
    # covered separately below rather than in this name-free list.
    _TYPE_CASES = [
        ("globex_openai_realtime", "openai_realtime", "OPENAI_API_KEY"),
        ("acme_groq", "groq", "GROQ_API_KEY"),
        ("acme_minimax", "minimax", "MINIMAX_API_KEY"),
        ("acme_telnyx", "telnyx", "TELNYX_API_KEY"),
        ("acme_azure", "azure", "AZURE_SPEECH_KEY"),
        ("acme_grok", "grok", "XAI_API_KEY"),
        ("acme_xai", "xai", "XAI_API_KEY"),
        ("acme_eleven", "elevenlabs", "ELEVENLABS_API_KEY"),
        ("acme_eleven_agent", "elevenlabs_agent", "ELEVENLABS_API_KEY"),
        ("acme_deepgram", "deepgram", "DEEPGRAM_API_KEY"),
        ("acme_google", "google_live", "GOOGLE_API_KEY"),
    ]

    _ALL_ENVS = sorted({e for _, _, e in _TYPE_CASES})

    def _clear(self, monkeypatch):
        for e in self._ALL_ENVS:
            monkeypatch.delenv(e, raising=False)
        monkeypatch.delenv("ELEVENLABS_AGENT_ID", raising=False)

    @pytest.mark.parametrize("pkey,ptype,env_name", _TYPE_CASES)
    def test_custom_type_block_env_unset_strips_inline_key(self, monkeypatch, pkey, ptype, env_name):
        self._clear(monkeypatch)
        cfg = {'providers': {pkey: {'type': ptype, 'api_key': 'yaml-literal'}}}
        inject_provider_api_keys(cfg)
        assert 'api_key' not in cfg['providers'][pkey]

    @pytest.mark.parametrize("pkey,ptype,env_name", _TYPE_CASES)
    def test_custom_type_block_env_set_injects_inline_key(self, monkeypatch, pkey, ptype, env_name):
        self._clear(monkeypatch)
        monkeypatch.setenv(env_name, "from-env")
        cfg = {'providers': {pkey: {'type': ptype}}}
        inject_provider_api_keys(cfg)
        assert cfg['providers'][pkey].get('api_key') == "from-env"

    @pytest.mark.parametrize("pkey,ptype,env_name", _TYPE_CASES)
    def test_custom_type_block_api_key_file_preserved(self, monkeypatch, pkey, ptype, env_name):
        self._clear(monkeypatch)
        cfg = {'providers': {pkey: {'type': ptype, 'api_key': 'yaml-literal',
                                    'api_key_file': '/run/secrets/key',
                                    'api_key_env': 'CUSTOM_ENV'}}}
        inject_provider_api_keys(cfg)
        assert 'api_key' not in cfg['providers'][pkey]
        assert cfg['providers'][pkey]['api_key_file'] == '/run/secrets/key'
        assert cfg['providers'][pkey]['api_key_env'] == 'CUSTOM_ENV'

    def test_globex_openai_realtime_by_type_roundtrip(self, monkeypatch):
        # The exact Codex finding: a block named globex_openai_realtime (no openai
        # name prefix until the underscore form, but identified by type) follows the
        # env-only contract. api_key_file is never touched.
        self._clear(monkeypatch)
        cfg = {'providers': {'globex_openai_realtime': {
            'type': 'openai_realtime', 'api_key': 'yaml-literal',
            'api_key_file': '/run/secrets/openai'}}}
        inject_provider_api_keys(cfg)
        assert 'api_key' not in cfg['providers']['globex_openai_realtime']
        assert cfg['providers']['globex_openai_realtime']['api_key_file'] == '/run/secrets/openai'

        monkeypatch.setenv("OPENAI_API_KEY", "k")
        cfg2 = {'providers': {'globex_openai_realtime': {
            'type': 'openai_realtime', 'api_key_file': '/run/secrets/openai'}}}
        inject_provider_api_keys(cfg2)
        assert cfg2['providers']['globex_openai_realtime']['api_key'] == "k"
        assert cfg2['providers']['globex_openai_realtime']['api_key_file'] == '/run/secrets/openai'

    def test_custom_type_openai_compatible_host_not_stomped(self, monkeypatch):
        # A custom block declared type: openai but pointing at a non-OpenAI host
        # (e.g. an OpenAI-compatible gateway) is NOT touched by the openai family
        # when OPENAI_API_KEY is unset — its inline literal survives, matching the
        # pre-refactor host-gating behavior.
        self._clear(monkeypatch)
        cfg = {'providers': {'gateway': {
            'type': 'openai', 'chat_base_url': 'https://openrouter.ai/api/v1',
            'api_key': 'inline-literal'}}}
        inject_provider_api_keys(cfg)
        assert cfg['providers']['gateway']['api_key'] == 'inline-literal'

    def test_custom_type_openai_with_openai_host_follows_contract(self, monkeypatch):
        # A custom block declared type: openai pointing at api.openai.com IS in the
        # openai family (host-gated match): inline literal stripped when env unset,
        # injected when set; api_key_file preserved.
        self._clear(monkeypatch)
        cfg = {'providers': {'gw': {
            'type': 'openai', 'base_url': 'https://api.openai.com/v1',
            'api_key': 'lit', 'api_key_file': '/run/secrets/openai'}}}
        inject_provider_api_keys(cfg)
        assert 'api_key' not in cfg['providers']['gw']
        assert cfg['providers']['gw']['api_key_file'] == '/run/secrets/openai'

        monkeypatch.setenv("OPENAI_API_KEY", "k")
        cfg2 = {'providers': {'gw': {
            'type': 'openai', 'base_url': 'https://api.openai.com/v1'}}}
        inject_provider_api_keys(cfg2)
        assert cfg2['providers']['gw']['api_key'] == "k"
