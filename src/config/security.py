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


def _matches_provider_family(
    name_lower: str,
    cfg_type: str,
    provider_cfg: Dict[str, Any],
    spec: Dict[str, Any],
) -> bool:
    """Return True if a provider instance belongs to ``spec``'s family.

    A block matches by (in priority order):
      - ``type``: cfg_type in spec["types"]
      - name: name_lower starts with any prefix in spec["name_prefixes"], OR
        equals any name in spec["name_exact"], OR ends with any suffix in
        spec["name_suffixes"]
      - host: any of the block's URL fields resolves to a host in spec["hosts"]

    ``type``-only families (e.g. a custom ``type: openai`` block) may carry a
    ``host_gate`` host set: such a block matches *only* when one of its URL
    fields points at that host, so we never stomp OpenAI-compatible providers
    (Groq/OpenRouter/etc.) that legitimately use ``type: openai`` semantics.
    """
    if cfg_type and cfg_type in spec["types"]:
        # type matches directly. For families with a host_gate, a bare type
        # match still counts ONLY for the canonical type names; the gate only
        # applies to ambiguous shared types (handled via "gated_types").
        if cfg_type in spec.get("gated_types", set()):
            hosts = {_url_host(provider_cfg.get(f, "")) for f in spec["url_fields"]}
            if hosts & spec["hosts"]:
                return True
        else:
            return True
    for prefix in spec.get("name_prefixes", ()):  # name-based match
        if name_lower.startswith(prefix):
            return True
    if name_lower in spec.get("name_exact", ()):  # exact-name match
        return True
    for suffix in spec.get("name_suffixes", ()):  # suffix match (e.g. *_google_live)
        if name_lower.endswith(suffix):
            return True
    if spec.get("hosts"):  # host-based match across the family's URL fields
        hosts = {_url_host(provider_cfg.get(f, "")) for f in spec["url_fields"]}
        if hosts & spec["hosts"]:
            return True
    return False


