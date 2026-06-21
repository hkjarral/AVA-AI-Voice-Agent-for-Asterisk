"""
Transport Orchestrator - Multi-Provider Audio Format Negotiation

This module implements the Transport Orchestrator that resolves audio format settings
for each call based on:
1. Audio profiles (declarative YAML config)
2. Provider capabilities (static or runtime ACK)
3. Per-call overrides (channel variables)
4. Context mapping (semantic routing)

The orchestrator produces a TransportProfile that specifies:
- AudioSocket wire format (always from YAML/dialplan)
- Provider input/output formats
- Internal processing rate
- Chunk size and idle cutoff settings
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Union
from structlog import get_logger

from ..providers.base import ProviderCapabilities

logger = get_logger(__name__)


@dataclass
class AudioProfile:
    """User-defined audio profile from YAML configuration."""
    name: str
    internal_rate_hz: int
    transport_out: Dict[str, Any]
    provider_pref: Dict[str, Any]
    chunk_ms: str | int = "auto"
    idle_cutoff_ms: int = 1200


@dataclass
class ContextConfig:
    """Context mapping for semantic routing (sales, support, etc.)."""
    prompt: Optional[str] = None
    greeting: Optional[str] = None
    profile: Optional[str] = None
    provider: Optional[str] = None
    pipeline: Optional[str] = None  # Pipeline name for modular STT/LLM/TTS (e.g., local_hybrid)
    tools: Optional[list] = None  # In-call tool names for function calling
    background_music: Optional[str] = None  # MOH class name for background music during calls
    
    # Phase tool configuration (Milestone 24)
    pre_call_tools: Optional[List[str]] = None  # Tool names to run after answer, before AI speaks
    post_call_tools: Optional[List[str]] = None  # Tool names to run after call ends
    
    # In-call HTTP tool configurations (defined inline in context)
    # NOTE: Admin UI stores `contexts.<name>.in_call_http_tools` as a list of enabled tool names.
    # Inline per-context tool definitions may also be supported as a dict (name -> config).
    in_call_http_tools: Optional[Union[List[str], Dict[str, Any]]] = None  # names or {name: config}
    
    # Global tool opt-out per context (Milestone 24)
    disable_global_pre_call_tools: Optional[List[str]] = None  # Global pre-call tools to disable
    disable_global_in_call_tools: Optional[List[str]] = None  # Global in-call tools to disable
    disable_global_post_call_tools: Optional[List[str]] = None  # Global post-call tools to disable

    # Per-agent post-call email overrides (H5). None means "unset" -> fall back to
    # per-context map / global config. email_enabled is tri-state (None = inherit).
    email_recipient: Optional[str] = None
    email_from: Optional[str] = None
    email_enabled: Optional[bool] = None


@dataclass
class TransportProfile:
    """Resolved transport settings for a call (locked at call start)."""
    profile_name: str
    wire_encoding: str
    wire_sample_rate: int
    provider_input_encoding: str
    provider_input_sample_rate: int
    provider_output_encoding: str
    provider_output_sample_rate: int
    internal_rate: int
    chunk_ms: int
    idle_cutoff_ms: int
    context: Optional[str] = None
    remediation: Optional[str] = None


class TransportOrchestrator:
    """
    Resolves transport profile per call with provider capability negotiation.
    
    Precedence (highest to lowest):
    1. AI_PROVIDER channel var → overrides provider selection
    2. AI_CONTEXT channel var → maps to context config (includes profile + provider)
    3. AI_AUDIO_PROFILE channel var → overrides profile only
    4. YAML profiles.default → fallback
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.profiles = self._load_profiles(config)
        self.contexts = self._load_contexts(config)
        self.default_profile_name = config.get('profiles', {}).get('default', 'telephony_ulaw_8k')

        # agents.db (operator) is the source of truth when present; YAML contexts
        # are the fallback for headless installs. Lazy import breaks the
        # agent_store <-> transport_orchestrator circular import (ContextConfig).
        from src.core.agent_store import EngineAgentStore
        self.agent_store = EngineAgentStore()
        
        # Store audio transport config for wire format detection
        self.audio_transport = config.get('audio_transport', 'audiosocket')
        audiosocket_config = config.get('audiosocket', {})
        self.audiosocket_format = audiosocket_config.get('format', 'slin16') if audiosocket_config else 'slin16'
        self.audiosocket_sample_rate = audiosocket_config.get('sample_rate', None) if audiosocket_config else None
        
        # If no profiles defined, synthesize from legacy config
        if not self.profiles:
            logger.info(
                "No audio profiles found in config; synthesizing legacy profile",
                default=self.default_profile_name,
            )
            self.profiles[self.default_profile_name] = self._synthesize_legacy_profile(config)
    
    def _load_profiles(self, config: Dict[str, Any]) -> Dict[str, AudioProfile]:
        """Load audio profiles from YAML config."""
        profiles = {}
        profiles_config = config.get('profiles', {})
        
        for name, profile_dict in profiles_config.items():
            if name == 'default' or not isinstance(profile_dict, dict):
                continue
            
            try:
                profiles[name] = AudioProfile(
                    name=name,
                    internal_rate_hz=profile_dict.get('internal_rate_hz', 8000),
                    transport_out=profile_dict.get('transport_out', {}),
                    provider_pref=profile_dict.get('provider_pref', {}),
                    chunk_ms=profile_dict.get('chunk_ms', 'auto'),
                    idle_cutoff_ms=profile_dict.get('idle_cutoff_ms', 1200),
                )
                logger.debug("Loaded audio profile", name=name, profile=profiles[name])
            except Exception as exc:
                logger.warning("Failed to load audio profile", name=name, error=str(exc))
        
        return profiles
    
    def _load_contexts(self, config: Dict[str, Any]) -> Dict[str, ContextConfig]:
        """Load context mappings from YAML config."""
        contexts = {}
        contexts_config = config.get('contexts', {})
        
        for name, context_dict in contexts_config.items():
            if not isinstance(context_dict, dict):
                continue
            
            try:
                contexts[name] = ContextConfig(
                    prompt=context_dict.get('prompt'),
                    greeting=context_dict.get('greeting'),
                    profile=context_dict.get('profile'),
                    provider=context_dict.get('provider'),
                    pipeline=context_dict.get('pipeline'),  # Modular pipeline name (e.g., local_hybrid)
                    tools=context_dict.get('tools'),  # In-call tools for function calling
                    background_music=context_dict.get('background_music'),  # MOH class for background music
                    # Phase tool configuration (Milestone 24)
                    pre_call_tools=context_dict.get('pre_call_tools'),
                    post_call_tools=context_dict.get('post_call_tools'),
                    # In-call HTTP tool configurations
                    in_call_http_tools=context_dict.get('in_call_http_tools'),
                    disable_global_pre_call_tools=context_dict.get('disable_global_pre_call_tools'),
                    disable_global_in_call_tools=(
                        context_dict.get('disable_global_in_call_tools')
                        or context_dict.get('disable_global_in_call_http_tools')  # legacy Admin UI key
                    ),
                    disable_global_post_call_tools=context_dict.get('disable_global_post_call_tools'),
                )
                logger.debug("Loaded context mapping", name=name, context=contexts[name])
            except Exception as exc:
                logger.warning("Failed to load context mapping", name=name, error=str(exc))
        
        return contexts
    
    def _synthesize_legacy_profile(self, config: Dict[str, Any]) -> AudioProfile:
        """
        Synthesize profile from legacy config when profiles.* not present.
        
        This provides backward compatibility for existing deployments.
        """
        # Extract legacy settings
        audiosocket_config = config.get('audiosocket', {})
        streaming_config = config.get('streaming', {})
        
        audiosocket_format = audiosocket_config.get('format', 'slin')
        streaming_rate = streaming_config.get('sample_rate', 8000)
        
        # Map format names
        encoding_map = {
            'slin': 'linear16',
            'slin16': 'linear16',
            'ulaw': 'mulaw',
            'mulaw': 'mulaw',
        }
        provider_encoding = encoding_map.get(audiosocket_format, 'linear16')
        
        profile = AudioProfile(
            name='legacy_compat',
            internal_rate_hz=streaming_rate,
            transport_out={
                'encoding': audiosocket_format,
                'sample_rate_hz': streaming_rate,
            },
            provider_pref={
                'input_encoding': provider_encoding,
                'input_sample_rate_hz': streaming_rate,
                'output_encoding': provider_encoding,
                'output_sample_rate_hz': streaming_rate,
            },
            chunk_ms='auto',
            idle_cutoff_ms=1200,
        )
        
        logger.info(
            "Synthesized legacy profile from config",
            profile=profile,
            suggestion="Add profiles.* block to config/ai-agent.yaml for explicit control"
        )
        
        return profile
    
    def resolve_transport(
        self,
        provider_name: str,
        provider_caps: Optional[ProviderCapabilities],
        channel_vars: Optional[Dict[str, str]] = None,
        provider_config: Optional[Any] = None,
        resolved_context: Optional[str] = None,
        routing_method: Optional[str] = None,
    ) -> TransportProfile:
        """
        Resolve transport profile for a call.

        Args:
            provider_name: Selected provider (deepgram, openai_realtime, etc.)
            provider_caps: Provider capabilities (static or from ACK)
            channel_vars: Asterisk channel variables (AI_PROVIDER, AI_AUDIO_PROFILE, AI_CONTEXT)
            provider_config: Provider configuration
            resolved_context: Context/agent slug already resolved by the caller
                (from AI_AGENT, AI_CONTEXT, or the agents.db default). When given, it
                is authoritative for context + audio-profile resolution so that
                AI_AGENT / DB-default calls apply the agent's audio_profile even
                though only AI_CONTEXT is present in channel_vars.
            routing_method: Dialplan channel-variable INTENT (Finding 1) used to
                disambiguate colliding context slugs during audio-profile lookup
                (``'ai_context'`` resolves display_name-first; otherwise slug-first).

        Returns:
            TransportProfile with resolved settings

        Raises:
            ValueError: If profile not found or negotiation fails
        """
        # Step 1: Resolve profile name with precedence
        profile_name, context_name = self._resolve_profile_name(
            channel_vars, resolved_context, routing_method)
        profile = self.profiles.get(profile_name)
        
        if not profile:
            raise ValueError(
                f"Audio profile '{profile_name}' not found. "
                f"Available: {list(self.profiles.keys())}"
            )
        
        logger.info(
            "Resolved audio profile for call",
            profile=profile_name,
            context=context_name,
            provider=provider_name,
        )
        
        # Step 2: Negotiate formats with provider capabilities
        transport = self._negotiate_formats(
            profile,
            provider_name,
            provider_caps,
            context_name,
            provider_config,
        )
        
        # Step 3: Validate and add remediation if needed
        transport = self._validate_and_remediate(transport, provider_caps)
        
        return transport
    
    def _resolve_profile_name(
        self,
        channel_vars: Dict[str, str],
        resolved_context: Optional[str] = None,
        routing_method: Optional[str] = None,
    ) -> tuple[str, Optional[str]]:
        """
        Resolve profile name from channel vars with precedence.

        The context name is the caller-resolved context when provided
        (from AI_AGENT / AI_CONTEXT / agents.db default); otherwise it falls back
        to the AI_CONTEXT channel var. This ensures AI_AGENT and DB-default calls
        apply the agent's audio_profile even though only AI_CONTEXT is present in
        channel_vars.

        Returns:
            (profile_name, context_name) tuple
        """
        # Context name: caller-resolved (DB-aware) takes precedence over AI_CONTEXT.
        context_name = (
            (resolved_context or '').strip()
            or channel_vars.get('AI_CONTEXT', '').strip()
            or None
        )

        # Precedence 1: AI_AUDIO_PROFILE directly specified (explicit per-call override)
        if 'AI_AUDIO_PROFILE' in channel_vars and channel_vars['AI_AUDIO_PROFILE']:
            profile_name = channel_vars['AI_AUDIO_PROFILE']
            logger.debug(
                "Profile from AI_AUDIO_PROFILE channel var",
                profile=profile_name,
                context=context_name,
            )
            return profile_name, context_name

        # Precedence 2: context maps to a context config (DB-aware) with a profile
        if context_name:
            context = self.get_context_config(context_name, routing_method)
            if context and context.profile:
                logger.debug(
                    "Profile from context mapping",
                    context=context_name,
                    profile=context.profile,
                )
                return context.profile, context_name

        # Precedence 3: Default from YAML
        profile_name = self.default_profile_name
        logger.debug("Profile from config default", profile=profile_name)
        return profile_name, context_name
    
    def _negotiate_formats(
        self,
        profile: AudioProfile,
        provider_name: str,
        provider_caps: Optional[ProviderCapabilities],
        context_name: Optional[str] = None,
        provider_config: Optional[Any] = None,
    ) -> TransportProfile:
        """
        Negotiate formats between profile preferences and provider capabilities.
        
        Wire format: For AudioSocket, use audiosocket.format (authoritative).
                     For RTP, use profile.transport_out (negotiated codec).
        Provider format: try profile preference, fallback to provider's supported formats.
        """
        # CRITICAL: Wire format depends on transport type
        if self.audio_transport == "audiosocket":
            # AudioSocket: use actual format from audiosocket.format config
            wire_enc = self.audiosocket_format
            wire_rate = self.audiosocket_sample_rate
            if not wire_rate:
                # Infer rate from format: slin=8kHz, slin16=16kHz
                wire_enc_lower = wire_enc.lower().strip()
                if wire_enc_lower in ('slin', 'linear', 'pcm'):
                    wire_rate = 8000
                elif wire_enc_lower in ('slin16', 'linear16', 'pcm16'):
                    wire_rate = 16000
                elif wire_enc_lower in ('ulaw', 'mulaw', 'g711_ulaw'):
                    wire_rate = 8000
                else:
                    wire_rate = 8000
        else:
            # RTP: use profile's transport_out (negotiated codec)
            wire_enc = profile.transport_out.get('encoding', 'slin')
            wire_rate = profile.transport_out.get('sample_rate_hz', 8000)
        
        # CRITICAL: Read provider's actual requirements from provider config
        # Modern providers (Google Live, OpenAI) have provider_input_* fields
        # Legacy providers (Deepgram Voice Agent) use input_* fields
        # Fall back to profile preferences if provider config unavailable
        if provider_config:
            # Try modern provider-specific fields first
            pref_in_enc = (
                getattr(provider_config, "provider_input_encoding", None) or
                getattr(provider_config, "input_encoding", None) or
                profile.provider_pref.get('input_encoding', 'linear16')
            )
            pref_out_enc = (
                getattr(provider_config, "provider_output_encoding", None) or
                getattr(provider_config, "output_encoding", None) or
                profile.provider_pref.get('output_encoding', 'linear16')
            )
            try:
                pref_in_rate = (
                    getattr(provider_config, "provider_input_sample_rate_hz", None) or
                    getattr(provider_config, "input_sample_rate_hz", None) or
                    profile.provider_pref.get('input_sample_rate_hz', 16000)
                )
            except Exception:
                pref_in_rate = profile.provider_pref.get('input_sample_rate_hz', 16000)
            try:
                pref_out_rate = (
                    getattr(provider_config, "provider_output_sample_rate_hz", None) or
                    getattr(provider_config, "output_sample_rate_hz", None) or
                    profile.provider_pref.get('output_sample_rate_hz', 16000)
                )
            except Exception:
                pref_out_rate = profile.provider_pref.get('output_sample_rate_hz', 16000)
        else:
            # Fallback to profile preferences (legacy behavior)
            pref_in_enc = profile.provider_pref.get('input_encoding', 'linear16')
            pref_out_enc = profile.provider_pref.get('output_encoding', 'linear16')
            pref_in_rate = profile.provider_pref.get('input_sample_rate_hz', 16000)
            pref_out_rate = profile.provider_pref.get('output_sample_rate_hz', 16000)
        
        # Negotiate with provider if capabilities available
        if provider_caps:
            provider_in_enc = self._select_encoding(
                pref_in_enc,
                provider_caps.input_encodings,
                "input"
            )
            provider_out_enc = self._select_encoding(
                pref_out_enc,
                provider_caps.output_encodings,
                "output"
            )
            provider_in_rate = self._select_sample_rate(
                pref_in_rate,
                provider_caps.input_sample_rates_hz,
                "input"
            )
            provider_out_rate = self._select_sample_rate(
                pref_out_rate,
                provider_caps.output_sample_rates_hz,
                "output"
            )
        else:
            # No capabilities - use profile preferences as-is
            provider_in_enc = pref_in_enc
            provider_out_enc = pref_out_enc
            provider_in_rate = pref_in_rate
            provider_out_rate = pref_out_rate
            
            logger.debug(
                "No provider capabilities available; using profile preferences",
                provider=provider_name,
                input_encoding=provider_in_enc,
                output_encoding=provider_out_enc,
            )
        
        # Resolve chunk_ms
        chunk_ms = 20 if profile.chunk_ms == 'auto' else int(profile.chunk_ms)
        
        transport = TransportProfile(
            profile_name=profile.name,
            wire_encoding=wire_enc,
            wire_sample_rate=wire_rate,
            provider_input_encoding=provider_in_enc,
            provider_input_sample_rate=provider_in_rate,
            provider_output_encoding=provider_out_enc,
            provider_output_sample_rate=provider_out_rate,
            internal_rate=profile.internal_rate_hz,
            chunk_ms=chunk_ms,
            idle_cutoff_ms=profile.idle_cutoff_ms,
            context=context_name,  # Propagate context for greeting/prompt injection
        )
        
        logger.debug(
            "Negotiated transport profile",
            profile=profile.name,
            transport=transport,
        )
        
        return transport
    
    def _select_encoding(
        self,
        preferred: str,
        supported: List[str],
        direction: str,
    ) -> str:
        """Select encoding with preference, fallback to first supported."""
        if not supported:
            logger.warning(
                f"Provider has no supported {direction} encodings; using preference",
                preferred=preferred,
            )
            return preferred
        
        # Normalize for comparison
        preferred_norm = self._normalize_encoding(preferred)
        supported_norm = [self._normalize_encoding(enc) for enc in supported]
        
        if preferred_norm in supported_norm:
            return preferred
        
        # Fallback to first supported
        fallback = supported[0]
        logger.info(
            f"Provider doesn't support preferred {direction} encoding; using fallback",
            preferred=preferred,
            fallback=fallback,
            supported=supported,
        )
        return fallback
    
    def _select_sample_rate(
        self,
        preferred: int,
        supported: List[int],
        direction: str,
    ) -> int:
        """Select sample rate with preference, fallback to first supported."""
        if not supported:
            logger.warning(
                f"Provider has no supported {direction} sample rates; using preference",
                preferred=preferred,
            )
            return preferred
        
        if preferred in supported:
            return preferred
        
        # Fallback to first supported
        fallback = supported[0]
        logger.info(
            f"Provider doesn't support preferred {direction} sample rate; using fallback",
            preferred=preferred,
            fallback=fallback,
            supported=supported,
        )
        return fallback
    
    def _normalize_encoding(self, encoding: str) -> str:
        """Normalize encoding name for comparison."""
        norm_map = {
            'linear16': 'linear16',
            'pcm16': 'linear16',
            'slin': 'linear16',
            'slin16': 'linear16',
            'mulaw': 'mulaw',
            'ulaw': 'mulaw',
            'g711_ulaw': 'mulaw',
            'g711ulaw': 'mulaw',
        }
        return norm_map.get(encoding.lower(), encoding.lower())
    
    def _validate_and_remediate(
        self,
        transport: TransportProfile,
        provider_caps: Optional[ProviderCapabilities],
    ) -> TransportProfile:
        """
        Validate transport profile and add remediation message if issues found.
        
        This is for logging/diagnostics only - transport is still usable.
        """
        issues = []
        
        if not provider_caps:
            return transport  # Can't validate without capabilities
        
        # Check if provider actually supports negotiated formats
        norm_in = self._normalize_encoding(transport.provider_input_encoding)
        supported_in_norm = [self._normalize_encoding(enc) for enc in provider_caps.input_encodings]
        
        if norm_in not in supported_in_norm:
            issues.append(
                f"Provider may not support input encoding {transport.provider_input_encoding} "
                f"(supported: {provider_caps.input_encodings})"
            )
        
        norm_out = self._normalize_encoding(transport.provider_output_encoding)
        supported_out_norm = [self._normalize_encoding(enc) for enc in provider_caps.output_encodings]
        
        if norm_out not in supported_out_norm:
            issues.append(
                f"Provider may not support output encoding {transport.provider_output_encoding} "
                f"(supported: {provider_caps.output_encodings})"
            )
        
        if transport.provider_input_sample_rate not in provider_caps.input_sample_rates_hz:
            issues.append(
                f"Provider may not support input rate {transport.provider_input_sample_rate} Hz "
                f"(supported: {provider_caps.input_sample_rates_hz})"
            )
        
        # Add remediation if issues found
        if issues:
            transport.remediation = "; ".join(issues)
            logger.warning(
                "Transport profile validation found potential issues",
                profile=transport.profile_name,
                issues=issues,
                note="Call will proceed; provider may adjust formats during handshake"
            )
        
        return transport
    
    def get_context_config(
        self, context_name: Optional[str], routing_method: Optional[str] = None
    ) -> Optional[ContextConfig]:
        """Resolve a context. agents.db is the source of truth when present (v1a);
        YAML is the fallback for headless installs and post-rollback recovery.
        When the DB is present, an inactive/unknown slug is NOT routable — we must
        NOT silently fall through to a same-named legacy YAML context, or a
        deactivated/deleted agent would keep routing. Only fall back to YAML when
        the DB is absent/unavailable. Spec: archived plan decisions D1/D2.

        ``routing_method`` carries the dialplan channel-variable INTENT (Finding 1):
        ``'ai_context'`` (legacy original-name selector) resolves display_name-first;
        ``'ai_agent'``/``'default'``/None resolve slug-first (canonical, anti-shadow)."""
        if not context_name:
            return None
        if self.agent_store.available():
            # agents.db is authoritative when present: inactive/unknown agent => not
            # routable. Fall back to YAML ONLY when the DB is present but unreadable
            # (corrupt/locked) — HIGH-9 — never for a clean not-found, so a
            # deactivated/deleted agent is not resurrected from YAML.
            from src.core.agent_store import AgentStoreReadError
            prefer = "display_name" if routing_method == "ai_context" else "slug"
            try:
                return self.agent_store.resolve(context_name, prefer=prefer)
            except AgentStoreReadError:
                logger.warning(
                    "agents.db unreadable; falling back to YAML contexts",
                    context=context_name)
                return self._yaml_context_config(context_name)
        # No DB (headless / pre-migration): fall back to YAML contexts.
        return self._yaml_context_config(context_name)

    def _yaml_context_config(self, context_name: Optional[str]) -> Optional[ContextConfig]:
        """Original YAML-backed context lookup (fallback path)."""
        if not context_name:
            return None
        return self.contexts.get(context_name)
