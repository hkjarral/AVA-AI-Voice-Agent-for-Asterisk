#!/usr/bin/env python3
"""Check provider catalogs, generated UI data, runtime defaults and sample config.

The normal CI gate is deterministic and offline. ``--check-upstream`` also
compares the public, credential-free local model catalogs with their publishers;
it is an explicit maintainer check, not a requirement for ordinary PR tests.
"""
from __future__ import annotations

import argparse
import ast
from datetime import date
import importlib.util
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = Path("src/utils/provider_catalog.json")

# These are consumer locations, not a second copy of model/voice identifiers.
RUNTIME_BINDINGS = {
    "OpenAIRealtimeProviderConfig": "openai_realtime",
    "OpenAIProviderConfig": "openai",
    "DeepgramProviderConfig": "deepgram",
    "GrokProviderConfig": "grok",
    "GoogleProviderConfig": "google",
    "TelnyxLLMProviderConfig": "telnyx",
    "MiniMaxLLMProviderConfig": "minimax",
    "GroqSTTProviderConfig": "groq",
    "GroqTTSProviderConfig": "groq",
    "ElevenLabsProviderConfig": "elevenlabs",
    "FishAudioProviderConfig": "fishaudio",
    "CambAiProviderConfig": "cambai",
    "AzureSTTProviderConfig": "azure",
    "AzureTTSProviderConfig": "azure",
    "LocalProviderConfig": "local",
}
# Dedicated classes own only their capability's defaults. Other classes own
# every default of their provider; missing fields must never be skipped.
RUNTIME_OWNED_FIELDS = {
    "GroqSTTProviderConfig": {"stt_model"},
    "GroqTTSProviderConfig": {"tts_model", "voice"},
    "AzureSTTProviderConfig": {"api_version"},
    "AzureTTSProviderConfig": {"voice_name"},
}
SAMPLE_BINDINGS = {
    "openai_realtime": ("openai_realtime", {"model", "voice", "input_transcription_model"}),
    "openai_llm": ("openai", {"chat_model", "reasoning_effort"}),
    "openai_stt": ("openai", {"stt_model"}),
    "openai_tts": ("openai", {"tts_model", "tts_transport", "tts_realtime_timeout_sec", "voice"}),
    "google_live": ("google_live", {"llm_model", "tts_voice_name"}),
    "google_llm": ("google", {"llm_model"}),
    "google_tts": ("google", {"tts_voice_name"}),
    "grok": ("grok", {"model", "voice"}),
    "deepgram": ("deepgram", {"model", "tts_model", "tts_version", "think_model"}),
    "deepseek_llm": ("deepseek", {"chat_model"}),
    "telnyx_llm": ("telnyx", {"chat_model"}),
    "minimax_llm": ("minimax", {"chat_model"}),
    "groq_stt": ("groq", {"stt_model"}),
    "groq_tts": ("groq", {"tts_model", "voice"}),
    "elevenlabs_tts": ("elevenlabs", {"model_id", "voice_id", "output_format"}),
    "fishaudio_tts": ("fishaudio", {"model"}),
    "cambai_tts": ("cambai", {"speech_model", "voice_id"}),
    "azure_stt": ("azure", {"api_version"}),
    "azure_tts": ("azure", {"voice_name"}),
    "ollama_llm": ("ollama", {"model"}),
}
# These model field names can be checked against a provider's published choices.
MODEL_FIELDS = {"model", "chat_model", "llm_model", "stt_model", "tts_model", "model_id", "speech_model", "stt_backend", "tts_backend", "think_model", "input_transcription_model"}