# Data-driven provider-type registry. Each family declares the env var that
# supplies its inline secret(s) plus the matchers that identify its instances.
# A single pass over ``providers_block`` (below) applies the same env-only
# contract to EVERY matching instance — canonical, multi-instance, or custom
# ``type:`` block — so newly-added types are covered by adding a row here, not
# another bespoke loop.
#
# secret_fields: env var -> inline literal field popped/injected for this family.
# url_fields:    URL keys inspected for host-based matching.
# hosts:         host set that triggers a host-based match.
# gated_types:   types that match ONLY when a URL field hits ``hosts`` (shared
#                OpenAI-compatible "type: openai" must point at api.openai.com).
PROVIDER_KEY_FAMILIES = [
    {
        "name": "openai",
        "secret_fields": {"OPENAI_API_KEY": "api_key"},
        "types": {"openai", "openai_realtime"},
        "gated_types": {"openai"},
        "name_prefixes": ("openai",),
        "url_fields": ("chat_base_url", "tts_base_url", "realtime_base_url", "base_url", "ws_url"),
        "hosts": {"api.openai.com"},
    },
    {
        "name": "groq",
        "secret_fields": {"GROQ_API_KEY": "api_key"},
        "types": {"groq"},
        "name_prefixes": ("groq",),
        "url_fields": ("chat_base_url",),
        "hosts": {"api.groq.com"},
    },
    {
        "name": "minimax",
        "secret_fields": {"MINIMAX_API_KEY": "api_key"},
        "types": {"minimax"},
        "name_prefixes": ("minimax",),
        "url_fields": ("chat_base_url", "base_url"),
        "hosts": {"api.minimax.io", "api.minimaxi.com"},
    },
    {
        "name": "telnyx",
        "secret_fields": {"TELNYX_API_KEY": "api_key"},
        "types": {"telnyx"},
        "name_prefixes": ("telnyx", "telenyx"),
        "url_fields": ("chat_base_url", "base_url"),
        "hosts": {"api.telnyx.com"},
    },
    {
        "name": "azure",
        "secret_fields": {"AZURE_SPEECH_KEY": "api_key"},
        "types": {"azure"},
        "name_prefixes": ("azure_stt",),
        "name_exact": ("azure_tts",),
        "url_fields": (),
        "hosts": set(),
    },
    {
        "name": "grok",
        "secret_fields": {"XAI_API_KEY": "api_key"},
        "types": {"grok", "xai"},
        "name_prefixes": ("grok", "xai"),
        "url_fields": (),
        "hosts": set(),
    },
    {
        "name": "elevenlabs",
        # agent_id is treated like a secret here (env-only) per Finding 3.
        "secret_fields": {"ELEVENLABS_API_KEY": "api_key", "ELEVENLABS_AGENT_ID": "agent_id"},
        "types": {"elevenlabs", "elevenlabs_agent"},
        "name_prefixes": ("elevenlabs",),
        "url_fields": (),
        "hosts": set(),
    },
    {
        "name": "deepgram",
        "secret_fields": {"DEEPGRAM_API_KEY": "api_key"},
        "types": {"deepgram"},
        "name_prefixes": ("deepgram",),
        "url_fields": (),
        "hosts": set(),
    },
    {
        "name": "google_live",
        "secret_fields": {"GOOGLE_API_KEY": "api_key"},
        "types": {"google_live"},
        "name_prefixes": ("google_live",),
        "name_suffixes": ("_google_live",),
        "url_fields": (),
        "hosts": set(),
    },
]


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
    - AZURE_SPEECH_KEY: Microsoft Azure Speech Service key (azure_stt, azure_tts)
    - XAI_API_KEY: Grok / xAI provider API key
    - MINIMAX_API_KEY: MiniMax provider API key
    - ELEVENLABS_API_KEY / ELEVENLABS_AGENT_ID: ElevenLabs key + agent id

    Env-only contract (Finding 3): provider secrets come from the env var (or
    the per-instance ``api_key_file`` / ``agent_id_file`` / ``*_env`` file-backed
    fields, which this function NEVER touches), never from an inline YAML
    literal. A single data-driven pass (see PROVIDER_KEY_FAMILIES) iterates
    EVERY block in ``providers``, determines its family by ``type`` first then
    name/host, and for each matching block INJECTS the inline literal when the
    env var is SET and STRIPS it when UNSET. This covers canonical,
    multi-instance, and custom ``type:`` blocks (e.g. openai_realtime,
    google_live) uniformly.

    Args:
        config_data: Configuration dictionary to modify in-place

    Complexity: 4
    """
    try:
        providers_block = config_data.get('providers', {}) or {}

        # Single data-driven pass over every provider block.
        for provider_name, provider_cfg in list(providers_block.items()):
            if not isinstance(provider_cfg, dict):
                continue
            name_lower = str(provider_name).lower()
            cfg_type = str(provider_cfg.get("type", "")).lower()

            for spec in PROVIDER_KEY_FAMILIES:
                if not _matches_provider_family(name_lower, cfg_type, provider_cfg, spec):
                    continue
                # Apply env-only contract for each inline secret field of the family.
                for env_name, field in spec["secret_fields"].items():
                    env_val = os.getenv(env_name)
                    if env_val:
                        provider_cfg[field] = env_val
                    else:
                        provider_cfg.pop(field, None)
                providers_block[provider_name] = provider_cfg
                # A block belongs to exactly one family; stop at the first match
                # so we don't double-process (and so an openai-host block is not
                # also matched by a later family).
                break

        # Inject Vertex AI project/location for google_live blocks (AAVA-191).
        # Separate from the secret pass: these are non-secret env injections that
        # only apply to google_live instances and use setdefault (never override
        # an explicit YAML value).
        gcp_project = os.getenv('GOOGLE_CLOUD_PROJECT')
        gcp_location = os.getenv('GOOGLE_CLOUD_LOCATION')
        if gcp_project or gcp_location:
            for provider_name, provider_cfg in list(providers_block.items()):
                if not isinstance(provider_cfg, dict):
                    continue
                name_lower = str(provider_name).lower()
                cfg_type = str(provider_cfg.get("type", "")).lower()
                if not (name_lower.startswith("google_live")
                        or name_lower.endswith("_google_live")
                        or cfg_type == "google_live"):
                    continue
                if gcp_project:
                    provider_cfg.setdefault('vertex_project', gcp_project)
                if gcp_location:
                    provider_cfg.setdefault('vertex_location', gcp_location)
                providers_block[provider_name] = provider_cfg

        # Auto-set GOOGLE_APPLICATION_CREDENTIALS for Vertex AI ADC.
        # Case 1: env var not set at all → set it if the default file exists.
        # Case 2: env var set but points to a missing file → override with the
        #         default mount path so ADC doesn't blow up at call time.
        # Case 3: env var set, file missing, AND no default fallback → unset the
        #         var so google.auth.default() doesn't crash on a stale path.
        default_creds_path = "/app/project/secrets/gcp-service-account.json"
        current_creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
        if not current_creds or not os.path.isfile(current_creds):
            if os.path.isfile(default_creds_path):
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = default_creds_path
            elif current_creds:
                # Stale pointer — remove so ADC falls back to API-key mode
                os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)

        config_data['providers'] = providers_block
    except Exception:
        # Non-fatal; Pydantic may still raise if keys are missing
        pass
