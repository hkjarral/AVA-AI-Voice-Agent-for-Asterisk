"""
Local AI Server WebSocket protocol contract (JSON Schema).

This module is intentionally dependency-light:
- It ships the canonical JSON Schema describing request/response payloads.
- It can optionally validate payloads if `jsonschema` is installed.

This is used as a refactor safety net: we freeze the external WS contract and
make it easy to diff/validate while moving internal code across modules.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, Optional


PROTOCOL_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://asterisk-ai-voice-agent.local/local-ai-server/ws-protocol.schema.json",
    "title": "Local AI Server WebSocket Protocol",
    "type": "object",
    "$defs": {
        "AuthRequest": {
            "type": "object",
            "required": ["type", "auth_token"],
            "properties": {
                "type": {"const": "auth"},
                "auth_token": {"type": "string", "minLength": 1},
                "token": {"type": "string"},
                "call_id": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "AuthResponse": {
            "type": "object",
            "required": ["type", "status"],
            "properties": {
                "type": {"const": "auth_response"},
                "status": {"enum": ["ok", "error"]},
                "message": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "SetModeRequest": {
            "type": "object",
            "required": ["type", "mode"],
            "properties": {
                "type": {"const": "set_mode"},
                "mode": {"enum": ["full", "stt", "llm", "tts"]},
                "call_id": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "ModeReady": {
            "type": "object",
            "required": ["type", "mode", "call_id"],
            "properties": {
                "type": {"const": "mode_ready"},
                "mode": {"enum": ["full", "stt", "llm", "tts"]},
                "call_id": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "StatusRequest": {
            "type": "object",
            "required": ["type"],
            "properties": {"type": {"const": "status"}},
            "additionalProperties": True,
        },
        "StatusResponse": {
            "type": "object",
            "required": ["type", "status", "stt_backend", "tts_backend", "models", "kroko", "kokoro", "config"],
            "properties": {
                "type": {"const": "status_response"},
                "status": {"type": "string"},
                "stt_backend": {"type": "string"},
                "tts_backend": {"type": "string"},
                "models": {
                    "type": "object",
                    "required": ["stt", "llm", "tts"],
                    "properties": {
                        "stt": {
                            "type": "object",
                            "required": ["backend", "loaded", "path", "display"],
                            "properties": {
                                "backend": {"type": "string"},
                                "loaded": {"type": "boolean"},
                                "path": {},
                                "display": {},
                                "language": {"type": ["string", "null"]},
                            },
                            "additionalProperties": True,
                        },
                        "llm": {
                            "type": "object",
                            "required": ["loaded", "path", "display", "config"],
                            "properties": {
                                "loaded": {"type": "boolean"},
                                "path": {},
                                "display": {},
                                "config": {"type": "object"},
                            },
                            "additionalProperties": True,
                        },
                        "tts": {
                            "type": "object",
                            "required": ["backend", "loaded", "path", "display"],
                            "properties": {
                                "backend": {"type": "string"},
                                "loaded": {"type": "boolean"},
                                "path": {},
                                "display": {},
                            },
                            "additionalProperties": True,
                        },
                    },
                    "additionalProperties": True,
                },
                "kroko": {
                    "type": "object",
                    "required": ["embedded", "port", "language", "url", "model_path"],
                    "properties": {
                        "embedded": {"type": "boolean"},
                        "port": {"type": ["integer", "null"]},
                        "language": {"type": ["string", "null"]},
                        "url": {"type": ["string", "null"]},
                        "model_path": {"type": ["string", "null"]},
                    },
                    "additionalProperties": True,
                },
                "kokoro": {
                    "type": "object",
                    "required": ["mode", "voice", "model_path", "api_base_url", "api_key_set"],
                    "properties": {
                        "mode": {"type": "string"},
                        "voice": {"type": ["string", "null"]},
                        "model_path": {"type": ["string", "null"]},
                        "api_base_url": {"type": ["string", "null"]},
                        "api_key_set": {"type": "boolean"},
                    },
                    "additionalProperties": True,
                },
                "config": {"type": "object"},
            },
            "additionalProperties": True,
        },
        "CapabilitiesRequest": {
            "type": "object",
            "required": ["type"],
            "properties": {"type": {"const": "capabilities"}},
            "additionalProperties": True,
        },
        "CapabilitiesResponse": {
            "type": "object",
            "required": ["type", "capabilities"],
            "properties": {
                "type": {"const": "capabilities_response"},
                "capabilities": {"type": "object"},
            },
            "additionalProperties": True,
        },
        "SwitchModelRequest": {
            "type": "object",
            "required": ["type"],
            "properties": {
                "type": {"const": "switch_model"},
                "dry_run": {"type": "boolean"},
                "stt_backend": {"type": "string"},
                "stt_model_path": {"type": "string"},
                "sherpa_model_path": {"type": "string"},
                "kroko_embedded": {"type": "boolean"},
                "kroko_port": {"type": "integer"},
                "kroko_language": {"type": "string"},
                "kroko_url": {"type": "string"},
                "kroko_model_path": {"type": "string"},
                "faster_whisper_language": {"type": "string"},
                "whisper_cpp_language": {"type": "string"},
                "sherpa_model_type": {"type": "string", "enum": ["online", "offline"]},
                "sherpa_vad_model_path": {"type": "string"},
                "tone_model_path": {"type": "string"},
                "tone_decoder_type": {"type": "string", "enum": ["beam_search", "greedy"]},
                "tone_kenlm_path": {"type": "string"},
                "tts_backend": {"type": "string"},
                "tts_model_path": {"type": "string"},
                "kokoro_voice": {"type": "string"},
                "kokoro_mode": {"type": "string"},
                "kokoro_model_path": {"type": "string"},
                "kokoro_api_base_url": {"type": "string"},
                "kokoro_api_key": {"type": "string"},
                "kokoro_api_model": {"type": "string"},
                "llm_model_path": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "SwitchResponse": {
            "type": "object",
            "required": ["type", "status", "message"],
            "properties": {
                "type": {"const": "switch_response"},
                "status": {"enum": ["success", "no_change", "error"]},
                "message": {"type": "string"},
                "changed": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": True,
        },
        "ReloadModelsRequest": {
            "type": "object",
            "required": ["type"],
            "properties": {"type": {"const": "reload_models"}},
            "additionalProperties": True,
        },
        "ReloadLLMRequest": {
            "type": "object",
            "required": ["type"],
            "properties": {"type": {"const": "reload_llm"}},
            "additionalProperties": True,
        },
        "ReloadResponse": {
            "type": "object",
            "required": ["type", "status", "message"],
            "properties": {
                "type": {"const": "reload_response"},
                "status": {"enum": ["success", "error"]},
                "message": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "LLMRequest": {
            "type": "object",
            "required": ["type", "text"],
            "properties": {
                "type": {"const": "llm_request"},
                "text": {"type": "string"},
                "mode": {"type": "string"},
                "call_id": {"type": "string"},
                "request_id": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "LLMResponse": {
            "type": "object",
            "required": ["type", "text", "call_id", "mode"],
            "properties": {
                "type": {"const": "llm_response"},
                "text": {"type": "string"},
                "call_id": {"type": "string"},
                "mode": {"type": "string"},
                "request_id": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "LLMToolRequest": {
            "type": "object",
            "required": ["type", "text"],
            "properties": {
                "type": {"const": "llm_tool_request"},
                "text": {"type": "string"},
                "mode": {"type": "string"},
                "call_id": {"type": "string"},
                "request_id": {"type": "string"},
                "tool_choice": {"enum": ["auto", "required", "none"]},
                "tool_policy": {"enum": ["auto", "strict", "compatible", "off"]},
                "protocol_version": {"type": "integer"},
                "allowed_tools": {"type": "array", "items": {"type": "string"}},
                "tools": {"type": "array", "items": {"type": "object"}},
                "latest_user_text": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "LLMToolResponse": {
            "type": "object",
            "required": ["type", "call_id", "tool_calls", "finish_reason"],
            "properties": {
                "type": {"const": "llm_tool_response"},
                "call_id": {"type": "string"},
                "request_id": {"type": "string"},
                "text": {"type": "string"},
                "tool_calls": {"type": "array", "items": {"type": "object"}},
                "finish_reason": {"enum": ["tool_calls", "stop", "error"]},
                "tool_path": {"enum": ["structured", "parser", "repair", "heuristic", "none"]},
                "tool_parse_failures": {"type": "integer"},
                "repair_attempts": {"type": "integer"},
                "structured_attempts": {"type": "integer"},
                "protocol_version": {"type": "integer"},
            },
            "additionalProperties": True,
        },
        "TTSRequest": {
            "type": "object",
            "required": ["type", "text"],
            "properties": {
                "type": {"const": "tts_request"},
                "text": {"type": "string"},
                "mode": {"type": "string"},
                "call_id": {"type": "string"},
                "request_id": {"type": "string"},
                "encoding": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "TTSResponse": {
            "type": "object",
            "required": ["type", "text", "call_id", "audio_data", "encoding", "sample_rate_hz", "byte_length"],
            "properties": {
                "type": {"const": "tts_response"},
                "text": {"type": "string"},
                "call_id": {"type": "string"},
                "audio_data": {"type": "string"},
                "encoding": {"type": "string"},
                "sample_rate_hz": {"type": "integer"},
                "byte_length": {"type": "integer"},
                "request_id": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "AudioFrameRequest": {
            "type": "object",
            "required": ["type", "data"],
            "properties": {
                "type": {"const": "audio"},
                "data": {"type": "string"},
                "rate": {"type": "integer"},
                "mode": {"type": "string"},
                "call_id": {"type": "string"},
                "request_id": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "STTResult": {
            "type": "object",
            "required": ["type", "text", "call_id", "mode", "is_final", "is_partial"],
            "properties": {
                "type": {"const": "stt_result"},
                "text": {"type": "string"},
                "call_id": {"type": "string"},
                "mode": {"type": "string"},
                "is_final": {"type": "boolean"},
                "is_partial": {"type": "boolean"},
                "confidence": {"type": ["number", "null"]},
                "request_id": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "TTSAudioMetadata": {
            "type": "object",
            "required": ["type", "call_id", "mode", "encoding", "sample_rate_hz", "byte_length"],
            "properties": {
                "type": {"const": "tts_audio"},
                "call_id": {"type": "string"},
                "mode": {"type": "string"},
                "encoding": {"type": "string"},
                "sample_rate_hz": {"type": "integer"},
                "byte_length": {"type": "integer"},
                "request_id": {"type": "string"},
            },
            "additionalProperties": True,
        },
    },
    "oneOf": [
        {"$ref": "#/$defs/AuthRequest"},
        {"$ref": "#/$defs/AuthResponse"},
        {"$ref": "#/$defs/SetModeRequest"},
        {"$ref": "#/$defs/ModeReady"},
        {"$ref": "#/$defs/StatusRequest"},
        {"$ref": "#/$defs/StatusResponse"},
        {"$ref": "#/$defs/CapabilitiesRequest"},
        {"$ref": "#/$defs/CapabilitiesResponse"},
        {"$ref": "#/$defs/SwitchModelRequest"},
        {"$ref": "#/$defs/SwitchResponse"},
        {"$ref": "#/$defs/ReloadModelsRequest"},
        {"$ref": "#/$defs/ReloadLLMRequest"},
        {"$ref": "#/$defs/ReloadResponse"},
        {"$ref": "#/$defs/LLMRequest"},
        {"$ref": "#/$defs/LLMResponse"},
        {"$ref": "#/$defs/LLMToolRequest"},
        {"$ref": "#/$defs/LLMToolResponse"},
        {"$ref": "#/$defs/TTSRequest"},
        {"$ref": "#/$defs/TTSResponse"},
        {"$ref": "#/$defs/AudioFrameRequest"},
        {"$ref": "#/$defs/STTResult"},
        {"$ref": "#/$defs/TTSAudioMetadata"},
    ],
}


def _optional_jsonschema_validator() -> Optional[Any]:
    try:
        import jsonschema  # type: ignore

        return jsonschema
    except Exception:
        return None


def validate_payload(payload: Dict[str, Any], *, schema: Dict[str, Any] = PROTOCOL_SCHEMA) -> None:
    """
    Validate payload using jsonschema when available; otherwise enforce minimal checks.
    Raises ValueError on failure.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    if "type" not in payload:
        raise ValueError("payload missing required key: type")

    jsonschema = _optional_jsonschema_validator()
    if jsonschema is None:
        return

    jsonschema.validate(instance=payload, schema=schema)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local AI Server WS protocol contract utilities")
    parser.add_argument("--write-schema", dest="write_schema", help="Write protocol.schema.json to path")
    parser.add_argument("--validate", dest="validate_path", help="Validate a JSON payload file against the schema")
    args = parser.parse_args()

    if args.write_schema:
        out_path = args.write_schema
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(PROTOCOL_SCHEMA, f, indent=2, sort_keys=False)
            f.write("\n")
        print(f"Wrote schema to {out_path}")

    if args.validate_path:
        with open(args.validate_path, "r") as f:
            payload = json.load(f)
        validate_payload(payload)
        print("OK")


if __name__ == "__main__":
    main()
