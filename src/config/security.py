"""
Security-critical configuration injection.

This module handles:
- Asterisk credentials (ONLY from environment variables)
- LLM configuration merge (YAML + environment variables)
- Provider API key injection (ONLY from environment variables)
- Environment variable token expansion

SECURITY POLICY:
- API keys and passwords MUST NEVER be in YAML files
- All credentials MUST come from environment variables only
- This separation prevents accidental credential exposure in version control
"""

import os
from typing import Any, Dict
from urllib.parse import urlparse


def _url_host(url: Any) -> str:
    try:
        return (urlparse(str(url)).hostname or "").lower()
    except Exception:
        return ""


def _is_nonempty_string(val: Any) -> bool:
    """
    Check if value is a non-empty string.
    
    Args:
        val: Value to check
        
    Returns:
        True if val is a string with non-whitespace content
        
    Complexity: 2
    """
    return isinstance(val, str) and val.strip() != ""


def expand_string_tokens(value: str) -> str:
    """
    Expand environment variable tokens in a string.
    
    Supports ${VAR} and $VAR syntax. If variable is undefined,
    it is left unchanged.
    
    Args:
        value: String that may contain ${VAR} or $VAR tokens
        
    Returns:
        String with environment variables expanded
        
    Complexity: 2
    """
    try:
        return os.path.expandvars(value or "")
    except Exception:
        return value or ""


def inject_asterisk_credentials(config_data: Dict[str, Any]) -> None:
    """
    Inject Asterisk credentials from environment variables ONLY.
    
    SECURITY: Credentials must NEVER be in YAML files.
    This function overwrites any YAML values with environment variables.
    
    Environment variables:
    - ASTERISK_HOST (default: 127.0.0.1)
    - ASTERISK_ARI_PORT (default: 8088)
    - ASTERISK_ARI_SCHEME (default: http, use https for WSS)
    - ASTERISK_ARI_SSL_VERIFY (default: true, set to false to skip SSL cert verification)
    - ASTERISK_ARI_USERNAME or ARI_USERNAME (required)
    - ASTERISK_ARI_PASSWORD or ARI_PASSWORD (required)
    
    Args:
        config_data: Configuration dictionary to modify in-place
        
    Complexity: 2
    """
    asterisk_yaml = (config_data.get('asterisk') or {}) if isinstance(config_data.get('asterisk'), dict) else {}
    
    # Parse ssl_verify from env (accepts true/false/1/0)
    ssl_verify_str = os.getenv("ASTERISK_ARI_SSL_VERIFY", "true").lower()
    ssl_verify = ssl_verify_str not in ("false", "0", "no")
    
    config_data['asterisk'] = {
        "host": os.getenv("ASTERISK_HOST", "127.0.0.1"),
        "port": int(os.getenv("ASTERISK_ARI_PORT", "8088")),
        "scheme": os.getenv("ASTERISK_ARI_SCHEME", "http"),
        "ssl_verify": ssl_verify,
        "username": os.getenv("ASTERISK_ARI_USERNAME") or os.getenv("ARI_USERNAME"),
        "password": os.getenv("ASTERISK_ARI_PASSWORD") or os.getenv("ARI_PASSWORD"),
        "app_name": asterisk_yaml.get("app_name", "asterisk-ai-voice-agent")
    }


def inject_llm_config(config_data: Dict[str, Any]) -> None:
    """
    Merge LLM configuration from YAML and environment variables.
    
    Precedence: YAML llm.* (if non-empty) > env vars > hardcoded defaults
    
    SECURITY: API keys ONLY from environment variables.
    
    Environment variables:
    - GREETING: Initial greeting (fallback)
    - AI_ROLE: System prompt/persona (fallback)
    - OPENAI_API_KEY: API key (REQUIRED, overrides YAML)
    
    Args:
        config_data: Configuration dictionary to modify in-place
        
    Complexity: 5
    """
    llm_yaml = (config_data.get('llm') or {}) if isinstance(config_data.get('llm'), dict) else {}
    
    # Resolve initial_greeting
    initial_greeting = llm_yaml.get('initial_greeting')
    if not _is_nonempty_string(initial_greeting):
        initial_greeting = os.getenv("GREETING", "Hello, how can I help you?")
    
    # Resolve prompt/persona
    prompt_val = llm_yaml.get('prompt')
    if not _is_nonempty_string(prompt_val):
        prompt_val = os.getenv("AI_ROLE", "You are a helpful assistant.")
    
    # Resolve model
    model_val = llm_yaml.get('model') or "gpt-4o"
    
    # SECURITY: API keys ONLY from environment variables, never YAML
    api_key_val = os.getenv("OPENAI_API_KEY")
    
    # Apply environment variable interpolation to support ${VAR} placeholders
    initial_greeting = expand_string_tokens(initial_greeting)
    prompt_val = expand_string_tokens(prompt_val)
    
    config_data['llm'] = {
        "initial_greeting": initial_greeting,
        "prompt": prompt_val,
        "model": model_val,
        "api_key": api_key_val,
    }


