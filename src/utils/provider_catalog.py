"""Verified provider catalog shared with generated Admin UI data.

Loading happens once at import; helpers return copies so callers cannot mutate
another call's catalog. Regenerate frontend data with generate_provider_catalog.py.
"""
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

_CATALOG = json.loads(Path(__file__).with_suffix('.json').read_text(encoding='utf-8'))

def provider_catalog() -> dict[str, Any]:
    return deepcopy(_CATALOG)

def provider_default(provider: str, field: str) -> Any:
    return deepcopy(_CATALOG['providers'][provider]['defaults'][field])

def provider_models(provider: str, role: str) -> list[dict[str, Any]]:
    return deepcopy(_CATALOG['providers'][provider].get('models', {}).get(role, []))

def provider_voices(provider: str, role: str) -> list[dict[str, str]]:
    return deepcopy(_CATALOG['providers'][provider].get('voices', {}).get(role, []))

def provider_voice_ids(provider: str, model: str) -> list[str]:
    return list(_CATALOG['providers'][provider].get('voice_models', {}).get(model, []))
