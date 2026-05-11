"""
ElevenLabs Provider Configuration
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class ElevenLabsVoiceSettings:
    """Voice customization settings for ElevenLabs TTS."""
    stability: float = 0.5
    similarity_boost: float = 0.75
    style: float = 0.0
    use_speaker_boost: bool = True


@dataclass
class ElevenLabsAgentConfig:
    """Configuration for ElevenLabs Conversational AI (Full Agent) provider."""
    # Authentication
    api_key: str = ""
    agent_id: str = ""  # Pre-created agent ID from ElevenLabs dashboard
    api_key_file: str = ""
    api_key_env: str = ""
    agent_id_file: str = ""
    agent_id_env: str = ""
    display_name: str = ""
    customer: str = ""
    
    # Provider type
    type: str = "full"
    enabled: bool = True
    capabilities: List[str] = field(default_factory=lambda: ["stt", "llm", "tts"])
    
    # Audio input configuration (from Asterisk)
    input_encoding: str = "ulaw"  # μ-law from telephony
    input_sample_rate_hz: int = 8000
    
    # Provider native format (internal)
    provider_input_encoding: str = "pcm16"
    provider_input_sample_rate_hz: int = 16000  # 16kHz recommended for ElevenLabs
    
    # Audio output configuration (from ElevenLabs)
    output_encoding: str = "pcm16"
    output_sample_rate_hz: int = 16000  # ElevenLabs output
    
    # Target format for telephony output
    target_encoding: str = "ulaw"
    target_sample_rate_hz: int = 8000
    
    # Voice settings
    voice_id: str = "21m00Tcm4TlvDq8ikWAM"  # Rachel - default voice
    model_id: str = "eleven_flash_v2_5"  # Fast model for conversations
    voice_settings: ElevenLabsVoiceSettings = field(default_factory=ElevenLabsVoiceSettings)
    
    # Agent behavior
    greeting: str = ""
    instructions: str = ""
    
    # Continuous audio streaming (for full duplex)
    continuous_input: bool = True
    
    # Input audio normalization
    input_gain_target_rms: int = 0
    input_gain_max_db: int = 0
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ElevenLabsAgentConfig":
        """Create config from dictionary (YAML)."""
        voice_settings_data = data.pop("voice_settings", {})
        voice_settings = ElevenLabsVoiceSettings(**voice_settings_data) if voice_settings_data else ElevenLabsVoiceSettings()
        
        return cls(
            api_key=data.get("api_key", ""),
            agent_id=data.get("agent_id", ""),
            api_key_file=data.get("api_key_file", ""),
            api_key_env=data.get("api_key_env", ""),
            agent_id_file=data.get("agent_id_file", ""),
            agent_id_env=data.get("agent_id_env", ""),
            display_name=data.get("display_name", ""),
            customer=data.get("customer", ""),
            type=data.get("type", "full"),
            enabled=data.get("enabled", True),
            capabilities=data.get("capabilities", ["stt", "llm", "tts"]),
            input_encoding=data.get("input_encoding", "ulaw"),
            input_sample_rate_hz=data.get("input_sample_rate_hz", 8000),
            provider_input_encoding=data.get("provider_input_encoding", "pcm16"),
            provider_input_sample_rate_hz=data.get("provider_input_sample_rate_hz", 16000),
            output_encoding=data.get("output_encoding", "pcm16"),
            output_sample_rate_hz=data.get("output_sample_rate_hz", 16000),
            target_encoding=data.get("target_encoding", "ulaw"),
            target_sample_rate_hz=data.get("target_sample_rate_hz", 8000),
            voice_id=data.get("voice_id", "21m00Tcm4TlvDq8ikWAM"),
            model_id=data.get("model_id", "eleven_flash_v2_5"),
            voice_settings=voice_settings,
            greeting=data.get("greeting", ""),
            instructions=data.get("instructions", ""),
            continuous_input=data.get("continuous_input", True),
            input_gain_target_rms=data.get("input_gain_target_rms", 0),
            input_gain_max_db=data.get("input_gain_max_db", 0),
        )


@dataclass  
class ElevenLabsTTSConfig:
    """Configuration for ElevenLabs TTS-only provider (for pipelines)."""
    # Authentication
    api_key: str = ""
    
    # Provider type
    type: str = "tts"
    enabled: bool = True
    capabilities: List[str] = field(default_factory=lambda: ["tts"])
    
    # Voice settings
    voice_id: str = "21m00Tcm4TlvDq8ikWAM"  # Rachel - default voice
    model_id: str = "eleven_flash_v2_5"  # Flash for low latency
    voice_settings: ElevenLabsVoiceSettings = field(default_factory=ElevenLabsVoiceSettings)
    
    # Output format
    output_format: str = "pcm_16000"  # pcm_16000, pcm_22050, mp3_44100_128
    
    # Target format for telephony
    target_encoding: str = "ulaw"
    target_sample_rate_hz: int = 8000
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ElevenLabsTTSConfig":
        """Create config from dictionary (YAML)."""
        voice_settings_data = data.pop("voice_settings", {})
        voice_settings = ElevenLabsVoiceSettings(**voice_settings_data) if voice_settings_data else ElevenLabsVoiceSettings()
        
        return cls(
            api_key=data.get("api_key", ""),
            type=data.get("type", "tts"),
            enabled=data.get("enabled", True),
            capabilities=data.get("capabilities", ["tts"]),
            voice_id=data.get("voice_id", "21m00Tcm4TlvDq8ikWAM"),
            model_id=data.get("model_id", "eleven_flash_v2_5"),
            voice_settings=voice_settings,
            output_format=data.get("output_format", "pcm_16000"),
            target_encoding=data.get("target_encoding", "ulaw"),
            target_sample_rate_hz=data.get("target_sample_rate_hz", 8000),
        )