def _read(root: Path, relative: str | Path) -> str:
    return (root / relative).read_text(encoding="utf-8")


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _iso_date(value: Any) -> bool:
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def check_catalog(data: Any) -> list[str]:
    """Validate catalog semantics independently of its generated consumers."""
    failures: list[str] = []
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return ["provider catalog must use schema_version 1"]
    if not _iso_date(data.get("verified_at")):
        failures.append("provider catalog verified_at must be an ISO date")
    providers = data.get("providers")
    if not isinstance(providers, dict) or not providers:
        return failures + ["provider catalog providers must be a non-empty object"]
    for provider_id, provider in providers.items():
        if not isinstance(provider, dict):
            failures.append(f"{provider_id}: provider must be an object")
            continue
        sources = provider.get("sources")
        if not isinstance(sources, list) or not sources or any(
            not isinstance(url, str) or urlparse(url).scheme != "https" or not urlparse(url).netloc
            for url in sources
        ):
            failures.append(f"{provider_id}: document at least one HTTPS official source")
        model_entries: dict[str, dict[str, Any]] = {}
        voice_entries: dict[str, dict[str, Any]] = {}
        all_voices: set[str] = set()
        for kind in ("models", "voices"):
            roles = provider.get(kind, {})
            if not isinstance(roles, dict):
                failures.append(f"{provider_id}.{kind}: expected an object of role lists")
                continue
            for role, entries in roles.items():
                if not isinstance(entries, list):
                    failures.append(f"{provider_id}.{kind}.{role}: expected a list")
                    continue
                ids: set[str] = set()
                for entry in entries:
                    if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"].strip():
                        failures.append(f"{provider_id}.{kind}.{role}: entry requires a non-empty id")
                        continue
                    identifier = entry["id"]
                    if identifier in ids:
                        failures.append(f"{provider_id}.{kind}.{role}: duplicate id {identifier!r}")
                    ids.add(identifier)
                    if not isinstance(entry.get("label"), str) or not entry["label"].strip():
                        failures.append(f"{provider_id}.{kind}.{role}.{identifier}: missing label")
                    if entry.get("status", "active") not in {"active", "preview", "deprecated", "retired", "legacy"}:
                        failures.append(f"{provider_id}.{kind}.{role}.{identifier}: unknown lifecycle status")
                    if "shutdown_date" in entry and not _iso_date(entry["shutdown_date"]):
                        failures.append(f"{provider_id}.{kind}.{role}.{identifier}: invalid shutdown_date")
                    if entry.get("status") == "deprecated" and not (entry.get("shutdown_date") or entry.get("retirement_date_unknown") is True):
                        failures.append(f"{provider_id}.{kind}.{role}.{identifier}: document the retirement date or explicitly mark it unknown")
                    if "languages" in entry:
                        languages = entry["languages"]
                        if not isinstance(languages, list) or not languages or any(
                            not isinstance(language, str) or not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]+)*", language)
                            for language in languages
                        ):
                            failures.append(f"{provider_id}.{kind}.{role}.{identifier}: languages must be non-empty language codes")
                        elif len(languages) != len(set(languages)):
                            failures.append(f"{provider_id}.{kind}.{role}.{identifier}: duplicate language codes")
                    if kind == "models":
                        model_entries[identifier] = entry
                    else:
                        all_voices.add(identifier)
                        voice_entries[identifier] = entry
        defaults = provider.get("defaults", {})
        if not isinstance(defaults, dict):
            failures.append(f"{provider_id}: defaults must be an object")
            continue
        for field, value in defaults.items():
            if field not in MODEL_FIELDS or not isinstance(value, str):
                continue
            # Aura/Flux encode the voice directly in the model identifier.
            if model_entries and value not in model_entries and value not in all_voices:
                failures.append(f"{provider_id}.{field}: default {value!r} is absent from the catalog")
            if any(entry.get("status") in {"retired", "deprecated", "legacy"} for entry in (model_entries.get(value, {}), voice_entries.get(value, {}))):
                failures.append(f"{provider_id}.{field}: new-provider default {value!r} is not active")
            roles = {
                "stt_model": ("stt", "flux_stt"), "input_transcription_model": ("stt",),
                "tts_model": ("tts", "flux_tts"), "model_id": ("tts",), "speech_model": ("tts",),
                "chat_model": ("llm",), "llm_model": ("llm", "live"), "think_model": ("llm",),
                "stt_backend": ("stt_backends",), "tts_backend": ("tts_backends",),
            }.get(field)
            if field == "model":
                roles = {
                    "openai_realtime": ("realtime",), "grok": ("realtime",),
                    "deepgram": ("stt", "flux_stt"), "fishaudio": ("tts",),
                    "ollama": ("llm",),
                }.get(provider_id)
            if roles:
                valid_ids = {
                    entry["id"] for kind in ("models", "voices") for role in roles
                    for entry in provider.get(kind, {}).get(role, [])
                    if isinstance(entry, dict) and isinstance(entry.get("id"), str)
                }
                if value not in valid_ids:
                    failures.append(f"{provider_id}.{field}: default {value!r} is absent from its role catalog {roles!r}")
        voice_models = provider.get("voice_models", {})
        if not isinstance(voice_models, dict):
            failures.append(f"{provider_id}.voice_models: expected an object")
            continue
        for model, voices in voice_models.items():
            if model not in model_entries:
                failures.append(f"{provider_id}.voice_models: unknown model {model!r}")
            if not isinstance(voices, list) or any(not isinstance(voice, str) for voice in voices):
                failures.append(f"{provider_id}.voice_models.{model}: expected voice ids")
                continue
            if len(voices) != len(set(voices)):
                failures.append(f"{provider_id}.voice_models.{model}: duplicate voices")
            unknown = set(voices) - all_voices
            if unknown:
                failures.append(f"{provider_id}.voice_models.{model}: unknown voices {sorted(unknown)!r}")
        default_model = defaults.get("tts_model") or defaults.get("model")
        default_voice = defaults.get("voice")
        if defaults.get("tts_model") and default_voice is not None and provider.get("models", {}).get("tts") and provider.get("voices", {}).get("tts") and default_model not in voice_models:
            failures.append(f"{provider_id}: default TTS model {default_model!r} requires a voice_models compatibility entry")
        if default_model in voice_models and default_voice is not None and default_voice not in voice_models[default_model]:
            failures.append(f"{provider_id}: default voice {default_voice!r} is incompatible with {default_model!r}")
    return failures


