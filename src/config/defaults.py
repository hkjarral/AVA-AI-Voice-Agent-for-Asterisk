"""
Default value application for configuration.

This module handles:
- Transport mode defaults (audio_transport, downstream_mode)
- AudioSocket configuration defaults
- ExternalMedia RTP configuration defaults
- Diagnostic settings (egress swap, force mulaw, attack ms, taps, logging)
- Barge-in configuration with environment variable overrides
"""

import os
from typing import Any, Dict


def apply_transport_defaults(config_data: Dict[str, Any]) -> None:
    """
    Apply transport and mode defaults from environment variables.
    
    Sets:
    - audio_transport: 'externalmedia' or 'audiosocket' (default: externalmedia)
    - downstream_mode: 'file' or 'stream' (default: stream)
    
    Environment variables:
    - AUDIO_TRANSPORT: Override audio transport mode
    - DOWNSTREAM_MODE: Override downstream playback mode
    
    Args:
        config_data: Configuration dictionary to modify in-place
        
    Complexity: 2
    """
    config_data.setdefault('audio_transport', os.getenv('AUDIO_TRANSPORT', 'externalmedia'))
    config_data.setdefault('downstream_mode', os.getenv('DOWNSTREAM_MODE', 'stream'))


def apply_audiosocket_defaults(config_data: Dict[str, Any]) -> None:
    """
    Apply AudioSocket configuration defaults with environment variable overrides.
    
    Sets:
    - host: AudioSocket server bind address (default: 127.0.0.1)
    - advertise_host: IP Asterisk connects to (default: None, falls back to host)
    - port: AudioSocket server port (default: 8090)
    - format: Audio format for AudioSocket payload (default: ulaw)
    
    Environment variables:
    - AUDIOSOCKET_HOST: Override bind address
    - AUDIOSOCKET_ADVERTISE_HOST: Override advertise address (for NAT/VPN deployments)
    - AUDIOSOCKET_PORT: Override port
    - AUDIOSOCKET_FORMAT: Override audio format
    
    Args:
        config_data: Configuration dictionary to modify in-place
        
    Complexity: 4
    """
    audiosocket_cfg = config_data.get('audiosocket', {}) or {}
    
    # Host default (bind address)
    audiosocket_cfg.setdefault('host', os.getenv('AUDIOSOCKET_HOST', '127.0.0.1'))
    
    # Advertise host (for NAT/VPN - IP Asterisk connects to)
    # Only set if env var is present and non-empty; otherwise leave as None (engine will fall back to host)
    advertise_host = os.getenv('AUDIOSOCKET_ADVERTISE_HOST', '').strip()
    if advertise_host:
        audiosocket_cfg['advertise_host'] = advertise_host
    
    # Port default with type conversion
    try:
        port_default = audiosocket_cfg.get('port', 8090)
        audiosocket_cfg.setdefault('port', int(os.getenv('AUDIOSOCKET_PORT', str(port_default))))
    except ValueError:
        audiosocket_cfg['port'] = 8090
    
    # Format default (matches third arg to AudioSocket(...) in dialplan)
    audiosocket_cfg.setdefault('format', os.getenv('AUDIOSOCKET_FORMAT', audiosocket_cfg.get('format', 'ulaw')))
    
    config_data['audiosocket'] = audiosocket_cfg


def apply_externalmedia_defaults(config_data: Dict[str, Any]) -> None:
    """
    Apply ExternalMedia RTP configuration defaults with environment variable overrides.
    
    Sets:
    - rtp_host: RTP server bind address (default: 127.0.0.1)
    - advertise_host: IP Asterisk sends RTP to (default: None, falls back to rtp_host)
    
    Environment variables:
    - EXTERNAL_MEDIA_RTP_HOST: Override RTP bind address
    - EXTERNAL_MEDIA_ADVERTISE_HOST: Override advertise address (for NAT/VPN deployments)
    
    Args:
        config_data: Configuration dictionary to modify in-place
        
    Complexity: 2
    """
    external_cfg = config_data.get('external_media', {}) or {}
    external_cfg.setdefault('rtp_host', os.getenv('EXTERNAL_MEDIA_RTP_HOST', external_cfg.get('rtp_host', '127.0.0.1')))
    
    # Advertise host (for NAT/VPN - IP Asterisk sends RTP to)
    # Only set if env var is present and non-empty; otherwise leave as None (engine will fall back to rtp_host)
    advertise_host = os.getenv('EXTERNAL_MEDIA_ADVERTISE_HOST', '').strip()
    if advertise_host:
        external_cfg['advertise_host'] = advertise_host
    
    config_data['external_media'] = external_cfg


