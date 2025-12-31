"""
Configuration package for AI Voice Agent.

This package contains:
- loaders: YAML file loading and parsing
- security: Credential and API key injection
- defaults: Default value application
- normalization: Config normalization and validation

IMPORTANT: This __init__.py re-exports all classes and functions from
the parent config.py module to maintain backward compatibility with
existing imports like 'from src.config import AsteriskConfig'.

NOTE: Python's import system treats 'src.config' as this package (directory),
not as the src/config.py module file. We use importlib to explicitly load
the config.py module and re-export its contents.
"""

import sys
import importlib.util
from pathlib import Path

# Load config.py module explicitly (bypassing package resolution)
config_py_path = Path(__file__).parent.parent / 'config.py'
spec = importlib.util.spec_from_file_location("_parent_config", config_py_path)
_parent_config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_parent_config)

# Re-export all public classes and functions
AsteriskConfig = _parent_config.AsteriskConfig
ExternalMediaConfig = _parent_config.ExternalMediaConfig
AudioSocketConfig = _parent_config.AudioSocketConfig
LocalProviderConfig = _parent_config.LocalProviderConfig
DeepgramProviderConfig = _parent_config.DeepgramProviderConfig
OpenAIProviderConfig = _parent_config.OpenAIProviderConfig
GoogleProviderConfig = _parent_config.GoogleProviderConfig
GroqSTTProviderConfig = _parent_config.GroqSTTProviderConfig
GroqTTSProviderConfig = _parent_config.GroqTTSProviderConfig
ElevenLabsProviderConfig = _parent_config.ElevenLabsProviderConfig
OpenAIRealtimeProviderConfig = _parent_config.OpenAIRealtimeProviderConfig
MCPConfig = _parent_config.MCPConfig
MCPServerConfig = _parent_config.MCPServerConfig
MCPToolConfig = _parent_config.MCPToolConfig
MCPServerRestartConfig = _parent_config.MCPServerRestartConfig
MCPServerDefaultsConfig = _parent_config.MCPServerDefaultsConfig
BargeInConfig = _parent_config.BargeInConfig
LLMConfig = _parent_config.LLMConfig
VADConfig = _parent_config.VADConfig
StreamingConfig = _parent_config.StreamingConfig
LoggingConfig = _parent_config.LoggingConfig
PipelineEntry = _parent_config.PipelineEntry
AppConfig = _parent_config.AppConfig
load_config = _parent_config.load_config
validate_production_config = _parent_config.validate_production_config

__all__ = [
    'AsteriskConfig',
    'ExternalMediaConfig',
    'AudioSocketConfig',
    'LocalProviderConfig',
    'DeepgramProviderConfig',
    'OpenAIProviderConfig',
    'GoogleProviderConfig',
    'GroqSTTProviderConfig',
    'GroqTTSProviderConfig',
    'ElevenLabsProviderConfig',
    'OpenAIRealtimeProviderConfig',
    'MCPConfig',
    'MCPServerConfig',
    'MCPToolConfig',
    'MCPServerRestartConfig',
    'MCPServerDefaultsConfig',
    'BargeInConfig',
    'LLMConfig',
    'VADConfig',
    'StreamingConfig',
    'LoggingConfig',
    'PipelineEntry',
    'AppConfig',
    'load_config',
    'validate_production_config',
]