def inject_provider_api_keys(config_data: Dict[str, Any]) -> None:
    """
    Inject provider API keys from environment variables ONLY.
    
    SECURITY: API keys must ONLY come from environment variables, never YAML.
    This function is specifically for pipeline adapters that need explicit API keys.
    
    Environment variables:
    - OPENAI_API_KEY: OpenAI provider API key
    - GROQ_API_KEY: Groq provider API key (Groq Speech + Groq OpenAI-compatible LLM)
    - DEEPGRAM_API_KEY: Deepgram provider API key
    - GOOGLE_API_KEY: Google provider API key
    - TELNYX_API_KEY: Telnyx AI Inference API key (OpenAI-compatible LLM)
    
    Args:
        config_data: Configuration dictionary to modify in-place
        
    Complexity: 4
    """
    try:
        providers_block = config_data.get('providers', {}) or {}
        
        # Inject OPENAI_API_KEY for OpenAI provider blocks (openai_llm/openai_stt/openai_tts/openai_realtime, etc.)
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            for provider_name, provider_cfg in list(providers_block.items()):
                if not isinstance(provider_cfg, dict):
                    continue
                name_lower = str(provider_name).lower()
                cfg_type = str(provider_cfg.get("type", "")).lower()
                if not (name_lower.startswith("openai") or cfg_type == "openai"):
                    continue

                url_fields = ("chat_base_url", "tts_base_url", "realtime_base_url", "base_url", "ws_url")
                url_hosts = {_url_host(provider_cfg.get(field, "")) for field in url_fields}

                # If the provider is explicitly named openai*, always inject. If it's only "type: openai",
                # inject only when it's actually pointing at OpenAI endpoints to avoid stomping other
                # OpenAI-compatible providers (e.g., Groq/OpenRouter/etc).
                is_openai_host = any(host == "api.openai.com" for host in url_hosts)
                if name_lower.startswith("openai") or is_openai_host:
                    provider_cfg["api_key"] = openai_key
                    providers_block[provider_name] = provider_cfg

        # Inject GROQ_API_KEY for any groq* provider blocks (groq_llm, groq_stt, groq_tts, etc.)
        groq_key = os.getenv('GROQ_API_KEY')
        if groq_key:
            for provider_name, provider_cfg in list(providers_block.items()):
                if not isinstance(provider_cfg, dict):
                    continue
                name_lower = str(provider_name).lower()
                cfg_type = str(provider_cfg.get("type", "")).lower()
                chat_host = _url_host(provider_cfg.get("chat_base_url", ""))
                if name_lower.startswith("groq") or cfg_type == "groq" or chat_host == "api.groq.com":
                    provider_cfg["api_key"] = groq_key
                    providers_block[provider_name] = provider_cfg

        # Inject TELNYX_API_KEY for any telnyx* provider blocks (telnyx_llm, etc.)
        telnyx_key = os.getenv("TELNYX_API_KEY")
        if telnyx_key:
            for provider_name, provider_cfg in list(providers_block.items()):
                if not isinstance(provider_cfg, dict):
                    continue
                name_lower = str(provider_name).lower()
                chat_host = _url_host(provider_cfg.get("chat_base_url", "") or provider_cfg.get("base_url", ""))
                if name_lower.startswith(("telnyx", "telenyx")) or chat_host == "api.telnyx.com":
                    provider_cfg["api_key"] = telnyx_key
                    providers_block[provider_name] = provider_cfg
        
        # Inject DEEPGRAM_API_KEY
        deepgram_block = providers_block.get('deepgram', {}) or {}
        if isinstance(deepgram_block, dict):
            deepgram_block['api_key'] = os.getenv('DEEPGRAM_API_KEY')
            providers_block['deepgram'] = deepgram_block
        
        # Inject GOOGLE_API_KEY (for google_live provider)
        google_live_block = providers_block.get('google_live', {}) or {}
        if isinstance(google_live_block, dict):
            google_live_block['api_key'] = os.getenv('GOOGLE_API_KEY')
            providers_block['google_live'] = google_live_block
        
        config_data['providers'] = providers_block
    except Exception:
        # Non-fatal; Pydantic may still raise if keys are missing
        pass