def apply_diagnostic_defaults(config_data: Dict[str, Any]) -> None:
    """
    Apply diagnostic settings from environment variables only.
    
    Diagnostic settings control egress swap mode, force mulaw, attack envelope,
    audio taps, and streaming log verbosity. These are read from environment
    variables to avoid polluting YAML configs.
    
    Environment variables:
    - DIAG_EGRESS_SWAP_MODE: Egress swap mode (default: none)
    - DIAG_EGRESS_FORCE_MULAW: Force mulaw output (default: false)
    - DIAG_ATTACK_MS: Attack envelope in ms (default: 0, disabled)
    - DIAG_ENABLE_TAPS: Enable diagnostic audio taps (default: false)
    - DIAG_TAP_PRE_SECS: Pre-event tap duration (default: 1)
    - DIAG_TAP_POST_SECS: Post-event tap duration (default: 1)
    - DIAG_TAP_OUTPUT_DIR: Tap output directory (default: /tmp/ai-engine-taps)
    - STREAMING_LOG_LEVEL: Streaming log verbosity (default: info)
    
    Args:
        config_data: Configuration dictionary to modify in-place
        
    Complexity: 6
    """
    # Ensure streaming block exists
    if 'streaming' not in config_data:
        config_data['streaming'] = {}
    
    streaming_cfg = config_data['streaming']
    
    # Egress swap mode (diagnostic only)
    streaming_cfg['egress_swap_mode'] = os.getenv('DIAG_EGRESS_SWAP_MODE', 'none')
    
    # Egress force mulaw (diagnostic only)
    env_force_mulaw = os.getenv('DIAG_EGRESS_FORCE_MULAW', 'false')
    streaming_cfg['egress_force_mulaw'] = env_force_mulaw.lower() in ('true', '1', 'yes')
    
    # Attack ms (diagnostic only - disabled by default)
    streaming_cfg['attack_ms'] = int(os.getenv('DIAG_ATTACK_MS', '0'))
    
    # Diagnostic audio taps (disabled by default)
    env_taps = os.getenv('DIAG_ENABLE_TAPS', 'false')
    streaming_cfg['diag_enable_taps'] = env_taps.lower() in ('true', '1', 'yes')
    streaming_cfg['diag_pre_secs'] = int(os.getenv('DIAG_TAP_PRE_SECS', '1'))
    streaming_cfg['diag_post_secs'] = int(os.getenv('DIAG_TAP_POST_SECS', '1'))
    streaming_cfg['diag_out_dir'] = os.getenv('DIAG_TAP_OUTPUT_DIR', '/tmp/ai-engine-taps')
    
    # Streaming logger verbosity
    streaming_cfg['logging_level'] = os.getenv('STREAMING_LOG_LEVEL', 'info')


def apply_barge_in_defaults(config_data: Dict[str, Any]) -> None:
    """
    Apply barge-in configuration with environment variable overrides.
    
    Barge-in allows users to interrupt agent speech. Configuration can be
    set in YAML and overridden by environment variables.
    
    Environment variables (optional overrides):
    - BARGE_IN_ENABLED: Enable/disable barge-in (true/false)
    - BARGE_IN_INITIAL_PROTECTION_MS: Initial protection window (default: 200)
    - BARGE_IN_MIN_MS: Minimum duration to trigger (default: 250)
    - BARGE_IN_ENERGY_THRESHOLD: Energy threshold for detection (default: 1000)
    - BARGE_IN_COOLDOWN_MS: Cooldown between triggers (default: 500)
    - BARGE_IN_POST_TTS_END_PROTECTION_MS: Post-TTS protection window (default: 250)
    
    Args:
        config_data: Configuration dictionary to modify in-place
        
    Complexity: 7
    """
    barge_cfg = config_data.get('barge_in', {}) or {}
    
    try:
        # Only override if environment variable is explicitly set
        if 'BARGE_IN_ENABLED' in os.environ:
            barge_cfg['enabled'] = os.getenv('BARGE_IN_ENABLED', 'true').lower() in ('1', 'true', 'yes')
        
        if 'BARGE_IN_INITIAL_PROTECTION_MS' in os.environ:
            barge_cfg['initial_protection_ms'] = int(os.getenv('BARGE_IN_INITIAL_PROTECTION_MS', '200'))
        
        if 'BARGE_IN_MIN_MS' in os.environ:
            barge_cfg['min_ms'] = int(os.getenv('BARGE_IN_MIN_MS', '250'))
        
        if 'BARGE_IN_ENERGY_THRESHOLD' in os.environ:
            barge_cfg['energy_threshold'] = int(os.getenv('BARGE_IN_ENERGY_THRESHOLD', '1000'))
        
        if 'BARGE_IN_COOLDOWN_MS' in os.environ:
            barge_cfg['cooldown_ms'] = int(os.getenv('BARGE_IN_COOLDOWN_MS', '500'))
        
        if 'BARGE_IN_POST_TTS_END_PROTECTION_MS' in os.environ:
            barge_cfg['post_tts_end_protection_ms'] = int(os.getenv('BARGE_IN_POST_TTS_END_PROTECTION_MS', '250'))
    
    except ValueError:
        # Ignore invalid integer conversions; keep YAML values
        pass
    
    config_data['barge_in'] = barge_cfg