def _default_value(node: ast.expr, data: dict[str, Any]) -> Any:
    """Read a literal/default lookup without importing application code."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "Field":
            default = next((kw.value for kw in node.keywords if kw.arg == "default"), None)
            if default is None:
                raise ValueError("Field has no explicit default")
            return _default_value(default, data)
        if node.func.id == "provider_default" and len(node.args) == 2:
            provider, field = (ast.literal_eval(arg) for arg in node.args)
            return data["providers"][provider]["defaults"][field]
    return ast.literal_eval(node)


def check_runtime(root: Path, data: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    module = ast.parse(_read(root, "src/config.py"))
    classes = {node.name: node for node in module.body if isinstance(node, ast.ClassDef)}
    for name, provider in RUNTIME_BINDINGS.items():
        if provider not in data["providers"]:
            failures.append(f"runtime binding {name}: missing catalog provider {provider!r}")
            continue
        cls = classes.get(name)
        if cls is None:
            failures.append(f"runtime binding: missing config class {name}")
            continue
        fields = {node.target.id: node.value for node in cls.body if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)}
        owned = RUNTIME_OWNED_FIELDS.get(name, set(data["providers"][provider]["defaults"]))
        for field in owned:
            if field not in fields:
                failures.append(f"{name}: catalog default {field!r} has no runtime field")
            if field not in data["providers"][provider]["defaults"]:
                failures.append(f"{name}: owned default {field!r} is absent from the catalog")
        for field, expected in data["providers"][provider]["defaults"].items():
            if field not in fields:
                # A catalog provider may serve two dedicated capability classes.
                continue
            try:
                actual = _default_value(fields[field], data)
            except (ValueError, TypeError, KeyError) as exc:
                failures.append(f"{name}.{field}: cannot verify default: {exc}")
                continue
            if actual != expected:
                failures.append(f"{name}.{field}: runtime default {actual!r} != catalog {expected!r}")
    for provider, cls_name, required in (
        ("openai", "OpenAIProviderConfig", {"tts_transport", "tts_realtime_timeout_sec", "reasoning_effort"}),
        ("deepgram", "DeepgramProviderConfig", {"tts_version"}),
    ):
        if cls_name not in classes or provider not in data["providers"]:
            continue
        fields = {node.target.id for node in classes[cls_name].body if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)}
        for field in required & data["providers"][provider]["defaults"].keys() - fields:
            failures.append(f"{cls_name}: catalog default {field!r} has no runtime field")
    return failures


def check_sample(root: Path, data: dict[str, Any]) -> list[str]:
    # PyYAML is already a project dependency in the CI job running this check.
    import yaml
    config = yaml.safe_load(_read(root, "config/ai-agent.yaml"))
    failures: list[str] = []
    providers = config.get("providers", {})
    if not isinstance(providers, dict):
        return ["sample configuration providers must be an object"]
    for key, (catalog_id, fields) in SAMPLE_BINDINGS.items():
        if key not in providers:
            continue
        expected_defaults = data["providers"][catalog_id]["defaults"]
        sample = providers[key]
        if not isinstance(sample, dict):
            failures.append(f"sample provider {key}: expected an object")
            continue
        for field in fields & expected_defaults.keys():
            if field not in sample:
                # Omission inherits the verified runtime default.
                continue
            if sample[field] != expected_defaults[field]:
                failures.append(f"sample {key}.{field}: {sample[field]!r} != catalog default {expected_defaults[field]!r}")
    return failures


def check_local_catalog(root: Path) -> list[str]:
    catalog = _load_module(root / "admin_ui/backend/api/models_catalog.py", "provider_check_local_catalog")
    failures: list[str] = []
    kokoro = catalog.KOKORO_TTS_MODELS[0]
    voices = kokoro.get("voice_files", {})
    if len(voices) != 54:
        failures.append(f"Kokoro catalog must contain all 54 verified v1.0 voices, found {len(voices)}")
    for metadata in ("voice_languages", "voice_lang_codes"):
        if set(kokoro.get(metadata, {})) != set(voices):
            failures.append(f"Kokoro {metadata} must describe every downloadable voice")
    for voice, url in voices.items():
        if not re.fullmatch(r"[abefhijpz][fm]_[a-z]+", voice):
            failures.append(f"Kokoro invalid voice id {voice!r}")
        if url != f"https://huggingface.co/hexgrad/Kokoro-82M/resolve/main/voices/{voice}.pt":
            failures.append(f"Kokoro {voice}: voice file URL does not match its id")
        prefix = voice[0]
        if kokoro.get("voice_lang_codes", {}).get(voice) != prefix:
            failures.append(f"Kokoro {voice}: incorrect KPipeline language code")
        if kokoro.get("voice_languages", {}).get(voice) != catalog.KOKORO_LANGUAGE_CODES.get(prefix):
            failures.append(f"Kokoro {voice}: incorrect language metadata")
    if set(kokoro.get("supported_languages", [])) != set(kokoro.get("voice_languages", {}).values()):
        failures.append("Kokoro advertised languages must match the downloadable voice languages")
    if "af_heart" not in voices:
        failures.append("Kokoro default af_heart is missing")
    constants = ast.parse(_read(root, "local_ai_server/constants.py"))
    language_codes = next(
        (ast.literal_eval(node.value) for node in constants.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "KOKORO_LANGUAGES" for target in node.targets)),
        (),
    )
    if set(language_codes) != set(catalog.KOKORO_LANGUAGE_CODES):
        failures.append("Kokoro server language codes drift from the voice catalog")
    server_constants = _load_module(root / "local_ai_server/constants.py", "provider_check_local_constants")
    if server_constants.KOKORO_STOCK_VOICES != set(voices):
        failures.append("Kokoro server stock voices drift from the downloadable voice catalog")
    for group in (catalog.VOSK_STT_MODELS, catalog.PIPER_TTS_MODELS):
        identifiers = [entry["id"] for entry in group]
        if len(identifiers) != len(set(identifiers)):
            failures.append("Local model catalog contains duplicate identifiers")
    return failures


def check_generated(root: Path) -> list[str]:
    generator = _load_module(root / "scripts/generate_provider_catalog.py", "provider_check_generator")
    failures: list[str] = []
    for path, expected in generator.generated_files(root).items():
        if not path.exists() or path.read_text(encoding="utf-8") != expected:
            failures.append(f"generated catalog {path.relative_to(root)} is stale; run scripts/generate_provider_catalog.py")
    return failures


def check_upstream(root: Path) -> list[str]:
    """Optional online identity comparison; performs no paid requests."""
    local = _load_module(root / "admin_ui/backend/api/models_catalog.py", "provider_check_upstream_catalog")
    failures: list[str] = []
    comparisons = (
        ("Kokoro", "https://huggingface.co/api/models/hexgrad/Kokoro-82M/tree/main/voices", {v for v in local.KOKORO_TTS_MODELS[0]["voice_files"]}),
        ("Piper", "https://huggingface.co/rhasspy/piper-voices/raw/v1.0.0/voices.json", {v["model_path"].removesuffix(".onnx") for v in local.PIPER_TTS_MODELS}),
        ("Vosk", "https://alphacephei.com/vosk/models", {v["model_path"] for v in local.VOSK_STT_MODELS}),
    )
    for name, url, current in comparisons:
        try:
            with urlopen(url, timeout=30) as response:  # nosec: fixed public publisher URLs, no credentials
                text = response.read(4_000_000).decode("utf-8")
            if name == "Kokoro":
                official = {Path(item["path"]).stem for item in json.loads(text) if item["path"].endswith(".pt")}
            elif name == "Piper":
                official = set(json.loads(text))
            else:
                official = set(re.findall(r"vosk-model[\w.-]+(?=\.zip)", text))
            if current - official:
                failures.append(f"{name}: unknown upstream ids {sorted(current - official)!r}")
            if name != "Vosk" and official - current:
                failures.append(f"{name}: missing upstream ids {sorted(official - current)!r}")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            failures.append(f"{name}: public upstream comparison failed: {exc}")
    return failures


def check_repository(root: Path = ROOT, *, upstream: bool = False) -> list[str]:
    try:
        data = json.loads(_read(root, CATALOG_PATH))
        failures = check_catalog(data)
        if failures:
            return failures
        failures.extend(check_generated(root))
        failures.extend(check_runtime(root, data))
        failures.extend(check_sample(root, data))
        failures.extend(check_local_catalog(root))
        if upstream:
            failures.extend(check_upstream(root))
        return failures
    except (OSError, SyntaxError, ValueError, KeyError, TypeError) as exc:
        return [f"cannot verify provider catalogs: {exc}"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-upstream", action="store_true", help="also compare credential-free publisher catalogs (requires internet)")
    args = parser.parse_args()
    failures = check_repository(upstream=args.check_upstream)
    if failures:
        print("provider model/voice catalogs are inconsistent:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("provider-models: cloud catalogs, generated UI data, runtime defaults, sample config and local voices are synchronized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
